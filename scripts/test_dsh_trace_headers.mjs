import assert from 'node:assert/strict';
import {apply} from '../integrations/dsh_trace_headers.mjs';
const original=globalThis.fetch; const observed=[];const hooks={};
globalThis.fetch=async (url,init)=>{await new Promise(resolve=>setTimeout(resolve,2));observed.push({url,session:init?.headers?.get?.('x-lmc-session-id')});return {};};
apply({on:(key,callback)=>{hooks[key]=callback;}},{allowedOrigins:['http://127.0.0.1:18434']});
async function consume(session){for await(const chunk of hooks['llm/stream']({sessionId:session},()=>({async *[Symbol.asyncIterator](){await fetch('http://127.0.0.1:18434/v1/chat/completions');yield session;}})))assert.equal(chunk,session);}
await Promise.all([consume('session-one'),consume('session-two')]);
assert.deepEqual(observed.map(v=>v.session).sort(),['session-one','session-two']);
await fetch('https://example.invalid/v1',{});assert.equal(observed.at(-1).session,undefined);
hooks.dispose();globalThis.fetch=original;console.log('PASS: DSH waterfall concurrent identities and loopback boundary');
