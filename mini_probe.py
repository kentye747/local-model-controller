"""Read-only mini DSH/Hermes metadata probe. No prompts, credentials or inference."""
import datetime
import contextlib
import hashlib
import json
import pathlib
import re
import socket
import os
import stat
import sqlite3
import shlex
import math
import subprocess
import time
import urllib.request
import urllib.parse

SAFE_ID = re.compile(r'[A-Za-z0-9_-]{1,180}\Z')

def run(args, timeout=.6):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                       env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','LC_ALL':'C'})
    return p.stdout if p.returncode == 0 else ''

def socket_pids(port,state):
    raw=run(['/usr/sbin/lsof','-w','-nP','-Fp','-iTCP:'+str(port),'-sTCP:'+state])
    return {int(x) for x in re.findall(r'^p(\d+)$',raw,re.M)}

def hermes_process(home, pid):
    """Prove a local Hermes owner without returning argv or credentials."""
    exe=run(['/bin/ps','-p',str(pid),'-o','comm=']).strip()
    roots=(str(home/'.hermes/tools')+'/', '/Applications/Hermes.app/')
    if not any(exe.startswith(root) for root in roots):return None
    args=shlex.split(run(['/bin/ps','-p',str(pid),'-o','args=']))
    if not any('hermes_cli.main' in arg for arg in args):return None
    stamp=run(['/bin/ps','-p',str(pid),'-o','lstart=']).strip()
    return datetime.datetime.strptime(stamp,'%a %b %d %H:%M:%S %Y').timestamp()

def idle_gateway_pid(home):
    """A live native reply can distinguish an idle keep-alive from active work."""
    path=home/'.hermes/gateway.sock'
    try:
        info=path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid():return None
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as channel:
            channel.settimeout(.5);channel.connect(str(path))
            channel.sendall(b'{"protocol":1,"verb":"active-work","id":"controller-idle","params":{}}\n')
            data=b''
            while b'\n' not in data and len(data)<65536:
                chunk=channel.recv(8192)
                if not chunk:return None
                data+=chunk
        reply=json.loads(data.split(b'\n',1)[0]);result=reply.get('result') or {}
        if (reply.get('ok') is True and reply.get('protocol')==1 and reply.get('id')=='controller-idle'
                and type(result.get('active_agents')) is int and result['active_agents']==0
                and result.get('active_work')==[] and type(result.get('pid')) is int):
            return result['pid']
    except (OSError,ValueError,TypeError,AttributeError):pass
    return None

def native_lease_rows(home, ports, now, process_reader, connection_reader,idle_reader=None):
    """Read only native ownership and session identity, never messages or prompts."""
    root=home/'.hermes'
    databases=[('default',root/'state.db')]
    # Multiplexed gateways keep each named profile's leases in its own DB.
    # Only inspect real immediate profile directories inside this Hermes home.
    profiles=root/'profiles'
    if profiles.exists():
        for folder in sorted(profiles.iterdir()):
            if (SAFE_ID.fullmatch(folder.name) and folder.is_dir() and not folder.is_symlink()
                    and not (folder/'state.db').is_symlink()):
                databases.append((folder.name,folder/'state.db'))
    sessions=[];clients=[];matched=set();identities={};connections={}
    def identity(pid):
        if pid not in identities:identities[pid]=process_reader(home,pid)
        return identities[pid]
    def connected(port):
        if port not in connections:connections[port]=connection_reader(port)
        return connections[port]
    errors=[]
    for profile,db in databases:
        if not db.exists():continue
        try:
            with contextlib.closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=.15)) as conn:
                conn.row_factory=sqlite3.Row
                leases=conn.execute('SELECT conversation_id,holder,acquired_at,expires_at FROM session_turn_leases WHERE expires_at>? LIMIT 64',(now,)).fetchall()
                for lease in leases:
                    match=re.match(r'^pid=(\d+):',str(lease['holder'] or ''))
                    if not match:continue
                    pid=int(match[1]);acquired=float(lease['acquired_at']);expires=float(lease['expires_at'])
                    if not all(math.isfinite(x) for x in (acquired,expires)) or not acquired<=now+2 or expires<=now:continue
                    born=identity(pid)
                    if born is None or born>acquired:continue
                    # The lease's canonical conversation is the authority; never pick the
                    # latest session by timestamp or read a different conversation's title.
                    rows=conn.execute('SELECT id,title,model,billing_base_url FROM sessions WHERE id=? AND ended_at IS NULL',
                                      (lease['conversation_id'],)).fetchall()
                    for row in rows:
                        url=urllib.parse.urlsplit(row['billing_base_url'] or '')
                        if (url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost','::1')
                                or url.path.rstrip('/')!='/v1' or url.port not in ports):continue
                        if pid not in connected(url.port):continue
                        sessions.append({'agent':'Hermes','session_id':row['id'],
                            'title':' '.join(str(row['title'] or 'Hermes 活动会话').split())[:160],
                            'model_id':row['model'],'base_url':'http://127.0.0.1:'+str(url.port)+'/v1',
                            'owner_pid':pid,'process_started_at':born,'lease_acquired_at':acquired,
                            'lease_expires_at':expires,'request_connected':True,
                            'phase':'模型请求中','provenance':'Hermes 原生会话处理租约','profile':profile})
                        matched.add((pid,url.port))
        except (OSError,ValueError,TypeError,sqlite3.Error) as exc:
            errors.append(exc)
    # Preserve a proven caller even when the native session lease is unavailable.
    # Its task stays explicitly unknown rather than borrowing a nearby title.
    idle=None;idle_checked=False
    for port in ports:
        for pid in connected(port):
            if (pid,port) in matched or identity(pid) is None:continue
            if idle_reader is not None and not idle_checked:
                idle=idle_reader();idle_checked=True
            if pid==idle:continue
            clients.append({'agent':'Hermes','owner_pid':pid,'port':port,
                            'request_connected':True})
    if errors and not sessions and not clients:raise errors[0]
    return sessions,clients

def direct_connections(port):
    listeners=socket_pids(port,'LISTEN')
    # macOS rewrites ps's comm to "sshd-session: user". Verify the actual
    # mapped executable instead of treating that display name as a path.
    def sshd(pid):
        files=run(['/usr/sbin/lsof','-w','-a','-p',str(pid),'-d','txt','-Fn'])
        return any(line in ('n/usr/libexec/sshd-session','n/usr/sbin/sshd') for line in files.splitlines())
    if not any(sshd(pid) for pid in listeners):
        return set()
    return socket_pids(port,'ESTABLISHED')-listeners

def snapshot(home=None,direct_ports=None):
    home=home or pathlib.Path.home()
    result={'schema':1,'host':socket.gethostname(),'generated_at':time.time(),'sessions':[],
            'gateway':{},'note':''}
    result.update(direct_sessions=[],direct_clients=[],direct_note='')
    ports=set(direct_ports if direct_ports is not None else (18434,18080))
    try:
        rows,clients=native_lease_rows(home,ports,time.time(),hermes_process,direct_connections,
                                      idle_reader=lambda:idle_gateway_pid(home))
        result.update(direct_sessions=rows,direct_clients=clients)
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,subprocess.TimeoutExpired):
        result['direct_note']='mini Hermes 会话租约暂不可读'
    result['generated_at']=time.time()
    return result

if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--direct-ports',default='18434,18080')
    args=parser.parse_args();ports={int(x) for x in args.direct_ports.split(',') if x}
    if len(ports)>16 or any(p<1 or p>65535 for p in ports):parser.error('invalid direct ports')
    print(json.dumps(snapshot(direct_ports=ports),ensure_ascii=False))
