import test_support  # Isolate settings before importing any application module.
import pathlib, unittest
from unittest.mock import patch
import local_controller
import activity as a

def models():
 return [dict(id='hermes:flash',model_id='flash',base_url='http://127.0.0.1:18434/v1',state='ready',busy=False),dict(id='tensorfold:27b',model_id='27b',base_url='http://127.0.0.1:18080/v1',state='stopped',busy=False)]
def record(open_turn=4):
 return {'rows':{k:{'val':v} for k,v in {'title':'本地模型控制器｜调用验证','turnBoundary':{'openTurnStartSeq':open_turn},'modelSelection':{'lastUsed':{'provider':'tf','model':'27b'},'pending':{'provider':'flash','model':'flash'}},'sessionStats':{'openStep':{'step':1},'pendingCalls':{}}}.items()}}
class AttributionTests(unittest.TestCase):
 def setUp(self):
  self.remote=patch('remote_activity.remote_activity',return_value=([],''));self.remote.start();self.addCleanup(self.remote.stop)
 def test_idle_hermes_sessions_do_not_count(self):
  rows=a.hermes_rows([{'status':'idle','model':'flash','title':'old'},{'status':'working','model':'flash','session_key':'a','title':'live'}],models())
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['title'],'live')
 def test_same_model_id_on_different_engines_needs_route(self):
  m=models();m[1]['model_id']='flash'
  self.assertIsNone(a.match_model(m,'flash'))
  self.assertEqual(a.match_model(m,'flash',base='http://localhost:18080/v1/'),'tensorfold:27b')
 def test_dsh_uses_current_not_pending_model(self):
  r=a.dsh_record(record(),{'tf':['http://127.0.0.1:18080/v1']},models(),100,99,True)
  self.assertEqual(r['model_key'],'tensorfold:27b');self.assertEqual(r['phase'],'模型请求中')
 def test_dsh_completed_turn_not_attributed(self):
  self.assertIsNone(a.dsh_record(record(None),{'tf':['http://127.0.0.1:18080/v1']},models(),100,99,True))
 def test_long_task_with_verified_same_writer_is_not_expired_by_age(self):
  r=a.dsh_record(record(),{'tf':['http://127.0.0.1:18080/v1']},models(),1000,1,True)
  self.assertEqual(r['confidence'],'live')
 def test_dsh_crashed_or_stale_writer_not_live(self):
  for modified,owner in [(99,False),(-30,True)]:
   r=a.dsh_record(record(),{'tf':['http://127.0.0.1:18080/v1']},models(),100,modified,owner)
   self.assertEqual(r['confidence'],'uncertain')
 def test_tools_are_distinct_from_model_request(self):
  r=record();r['rows']['sessionStats']['val']['pendingCalls']={'x':{}}
  result=a.dsh_record(r,{'tf':['http://127.0.0.1:18080/v1']},models(),100,99,True)
  self.assertEqual(result['phase'],'执行工具')
 def test_route_ambiguity_is_not_guessed(self):
  self.assertIsNone(a.dsh_record(record(),{'tf':['a','b']},models(),100,99,True))
 def test_failed_source_is_unknown_and_has_no_previous_task(self):
  m=models()
  with patch.object(a,'hermes_activity',return_value=([],'Hermes 实时接口暂不可读')),patch.object(a,'dsh_activity',return_value=([],'')):
   a.attach(m,pathlib.Path('/tmp'))
  self.assertEqual(m[0]['activity_state'],'unknown');self.assertEqual(m[0]['activities'],[])
  self.assertEqual(m[1]['activity_state'],'unloaded')
 def test_busy_unattributed_request_is_not_idle(self):
  m=models();m[0]['busy']=True
  with patch.object(a,'hermes_activity',return_value=([],'')),patch.object(a,'dsh_activity',return_value=([],'')):
   a.attach(m,pathlib.Path('/tmp'))
  self.assertEqual(m[0]['activity_note'],'有请求，调用方未确认')
 def test_multiple_agents_can_share_one_model(self):
  m=models();r=dict(model_key=m[0]['id'],agent='Hermes',session_id='h',title='H',phase='处理任务',source='live',confidence='live')
  d={**r,'agent':'DSH','session_id':'d','title':'D'}
  with patch.object(a,'hermes_activity',return_value=([r],'')),patch.object(a,'dsh_activity',return_value=([d],'')):
   a.attach(m,pathlib.Path('/tmp'))
  self.assertEqual([i['agent'] for i in m[0]['activities']],['Hermes','DSH'])
if __name__=='__main__':unittest.main()
