import json, pathlib, sqlite3, tempfile, unittest
import conversation as c

class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=pathlib.Path(self.temp.name)
        self.cache=self.home/'.dsh/storages/session_projcache/sessions';self.cache.mkdir(parents=True)
    def tearDown(self):self.temp.cleanup()
    def dsh(self,sid,title):
        (self.cache/(sid+'.json')).write_text(json.dumps({'record':{'rows':{'title':{'val':title}}}}))
    def test_hermes_uses_saved_identity_not_title(self):
        db=self.home/'.hermes/state.db';db.parent.mkdir()
        with sqlite3.connect(db) as conn:
            conn.execute('CREATE TABLE sessions(id TEXT,title TEXT)');conn.execute('INSERT INTO sessions VALUES (?,?)',('session-123','test'))
        result=c.destination(self.home,'Hermes','session-123')
        self.assertEqual(result['url'],'hermes://open/session-123')
        with self.assertRaises(RuntimeError):c.destination(self.home,'Hermes','missing')
    def test_deleted_dsh_session_does_not_open_app_as_success(self):
        with self.assertRaisesRegex(RuntimeError,'移除'):c.destination(self.home,'DSH','session-missing')
    def test_dsh_resolves_full_current_title(self):
        title='标题'*100;self.dsh('session-123',title)
        self.assertEqual(c.destination(self.home,'DSH','session-123')['title'],title)
    def test_renamed_dsh_title_is_read_at_click_time(self):
        self.dsh('session-123','old');self.dsh('session-123','new')
        self.assertEqual(c.destination(self.home,'DSH','session-123')['title'],'new')
    def test_duplicate_and_prefix_titles_are_not_guessed(self):
        self.dsh('session-123','Task')
        for name in ['Task','Task another']:
            self.dsh('session-456',name)
            with self.assertRaisesRegex(RuntimeError,'无法安全'):c.destination(self.home,'DSH','session-123')
    def test_unnamed_dsh_is_not_guessed(self):
        self.dsh('session-123',None)
        with self.assertRaisesRegex(RuntimeError,'尚未生成'):c.destination(self.home,'DSH','session-123')
    def test_path_traversal_and_other_agents_are_refused(self):
        for sid in ['../../state','a/b','x?foo=bar','']:
            with self.assertRaises(RuntimeError):c.destination(self.home,'DSH',sid)
        with self.assertRaises(RuntimeError):c.destination(self.home,'Unknown','session-123')

if __name__=='__main__':unittest.main()
