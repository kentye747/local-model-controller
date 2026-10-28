"""Passive local task attribution. No prompts, transcripts or credentials are cached."""
from __future__ import annotations
import concurrent.futures, json, pathlib, re, socket, sqlite3, time, urllib.parse, urllib.request
import psutil, yaml
from runtime_config import settings, agent_home
HERMES_APP=settings()['paths']['hermes_app'].rstrip('/')+'/'

ACTIVE_HERMES={'working','streaming','starting','resuming','waiting'}

def title_text(value):
    return ' '.join(str(value or '未命名任务').split())[:160]

def canonical(url):
    p=urllib.parse.urlsplit(str(url or ''))
    host='127.0.0.1' if p.hostname=='localhost' else p.hostname
    return (p.scheme,host,p.port or (80 if p.scheme=='http' else 443),p.path.rstrip('/'))

def match_model(models,model_id,base=None,provider=None):
    candidates=[m for m in models if m['model_id']==model_id]
    if base:
        candidates=[m for m in candidates if canonical(m['base_url'])==canonical(base)]
    elif provider:
        prefix={'tensorfold':'tensorfold:','llamacpp':'hermes:','llama.cpp':'hermes:','lmstudio':'lmstudio:'}.get(provider.removeprefix('custom:'))
        if prefix:candidates=[m for m in candidates if m['id'].startswith(prefix)]
    return candidates[0]['id'] if len(candidates)==1 else None

def hermes_rows(sessions,models,routes=None):
    found=[]
    for s in sessions:
        if s.get('status') not in ACTIVE_HERMES:continue
        sid=s.get('session_key') or s.get('id','');route=(routes or {}).get(sid,{})
        key=match_model(models,s.get('model'),route.get('base_url'),route.get('provider'))
        if not key:continue
        phase={'waiting':'等待确认','starting':'准备任务','resuming':'恢复任务'}.get(s['status'],'处理任务')
        found.append({'model_key':key,'agent':'Hermes','session_id':sid,'title':title_text(s.get('title')),'phase':phase,'source':'Hermes 实时会话','confidence':'live'})
    return found

def hermes_activity(home,models):
    # Discover only the existing bundled backend; never start Hermes or trust an arbitrary port.
    ports=[]
    for p in psutil.process_iter(['pid','cmdline','exe']):
        try:
            args=p.info.get('cmdline') or []
            if not any('hermes_cli.main' in a for a in args) or not any(a in ('serve','web') for a in args):continue
            if not str(p.info.get('exe') or '').startswith(HERMES_APP):continue
            ports.extend(c.laddr.port for c in p.net_connections(kind='tcp') if c.status=='LISTEN' and c.laddr.ip=='127.0.0.1')
        except (psutil.Error,OSError):continue
    if not ports:return [],'Hermes 未打开或实时接口不可读'
    import websocket
    op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for port in ports[:2]:
        try:
            with op.open(f'http://127.0.0.1:{port}/',timeout=1.0) as f:html=f.read(150000).decode()
            token_match=re.search(r'window.__HERMES_SESSION_TOKEN__=("[^"]+")',html)
            if not token_match:continue
            token=json.loads(token_match[1]);ws=None
            try:
                ws=websocket.create_connection(f'ws://127.0.0.1:{port}/api/ws?token='+urllib.parse.quote(token),timeout=1.5,suppress_origin=True)
                ws.send(json.dumps({'jsonrpc':'2.0','id':'model-controller-activity','method':'session.active_list','params':{}}))
                deadline=time.monotonic()+2
                while time.monotonic()<deadline:
                    frame=json.loads(ws.recv())
                    if frame.get('id')!='model-controller-activity':continue
                    if 'error' in frame:raise ValueError('live read failed')
                    sessions=frame['result']['sessions'];routes={}
                    active_ids=[s.get('session_key') for s in sessions if s.get('status') in ACTIVE_HERMES]
                    db=agent_home(home,'hermes')/'state.db'
                    if active_ids and db.exists():
                        with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=.2) as conn:
                            for sid in active_ids:
                                row=conn.execute('SELECT model_config,billing_base_url FROM sessions WHERE id=?',(sid,)).fetchone()
                                if row:
                                    config=json.loads(row[0] or '{}');routes[sid]={'provider':config.get('provider'),'base_url':row[1] or config.get('base_url')}
                    return hermes_rows(sessions,models,routes),''
            finally:
                if ws:ws.close()
        except (OSError,ValueError,KeyError,sqlite3.Error,websocket.WebSocketException):continue
    return [],'Hermes 实时接口暂不可读'

