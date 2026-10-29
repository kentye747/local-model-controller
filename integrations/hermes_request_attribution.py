"""Optional Hermes execution middleware. Writes allowlisted metadata after response.

Configure journal_path in plugin settings to the controller data directory's
request-attribution.jsonl. No network, prompt, response content or config copying.
"""
import fcntl,json,os,pathlib,re,socket
ID=re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')

def record(path,response,context):
    def field(obj,key):return obj.get(key) if isinstance(obj,dict) else getattr(obj,key,None)
    request_id=field(response,'id');model=field(response,'model')
    provider=context.get('provider','').removeprefix('custom:')
    engine={'tensorfold':'TensorFold','lmstudio':'LM Studio'}.get(provider)
    # llama.cpp's timing records lack the returned completion ID. Do not guess.
    if not engine:return False
    row=dict(schema=1,engine=engine,request_id=request_id,model_id=model,origin='Hermes',task_id=context.get('task_id'),session_id=context.get('session_id'),host=socket.gethostname())
    if not isinstance(model,str) or not model or len(model)>300:return False
    if not all(isinstance(row[k],str) and ID.fullmatch(row[k]) for k in ('request_id','task_id','session_id','host')):return False
    p=pathlib.Path(path).expanduser()
    if not p.parent.is_dir():return False
    fd=os.open(p,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        if os.fstat(f.fileno()).st_size>8*1024*1024:return False
        f.write(json.dumps(row,separators=(',',':'))+'\n')
    return True

def register(ctx):
    def observe(*,request,next_call,**context):
        response=next_call(request)
        try:
            path=ctx.get_config('journal_path',None)
            if path:record(path,response,context)
        except (OSError,ValueError,TypeError):pass
        return response
    ctx.register_middleware('llm_execution',observe)
