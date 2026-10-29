import test_support
import contextlib,json,pathlib,sqlite3,tempfile,unittest,socket
from unittest.mock import patch
from history_titles import local_titles,attach_titles
class TitleTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=pathlib.Path(self.tmp.name)
 def hermes(self,path,title):
  path.parent.mkdir(parents=True,exist_ok=True)
  with sqlite3.connect(path) as c:c.execute('CREATE TABLE sessions(id TEXT,title TEXT)');c.execute('INSERT INTO sessions VALUES(?,?)',('session-1',title))
 def test_exact_identifier_and_ambiguous_profile_refusal(self):
  self.hermes(self.home/'.hermes/state.db','Real title');self.assertEqual(local_titles([dict(origin='Hermes',session_id='session-1')],self.home)[0]['title'],'Real title')
  self.assertEqual(local_titles([dict(origin='Hermes',session_id='session-2')],self.home),[])
  self.hermes(self.home/'.hermes/profiles/other/state.db','Different title');self.assertEqual(local_titles([dict(origin='Hermes',session_id='session-1')],self.home),[])
 def test_dsh_does_not_read_transcripts_or_accept_path_traversal(self):
  p=self.home/'.dsh/storages/session_projcache/sessions/session-1.json';p.parent.mkdir(parents=True);p.write_text(json.dumps({'record':{'rows':{'title':{'val':'Task title'},'private':{'val':'PRIVATE'}}}}))
  rows=local_titles([dict(origin='DSH',session_id='session-1'),dict(origin='DSH',session_id='../secret')],self.home);self.assertEqual(len(rows),1);self.assertNotIn('PRIVATE',json.dumps(rows))
 def test_multiple_requests_in_one_native_session_keep_the_title(self):
  self.hermes(self.home/'.hermes/state.db','Real title')
  rows=[dict(request_origin='Hermes',session_id='session-1',request_host=socket.gethostname(),attribution_status='exact',request_id='completion-'+str(i)) for i in range(4)]
  with patch('runtime_config.settings',return_value={'remote':{'enabled':False}}):attach_titles(rows,self.home)
  self.assertEqual([r.get('task_title') for r in rows],['Real title']*4)