def dsh_record(record,model_routes,models,now,modified,owner_alive):
    rows=record.get('rows',{})
    def value(key,default=None):return (rows.get(key) or {}).get('val',default)
    boundary=value('turnBoundary',{}) or {}
    if boundary.get('openTurnStartSeq') is None:return None
    selection=value('modelSelection',{}) or {}
    # pending describes the NEXT step, never the current request.
    used=selection.get('lastUsed') or {}
    bases=model_routes.get(used.get('provider'),[])
    if len(bases)!=1:return None
    key=match_model(models,used.get('model'),base=bases[0])
    if not key:return None
    stats=value('sessionStats',{}) or {}
    step=stats.get('openStep') or {};pending=stats.get('pendingCalls') or {}
    phase='执行工具' if pending else '处理任务'
    if step and not pending:phase='模型请求中'
    # A verified writer that predates this projection can legitimately spend minutes
    # in one tool/request without writing another event. Age alone isn't a heartbeat.
    certain=owner_alive and 0<=modified<=now+2
    return {'model_key':key,'agent':'DSH','session_id':'','title':title_text(value('title')),'phase':phase if certain else '状态待确认','source':'DSH 本机会话记录','confidence':'live' if certain else 'uncertain'}

def dsh_routes(home):
    routes={}
    paths=[agent_home(home,'dsh')/'profiles/desktop/cordis.patch.yml',agent_home(home,'dsh')/'profiles/web/cordis.patch.yml',agent_home(home,'dsh')/'settings.yaml']
    for p in paths:
        if not p.exists():continue
        data=yaml.safe_load(p.read_text())
        if isinstance(data,list):configs=[x.get('config',{}) for x in data if isinstance(x,dict) and x.get('id')=='llm-pi-ai' and not x.get('disabled')]
        elif isinstance(data,dict):configs=[data.get('llm-pi-ai',{})]
        else:continue
        for conf in configs:
            for provider,info in conf.get('providers',{}).items():
                base=info.get('baseURL')
                if base and base not in routes.setdefault(provider,[]):routes[provider].append(base)
    return routes

def dsh_activity(home,models):
    cache=agent_home(home,'dsh')/'storages/session_projcache/sessions'
    if not cache.exists():return [],'DSH 会话状态暂不可读'
    try:
        running=any(p.info.get('name')=='DeepSeek Harness' for p in psutil.process_iter(['name']))
        routes=dsh_routes(home);found=[];now=time.time();errors=False
        # Read only small projection metadata, not session transcripts or business files.
        for path in cache.glob('*.json'):
            try:
                stat=path.stat()
                if stat.st_size>2*1024*1024:errors=True;continue
                data=json.loads(path.read_text());record=data.get('record',{})
                rows=record.get('rows',{});boundary=(rows.get('turnBoundary') or {}).get('val') or {}
                if boundary.get('openTurnStartSeq') is None:continue
                # A running app alone does not prove an old open turn is still alive.
                # Check the actual native session's held ownership file through lsof, read-only.
                owner=False
                if running:
                    session_dirs=list((agent_home(home,'dsh')/'sessions').glob('*/'+path.stem))
                    for folder in session_dirs:
                        try:
                            import subprocess
                            result=subprocess.run(['/usr/sbin/lsof','-w','-n','-Fpc','--',str(folder/'session.lock')],capture_output=True,text=True,timeout=.7)
                            if result.returncode==0 and 'cDeepSeek' in result.stdout:
                                for pid in re.findall(r'^p(\d+)$',result.stdout,re.M):
                                    process=psutil.Process(int(pid))
                                    if (process.exe().startswith('/Applications/DeepSeek Harness.app/')
                                            and process.create_time()<=stat.st_mtime):owner=True;break
                                if owner:break
                        except (OSError,psutil.Error,subprocess.TimeoutExpired):pass
                item=dsh_record(record,routes,models,now,stat.st_mtime,owner)
                if item:
                    item['session_id']=path.stem;found.append(item)
            except (OSError,ValueError,TypeError,KeyError):errors=True
        return found,('部分 DSH 状态暂不可读' if errors else '')
    except (OSError,ValueError,TypeError):return [],'DSH 会话状态暂不可读'

