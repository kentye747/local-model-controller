"""Local numeric model history. Never persist prompts, replies, argv or task titles."""
from __future__ import annotations
import contextlib, csv, datetime as dt, fcntl, hashlib, json, math, os, pathlib, re, sqlite3, time

from zoneinfo import ZoneInfo
from runtime_config import settings, agent_home
TZ=ZoneInfo(settings()['history_timezone']) if settings()['history_timezone'] else None
DB_NAME='usage-history.sqlite3'
LIMIT=16*1024*1024
FIELDS=('input_tokens','cached_tokens','output_tokens','input_seconds','output_seconds',
        'input_tps','output_tps','total_seconds','queue_seconds','ttft_seconds')

def number(value,integer=False):
    if isinstance(value,bool):return None
    try:
        n=float(value)
        return (int(n) if integer else n) if math.isfinite(n) and n>=0 else None
    except (ValueError,TypeError,OverflowError):return None

def rate(value):
    n=number(value)
    return n if n and n>0 else None

def digest(value):return hashlib.sha256(value.encode()).hexdigest()

def connect(root):
    root=pathlib.Path(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    db=root/DB_NAME;c=sqlite3.connect(db,timeout=2);c.row_factory=sqlite3.Row
    os.chmod(db,0o600);c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA busy_timeout=2000')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS models(id TEXT PRIMARY KEY,name TEXT,engine TEXT,native_id TEXT);
    CREATE TABLE IF NOT EXISTS events(
      event_key TEXT PRIMARY KEY,model_key TEXT NOT NULL,completed_at REAL,observed_at REAL NOT NULL,
      input_tokens INTEGER,cached_tokens INTEGER,output_tokens INTEGER,
      input_seconds REAL,output_seconds REAL,input_tps REAL,output_tps REAL,total_seconds REAL,
      queue_seconds REAL,ttft_seconds REAL,finish_reason TEXT,source TEXT,time_basis TEXT);
    CREATE INDEX IF NOT EXISTS event_window ON events(model_key,completed_at);
    CREATE TABLE IF NOT EXISTS cursors(
      id TEXT PRIMARY KEY,path TEXT,offset INTEGER,size INTEGER,last_read REAL,
      backfill_until INTEGER,generation INTEGER,state TEXT);
    CREATE TABLE IF NOT EXISTS samples(
      model_key TEXT,bucket INTEGER,active_seconds REAL,peak_memory REAL,peak_swap REAL,
      PRIMARY KEY(model_key,bucket));
    CREATE TABLE IF NOT EXISTS agent_samples(
      model_key TEXT,bucket INTEGER,agent TEXT,active_seconds REAL,
      PRIMARY KEY(model_key,bucket,agent));
    CREATE TABLE IF NOT EXISTS rate_samples(
      sample_key TEXT PRIMARY KEY,model_key TEXT,at REAL,input_tps REAL,output_tps REAL,
      input_seconds REAL,output_seconds REAL);
    ''')
    import request_attribution
    request_attribution.ingest(c,root)
    c.execute("INSERT OR IGNORE INTO meta VALUES('started_at',?)",(str(time.time()),));c.commit()
    return c

def register(c,models):
    for m in models:
        c.execute('INSERT INTO models VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,engine=excluded.engine,native_id=excluded.native_id',
                  (m['id'],m.get('name',m['model_id']),m.get('engine',''),m['model_id']))

def historical_model(c,engine,mid):
    candidates=c.execute('SELECT id FROM models WHERE native_id=? AND engine LIKE ?',
                         (mid,engine+'%')).fetchall()
    if len(candidates)==1:return candidates[0][0]
    if len(candidates)>1:return None
    prefix={'Hermes':'hermes:','TensorFold':'tensorfold:','LM Studio':'lmstudio:'}[engine]
    key=prefix+mid
    c.execute('INSERT OR IGNORE INTO models VALUES(?,?,?,?)',(key,mid,engine,mid))
    return key

def insert(c,event):
    if not event.get('model_key') or not event.get('event_key'):return False
    clean={k:number(event.get(k),k.endswith('_tokens')) for k in FIELDS}
    for k in ('input_tps','output_tps'):clean[k]=rate(clean[k])
    reason=event.get('finish_reason')
    if reason not in ('stop','length','tool_calls','cancelled','error'):reason=None
    values=[event['event_key'],event['model_key'],number(event.get('completed_at')),
            event.get('observed_at',time.time()),*[clean[k] for k in FIELDS],reason,
            event.get('source',''),event.get('time_basis','unknown')]
    legacy=event.get('_legacy_event_key')
    if legacy and legacy!=event['event_key']:
        if not c.execute('SELECT 1 FROM events WHERE event_key=?',(event['event_key'],)).fetchone():
            c.execute('UPDATE events SET event_key=? WHERE event_key=?',(event['event_key'],legacy))
        else:c.execute('DELETE FROM events WHERE event_key=?',(legacy,))
    result=c.execute('INSERT OR IGNORE INTO events VALUES('+','.join('?' for _ in values)+')',values)
    return result.rowcount==1

LOAD=re.compile(r"^\[(\d+)\]\s+[\d.]+.*?\bload_model:\s+loading model ['\"]([^'\"]+)['\"]")
TIMING=re.compile(r'^(?:\[(\d+)\]\s+)?([\d.]+).*?slot\s+print_timing:\s+id\s+(\d+)\s*\|\s*task\s+(\d+)\s*\|\s*(prompt eval time|eval time|total time)\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens')

def llama_line(c,line,state,at,observed):
    load=LOAD.search(line)
    if load:
        mid=re.sub(r'-\d{5}-of-\d{5}$','',pathlib.Path(load[2]).stem)
        key=historical_model(c,'Hermes',mid)
        state.setdefault('models',{})[load[1]]=key
        return None
    identity=re.search(r'^(?:\[(\d+)\])?.*?LMC_REQUEST (\{.*\})$',line.strip())
    if identity:
        try:
            meta=json.loads(identity[2]);rid=meta.get('completion_id');task=meta.get('task')
            if isinstance(task,int) and isinstance(rid,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',rid):
                entries=state.setdefault('requests',{});entries[(identity[1] or 'standalone')+':'+str(task)]=meta
                while len(entries)>512:entries.pop(next(iter(entries)))
        except (ValueError,TypeError):pass
        return None
    m=TIMING.search(line)
    if not m:return None
    pid,stamp,slot,task,kind,ms,tokens=m.groups();pid=pid or 'standalone';key=state.get('models',{}).get(pid)
    if not key:return None
    pending=state.setdefault('pending',{});identity=pid+':'+slot+':'+task
    row=pending.setdefault(identity,{'model_key':key})
    seconds=float(ms)/1000;count=int(tokens)
    rates=re.findall(r'([\d.]+)\s+tokens per second',line)
    native_rate=rate(rates[-1]) if rates else None
    if kind=='prompt eval time':row.update(input_tokens=count,input_seconds=seconds,input_tps=native_rate)
    elif kind=='eval time':row.update(output_tokens=count,output_seconds=seconds,output_tps=native_rate)
    else:
        pending.pop(identity,None)
        if 'output_tokens' not in row:return None
        meta=state.get('requests',{}).pop(pid+':'+task,{})
        event_id=digest('llama:'+key+':'+identity+':'+stamp)
        legacy_id=event_id
        if meta:
            event_id=digest('llama-native:'+key+':'+meta['completion_id']+':'+task)
            c.execute('INSERT OR IGNORE INTO request_completions(event_key,request_id,caller_request_id) VALUES(?,?,?)',(event_id,meta['completion_id'],meta.get('caller_request_id')))
            import request_attribution as attribution
            if meta.get('origin') in ('Hermes','DSH') and all(isinstance(meta.get(k),str) and attribution.ID.fullmatch(meta[k]) for k in ('task_id','session_id')):
                values=(event_id,key,meta['origin'],meta['task_id'],meta['session_id'],meta.get('host',''))
                prior=c.execute('SELECT origin,task_id,session_id,host FROM request_attribution WHERE event_key=? AND model_key=?',values[:2]).fetchone()
                if prior and tuple(prior)!=values[2:]:c.execute('UPDATE request_attribution SET conflict=1 WHERE event_key=? AND model_key=?',values[:2])
                else:c.execute('INSERT OR IGNORE INTO request_attribution VALUES(?,?,?,?,?,?,0)',values)
        row.update(event_key=event_id,_legacy_event_key=legacy_id,total_seconds=seconds,
                   completed_at=at,observed_at=observed,source='llama.cpp 请求计时',
                   time_basis='observed' if at is not None else 'unknown')
        return row
    while len(pending)>128:pending.pop(next(iter(pending)))
    return None

TF_DONE=re.compile(r'\[tensorfold\]\s+done\s+(chatcmpl-[A-Za-z0-9_-]+)\s')

def tf_line(c,line,at,observed):
    m=TF_DONE.search(line)
    if not m:return None
    raw=dict(re.findall(r'\b(prompt|cached|tokens|decode|elapsed|queued|ttft|finish|tok/s)=([^\s]+)',line))
    output=number(raw.get('tokens'),True);prompt=number(raw.get('prompt'),True);cached=number(raw.get('cached'),True)
    if output is None:return None
    evaluated=prompt-cached if prompt is not None and cached is not None and 0<=cached<=prompt else None
    return dict(event_key=digest('tf:'+m[1]),model_key=historical_model(c,'TensorFold','qwen3.8-27b-tensorfold'),
        completed_at=at,observed_at=observed,input_tokens=evaluated,cached_tokens=cached,
        output_tokens=output,output_seconds=number(raw.get('decode')),output_tps=rate(raw.get('tok/s')),
        total_seconds=number(raw.get('elapsed')),queue_seconds=number(raw.get('queued')),
        ttft_seconds=number(raw.get('ttft')),finish_reason=raw.get('finish'),source='TensorFold 请求计时',
        time_basis='observed' if at is not None else 'unknown')

def stamp(text):
    values=re.findall(r'\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?',text)
    if not values:return None
    try:
        d=dt.datetime.fromisoformat(values[-1].replace('Z','+00:00'))
        return (d if d.tzinfo else d.replace(tzinfo=TZ)).timestamp()
    except ValueError:return None

def lm_record(c,event,mid,at,observed,key):
    if not isinstance(event,dict):return None
    mid=event.get('model') or mid
    stats=event.get('stats') if isinstance(event.get('stats'),dict) else {}
    timings=event.get('timings') if isinstance(event.get('timings'),dict) else {}
    usage=event.get('usage') if isinstance(event.get('usage'),dict) else {}
    def pick(*names):
        for source in (stats,timings):
            for name in names:
                if name in source:return source[name]
    inp=number(pick('prompt_tokens_count','promptTokensCount','prompt_n','prompt_tokens'),True)
    if inp is None:inp=number(usage.get('prompt_tokens'),True)
    out=number(pick('predicted_tokens_count','predictedTokensCount','predicted_n','completion_tokens'),True)
    if out is None:out=number(usage.get('completion_tokens'),True)
    cached=number(pick('cached_prompt_tokens_count','cachedPromptTokensCount','cached_tokens'),True)
    in_rate=rate(pick('prompt_tokens_per_second','promptTokensPerSecond','prompt_per_second'))
    out_rate=rate(pick('tokens_per_second','tokensPerSecond','predicted_per_second'))
    if all(v is None for v in (inp,out,in_rate,out_rate)):return None
    in_sec=number(pick('prompt_seconds'));out_sec=number(pick('prediction_seconds','generation_seconds'))
    if in_sec is None:
        ms=number(pick('prompt_ms'));in_sec=ms/1000 if ms is not None else None
    if out_sec is None:
        ms=number(pick('predicted_ms'));out_sec=ms/1000 if ms is not None else None
    # Duration reconstructed from the same native rate/count pair is exact for
    # weighting that record; it is never derived from TTFT.
    if in_sec is None and inp is not None and in_rate:in_sec=inp/in_rate
    if out_sec is None and out is not None and out_rate:out_sec=out/out_rate
    total=number(pick('total_seconds','totalTimeSeconds'))
    if total is None:
        ms=number(pick('total_ms'));total=ms/1000 if ms is not None else None
    if total is None and in_sec is not None and out_sec is not None:total=in_sec+out_sec
    request_id=event.get('id') or event.get('request_id')
    evaluated=inp-cached if inp is not None and cached is not None and cached<=inp else inp
    if inp is not None and cached is not None and cached>inp:cached=None
    choices=event.get('choices') or [];finish=choices[0].get('finish_reason') if choices and isinstance(choices[0],dict) else None
    if at is None:
        created=number(event.get('created'))
        if created and 946684800<=created<=observed+86400:at=created
    return dict(event_key=digest('lm:'+str(request_id or key)),model_key=historical_model(c,'LM Studio',str(mid)),
        completed_at=at,observed_at=observed,input_tokens=evaluated,cached_tokens=cached,output_tokens=out,
        input_seconds=in_sec,output_seconds=out_sec,input_tps=in_rate,output_tps=out_rate,
        total_seconds=total,ttft_seconds=number(pick('timeToFirstTokenSec','time_to_first_token_seconds')),
        finish_reason=event.get('finish_reason') or pick('stopReason','stop_reason') or finish,source='LM Studio 完成统计',
        time_basis='native' if at is not None else 'unknown')

def observe_lm_event(root,event):
    data=event.get('data') or {}
    if not isinstance(data,dict) or data.get('type')!='llm.prediction.output':return
    mid=data.get('modelIdentifier');stats=data.get('stats') or {}
    if not mid or not isinstance(stats,dict):return
    inp=rate(stats.get('promptTokensPerSecond'));out=rate(stats.get('tokensPerSecond'))
    if inp is None and out is None:return
    at=number(event.get('timestamp'))
    if at and at>1e11:at/=1000
    at=at or time.time()
    n_in=number(stats.get('promptTokensCount'));n_out=number(stats.get('predictedTokensCount') or stats.get('numGeneratedTokens'))
    in_sec=n_in/inp if n_in is not None and inp else None
    out_sec=n_out/out if n_out is not None and out else None
    key=digest(json.dumps([mid,at,data.get('predictionIdentifier'),inp,out,n_in,n_out],ensure_ascii=False))
    try:
        with contextlib.closing(connect(root)) as c:
            model_key=historical_model(c,'LM Studio',str(mid))
            c.execute('INSERT OR IGNORE INTO rate_samples VALUES(?,?,?,?,?,?,?)',(key,model_key,at,inp,out,in_sec,out_sec));c.commit()
    except (OSError,ValueError,sqlite3.Error):pass

LM_START=re.compile(rb'\[([^\]\r\n]+)\] Generated prediction:\s*(\{)')

def ingest_file(c,path,kind,budget,now):
    stat=path.stat();identity=kind+':'+str(stat.st_dev)+':'+str(stat.st_ino)
    old=c.execute('SELECT * FROM cursors WHERE id=?',(identity,)).fetchone()
    offset=old['offset'] if old else 0;generation=old['generation'] if old else 0
    state=json.loads(old['state']) if old else {};backfill=old['backfill_until'] if old else stat.st_size
    if offset>stat.st_size:
        offset=0;generation+=1;state={'models':state.get('models',{})};backfill=stat.st_size
    if old and now-old['last_read']>30:backfill=max(backfill,stat.st_size)
    with path.open('rb') as f:f.seek(offset);chunk=f.read(min(budget,LIMIT))
    used=0
    if kind=='lm':
        matches=list(LM_START.finditer(chunk));decoder=json.JSONDecoder();stop=None;skip_to_end=False
        for index,match in enumerate(matches):
            start=match.start(2)
            try:
                text=chunk[start:].decode('utf-8',errors='replace');value,end=decoder.raw_decode(text)
            except ValueError:
                if index+1<len(matches):state['skipped_partial']=state.get('skipped_partial',0)+1;continue
                if offset+len(chunk)>=stat.st_size and now-stat.st_mtime>30:
                    state['skipped_partial']=state.get('skipped_partial',0)+1;skip_to_end=True;break
                stop=chunk.rfind(b'\n',0,match.start())+1;break
            end_byte=start+len(text[:end].encode('utf-8'));native_at=stamp(chunk[max(0,match.start()-120):match.start()].decode(errors='ignore'));at=native_at
            if at is None and offset+end_byte>backfill:at=now
            event=lm_record(c,value,match[1].decode(errors='replace'),at,now,identity+':'+str(generation)+':'+str(offset+match.start()))
            if event and native_at is None and at is not None:event['time_basis']='observed'
            if event:insert(c,event)
        used=len(chunk) if skip_to_end else stop if stop is not None else (chunk.rfind(b'\n')+1 if chunk else 0)
        if used==0 and len(chunk)==LIMIT:used=len(chunk);state['oversized_records']=state.get('oversized_records',0)+1
    else:
        for raw in chunk.splitlines(keepends=True):
            if not raw.endswith(b'\n'):break
            used+=len(raw);line=raw.decode(errors='replace');at=stamp(line)
            if at is None and offset+used>backfill:at=now
            event=llama_line(c,line,state,at,now) if kind=='llama' else tf_line(c,line,at,now)
            if event:insert(c,event)
        if used==0 and len(chunk)==LIMIT:used=len(chunk);state['oversized_records']=state.get('oversized_records',0)+1
    c.execute('INSERT INTO cursors VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path,offset=excluded.offset,size=excluded.size,last_read=excluded.last_read,backfill_until=excluded.backfill_until,generation=excluded.generation,state=excluded.state',
              (identity,str(path),offset+used,stat.st_size,now,backfill,generation,json.dumps(state,separators=(',',':'))))
    return len(chunk)

def sample(c,models,memory,now):
    saved=c.execute("SELECT value FROM meta WHERE key='last_sample'").fetchone()
    previous=json.loads(saved[0]) if saved else {};elapsed=now-previous.get('at',now)
    step=elapsed if 0<=elapsed<=15 else 0;bucket=int(now//60)*60;busy=[]
    for m in models:
        if m.get('busy') is not True:continue
        busy.append(m['id']);duration=step if m['id'] in previous.get('busy',[]) else 0
        c.execute('INSERT INTO samples VALUES(?,?,?,?,?) ON CONFLICT(model_key,bucket) DO UPDATE SET active_seconds=active_seconds+excluded.active_seconds,peak_memory=max(peak_memory,excluded.peak_memory),peak_swap=max(peak_swap,excluded.peak_swap)',
                  (m['id'],bucket,duration,memory.get('used',0),memory.get('swap',0)))
        agents={a['agent'] for a in m.get('activities',[]) if a.get('confidence')=='live'}
        for agent in agents:
            c.execute('INSERT INTO agent_samples VALUES(?,?,?,?) ON CONFLICT(model_key,bucket,agent) DO UPDATE SET active_seconds=active_seconds+excluded.active_seconds',
                      (m['id'],bucket,str(agent)[:80],duration))
    c.execute("INSERT INTO meta VALUES('last_sample',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps({'at':now,'busy':busy}),))

def capture(root,home,models,memory,max_bytes=LIMIT,sample_stats=True):
    root=pathlib.Path(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        with (root/'usage-history.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return ''
            with contextlib.closing(connect(root)) as c:
                register(c,models);now=time.time()
                paths=[(agent_home(home,'hermes')/'logs/llama-server.log','llama'),(home/'.local/share/tensorfold/logs/server.log','tf')]
                paths.extend((p,'lm') for p in sorted((home/'.lmstudio/server-logs').glob('*/*.log'),key=lambda p:p.stat().st_mtime)[-32:])
                if not c.execute("SELECT 1 FROM meta WHERE key='native_identity_backfill_v1'").fetchone():
                    for path,kind in paths:
                        if kind!='llama' or not path.exists():continue
                        st=path.stat();identity=kind+':'+str(st.st_dev)+':'+str(st.st_ino)
                        old=c.execute('SELECT state FROM cursors WHERE id=?',(identity,)).fetchone()
                        if old:
                            state=json.loads(old['state']);state.pop('pending',None);state.pop('requests',None)
                            with path.open('rb') as f:
                                f.seek(max(0,st.st_size-2*1024*1024));f.readline();offset=f.tell()
                            c.execute('UPDATE cursors SET offset=?,backfill_until=?,state=? WHERE id=?',(offset,st.st_size,json.dumps(state),identity))
                    c.execute("INSERT INTO meta VALUES('native_identity_backfill_v1','1')")
                remaining=max_bytes
                for path,kind in paths:
                    if not path.exists():continue
                    if remaining>0:remaining-=ingest_file(c,path,kind,remaining,now)
                    else:
                        st=path.stat();key=kind+':'+str(st.st_dev)+':'+str(st.st_ino)
                        c.execute('INSERT OR IGNORE INTO cursors VALUES(?,?,?,?,?,?,?,?)',(key,str(path),0,st.st_size,now,st.st_size,0,'{}'))
                        c.execute('UPDATE cursors SET size=? WHERE id=?',(st.st_size,key))
                if sample_stats:sample(c,models,memory,now)
                c.commit()
        return ''
    except (OSError,ValueError,TypeError,sqlite3.Error):return '使用历史暂不可写，现有记录保留'

def percentile(values,p):
    values=sorted(v for v in values if v is not None)
    if not values:return None
    k=(len(values)-1)*p;i=int(k);j=min(i+1,len(values)-1)
    return values[i]+(values[j]-values[i])*(k-i)

def bounds(days,now):
    if days not in (0,1,7,30):raise ValueError('未知时间范围')
    if not days:return None
    d=dt.datetime.fromtimestamp(now,TZ).replace(hour=0,minute=0,second=0,microsecond=0)
    return (d-dt.timedelta(days=days-1)).timestamp()

def where(model,days,now,table='events'):
    clauses=[];params=[];cutoff=bounds(days,now)
    if model!='all':clauses.append('model_key=?');params.append(model)
    if cutoff is not None:clauses.append(('completed_at' if table=='events' else 'bucket')+'>=?');params.append(cutoff)
    return (' WHERE '+' AND '.join(clauses) if clauses else ''),params

def summary(rows,rate_rows=None):
    rate_rows=rate_rows or []
    def speed_rows(prefix,weighted=False):
        streamed={r['model_key'] for r in rate_rows if r[prefix+'_tps'] and (not weighted or r[prefix+'_seconds'])}
        return [r for r in rows if r['model_key'] not in streamed]+rate_rows
    def total(field):
        vals=[r[field] for r in rows if r[field] is not None]
        return sum(vals) if vals else None
    def weighted(prefix):
        valid=[r for r in speed_rows(prefix,True) if r[prefix+'_tps'] and r[prefix+'_seconds'] and r[prefix+'_seconds']>0]
        return sum(r[prefix+'_tps']*r[prefix+'_seconds'] for r in valid)/sum(r[prefix+'_seconds'] for r in valid) if valid else None
    durations=[r['total_seconds'] for r in rows]
    known=[r for r in rows if r['finish_reason'] is not None];cached=[r for r in rows if r['cached_tokens'] is not None and r['input_tokens'] is not None]
    denom=sum(r['cached_tokens']+r['input_tokens'] for r in cached)
    outputs=[r['output_tokens'] for r in rows if r['output_tokens'] is not None]
    return dict(requests=len(rows),input_tokens=total('input_tokens'),output_tokens=total('output_tokens'),
        cached_tokens=total('cached_tokens'),avg_input_tps=weighted('input'),avg_output_tps=weighted('output'),
        max_input_tps=max([r['input_tps'] for r in speed_rows('input') if r['input_tps']] or [None]),
        max_output_tps=max([r['output_tps'] for r in speed_rows('output') if r['output_tps']] or [None]),
        p50_seconds=percentile(durations,.5),p95_seconds=percentile(durations,.95),
        avg_ttft_seconds=sum(r['ttft_seconds'] for r in rows if r['ttft_seconds'] is not None)/sum(r['ttft_seconds'] is not None for r in rows) if any(r['ttft_seconds'] is not None for r in rows) else None,
        avg_queue_seconds=sum(r['queue_seconds'] for r in rows if r['queue_seconds'] is not None)/sum(r['queue_seconds'] is not None for r in rows) if any(r['queue_seconds'] is not None for r in rows) else None,
        avg_output_tokens=sum(outputs)/len(outputs) if outputs else None,
        cache_ratio=sum(r['cached_tokens'] for r in cached)/denom if denom else None,
        cache_records=len(cached),truncated=sum(r['finish_reason']=='length' for r in rows),finish_known=len(known),
        unknown_time=sum(r['completed_at'] is None for r in rows))

def report(root,model='all',days=0,now=None,offset=0):
    now=now or time.time()
    offset=max(0,int(offset))
    with contextlib.closing(connect(root)) as c:
        w,args=where(model,days,now);rows=[dict(r) for r in c.execute('SELECT * FROM events'+w,args)]
        rw,ra=where(model,days,now);rw=rw.replace('completed_at','at')
        rates=[dict(r) for r in c.execute('SELECT * FROM rate_samples'+rw,ra)]
        totals=summary(rows,rates)
        sw,sa=where(model,days,now,'samples')
        active=c.execute('SELECT SUM(active_seconds),MAX(peak_memory),MAX(peak_swap) FROM samples'+sw,sa).fetchone()
        totals.update(active_seconds=active[0] or 0,peak_system_memory=active[1],peak_system_swap=active[2])
        agents=[dict(agent=r[0],seconds=r[1]) for r in c.execute('SELECT agent,SUM(active_seconds) FROM agent_samples'+sw+' GROUP BY agent ORDER BY SUM(active_seconds) DESC',sa)]
        catalog=[dict(r) for r in c.execute('SELECT * FROM models ORDER BY engine,name')]
        recent=[dict(r) for r in c.execute('SELECT * FROM events'+w+' ORDER BY completed_at IS NULL,completed_at DESC,observed_at DESC,event_key LIMIT 80 OFFSET ?',args+[offset])]
        name_by_key={m['id']:m['name'] for m in catalog}
        for r in recent:
            import request_attribution
            request_attribution.attach(c,r)
            r['id']=r.pop('event_key');r['model_name']=name_by_key.get(r['model_key'],r['model_key'])
        from history_titles import attach_titles
        attach_titles(recent, __import__('runtime_config').HOME)
        trend_days=7 if days in (1,7) else 30
        today=dt.datetime.fromtimestamp(now,TZ).date();daily={}
        for i in range(trend_days):
            day=str(today-dt.timedelta(days=trend_days-i-1));daily[day]=dict(day=day,input_tokens=0,output_tokens=0,requests=0)
        for r in rows:
            if r['completed_at'] is None:continue
            day=str(dt.datetime.fromtimestamp(r['completed_at'],TZ).date())
            if day in daily:
                daily[day]['input_tokens']+=r['input_tokens'] or 0;daily[day]['output_tokens']+=r['output_tokens'] or 0;daily[day]['requests']+=1
        cursor=c.execute('SELECT COALESCE(SUM(size),0),COALESCE(SUM(MAX(0,size-offset)),0),COALESCE(SUM(COALESCE(json_extract(state,\'$.oversized_records\'),0)+COALESCE(json_extract(state,\'$.skipped_partial\'),0)),0) FROM cursors').fetchone()
        started=float(c.execute("SELECT value FROM meta WHERE key='started_at'").fetchone()[0])
        return dict(models=catalog,summary=totals,daily=list(daily.values()),recent=recent,agents=agents,
            model=model,days=days,offset=offset,started_at=started,updated_at=now,scanned_bytes=cursor[0]-cursor[1],remaining_bytes=cursor[1],skipped_records=cursor[2],
            database_bytes=sum(p.stat().st_size for p in pathlib.Path(root).glob(DB_NAME+'*') if p.is_file()),
            note='llama.cpp 输入为实际评估 token；TensorFold 输入扣除已缓存 token；LM Studio 按原生报告。平均速度按原生单次均速及处理时长加权，峰值是已记录单次均速，短输出可能偏高。未提供的值显示 —。失败请求未完整提供，不计算失败率。')

def export_csv(root,model,days,destination,now=None):
    now=now or time.time();path=pathlib.Path(destination).expanduser()
    if path.suffix.lower()!='.csv':raise ValueError('请选择CSV文件')
    def cell(v):
        if isinstance(v,str) and v.startswith(('=','+','-','@')):return "'"+v
        return '' if v is None else v
    with contextlib.closing(connect(root)) as c,path.open('w',encoding='utf-8-sig',newline='') as f:
        w,args=where(model,days,now);writer=csv.writer(f)
        writer.writerow(['时间(本地时区)','时间来源','模型','输入token','缓存token','输出token','输入tok/s','输出tok/s','请求秒数','排队秒数','首token秒数','结束原因','统计来源'])
        for r in c.execute('SELECT events.*,models.name AS name FROM events LEFT JOIN models ON models.id=events.model_key'+w.replace('model_key','events.model_key')+' ORDER BY completed_at IS NULL,completed_at DESC',args):
            at=dt.datetime.fromtimestamp(r['completed_at'],TZ).isoformat() if r['completed_at'] is not None else '时间不详'
            writer.writerow([cell(v) for v in (at,r['time_basis'],r['name'] or r['model_key'],r['input_tokens'],r['cached_tokens'],r['output_tokens'],r['input_tps'],r['output_tps'],r['total_seconds'],r['queue_seconds'],r['ttft_seconds'],r['finish_reason'],r['source'])])
    return str(path)
