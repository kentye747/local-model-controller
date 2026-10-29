"""Read exact native session titles for display only; never read messages or persist titles."""
import contextlib,json,os,pathlib,re,socket,sqlite3,subprocess,shlex
SAFE=re.compile(r'^[A-Za-z0-9_-]{1,180}$')
def local_titles(queries,home=None):
    home=pathlib.Path(home or pathlib.Path.home());result=[]
    for q in queries[:80]:
        if not isinstance(q,dict):continue
        origin=q.get('origin');sid=q.get('session_id')
        if not isinstance(sid,str) or not SAFE.fullmatch(sid):continue
        titles=[]
        if origin=='Hermes':
            roots=[home/'.hermes']+list((home/'.hermes/profiles').glob('*'))[:32]
            for root in roots:
                db=root/'state.db'
                if not db.is_file():continue
                try:
                    with contextlib.closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)) as c:
                        row=c.execute('SELECT title FROM sessions WHERE id=?',(sid,)).fetchone()
                        if row and row[0]:titles.append(str(row[0])[:240])
                except sqlite3.Error:continue
        elif origin=='DSH':
            path=home/'.dsh/storages/session_projcache/sessions'/(sid+'.json')
            try:
                if path.stat().st_size<=2*1024*1024:
                    rows=json.loads(path.read_text()).get('record',{}).get('rows',{})
                    title=(rows.get('title') or {}).get('val')
                    if isinstance(title,str) and title:titles.append(title[:240])
            except (OSError,ValueError,TypeError):pass
        if len(set(titles))==1:result.append(dict(origin=origin,session_id=sid,title=titles[0],host=socket.gethostname()))
    return result

def attach_titles(rows,home):
    from runtime_config import settings
    queries=[]
    for row in rows:
        if row.get('attribution_status')!='exact':continue
        query=dict(origin=row['request_origin'],session_id=row['session_id'])
        if query not in queries:queries.append(query)
    if not queries:return
    candidates=local_titles(queries,home)
    cfg=settings()['remote']
    if cfg.get('enabled'):
        command=shlex.quote(cfg['python'])+' -B '+shlex.quote(str(pathlib.PurePosixPath(cfg['probe_path']).parent/'history_titles_probe.py'))
        try:
            p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=2',cfg['ssh_host'],command],input=json.dumps(queries),capture_output=True,text=True,timeout=4)
            if p.returncode==0 and len(p.stdout)<=65536:
                data=json.loads(p.stdout)
                if data.get('host')==cfg['expected_hostname']:
                    for item in data.get('titles',[]):
                        if isinstance(item,dict) and isinstance(item.get('title'),str) and len(item['title'])<=240 and item.get('host')==data['host'] and {'origin':item.get('origin'),'session_id':item.get('session_id')} in queries:candidates.append(item)
        except (OSError,ValueError,TypeError,subprocess.TimeoutExpired):pass
    for row in rows:
        matches=[m for m in candidates if m.get('origin')==row.get('request_origin') and m.get('session_id')==row.get('session_id') and (not row.get('request_host') or m['host']==row['request_host'])]
        if len(matches)==1:row.update(task_title=matches[0]['title'],request_host=matches[0]['host'])
        else:row['task_title']=None

if __name__=='__main__':
    import sys
    raw=sys.stdin.read(65537)
    if len(raw)>65536:raise SystemExit('Request too large')
    queries=json.loads(raw)
    if not isinstance(queries,list):raise SystemExit('Expected list')
    print(json.dumps({'host':socket.gethostname(),'titles':local_titles(queries)},ensure_ascii=False))
