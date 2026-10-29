"""Exact response-ID attribution. No prompts, replies, titles or credentials retained."""
import hashlib,json,os,pathlib,re,stat
ID=re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')

def event_key(engine,request_id):
    if not isinstance(engine,str):return None
    prefix={'TensorFold':'tf:','LM Studio':'lm:'}.get(engine)
    if not prefix or not isinstance(request_id,str) or not ID.fullmatch(request_id):return None
    return hashlib.sha256((prefix+request_id).encode()).hexdigest()

def ingest(c,root):
    c.execute('CREATE TABLE IF NOT EXISTS request_attribution(event_key TEXT,model_key TEXT,origin TEXT,task_id TEXT,session_id TEXT,host TEXT,conflict INTEGER DEFAULT 0,PRIMARY KEY(event_key,model_key))')
    c.execute('CREATE TABLE IF NOT EXISTS request_completions(event_key TEXT PRIMARY KEY,request_id TEXT NOT NULL)')
    path=pathlib.Path(root)/'request-attribution.jsonl'
    if not path.exists():return
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:return
    if info.st_size>8*1024*1024:return
    with path.open() as f:
        for line in f:
            if len(line)>4096:continue
            try:r=json.loads(line)
            except ValueError:continue
            if not isinstance(r,dict) or r.get('schema')!=1:continue
            key=event_key(r.get('engine'),r.get('request_id'))
            if not key:continue
            if not isinstance(r.get('model_id'),str) or not r['model_id'] or len(r['model_id'])>300:continue
            if r.get('origin') not in ('Hermes','DSH','Controller'):continue
            if not all(isinstance(r.get(k),str) and ID.fullmatch(r[k]) for k in ('task_id','session_id','host')):continue
            matches=c.execute('SELECT id FROM models WHERE native_id=? AND engine LIKE ?', (r.get('model_id'),r['engine']+'%')).fetchall()
            if len(matches)!=1:continue
            c.execute('INSERT OR IGNORE INTO request_completions(event_key,request_id) VALUES(?,?)',(key,r['request_id']))
            values=(key,matches[0][0],r['origin'],r['task_id'],r['session_id'],r['host'])
            old=c.execute('SELECT origin,task_id,session_id,host FROM request_attribution WHERE event_key=? AND model_key=?',values[:2]).fetchone()
            if old and tuple(old)!=values[2:]:
                c.execute('UPDATE request_attribution SET conflict=1 WHERE event_key=? AND model_key=?',values[:2])
            else:c.execute('INSERT OR IGNORE INTO request_attribution VALUES(?,?,?,?,?,?,0)',values)

def attach(c,row):
    match=c.execute('SELECT * FROM request_attribution WHERE event_key=? AND model_key=?',(row.get('event_key') or row.get('id'),row['model_key'])).fetchone()
    completion=c.execute('SELECT request_id FROM request_completions WHERE event_key=?',(row.get('event_key') or row.get('id'),)).fetchone()
    row['request_id']=completion[0] if completion else None
    row.update(request_origin=None,task_id=None,session_id=None,request_host=None,attribution_status='unrecorded')
    if match:
        if match['conflict']:row['attribution_status']='conflict'
        else:row.update(request_origin=match['origin'],task_id=match['task_id'],session_id=match['session_id'],request_host=match['host'],attribution_status='exact')
    return row
