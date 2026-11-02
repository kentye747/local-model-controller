import test_support
import json,pathlib,socket,unittest
from unittest.mock import patch
import live_requests as live
import activity
import local_controller as controller

def slot(rid='chatcmpl-one',sid='session-one',host=None,origin='Hermes'):
    return {'is_processing':True,'request_identity':dict(completion_id=rid,origin=origin,
        session_id=sid,task_id='task-one',host=host or socket.gethostname()),
        'params':{'prompt':'PRIVATE','api_key':'PRIVATE'}}

def model(slots):
    return dict(id='hermes:flash',model_id='flash',state='ready',busy=True,native_requests=live.current_requests(slots))

class LiveRequestTests(unittest.TestCase):
    def setUp(self):
        self.title=patch.object(live,'attach_titles',side_effect=lambda rows,home:[r.update(task_title='Native '+r['session_id']) for r in rows]);self.title.start();self.addCleanup(self.title.stop)
        self.cfg=patch.object(live,'settings',return_value={'remote':{'enabled':True,'expected_hostname':'remote.local'}});self.cfg.start();self.addCleanup(self.cfg.stop)

    def test_idle_previous_task_never_appears_as_current(self):
        old=slot();old['is_processing']=False
        self.assertEqual(live.current_requests([old]),[])
        m=model([slot()]);m['busy']=False
        self.assertEqual(live.activities([m],pathlib.Path('/unused')),({},{}))

    def test_no_native_id_support_does_not_guess(self):
        self.assertIsNone(live.current_requests([{'is_processing':True,'id_task':7}]))
        self.assertEqual(live.activities([model([{'is_processing':True}])],pathlib.Path('/unused')),({},{}))

    def test_only_allowlisted_metadata_survives(self):
        raw=slot();raw['request_identity']['api_key']='PRIVATE';raw['request_identity']['prompt']='PRIVATE'
        result=live.current_requests([raw]);self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('params',json.dumps(result))
        for key,bad in [('completion_id',{}),('session_id','../secret'),('origin','Cloud'),('task_id',[]),('host',True)]:
            raw=slot();raw['request_identity'][key]=bad
            self.assertNotEqual(live.current_requests([raw])[0].get('attribution_status'),'exact')

    def test_concurrent_requests_use_native_ids_hosts_and_sessions(self):
        m=model([slot(),slot('chatcmpl-two','session-two','remote.local','DSH')]);rows,unknown=live.activities([m],pathlib.Path('/unused'))
        self.assertFalse(unknown[m['id']]);a,b=rows[m['id']]
        self.assertEqual((a['agent'],a['session_id'],a['request_id']),('Hermes','session-one','chatcmpl-one'));self.assertTrue(a['can_open'])
        self.assertEqual((b['agent'],b['session_id'],b['request_id']),('DSH · mini','session-two','chatcmpl-two'));self.assertFalse(b['can_open'])

    def test_same_session_requests_remain_distinct_and_batch_slots_collapse(self):
        rows,_=live.activities([model([slot(),slot(),slot('chatcmpl-two')])],pathlib.Path('/unused'))
        self.assertEqual({r['request_id'] for r in rows['hermes:flash']},{'chatcmpl-one','chatcmpl-two'})

    def test_reused_completion_id_with_conflicting_identity_is_withheld(self):
        rows,unknown=live.activities([model([slot(),slot(sid='other')])],pathlib.Path('/unused'))
        self.assertEqual(rows['hermes:flash'],[]);self.assertTrue(unknown['hermes:flash'])

    def test_unlabelled_current_request_stays_unknown_beside_known_request(self):
        raw=slot('chatcmpl-unknown');raw['request_identity']={'completion_id':'chatcmpl-unknown','task':7}
        rows,unknown=live.activities([model([slot(),raw])],pathlib.Path('/unused'))
        self.assertEqual(len(rows['hermes:flash']),1);self.assertTrue(unknown['hermes:flash'])

    def test_native_requests_replace_wrong_process_candidate(self):
        m=model([slot()]);wrong={'model_key':m['id'],'agent':'DSH','session_id':'wrong','title':'Wrong','phase':'处理任务','source':'process','confidence':'live'}
        with patch.object(activity,'hermes_activity',return_value=([wrong],'')),patch.object(activity,'dsh_activity',return_value=([],'')),patch.object(activity,'hermes_cli_activity',return_value=([],'')),patch('remote_activity.remote_activity',return_value=([],'')):
            activity.attach([m],pathlib.Path('/unused'))
        self.assertEqual([r['session_id'] for r in m['activities']],['session-one']);self.assertEqual(m['activity_note'],'')

    def test_controller_never_forwards_raw_slot_parameters(self):
        with patch.object(controller,'managed',return_value=('http://127.0.0.1:1234','PRIVATE')),patch.object(controller,'request',side_effect=[{'data':[{'id':'flash','status':{'value':'loaded'}}]},[slot()]]):
            result=controller.llama_snapshot()
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertEqual(result['models'][0]['native_requests'][0]['request_id'],'chatcmpl-one')

if __name__=='__main__':unittest.main()
