/** Native llm/stream identity -> loopback HTTP headers. No prompt/body logging. */
import { AsyncLocalStorage } from 'node:async_hooks';
import { randomUUID } from 'node:crypto';
import { hostname } from 'node:os';
import { appendFileSync } from 'node:fs';
export const name = 'lmc-request-trace';
export const inject = ['llm'];
const valid = value => typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,200}$/.test(value);
export function traceFetch(original, scope, allowedOrigins) {
  return function(input, init) {
    const identity=scope.getStore();
    const url=new URL(typeof input==='string' || input instanceof URL ? input : input.url);
    if (!identity || !allowedOrigins.includes(url.origin)) return original.call(this,input,init);
    const headers=new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    for (const key of [...headers.keys()]) if (key.toLowerCase().startsWith('x-lmc-')) headers.delete(key);
    headers.set('X-LMC-Origin','DSH'); headers.set('X-LMC-Session-Id',identity.session);
    headers.set('X-LMC-Task-Id',identity.session); headers.set('X-LMC-Host',hostname());
    headers.set('X-LMC-Request-Id',identity.request);
    return original.call(this,input,{...init,headers});
  };
}
export function apply(ctx,config={}) {
  const allowed=config.allowedOrigins ?? ['http://127.0.0.1:18434','http://127.0.0.1:18080'];
  if (!allowed.every(value => {const url=new URL(value);return ['127.0.0.1','localhost','[::1]'].includes(url.hostname) && url.protocol==='http:';})) throw Error('Trace destinations must be local loopback origins');
  const receipt=(kind,identity={})=>{if(config.diagnosticsPath)try{appendFileSync(config.diagnosticsPath,JSON.stringify({kind,...identity,at:Date.now()})+'\n',{mode:0o600});}catch{}};
  receipt('loaded');
  const scope=new AsyncLocalStorage(); const original=globalThis.fetch;
  const wrapped=traceFetch(function(input,init){if(scope.getStore())receipt('fetch',scope.getStore());return original.call(this,input,init);},scope,allowed); globalThis.fetch=wrapped;
  ctx.on('dispose',()=>{if(globalThis.fetch===wrapped)globalThis.fetch=original;});
  ctx.on('llm/stream',(options,next)=>{
    if (!valid(options.sessionId)) return next();
    const identity={session:String(options.sessionId),request:randomUUID()}; receipt('stream',identity);
    return {async *[Symbol.asyncIterator](){
      const iterator=scope.run(identity,()=>next()[Symbol.asyncIterator]());
      let complete=false;
      try {
        while(true){const result=await scope.run(identity,()=>iterator.next());if(result.done){complete=true;return;}yield result.value;}
      } finally {if(!complete && iterator.return)await scope.run(identity,()=>iterator.return());}
    }};
  });
}
