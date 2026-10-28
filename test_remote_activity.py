import pathlib,unittest
from unittest.mock import patch
import remote_activity as r

def models():return [dict(id='hermes:flash',model_id='flash',base_url='http://127.0.0.1:18434/v1',busy=True)]
def route():return dict(remote_port=18434,local_port=18434,connected=True)
def sample():return dict(schema=1,host=r.EXPECTED_HOST,generated_at=100,note='',gateway={},direct_sessions=[dict(agent='Hermes',session_id='sample-session',title='Sample task',phase='模型请求中',model_id='flash',base_url='http://127.0.0.1:18434/v1',owner_pid=42,process_started_at=1,lease_acquired_at=90,lease_expires_at=110,request_connected=True)])

class RemoteTests(unittest.TestCase):
    def test_direct_profile_metadata_does_not_need_a_business_gateway(self):
        rows,note=r.map_snapshot(sample(),models(),False,101,[route()])
        self.assertEqual(rows[0]['title'],'Sample task');self.assertEqual(note,'')
    def test_stale_and_wrong_host_are_not_attributed(self):
        for change in (dict(generated_at=80),dict(generated_at=105),dict(host='wrong.local')):
            data=sample();data.update(change);self.assertEqual(r.map_snapshot(data,models(),False,101,[route()])[0],[])
    def test_completed_request_does_not_reuse_a_title(self):
        data=sample();data['direct_sessions']=[];self.assertEqual(r.map_snapshot(data,models(),False,101,[route()])[0],[])
    def test_unconfigured_gateway_is_not_an_authority(self):
        data=sample();data['direct_sessions']=[];data['gateway']={'active':True,'upstream_active':True}
        self.assertEqual(r.map_snapshot(data,models(),True,101,[route()])[0],[])
    def test_timeout_has_no_cached_title(self):
        import subprocess
        with patch.dict(r.REMOTE,enabled=True),patch.object(r,'forward_routes',return_value=[]),patch.object(r.subprocess,'run',side_effect=subprocess.TimeoutExpired('ssh',5)):
            rows,note=r.remote_activity(pathlib.Path('/unused'),models())
        self.assertEqual(rows,[]);self.assertIn('未沿用旧任务',note)
