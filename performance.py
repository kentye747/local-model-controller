"""Passive numeric telemetry only. No prompts, replies or benchmark requests are stored."""
import json, math, pathlib, re, time

def lm_event(event):
    data=event.get('data') or {}
    if data.get('type')!='llm.prediction.output': return None
    stats=data.get('stats') or {};mid=data.get('modelIdentifier')
    if not mid:return None
    inp=positive(stats.get('promptTokensPerSecond'))
    out=positive(stats.get('tokensPerSecond'))
    if inp is None and out is None:return None
    timestamp=(event.get('timestamp') or time.time()*1000)/1000
    label='最近一次 · '+time.strftime('%m-%d %H:%M:%S',time.localtime(timestamp))
    return mid,{'input':inp,'output':out,'label':label,'at':timestamp}

def positive(value):
    try:
        n=float(value)
        return n if math.isfinite(n) and n>0 else None
    except (TypeError, ValueError): return None

def prometheus(text):
    values={}
    for line in text.splitlines():
        if not line or line.startswith('#'): continue
        fields=line.split()
        if len(fields)>=2:
            try: values[fields[0].split('{',1)[0]]=float(fields[1])
            except ValueError: pass
    return {'input':positive(values.get('llamacpp:prompt_tokens_seconds')),
            'output':positive(values.get('llamacpp:predicted_tokens_seconds')),
            'label':'引擎累计均速'}

def lm_log(text):
    """Read only structured completed prediction metrics, attributed to exact model ID."""
    result={}
    pattern=r'\[([^\]\n]+)\] Generated prediction:\s*(\{)'
    for match in re.finditer(pattern,text):
        try: event,_=json.JSONDecoder().raw_decode(text[match.start(2):])
        except ValueError: continue
        if not isinstance(event,dict): continue
        mid=event.get('model') or match.group(1)
        stats=event.get('stats') or {}
        timing=event.get('timings') or {}
        inp=positive(stats.get('prompt_tokens_per_second')) or positive(stats.get('prompt_eval_tokens_per_second')) or positive(timing.get('prompt_per_second'))
        out=positive(stats.get('tokens_per_second')) or positive(stats.get('tokensPerSecond')) or positive(timing.get('predicted_per_second'))
        if inp is None and out is None: continue
        stamp=re.findall(r'\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]',text[max(0,match.start()-100):match.start()])
        import datetime
        timestamp=datetime.datetime.strptime(stamp[-1],'%Y-%m-%d %H:%M:%S').timestamp() if stamp else 0
        result[mid]={'input':inp,'output':out,'label':'最近一次'+(' · '+stamp[-1][5:] if stamp else ''),'at':timestamp}
    return result

def attach(models,tf,llama,root,request_text,read_json,save_json,home):
    for m in models:
        m.update(input_tps=None,output_tps=None,speed_label='未加载' if m['state'] in ('stopped','offline') else '等待统计')
    # TensorFold already includes the rolling live window in the health read.
    m=next((m for m in models if m['id'].startswith('tensorfold:')),None)
    if m and m['state']=='ready':
        live=(tf.get('health') or {}).get('live',{})
        inp=positive(live.get('prefill_tokens_per_second'));out=positive(live.get('decode_tokens_per_second'))
        cached=read_json(root/'tf-last-speed.json',{}) or {}
        if inp is not None or out is not None:
            cached={'input':inp,'output':out,'label':'上次活跃 · '+time.strftime('%m-%d %H:%M:%S')}
            save_json(root/'tf-last-speed.json',cached)
            m.update(input_tps=inp,output_tps=out,speed_label='实时吞吐（含间隔）')
        elif cached: m.update(input_tps=cached.get('input'),output_tps=cached.get('output'),speed_label=cached['label'])
        else: m['speed_label']='空闲 · 等待请求'
    for m in models:
        if m['id'].startswith('hermes:') and m['state']=='ready':
            try:
                import urllib.parse
                data=prometheus(request_text('/metrics?'+urllib.parse.urlencode({'model':m['model_id']})))
                m.update(input_tps=data['input'],output_tps=data['output'],speed_label=data['label'] if data['input'] or data['output'] else '等待请求统计')
            except Exception: m['speed_label']='统计暂不可用'
    # Read at most 256 KiB of the newest server log, only when its file version changes.
    loaded=[m for m in models if m['id'].startswith('lmstudio:') and m['state'] in ('ready','resident')]
    if loaded:
        files=list((home/'.lmstudio/server-logs').glob('*/*.log'))
        cached={'rates':{}}
        if files:
            path=max(files,key=lambda p:p.stat().st_mtime_ns)
            stat=path.stat();signature=[str(path),stat.st_size,stat.st_mtime_ns]
            cached=read_json(root/'lm-speed-cache.json',{}) or {}
            if cached.get('signature')!=signature:
                with path.open('rb') as f:
                    f.seek(max(0,stat.st_size-262144));text=f.read(262144).decode(errors='ignore')
                cached={'signature':signature,'rates':lm_log(text)}
                save_json(root/'lm-speed-cache.json',cached)
        observed=read_json(root/'lm-observed-speed.json',{}) or {}
        for m in loaded:
            candidates=[r for r in [cached.get('rates',{}).get(m['model_id']),observed.get(m['model_id'])] if r]
            rate=max(candidates,key=lambda r:r.get('at',0)) if candidates else None
            if rate: m.update(input_tps=rate['input'],output_tps=rate['output'],speed_label=rate['label'])
            else: m['speed_label']='等待完成请求 · 未提供时显示 —'

def watch_lm(parent_pid):
    """Listen while the GUI lives; discard model output and persist numeric stats only."""
    import local_controller as c
    import selectors, subprocess, signal, os
    owner=c.psutil.Process(parent_pid);birth=owner.create_time();process=None
    def terminate(*_):raise SystemExit
    signal.signal(signal.SIGTERM,terminate)
    def owned():
        try:return owner.is_running() and owner.create_time()==birth
        except c.psutil.Error:return False
    try:
        while owned():
            active=any(p.info.get('name') in ('LM Studio','llmster') for p in c.psutil.process_iter(['name']))
            if not active or not c.preferences()['monitor_speeds']:
                if process and process.poll() is None:process.terminate();process.wait(timeout=3)
                process=None;time.sleep(1);continue
            if process is None or process.poll() is not None:
                if process is not None:time.sleep(2)
                process=subprocess.Popen([str(c.LMS),'log','stream','--source','model','--filter','output','--stats','--json'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                os.set_blocking(process.stdout.fileno(),False)
                pending=b''
            readable=selectors.DefaultSelector();readable.register(process.stdout,selectors.EVENT_READ)
            events=readable.select(timeout=1);readable.close()
            if not events:continue
            chunk=os.read(process.stdout.fileno(),65536)
            if not chunk:time.sleep(.2);continue
            pending+=chunk
            if len(pending)>4*1024*1024:pending=b'';continue
            while b'\n' in pending:
                line,pending=pending.split(b'\n',1)
                try:
                    event=json.loads(line);result=lm_event(event)
                except (ValueError,TypeError):continue
                if result:
                    mid,rate=result
                    saved=c.read_json(c.ROOT/'lm-observed-speed.json',{}) or {}
                    saved[mid]=rate;c.save_json(c.ROOT/'lm-observed-speed.json',saved)
                    import usage_history
                    usage_history.observe_lm_event(c.ROOT,event)
    except (ProcessLookupError,c.psutil.NoSuchProcess):pass
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()

if __name__=='__main__':
    import sys
    if len(sys.argv)==3 and sys.argv[1]=='--watch-lm':watch_lm(int(sys.argv[2]))
