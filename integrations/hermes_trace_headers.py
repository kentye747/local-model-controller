"""Pass native task identity to an instrumented local engine; never modify prompts."""
import re,socket,urllib.parse
SAFE=re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')
def register(ctx):
    def identify(*,request,next_call,**context):
        url=urllib.parse.urlsplit(context.get('base_url') or '')
        if url.hostname not in ('localhost','127.0.0.1','::1'):return next_call(request)
        sid=context.get('session_id') or '';tid=context.get('task_id') or sid
        if not all(isinstance(v,str) and SAFE.fullmatch(v) for v in (sid,tid)):return next_call(request)
        updated=dict(request);headers={k:v for k,v in (request.get('extra_headers') or {}).items() if not k.lower().startswith('x-lmc-')}
        headers.update({'X-LMC-Origin':'Hermes','X-LMC-Session-Id':sid,'X-LMC-Task-Id':tid,'X-LMC-Host':socket.gethostname(),'X-LMC-Request-Id':context.get('api_request_id','')})
        updated['extra_headers']=headers
        return next_call(updated)
    ctx.register_middleware('llm_execution',identify)
