import test_support
import contextlib,json,pathlib,tempfile,unittest
import usage_history as h
import request_attribution as a
from integrations.hermes_request_attribution import record,register
class AttributionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name)
 def write(self,rows):
  p=self.root/'request-attribution.jsonl';p.write_text(''.join(json.dumps(r)+'\n' for r in rows));p.chmod(0o600)
 def row(self,**kw):return dict(schema=1,engine='LM Studio',request_id='completion-1',model_id='model',origin='Hermes',task_id='task-1',session_id='session-1',host='host.local',**kw)
 def seed(self,c):h.register(c,[dict(id='lmstudio:model',model_id='model',name='model',engine='LM Studio')]);c.commit()
 def test_exact_id_and_model_not_time_or_order(self):
  with contextlib.closing(h.connect(self.root)) as c:
   self.seed(c);self.write([self.row()]);a.ingest(c,self.root)
   r=a.attach(c,dict(event_key=a.event_key('LM Studio','completion-1'),model_key='lmstudio:model'));self.assertEqual(r['task_id'],'task-1')
   self.assertIsNone(a.attach(c,dict(event_key=a.event_key('LM Studio','completion-2'),model_key='lmstudio:model'))['task_id'])
   self.assertIsNone(a.attach(c,dict(event_key=a.event_key('LM Studio','completion-1'),model_key='different'))['task_id'])
 def test_conflict_is_sticky_and_replay_deduplicates(self):
  with contextlib.closing(h.connect(self.root)) as c:
   self.seed(c);r=self.row();self.write([r,r,{**r,'task_id':'task-2'}]);a.ingest(c,self.root);a.ingest(c,self.root)
   got=a.attach(c,dict(event_key=a.event_key('LM Studio','completion-1'),model_key='lmstudio:model'));self.assertEqual(got['attribution_status'],'conflict');self.assertIsNone(got['task_id']);self.assertEqual(c.execute('SELECT count(*) FROM request_attribution').fetchone()[0],1)
 def test_producer_omits_contents_and_refuses_unsupported_engine(self):
  p=self.root/'request-attribution.jsonl';response=dict(id='completion-1',model='model',choices=[{'content':'PRIVATE'}]);ctx=dict(provider='lmstudio',task_id='task-1',session_id='session-1')
  self.assertTrue(record(p,response,ctx));self.assertNotIn('PRIVATE',p.read_text());self.assertFalse(record(p,response,{**ctx,'provider':'llamacpp'}))
 def test_middleware_calls_downstream_once_and_preserves_response(self):
  class Context:
   def get_config(_,key,default=None):return str(self.root/'request-attribution.jsonl')
   def register_middleware(_,kind,callback):self.callback=callback
  register(Context());response=dict(id='completion-1',model='model');calls=[]
  def next_call(request):calls.append(request);return response
  self.assertIs(self.callback(request={'messages':['PRIVATE']},next_call=next_call,provider='lmstudio',task_id='task-1',session_id='session-1'),response)
  self.assertEqual(len(calls),1);self.assertNotIn('PRIVATE',(self.root/'request-attribution.jsonl').read_text())
 def test_llama_native_marker_matches_task_even_when_completions_reorder(self):
  from test_usage_history import timing
  with contextlib.closing(h.connect(self.root)) as c:
   h.register(c,[dict(id='hermes:model',model_id='model',engine='Hermes · llama.cpp')]);state={'models':{'8':'hermes:model'}}
   for task in (1,2):
    meta=dict(task=task,completion_id='completion-'+str(task),origin='Hermes',task_id='task-'+str(task),session_id='session-'+str(task),host='local')
    h.llama_line(c,'[8] 0.01 I srv handle: LMC_REQUEST '+json.dumps(meta),state,100,100)
    h.llama_line(c,timing('eval time',2,task*10,task),state,100,100)
   for task in (2,1):
    event=h.llama_line(c,timing('total time',2,task*10,task),state,100,100)
    row=a.attach(c,event);self.assertEqual(row['task_id'],'task-'+str(task));self.assertEqual(row['output_tokens'],task*10)
 def test_native_identity_upgrade_does_not_duplicate_legacy_event(self):
  from test_usage_history import timing
  with contextlib.closing(h.connect(self.root)) as c:
   h.register(c,[dict(id='hermes:model',model_id='model',engine='Hermes · llama.cpp')]);state={'models':{'8':'hermes:model'}}
   h.llama_line(c,timing('eval time',2,10),state,100,100);old=h.llama_line(c,timing('total time',2,10),state,100,100);h.insert(c,old)
   meta=dict(task=1,completion_id='native-id',origin='Hermes',task_id='task',session_id='session',host='local')
   h.llama_line(c,'[8] 0.01 I srv: LMC_REQUEST '+json.dumps(meta),state,100,100)
   h.llama_line(c,timing('eval time',2,10),state,100,100);new=h.llama_line(c,timing('total time',2,10),state,100,100);h.insert(c,new);h.insert(c,new)
   self.assertEqual(c.execute('SELECT count(*) FROM events').fetchone()[0],1);self.assertEqual(a.attach(c,new)['request_id'],'native-id')
 def test_trace_headers_stay_local_and_preserve_original_request(self):
  from integrations.hermes_trace_headers import register
  class Context:
   def register_middleware(_,kind,callback):self.callback=callback
  register(Context());request={'extra_headers':{'Accept':'application/json'},'messages':['PRIVATE']};received=[]
  def next_call(value):received.append(value);return 'unchanged'
  ctx=dict(session_id='session',task_id='task',api_request_id='request',base_url='http://127.0.0.1:18434/v1')
  self.assertEqual(self.callback(request=request,next_call=next_call,**ctx),'unchanged');self.assertNotIn('X-LMC-Origin',request['extra_headers']);self.assertEqual(received[-1]['extra_headers']['X-LMC-Session-Id'],'session')
  self.callback(request=request,next_call=next_call,**{**ctx,'base_url':'https://cloud.invalid/v1'});self.assertIs(received[-1],request)
 def test_malformed_types_are_ignored(self):
  with contextlib.closing(h.connect(self.root)) as c:
   self.seed(c);self.write([{**self.row(),'model_id':{}},{**self.row(),'engine':[]},{**self.row(),'session_id':None}]);a.ingest(c,self.root)
   self.assertEqual(c.execute('SELECT count(*) FROM request_attribution').fetchone()[0],0)
 def test_untrusted_journal_is_not_accepted(self):
  with contextlib.closing(h.connect(self.root)) as c:
   self.seed(c);self.write([self.row()]);(self.root/'request-attribution.jsonl').chmod(0o644);a.ingest(c,self.root)
   self.assertEqual(c.execute('SELECT count(*) FROM request_attribution').fetchone()[0],0)
 def test_old_database_and_report_keep_totals(self):
  with contextlib.closing(h.connect(self.root)) as c:
   self.seed(c);h.insert(c,dict(event_key=a.event_key('LM Studio','completion-1'),model_key='lmstudio:model',observed_at=100,output_tokens=4));c.commit()
  self.write([self.row()]);report=h.report(self.root,'all',0,now=100)
  self.assertEqual(report['recent'][0]['task_id'],'task-1');self.assertEqual(report['summary']['requests'],1);self.assertEqual(report['summary']['output_tokens'],4)
if __name__=='__main__':unittest.main()
