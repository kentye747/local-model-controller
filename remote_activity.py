"""Fresh, bounded read of a deployed metadata-only probe over existing SSH."""
import json
import pathlib
import subprocess
import time
import urllib.parse
import psutil

import shlex
from runtime_config import settings
REMOTE=settings()['remote']
HOST=REMOTE['ssh_host']
COMMAND=shlex.quote(REMOTE['python'])+' -B '+shlex.quote(REMOTE['probe_path'])
GATEWAY_SHA=REMOTE['gateway_sha256']
EXPECTED_HOST=REMOTE['expected_hostname']

def parse_forward(value):
    parts=value.split(':')
    if len(parts)==3:remote,host,local=parts
    elif len(parts)==4:
        bind,remote,host,local=parts
        if bind not in ('127.0.0.1','localhost'):return None
    else:return None
    if host not in ('127.0.0.1','localhost'):return None
    try:remote,local=int(remote),int(local)
    except ValueError:return None
    return (remote,local) if 0<remote<=65535 and 0<local<=65535 else None

def forward_routes():
    # Read the actual running SSH command, not just its launchd configuration.
    found=[]
    for p in psutil.process_iter(['exe','cmdline']):
        try:
            args = p.info.get('cmdline') or []
            if p.info.get('exe') != '/usr/bin/ssh' or not args or args[-1] != HOST:continue
            values=[args[i+1] for i in range(len(args)-1) if args[i]=='-R']
            values.extend(a[2:] for a in args if a.startswith('-R') and len(a)>2)
            active={c.raddr.port for c in p.net_connections(kind='tcp') if c.raddr
                    and c.raddr.ip in ('127.0.0.1','::1') and c.status=='ESTABLISHED'}
            for value in values:
                pair=parse_forward(value)
                if pair:found.append({'remote_port':pair[0],'local_port':pair[1],
                                      'transport_pid':p.pid,'connected':pair[1] in active})
        except (OSError,psutil.Error):continue
    # Reject conflicting destinations for one mini endpoint.
    return [row for row in found if len({r['local_port'] for r in found
            if r['remote_port']==row['remote_port']})==1]

def tunnel_alive():
    return any(r['remote_port']==19434 and r['local_port']==18434 for r in forward_routes())

def direct_rows(data,models,forwards,now):
    found=[]
    def target(port,mid=None):
        local={f['local_port'] for f in forwards if f['remote_port']==port and f.get('connected')}
        if len(local)!=1:return None
        candidates=[m for m in models if m.get('busy') is True
                    and m['base_url'].rstrip('/')=='http://127.0.0.1:'+str(next(iter(local)))+'/v1'
                    and (not mid or m['model_id']==mid)]
        return candidates[0] if len(candidates)==1 else None
    for s in data.get('direct_sessions',[]):
        try:
            url=urllib.parse.urlsplit(s.get('base_url') or '')
            if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost','::1') or url.path.rstrip('/')!='/v1':continue
            if s.get('agent')!='Hermes' or not s.get('request_connected') or not s.get('owner_pid'):continue
            if not s.get('process_started_at',now+1)<=s.get('lease_acquired_at',0)<=now+2:continue
            if not s.get('lease_expires_at',0)>now:continue
            m=target(url.port,s.get('model_id'))
            if not m:continue
            found.append({'model_key':m['id'],'agent':'Hermes · mini','session_id':s['session_id'],
                'title':s['title'],'phase':s['phase'],'source':'mini Hermes 原生会话租约、PID 与实际 SSH 模型连接',
                'confidence':'live','host':'mini','can_open':False})
        except (KeyError,ValueError,TypeError):continue
    for s in data.get('direct_clients',[]):
        if s.get('agent')!='Hermes' or not s.get('request_connected') or not s.get('owner_pid'):continue
        m=target(s.get('port'))
        if not m or any(x['model_key']==m['id'] for x in found):continue
        found.append({'model_key':m['id'],'agent':'Hermes · mini','session_id':'process-'+str(s['owner_pid']),
            'title':'正在请求模型，任务名暂不可读','phase':'会话详情待确认',
            'source':'mini Hermes 进程与实际 SSH 模型连接；未取得有效会话租约',
            'confidence':'uncertain','host':'mini','can_open':False})
    for row in found:
        if sum(x['model_key']==row['model_key'] for x in found)>1:
            row.update(confidence='uncertain',phase='多个活动会话，请求归属待细分')
    return found

def map_snapshot(data, models, tunnel, now, forwards=None):
    if (data.get('schema') != 1 or data.get('host') != EXPECTED_HOST
            or not isinstance(data.get('generated_at'),(int,float))
            or not 0 <= now-data['generated_at'] <= 8):
        return [],'mini 活动来源已过期或主机未确认'
    direct=direct_rows(data,models,forwards or [],now)
    if data.get('note'):return (direct,'') if direct else ([],data['note'])
    g = data.get('gateway') or {}
    if (not GATEWAY_SHA or not tunnel or g.get('base_url') != 'http://127.0.0.1:19440/v1'
            or g.get('upstream_base') != 'http://127.0.0.1:19434/v1'
            or g.get('source_sha256') != GATEWAY_SHA or g.get('release') != GATEWAY_SHA[:12]):
        return (direct,'') if direct else ([],data.get('direct_note') or 'mini 到 Studio 的模型路由暂不可确认')
    candidates = [m for m in models if m['model_id']==g.get('model_id')
                  and m['base_url'].rstrip('/')=='http://127.0.0.1:18434/v1']
    if len(candidates) != 1:return (direct,'') if direct else ([], 'mini 目标模型暂未匹配')
    # The gateway can fall back to a model on mini. A configured Studio route
    # alone is not evidence that this request actually reached Studio.
    if not g.get('active') or not g.get('upstream_active'):return direct,('' if direct else data.get('direct_note',''))
    found=[]
    for s in data.get('sessions',[]):
        if s.get('base_url') != g['base_url'] or s.get('model_id') != g['model_id']:continue
        if s.get('agent') != 'DSH' or not s.get('owner_pid') or not s.get('session_id'):continue
        if not s.get('request_connected'):continue
        found.append({'model_key':candidates[0]['id'],'agent':'DSH · mini',
            'session_id':s['session_id'],'title':s['title'],'phase':s['phase'],
            'source':'mini 原生会话 · 已核验会话锁与 SSH 模型路由',
            'confidence':'live','host':'mini','can_open':False})
    # A DSH process may own several active sessions. Its TCP socket cannot
    # distinguish those sessions; retain all real candidates and state this.
    if len(found)>1:
        for row in found:
            row.update(confidence='uncertain',phase='活动会话 · 请求归属待细分',
                       source='mini 原生活动会话；同进程并发请求尚无逐会话标识')
    return direct+found,''

def remote_activity(home, models):
    if not REMOTE['enabled']:return [],''
    try:
        forwards=forward_routes()
        ports=','.join(str(p) for p in sorted({r['remote_port'] for r in forwards}))
        p = subprocess.run(['/usr/bin/ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=1',
            '-o','ConnectionAttempts=1',HOST,COMMAND+' --direct-ports '+(ports or '18434,18080')],capture_output=True,text=True,timeout=5)
        if p.returncode or len(p.stdout)>131072:raise ValueError('probe unavailable')
        gateway_forward=any(r['remote_port']==19434 and r['local_port']==18434 for r in forwards)
        return map_snapshot(json.loads(p.stdout),models,gateway_forward,time.time(),forwards=forwards)
    except (OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired):
        return [],'mini 活动状态暂不可读（未沿用旧任务）'
