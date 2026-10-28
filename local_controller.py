"""Local-only model lifecycle and agent configuration bridge. No inference on status."""
from __future__ import annotations
import concurrent.futures, contextlib, fcntl, hashlib, json, os, pathlib, re, site, socket, subprocess, sys, tempfile, time, urllib.parse, urllib.request

from runtime_config import HOME, DATA_DIR, settings, merge
CONFIG = settings()
PATHS = CONFIG['paths']
PAYLOAD = pathlib.Path(PATHS['hermes_app'])/'Contents/Resources/agent-payload'
if not os.environ.get('LMC_DISABLE_HERMES'):
    sys.path.insert(0,str(PAYLOAD/'hermes-agent'))
    sys.path.append(str(HOME/'.hermes/hermes-agent'))
import psutil, yaml
ROOT = DATA_DIR
TF_CONFIG = CONFIG['tensorfold']
TF = pathlib.Path(TF_CONFIG['command']).expanduser()
LMS = pathlib.Path(PATHS['lms'])
DSH = pathlib.Path(PATHS['dsh_source'])
DSH_HOME = pathlib.Path(PATHS['dsh_home'])
NODE = pathlib.Path(PATHS['node'])
TF_ID = TF_CONFIG['model_id']
PREFERENCES = {'memory_limit':90, 'evict_idle':False, 'monitor_speeds':True}

def run(args, timeout=15, **kw):
    p = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout, **kw)
    if p.returncode:
        message = (p.stderr or p.stdout or '操作未完成').strip()[-1800:]
        # Do not expose command lines, headers or credentials in UI errors.
        message = re.sub(r'(?i)(bearer\s+|api[-_]?key[=: ]+)[\w.-]+', r'\1[已隐藏]', message)
        raise RuntimeError(message)
    return p.stdout

def read_json(path, default=None):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return default

def read_yaml(path):
    if not path.exists(): return {}
    value = yaml.safe_load(path.read_text())
    if value is None: return {}
    if not isinstance(value, dict): raise RuntimeError(f'{path.name} 需要是配置映射，原文件未修改。')
    return value

def atomic(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd,'w') as f: f.write(content); f.flush(); os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)

def save_json(path,value): atomic(path,json.dumps(value,ensure_ascii=False,indent=2))
def preferences(): return {**PREFERENCES, **(read_json(ROOT/'preferences.json',{}) or {})}

def request(base, route, key='', data=None, timeout=3):
    url = urllib.parse.urlsplit(base)
    if url.hostname not in ('127.0.0.1','localhost') or url.scheme != 'http':
        raise RuntimeError('控制器只连接本机回环地址。')
    headers={'Content-Type':'application/json'}
    if key: headers['Authorization']='Bearer '+key
    req=urllib.request.Request(base.rstrip('/')+route, headers=headers, data=None if data is None else json.dumps(data).encode())
    with urllib.request.urlopen(req,timeout=timeout) as f: return json.load(f)

_native_reader_cache=None
_native_reader_checked=False

def native_managed_reader():
    """Use Hermes' own Python for its optional, dependency-sensitive ownership reader."""
    global _native_reader_cache,_native_reader_checked
    if _native_reader_checked:return _native_reader_cache
    _native_reader_checked=True
    tools=PAYLOAD/'tools'
    candidates=sorted(tools.glob('python-*/bin/python3'),reverse=True)
    if not candidates:return None
    script="""import json,pathlib,site,sys
p=pathlib.Path(sys.argv[1])
for folder in (p/'venv/lib').glob('python*/site-packages'):site.addsitedir(str(folder))
sys.path.insert(0,str(p/'hermes-agent'))
from hermes_cli.local_runtime.endpoint import managed_root
print(json.dumps(managed_root()))
"""
    try:
        env={**os.environ,'HERMES_HOME':str(hermes_home())}
        proc=subprocess.run([str(candidates[0]),'-B','-c',script,str(PAYLOAD)],capture_output=True,text=True,timeout=3,env=env)
        if proc.returncode or len(proc.stdout)>16384:return None
        row=json.loads(proc.stdout)
        if not isinstance(row,list) or len(row)!=2 or not all(isinstance(x,str) for x in row):return None
        url=urllib.parse.urlsplit(row[0])
        if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost'):return None
        _native_reader_cache=tuple(row)
    except (OSError,ValueError,TypeError,subprocess.TimeoutExpired):pass
    return _native_reader_cache