def hermes_cli_activity(home,models):
    """Find active Hermes CLI chat calls by exact model args and live loopback socket."""
    found=[]
    for proc in psutil.process_iter(['pid','exe','cmdline']):
        try:
            exe=proc.info.get('exe') or ''
            args=proc.info.get('cmdline') or []
            if not exe.startswith(HERMES_APP) or 'chat' not in args:
                continue
            def option(name):
                if name not in args:return ''
                i=args.index(name)
                return args[i+1] if i+1<len(args) and not args[i+1].startswith('--') else ''
            if option('--provider') not in ('llamacpp','llama.cpp'):
                continue
            model_id=option('--model')
            if not model_id:
                continue
            ports={c.raddr.port for c in proc.net_connections(kind='tcp')
                   if c.status=='ESTABLISHED' and c.raddr
                   and c.raddr.ip in ('127.0.0.1','::1')}
            matches=[m for m in models if m.get('busy') is True
                     and m['model_id']==model_id
                     and urllib.parse.urlsplit(m['base_url']).port in ports]
            if len(matches)!=1:
                continue
            label=option('--continue')
            if not label:
                workspace=option('--in')
                label=pathlib.Path(workspace).name if workspace else 'Hermes CLI 任务'
            found.append({'model_key':matches[0]['id'],'agent':'Hermes CLI',
                'session_id':'cli-'+str(proc.info['pid']),
                'title':title_text(label),'phase':'处理任务',
                'source':'Hermes CLI 活动进程与模型连接','confidence':'live',
                'host':'studio-cli','can_open':False})
        except (psutil.Error,OSError,ValueError,IndexError,TypeError):
            continue
    for item in found:
        if sum(row['model_key']==item['model_key'] for row in found)>1:
            item['confidence']='uncertain'
            item['phase']='多个 Hermes CLI 任务共用模型，请求归属待细分'
    return found,''

def attach(models,home):
    from remote_activity import remote_activity
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(f,home,models) for f in
                 (hermes_activity,dsh_activity,hermes_cli_activity,remote_activity)]
        results=[]
        for f in futures:
            try:results.append(f.result())
            except Exception:results.append(([],'部分 Agent 状态暂不可读'))
    activities=[a for rows,_ in results for a in rows];notes=[note for _,note in results if note]
    for m in models:
        matches=[{k:v for k,v in a.items() if k!='model_key'} for a in activities if a['model_key']==m['id']]
        m['activities']=matches
        if m['state'] in ('stopped','offline'):m['activity_state']='unloaded';m['activity_note']='模型未加载'
        elif matches:
            m['activity_state']='live' if any(a['confidence']=='live' for a in matches) else 'unknown'
            m['activity_note']='；'.join(notes)
        elif m.get('busy') is True:m['activity_state']='unknown';m['activity_note']='有请求，调用方未确认'
        elif notes:m['activity_state']='unknown';m['activity_note']='；'.join(notes)
        elif m.get('busy') is None:m['activity_state']='unknown';m['activity_note']='调用状态暂不可读'
        else:m['activity_state']='idle';m['activity_note']='暂无进行中的 Agent 任务'
