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