def managed():
    explicit=CONFIG['llama'].get('base_url','').rstrip('/').removesuffix('/v1')
    if explicit:
        url=urllib.parse.urlsplit(explicit)
        if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost'):
            raise RuntimeError('控制器只连接本机回环地址。')
        return explicit,os.environ.get(CONFIG['llama']['api_key_env'],'')
    if os.environ.get('LMC_DISABLE_HERMES'):return None
    try:
        from hermes_cli.local_runtime.endpoint import managed_root
        result=managed_root()
        if result:return result
    except (ImportError,SyntaxError):pass
    return native_managed_reader()

def system_memory():
    total=int(run(['/usr/sbin/sysctl','-n','hw.memsize']).strip())
    vm=run(['/usr/bin/vm_stat'])
    page=int(re.search(r'page size of (\d+)',vm).group(1))
    pages={k.strip():int(v) for k,v in re.findall(r'^([^:\n]+):\s+(\d+)\.',vm,re.M)}
    wired=pages.get('Pages wired down',0)*page
    compressed=pages.get('Pages occupied by compressor',0)*page
    # Active + wired + physical compressor: caches on inactive pages stay reclaimable.
    used=min(total,(pages.get('Pages active',0)*page)+wired+compressed)
    swap=run(['/usr/sbin/sysctl','vm.swapusage'])
    match=re.search(r'used = ([\d.]+)([MGT])',swap)
    swapped=float(match.group(1))*{'M':2**20,'G':2**30,'T':2**40}[match.group(2)] if match else 0
    try: pressure=int(run(['/usr/sbin/sysctl','-n','kern.memorystatus_vm_pressure_level']).strip())
    except Exception: pressure=0
    return {'total':total,'used':used,'available':total-used,'percent':100*used/total,'wired':wired,'compressed':compressed,'swap':int(swapped),'pressure':{1:'正常',2:'偏高',4:'紧张'}.get(pressure,'未知')}

def lm_inventory(scan=False):
    cache=ROOT/'lm-inventory.json'
    if scan:
        items=json.loads(run([LMS,'ls','--json'],timeout=30))
        save_json(cache,items)
    else: items=read_json(cache,[])
    return [m for m in items if m.get('type')=='llm']

def llama_snapshot():
    root=managed()
    if not root: return {'models':[],'root':None,'error':'Hermes 本地运行时未启动'}
    try:
        models=request(root[0],'/models',root[1]).get('data',[])
        result=[]
        for m in models:
            value=m.get('status',{}).get('value','unknown')
            busy=None
            if value in ('loaded','ready'):
                try:
                    slots=request(root[0],'/slots?'+urllib.parse.urlencode({'model':m['id']}),root[1])
                    busy=any(s.get('is_processing') for s in slots)
                except Exception: pass
            # Weight bytes, not process RSS: wired Metal allocations do not appear fully in RSS.
            meta=m.get('meta',{})
            result.append({'id':m['id'],'value':value,'busy':busy,'weight':meta.get('size',0),'vision':'image' in m.get('architecture',{}).get('input_modalities',[])})
        return {'models':result,'root':root[0],'error':''}
    except Exception: return {'models':[],'root':root[0],'error':'Hermes 运行时暂时无法读取'}

def tf_snapshot():
    if not TF_CONFIG['enabled']:return {'disabled':True}
    try: return json.loads(run([TF,'status'],timeout=8))
    except Exception: return {'error':'TensorFold 状态读取失败'}

def lm_snapshot():
    # lms ps wakes the app. Status must not start applications or load models.
    running=any(p.info.get('name','') in ('LM Studio','llmster','lms') for p in psutil.process_iter(['name']))
    if not running: return {'running':False,'models':[]}
    try:
        loaded=json.loads(run([LMS,'ps','--json'],timeout=8))
        server=json.loads(run([LMS,'server','status','--json'],timeout=8))
        port=server.get('port',1234); api_ids=[];api_error=''
        if server.get('running'):
            try: api_ids=[x['id'] for x in request(f'http://127.0.0.1:{port}','/v1/models').get('data',[])]
            except Exception: api_error='API 无法访问或需要认证'
        return {'running':True,'models':loaded,'api_running':bool(server.get('running')),'api_ids':api_ids,'port':port,'api_error':api_error}
    except Exception: return {'running':True,'models':[],'error':'LM Studio 状态暂不可读'}

def compact_model(key,name,engine,model_id,url,context,estimate,vision=True):
    return {'id':key,'name':name,'engine':engine,'model_id':model_id,'base_url':url,'context':context,'estimated_bytes':estimate,'vision':vision,'state':'stopped','state_text':'未加载','memory_bytes':None,'memory_kind':'','busy':False,'can_load':True,'can_release':False,'detail':'','error':''}

def models_snapshot(tf,llama,lm):
    models=[]
    m=compact_model(TF_CONFIG['key'],TF_CONFIG['name'],'TensorFold',TF_ID,TF_CONFIG['base_url'],TF_CONFIG['context'],TF_CONFIG['estimated_bytes'],TF_CONFIG['vision'])
    h=tf.get('health') or {}
    owns=bool(tf.get('pid'))
    if h and (h.get('model')!=TF_ID or not owns): m.update(state='foreign',state_text='端口被其他服务占用',can_load=False)
    elif h and h.get('status')=='ok' and not h.get('warming'):
        live=h.get('live',{}); busy=(live.get('connections',0)+live.get('waiting',0))>0
        m.update(state='ready',state_text='使用中' if busy else '已就绪',busy=busy,can_load=False,can_release=not busy,memory_bytes=h.get('memory',{}).get('active'),memory_kind='运行时报告')
    elif owns or tf.get('launchd'): m.update(state='loading',state_text='加载中',can_load=False,can_release=True)
    elif tf.get('error'): m.update(state='unknown',state_text=tf['error'],can_load=False,busy=None)
    m['detail']=str(m['context']//1024)+'K 上下文'
    if not tf.get('disabled') and (TF_CONFIG['enabled'] or tf.get('health') or tf.get('pid')):models.append(m)
    known=llama['models']
    if not known:
        preset=hermes_home()/'runtimes/llamacpp/presets.ini'
        import configparser
        cfg=configparser.ConfigParser(interpolation=None)
        cfg.read(preset)
        known=[{'id':s,'value':'unknown','busy':None,'weight':0,'vision':bool(cfg[s].get('mmproj'))} for s in cfg.sections()]
    for item in known:
        mid=item['id']; ready=item['value'] in ('loaded','ready'); loading=item['value']=='loading'
        m=compact_model('hermes:'+mid,mid,'Hermes · llama.cpp',mid,(llama['root'] or 'http://127.0.0.1:18434')+'/v1',CONFIG['llama']['context'],int(item['weight']*1.08+2*2**30) if item['weight'] else 0,item['vision'])
        unreadable=bool(llama.get('error') and llama['root'])
        m.update(state='unknown' if unreadable else 'ready' if ready else 'loading' if loading else 'stopped' if llama['root'] else 'offline',state_text='运行时状态未知' if unreadable else ('使用中' if item['busy'] else '已就绪') if ready else '加载中' if loading else '未加载' if llama['root'] else '请先打开 Hermes',busy=item['busy'],can_load=bool(llama['root']) and not unreadable and not ready and not loading,can_release=ready and not unreadable and item['busy'] is False,memory_bytes=item['weight'] if ready else None,memory_kind='权重估算',detail=str(m['context']//1024)+'K 配置上下文')
        models.append(m)
    loaded=lm['models']
    seen=set()
    inventory=lm_inventory()
    inventory.sort(key=lambda item:not any(x.get('modelKey')==item['modelKey'] or x.get('path')==item.get('path') for x in loaded))
    for item in inventory:
        # Different LM Studio aliases can refer to the same resource; prefer a single row.
        # Equal architecture, rounded size and quantization do not prove identical
        # weights (fine-tunes and repacks commonly share those properties).
        identity=item['modelKey']
        if identity in seen: continue
        seen.add(identity)
        key=item['modelKey']; mid=key
        found=next((x for x in loaded if x.get('modelKey')==key or x.get('path')==item.get('path') or x.get('identifier')==key),None)
        if found: mid=found.get('identifier') or found.get('id') or key
        m=compact_model('lmstudio:'+key,item.get('displayName',key),'LM Studio',mid,f"http://127.0.0.1:{lm.get('port',1234)}/v1",min(65536,item.get('maxContextLength',65536)),int(item.get('sizeBytes',0)*1.10+4*2**30),item.get('vision',False))
        m['load_key']=key
        m.update(detail=f"{item.get('format','').upper()} · {item.get('quantization',{}).get('name','')} · {m['context']//1024}K 上下文")
        if found:
            # New CLI builds report idle/queue state; older builds fall back to confirmation.
            memory=found.get('memoryUsageBytes') or found.get('sizeBytes')
            api_ready=mid in lm.get('api_ids',[])
            activity=found.get('status');queued=found.get('queued')
            busy=(activity!='idle' or queued>0) if isinstance(activity,str) and isinstance(queued,int) else None
            m.update(state='ready' if api_ready else 'resident',state_text=('使用中' if busy else '已就绪') if api_ready else '已加载 · '+(lm.get('api_error') or 'API 未启动'),can_load=not api_ready and not lm.get('api_error'),can_release=busy is not True,busy=busy,memory_bytes=memory,memory_kind='运行时报告' if found.get('memoryUsageBytes') else '权重估算')
        elif lm.get('error'):
            m.update(state='unknown',state_text=lm['error'],can_load=False,busy=None)
        models.append(m)
    return models

def hermes_home():
    configured=pathlib.Path(PATHS['hermes_home'])
    from runtime_config import HOME as original_home
    return HOME/'.hermes' if configured==original_home/'.hermes' else configured

def hermes_config():
    return read_yaml(hermes_home()/'config.yaml')

def agents_snapshot(models):
    agents=[]
    try:
        c=hermes_config(); model=c.get('model',{})
        provider=model.get('provider',''); mid=model.get('default','')
        base=str(model.get('base_url','')).rstrip('/')
        match=next((m for m in models if m['model_id']==mid and m['base_url']==base and (provider=='tensorfold' and m['id'].startswith('tensorfold:') or provider in ('llamacpp','llama.cpp') and m['id'].startswith('hermes:') or provider=='lmstudio' and m['id'].startswith('lmstudio:'))),None)
        running=any('hermes' in str(p.info.get('name') or '').lower() for p in psutil.process_iter(['name']))
        agents.append({'id':'hermes','name':'Hermes','selection':match['id'] if match else '', 'current':match['name'] if match else (mid or '未配置'),'configured':bool(match),'note':'默认配置；已有会话请检查 Hermes 的模型菜单。','running':running,'can_restore':(ROOT/'hermes-last.json').exists()})
    except Exception: agents.append({'id':'hermes','name':'Hermes','selection':'','current':'配置读取失败','configured':False,'note':'请检查 Hermes 配置文件。','running':False,'can_restore':False})
    try:
        c=read_yaml(DSH_HOME/'settings.yaml'); selected=c.get('agent-default-model',{})
        provider=selected.get('provider'); mid=selected.get('model')
        profiles=c.get('llm-pi-ai',{}).get('providers',{})
        url=profiles.get(provider,{}).get('baseURL','')
        match=next((m for m in models if m['model_id']==mid and m['base_url']==url),None)
        agents.append({'id':'dsh','name':'DeepSeek Harness','selection':match['id'] if match else '', 'current':match['name'] if match else (mid or '尚未分配本地模型'),'configured':bool(match),'note':'设置作用于新会话；旧会话保留自己的模型。','running':dsh_running(),'can_restore':(ROOT/'dsh-last.json').exists()})
    except Exception: agents.append({'id':'dsh','name':'DeepSeek Harness','selection':'','current':'配置读取失败','configured':False,'note':'请检查 settings.yaml。','running':False,'can_restore':False})
    return agents

def dsh_running():
    state=read_json(ROOT/'dsh-process.json',{}) or {}
    try:
        p=psutil.Process(state.get('pid',-1))
        return p.create_time()==state.get('created') and str(DSH/'apps/cli/lib/bin.js') in p.cmdline()
    except (psutil.Error, ValueError): return False

def recheck_unattributed_llama(models,home):
    """Reconcile the small timing gap between engine and caller snapshots once."""
    unknown=[m for m in models if m['id'].startswith('hermes:') and m['state']=='ready'
             and m.get('busy') is True and not m.get('activities')]
    if not unknown:return
    fresh=llama_snapshot()
    if fresh.get('error'):return
    by_id={x['id']:x for x in fresh.get('models',[])}
    retry=False
    for m in unknown:
        state=by_id.get(m['model_id'],{})
        if state.get('value') not in ('loaded','ready'):continue
        if state.get('busy') is False:
            m.update(busy=False,state_text='已就绪',can_release=True,
                     activity_state='idle',activity_note='暂无进行中的 Agent 任务')
        elif state.get('busy') is True:retry=True
    if retry:
        import activity
        activity.attach(models,home)

def snapshot():
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(f) for f in (tf_snapshot,llama_snapshot,lm_snapshot,system_memory)]
        tf,llama,lm,memory=[f.result() for f in futures]
    models=models_snapshot(tf,llama,lm)
    import performance
    def metric_text(route):
        endpoint=managed()
        if not endpoint: raise RuntimeError('运行时未启动')
        req=urllib.request.Request(endpoint[0]+route,headers={'Authorization':'Bearer '+endpoint[1]})
        with urllib.request.urlopen(req,timeout=2) as response: return response.read(131072).decode()
    if preferences()['monitor_speeds']:
        try: performance.attach(models,tf,llama,ROOT,metric_text,read_json,save_json,HOME)
        except Exception:
            for m in models:
                m.setdefault('input_tps',None);m.setdefault('output_tps',None);m.setdefault('speed_label','统计暂不可用')
    else:
        for m in models:m.update(input_tps=None,output_tps=None,speed_label='监测已关闭')
    import activity
    activity.attach(models,HOME)
    recheck_unattributed_llama(models,HOME)
    import usage_history
    history_note=usage_history.capture(ROOT,HOME,models,memory)
    return {'models':models,'agents':agents_snapshot(models),'memory':memory,'preferences':preferences(),'updated':time.strftime('%H:%M:%S'),'history_note':history_note}

def find_model(key):
    s=snapshot(); model=next((m for m in s['models'] if m['id']==key),None)
    if not model: raise RuntimeError('该模型已不在本机列表，请刷新。')
    return s,model

def release(model, allow_unknown=False):
    _,model=find_model(model['id'])
    if model['state'] in ('foreign','unknown'): raise RuntimeError('不能释放归属或状态不明的服务。')
    if model['state'] not in ('ready','loading','resident'): return
    if model['busy'] is True: raise RuntimeError(model['name']+' 正在处理请求，请完成后再释放。')
    if model['id'].startswith('lmstudio:'):
        if model['busy'] is None and not allow_unknown: raise RuntimeError('LM Studio 未提供可靠的请求状态。请确认它没有任务后，再使用“确认释放”。')
        run([LMS,'unload',model['model_id']],timeout=120)
    elif model['id'].startswith('tensorfold:'):
        if not model['can_release']: raise RuntimeError('不能释放未确认归属或请求状态的模型。')
        run([TF,'stop'],timeout=50)
    else:
        root=managed()
        if not root or model['busy'] is not False: raise RuntimeError('尚未确认模型空闲，本次没有释放。')
        request(root[0],'/models/unload',root[1],{'model':model['model_id']},timeout=120)
        for _ in range(60):
            current=llama_snapshot()
            item=next((x for x in current['models'] if x['id']==model['model_id']),None)
            if item and item['value'] not in ('loaded','ready','unloading'): break
            time.sleep(.3)
        else: raise RuntimeError('已发送释放请求，但模型仍未卸载，请稍后刷新。')

def load(key):
    s,m=find_model(key)
    if m['state']=='ready': return {'message':'模型已经就绪。'}
    if not m['can_load']: raise RuntimeError('模型目前不能加载：'+m['state_text'])
    if key.startswith('lmstudio:') and m['state']=='resident':
        run([LMS,'server','start'],timeout=30)
        _,now=find_model(key)
        if now['state']!='ready': raise RuntimeError('API 服务已请求启动，但模型身份尚未核验通过。请刷新。')
        return {'message':'LM Studio 推理 API 已启动，现有模型未重复加载。'}
    prefs=preferences()
    if prefs['evict_idle']:
        others=[x for x in s['models'] if x['id']!=key and x['state'] in ('ready','resident')]
        # Check ALL before mutating any, so a busy model cannot cause partial eviction.
        if any(x['busy'] is not False for x in others): raise RuntimeError('其他模型正在使用，或无法确认是否空闲。请稍后加载；没有释放任何模型。')
        for other in others: release(other)
        s,m=find_model(key)
    budget=s['memory']['total']*prefs['memory_limit']/100-s['memory']['used']
    if m['estimated_bytes']<=0:raise RuntimeError('无法估算加载内存；请配置模型大小后再加载。')
    if m['estimated_bytes']>budget:
        raise RuntimeError(f"预计还需约 {m['estimated_bytes']/2**30:.1f} GiB，加载保护预算剩余 {max(0,budget)/2**30:.1f} GiB。请先释放其他模型，或在内存设置中调整上限。")
    if key.startswith('tensorfold:'): run([TF,'start'],timeout=30)
    elif key.startswith('hermes:'):
        root=managed()
        if not root: raise RuntimeError('请先打开 Hermes，等待本地运行时启动。')
        request(root[0],'/models/load',root[1],{'model':m['model_id']},timeout=600)
    else:
        run([LMS,'load',m['load_key'],'--identifier',m['load_key'],'--context-length',str(m['context']),'--yes'],timeout=600)
        run([LMS,'server','start'],timeout=30)
    # A process launch is not a readiness claim.
    for _ in range(180):
        _,now=find_model(key)
        if now['state']=='ready': return {'message':m['name']+' 已加载。'}
        if now['state'] in ('foreign','unknown'): raise RuntimeError(now['state_text'])
        time.sleep(1)
    raise RuntimeError('加载尚未完成，请刷新状态或查看运行日志。')

def backup(path,kind):
    if path.exists():
        content=path.read_text()
        atomic(ROOT/'backups'/f'{kind}-{time.time_ns()}.yaml',content)

def save_hermes_config(patch,merge_existing=True):
    current=hermes_config() if merge_existing else {}
    atomic(hermes_home()/'config.yaml',yaml.safe_dump(merge(current,patch),allow_unicode=True,sort_keys=False))

def assign_hermes(model):
    before=hermes_config()
    key='local-controller'
    if model['id'].startswith('hermes:'):
        root=managed()
        if not root: raise RuntimeError('请先打开 Hermes 本地运行时，再分配此模型。')
        provider='llamacpp'; key=root[1]; base=root[0]+'/v1'
    elif model['id'].startswith('tensorfold:'): provider='tensorfold'; base=model['base_url']
    else: provider='lmstudio'; base=model['base_url']
    backup(hermes_home()/'config.yaml','hermes')
    save_json(ROOT/'hermes-last.json',{'model':before.get('model')})
    mid=model['model_id']
    patch={'model':{'provider':provider,'base_url':base,'default':mid,'api_key':key,'context_length':model['context'],'api_mode':'chat_completions'},'providers':{provider:{'base_url':base,'api_key':key,'api_mode':'chat_completions','models':{mid:{'context_length':model['context'],'supports_vision':model['vision'],'supports_tools':True}}}}}
    if provider=='tensorfold': patch['agent']={'reasoning_overrides':{mid:'none'}}
    save_hermes_config(patch,merge_existing=True)
    after=hermes_config()
    assert after.get('model',{}).get('default')==mid,'Hermes 配置回读失败'
    for key in ('browser','computer_use'): assert after.get(key)==before.get(key),key+' 配置回读不一致'
    assert after.get('agent',{}).get('environment_hint')==before.get('agent',{}).get('environment_hint'),'环境说明回读不一致'

def set_env(name,value):
    path=DSH_HOME/'.env'
    text=path.read_text() if path.exists() else ''
    line=name+'='+value
    if re.search(r'^'+re.escape(name)+r'=',text,re.M): text=re.sub(r'^'+re.escape(name)+r'=.*$',lambda _:line,text,flags=re.M)
    else: text=text.rstrip('\n')+'\n'+line+'\n'
    atomic(path,text)

def assign_dsh(model):
    path=DSH_HOME/'settings.yaml'
    before=read_yaml(path)
    config=json.loads(json.dumps(before))
    route='local-controller-'+model['id'].split(':')[0]
    env_name='LOCAL_CONTROLLER_NO_KEY'
    if model['id'].startswith('hermes:'):
        root=managed()
        if not root: raise RuntimeError('请先打开 Hermes 本地运行时。')
        base=root[0]+'/v1'; env_name='LOCAL_CONTROLLER_HERMES_KEY'; secret=root[1]
    else: base=model['base_url']; secret='local-only'
    backup(path,'dsh')
    save_json(ROOT/'dsh-last.json',{'selection':before.get('agent-default-model')})
    set_env(env_name,secret)
    providers=config.setdefault('llm-pi-ai',{}).setdefault('providers',{})
    catalog={entry['id']:entry for entry in providers.get(route,{}).get('models',[]) if isinstance(entry,dict) and entry.get('id')}
    catalog[model['model_id']]={'id':model['model_id'],'name':model['name'],'contextWindow':model['context'],'maxTokens':4096,'input':['text','image'] if model['vision'] else ['text'],'reasoningEfforts':False}
    providers[route]={'displayName':model['engine']+' · 本机','apiKeyEnv':env_name,'api':'openai-completions','baseURL':base,'compat':{'supportsDeveloperRole':False,'maxTokensField':'max_tokens','supportsStore':False},'models':list(catalog.values())}
    config['agent-default-model']={'provider':route,'model':model['model_id']}
    atomic(path,yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
    after=read_yaml(path)
    assert after['agent-default-model']==config['agent-default-model'],'Harness 配置回读失败'

def restore(agent):
    if agent=='hermes':
        previous=read_json(ROOT/'hermes-last.json')
        if previous is None: raise RuntimeError('没有上次配置。')
        config=hermes_config(); backup(hermes_home()/'config.yaml','hermes-restore')
        if previous['model'] is None: config.pop('model',None)
        else: config['model']=previous['model']
        save_hermes_config(config,merge_existing=False)
    elif agent=='dsh':
        previous=read_json(ROOT/'dsh-last.json')
        if previous is None: raise RuntimeError('没有上次配置。')
        path=DSH_HOME/'settings.yaml'; config=read_yaml(path);backup(path,'dsh-restore')
        if previous['selection'] is None: config.pop('agent-default-model',None)
        else: config['agent-default-model']=previous['selection']
        atomic(path,yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
    else: raise RuntimeError('未知 Agent。')
    return {'message':'已恢复该 Agent 上一次的默认模型配置。'}

def open_agent(agent):
    if agent=='hermes': run(['/usr/bin/open',PATHS['hermes_app']]); return {'message':'Hermes 已打开。'}
    if agent!='dsh': raise RuntimeError('未知 Agent。')
    if pathlib.Path(PATHS['dsh_app']).exists():
        run(['/usr/bin/open',PATHS['dsh_app']]);return {'message':'DeepSeek Harness 已打开。'}
    if not dsh_running():
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',3080))==0: raise RuntimeError('3080 端口已有其他服务，本次没有启动或替换它。')
        ROOT.mkdir(parents=True,exist_ok=True)
        env=os.environ.copy(); env['DSH_HOME']=str(DSH_HOME)
        env['PATH']=str(NODE.parent)+':'+env.get('PATH','/usr/bin:/bin')
        log=open(ROOT/'dsh.log','ab');os.chmod(ROOT/'dsh.log',0o600)
        p=subprocess.Popen([str(NODE),str(DSH/'apps/cli/lib/bin.js'),'web','--host','127.0.0.1','--port','3080','--no-open'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,cwd=DSH,env=env,start_new_session=True)
        log.close();save_json(ROOT/'dsh-process.json',{'pid':p.pid,'created':psutil.Process(p.pid).create_time()})
        for _ in range(60):
            if p.poll() is not None: raise RuntimeError('Harness 启动未完成，请从更多菜单查看 Harness 日志。')
            try:
                url=dsh_url()
                import http.cookiejar
                opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                with opener.open(url,timeout=1) as f:
                    if f.status==200: break
            except Exception: time.sleep(.5)
        else: raise RuntimeError('Harness 仍在启动，请稍后再点“打开”。')
    return {'message':'DeepSeek Harness 已启动，仅监听本机。','open_url':dsh_url()}

def dsh_url():
    # Native browser entry keeps DSH's own launch-token exchange intact.
    text=(ROOT/'dsh.log').read_text()[-65536:]
    urls=re.findall(r'dsh web: (http://127\.0\.0\.1:3080/\?token=[A-Za-z0-9_-]+)',text)
    if not urls: raise RuntimeError('尚未读取到 Harness 的登录入口，请稍后重试。')
    return urls[-1]

def test_model(key):
    _,m=find_model(key)
    if m['state']!='ready' or m['busy'] is True: raise RuntimeError('请先加载模型，并等待当前请求结束。')
    root=managed() if key.startswith('hermes:') else None
    key_value=root[1] if root else ''
    base=root[0] if root else m['base_url'].removesuffix('/v1')
    result=request(base,'/v1/chat/completions',key_value,{'model':m['model_id'],'messages':[{'role':'user','content':'只回复 OK'}],'max_tokens':16,'temperature':0},timeout=60)
    message=result['choices'][0]['message']
    answer=(message.get('content') or '').strip()
    if not answer:
        raise RuntimeError('端点可访问，但未返回最终答复，短测试可能被思考过程耗尽；不计为测试通过。')
    if answer!='OK':
        raise RuntimeError('端点可访问，但最终答复未符合仅回复 OK 的要求；不计为测试通过。')
    return {'message':m['name']+' 实测响应：OK'}

def main(args):
    action=args[0]
    if action=='settings-open':
        path=ROOT/'settings.json'
        if not path.exists():save_json(path,settings())
        return {'message':'连接设置已打开。','open_url':path.as_uri()}
    if action=='status': return snapshot()
    if action=='history':
        import usage_history
        return usage_history.report(ROOT,args[1] if len(args)>1 else 'all',int(args[2]) if len(args)>2 else 0,offset=int(args[3]) if len(args)>3 else 0)
    if action=='history-export':
        import usage_history
        path=usage_history.export_csv(ROOT,args[1],int(args[2]),args[3])
        return {'message':'使用历史 CSV 已导出。','path':path}
    if action=='conversation':
        import conversation
        return {'target':conversation.destination(HOME,args[1],args[2])}
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Prevent duplicate actions from two controller instances or fast repeated clicks.
    with (ROOT/'actions.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('另一项模型操作还在进行，请稍候。')
        if action=='load': return load(args[1])
        if action=='release':
            _,m=find_model(args[1]);release(m,allow_unknown='--confirm-unknown' in args)
            return {'message':m['name']+' 已释放。后续 Agent 请求可能按需重新加载。'}
        if action=='release_idle':
            s=snapshot(); released=[]; skipped=[]
            for m in s['models']:
                if m['state']!='ready': continue
                if m['busy'] is not False: skipped.append(m['name']);continue
                release(m);released.append(m['name'])
            text='已释放：'+('、'.join(released) or '没有可释放的空闲模型')
            if skipped: text+='。保留使用中或状态不明的模型：'+'、'.join(skipped)
            return {'message':text}
        if action=='assign':
            if args[1] not in ('hermes','dsh'): raise RuntimeError('未知 Agent。')
            _,model=find_model(args[2])
            if model['state'] in ('foreign','unknown'): raise RuntimeError('模型归属或运行状态不明，请刷新后再分配。')
            if args[1]=='hermes': assign_hermes(model)
            else: assign_dsh(model)
            return {'message':'默认模型已保存。'+('该模型尚未加载，请在模型管理中加载。' if model['state']!='ready' else '请新建会话使用。')}
        if action=='restore': return restore(args[1])
        if action=='open': return open_agent(args[1])
        if action=='test': return test_model(args[1])
        if action=='preferences':
            limit=int(args[1]);evict=args[2]=='true'
            if not 60<=limit<=95: raise RuntimeError('内存保护上限应在 60% 至 95% 之间。')
            monitor=args[3]=='true' if len(args)>3 else True
            save_json(ROOT/'preferences.json',{'memory_limit':limit,'evict_idle':evict,'monitor_speeds':monitor})
            return {'message':'加载保护设置已保存；只影响本控制器发起的加载。'}
        if action=='scan': lm_inventory(scan=True); return {'message':'本机 LM Studio 模型列表已更新。'}
        raise RuntimeError('未知操作。')

if __name__=='__main__':
    try: print(json.dumps(main(sys.argv[1:]),ensure_ascii=False))
    except Exception as e:
        print(json.dumps({'error':str(e)},ensure_ascii=False)); sys.exit(1)
