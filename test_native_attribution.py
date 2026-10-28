import json, pathlib, sqlite3, tempfile, unittest
from types import SimpleNamespace
from unittest.mock import patch
import local_controller
import activity
import mini_probe as p
import remote_activity as r

def models():
    return [dict(id='hermes:flash',model_id='flash',base_url='http://127.0.0.1:18434/v1',state='ready',busy=True)]

def route(active=True,local=18434):
    return dict(remote_port=18434,local_port=local,transport_pid=17,connected=active)

def session(sid='native1'):
    return dict(agent='Hermes',session_id=sid,title='会话标题',model_id='flash',
        base_url='http://127.0.0.1:18434/v1',owner_pid=42,request_connected=True,
        process_started_at=1,lease_acquired_at=90,lease_expires_at=110,phase='模型请求中')

def payload():
    return dict(schema=1,host='inference-host.local',generated_at=100,
        gateway={},note='Gateway metadata not configured',direct_sessions=[session()])

class NativeAttributionTests(unittest.TestCase):
    def profile_db(self,home,profile='default',sid='native1',title='真实标题',expires=110):
        root=home/'.hermes'
        if profile!='default':root=root/'profiles'/profile
        root.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(root/'state.db') as c:
            c.execute('create table session_turn_leases(conversation_id,holder,acquired_at,expires_at)')
            c.execute('create table sessions(id,title,model,billing_base_url,ended_at)')
            c.execute('insert into sessions values(?,?,?,?,NULL)',(sid,title,'flash','http://127.0.0.1:18434/v1'))
            c.execute('insert into session_turn_leases values(?,?,90,?)',(sid,'pid=42:turn=x',expires))
        return root/'state.db'

    def test_named_profile_lease_is_found_without_default_db(self):
        with tempfile.TemporaryDirectory() as td:
            h=pathlib.Path(td);self.profile_db(h,'sample-profile')
            rows,clients=p.native_lease_rows(h,{18434},100,lambda home,pid:1,lambda port:{42})
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['profile'],'sample-profile')
            self.assertEqual(rows[0]['title'],'真实标题');self.assertEqual(clients,[])
            d=payload();d['direct_sessions']=rows
            self.assertEqual(r.direct_rows(d,models(),[route()],100)[0]['title'],'真实标题')

    def test_default_and_named_profiles_keep_distinct_exact_titles(self):
        with tempfile.TemporaryDirectory() as td:
            h=pathlib.Path(td);self.profile_db(h,title='默认任务')
            self.profile_db(h,'other',sid='native2',title='独立任务')
            rows,clients=p.native_lease_rows(h,{18434},100,lambda home,pid:1,lambda port:{42})
            self.assertEqual({x['title'] for x in rows},{'默认任务','独立任务'});self.assertEqual(clients,[])
            d=payload();d['direct_sessions']=rows
            self.assertTrue(all(x['confidence']=='uncertain' for x in r.direct_rows(d,models(),[route()],100)))

    def test_profile_stale_reused_pid_wrong_port_and_disconnected_rejected(self):
        for born,ports,connected,expires in [(95,{18434},{42},110),(1,{18080},{42},110),
                                                   (1,{18434},set(),110),(1,{18434},{42},99)]:
            with self.subTest(born=born,ports=ports,connected=connected,expires=expires),tempfile.TemporaryDirectory() as td:
                h=pathlib.Path(td);self.profile_db(h,'other',expires=expires)
                self.assertEqual(p.native_lease_rows(h,ports,100,lambda home,pid:born,lambda port:connected)[0],[])

    def test_unreadable_profile_does_not_hide_valid_profile(self):
        with tempfile.TemporaryDirectory() as td:
            h=pathlib.Path(td);self.profile_db(h,'valid')
            bad=h/'.hermes/profiles/bad';bad.mkdir();(bad/'state.db').write_text('invalid sqlite')
            self.assertEqual(p.native_lease_rows(h,{18434},100,lambda home,pid:1,lambda port:{42})[0][0]['profile'],'valid')

    def test_profile_directory_and_database_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as td,tempfile.TemporaryDirectory() as outside:
            h=pathlib.Path(td);other=pathlib.Path(outside);db=self.profile_db(other)
            profiles=h/'.hermes/profiles';profiles.mkdir(parents=True)
            (profiles/'linked').symlink_to(db.parent,target_is_directory=True)
            (profiles/'linked-db').mkdir();(profiles/'linked-db/state.db').symlink_to(db)
            self.assertEqual(p.native_lease_rows(h,{18434},100,lambda home,pid:1,lambda port:{42})[0],[])

    def test_live_idle_gateway_keepalive_is_not_reported_as_a_request(self):
        with tempfile.TemporaryDirectory() as td:
            h=pathlib.Path(td);self.profile_db(h,'other',expires=99)
            query=lambda idle:p.native_lease_rows(h,{18434},100,lambda home,pid:1,lambda port:{42},idle_reader=lambda:idle)
            self.assertEqual(query(42),([],[]))
            self.assertEqual(len(query(None)[1]),1)  # Failed live read preserves uncertainty.
            self.assertEqual(len(query(43)[1]),1)  # Never suppress a different owner.

    def test_valid_lease_survives_later_idle_sample_and_does_not_call_idle_reader(self):
        with tempfile.TemporaryDirectory() as td:
            h=pathlib.Path(td);self.profile_db(h,'other')
            reader=unittest.mock.Mock(return_value=42)
            self.assertEqual(len(p.native_lease_rows(h,{18434},100,lambda home,pid:1,lambda port:{42},idle_reader=reader)[0]),1)
            reader.assert_not_called()

    def test_idle_reply_requires_success_matching_id_protocol_count_and_empty_work(self):
        good=dict(ok=True,protocol=1,id='controller-idle',result=dict(pid=42,active_agents=0,active_work=[]))
        cases=[(good,42),({**good,'ok':False},None),({**good,'protocol':2},None),
               ({**good,'id':'other'},None),({**good,'result':dict(pid=42,active_agents=1,active_work=[])},None),
               ({**good,'result':dict(pid=42,active_agents=0,active_work=[dict(kind='chat')])},None),
               ({**good,'result':dict(pid=42,active_agents=False,active_work=[])},None)]
        for reply,expected in cases:
            channel=unittest.mock.MagicMock();channel.__enter__.return_value=channel
            channel.recv.return_value=(json.dumps(reply)+'\n').encode()
            with patch.object(pathlib.Path,'lstat',return_value=SimpleNamespace(st_mode=0o140600,st_uid=p.os.getuid())),patch.object(p.socket,'socket',return_value=channel):
                self.assertEqual(p.idle_gateway_pid(pathlib.Path('/tmp')),expected)

    def test_direct_hermes_does_not_depend_on_private_gateway_release(self):
        rows,note=r.map_snapshot(payload(),models(),False,101,forwards=[route()])
        self.assertEqual(note,'');self.assertEqual(rows[0]['agent'],'Hermes · mini')
        self.assertEqual(rows[0]['title'],'会话标题');self.assertFalse(rows[0]['can_open'])

    def test_forward_requires_actual_connected_destination(self):
        for forwards in [[route(False)],[route(local=18080)],[],[route(),route(local=18080)]]:
            self.assertEqual(r.direct_rows(payload(),models(),forwards,101),[])

    def test_expired_lease_or_reused_pid_never_borrows_title(self):
        for change in [dict(lease_expires_at=100),dict(process_started_at=95),dict(request_connected=False)]:
            data=payload();data['direct_sessions'][0].update(change)
            self.assertEqual(r.direct_rows(data,models(),[route()],101),[])

    def test_wrong_model_or_remote_endpoint_refused(self):
        for change in [dict(model_id='wrong'),dict(base_url='http://203.0.113.1:18434/v1'),dict(base_url='http://127.0.0.1:18434/other')]:
            data=payload();data['direct_sessions'][0].update(change)
            self.assertEqual(r.direct_rows(data,models(),[route()],101),[])

    def test_multiple_native_leases_stay_explicitly_uncertain(self):
        data=payload();data['direct_sessions'].append(session('native2'))
        rows=r.direct_rows(data,models(),[route()],101)
        self.assertEqual(len(rows),2);self.assertTrue(all(x['confidence']=='uncertain' for x in rows))

    def test_proven_process_with_missing_lease_preserves_only_caller(self):
        data=payload();data['direct_sessions']=[]
        data['direct_clients']=[dict(agent='Hermes',owner_pid=42,port=18434,request_connected=True)]
        rows=r.direct_rows(data,models(),[route()],101)
        self.assertEqual(rows[0]['agent'],'Hermes · mini')
        self.assertEqual(rows[0]['confidence'],'uncertain');self.assertIn('任务名暂不可读',rows[0]['title'])

    def test_forward_parser_supports_both_existing_tunnels(self):
        self.assertEqual(r.parse_forward('18434:127.0.0.1:18434'),(18434,18434))
        self.assertEqual(r.parse_forward('127.0.0.1:19434:127.0.0.1:18434'),(19434,18434))
        for value in ['0:127.0.0.1:18434','18434:example.com:18434','0.0.0.0:18434:127.0.0.1:18434']:
            self.assertIsNone(r.parse_forward(value))

    def test_forward_discovery_rejects_conflicting_remote_endpoints(self):
        connection=SimpleNamespace(raddr=SimpleNamespace(ip='127.0.0.1',port=18434),status='ESTABLISHED')
        def proc(pid,local):
            return SimpleNamespace(pid=pid,info=dict(exe='/usr/bin/ssh',cmdline=['ssh','-R','18434:127.0.0.1:'+str(local),'inference-host']),net_connections=lambda **kw:[connection])
        with patch.object(r.psutil,'process_iter',return_value=[proc(1,18434),proc(2,18080)]):
            self.assertEqual(r.forward_routes(),[])

    def test_native_database_lease_is_exact_and_prompt_free(self):
        with tempfile.TemporaryDirectory() as td:
            h=pathlib.Path(td);(h/'.hermes').mkdir()
            with sqlite3.connect(h/'.hermes/state.db') as c:
                c.execute('create table session_turn_leases(conversation_id,holder,acquired_at,expires_at)')
                c.execute('create table sessions(id,title,model,billing_base_url,ended_at)')
                c.execute('create table messages(content)');c.execute("insert into messages values('PRIVATE PROMPT')")
                c.execute("insert into sessions values('native1','真实标题','flash','http://127.0.0.1:18434/v1',NULL)")
                c.execute("insert into session_turn_leases values('native1','pid=42:turn=x',90,110)")
            query=lambda born:p.native_lease_rows(h,{18434},100,lambda home,pid:born,lambda port:{42})
            rows,clients=query(1);self.assertEqual(rows[0]['title'],'真实标题');self.assertEqual(clients,[])
            self.assertNotIn('PRIVATE PROMPT',json.dumps(rows))
            rows,clients=query(95);self.assertEqual(rows,[]);self.assertEqual(len(clients),1)
            with sqlite3.connect(h/'.hermes/state.db') as c:c.execute('update session_turn_leases set expires_at=99')
            self.assertEqual(query(1)[0],[])

    def test_native_database_does_not_choose_nearby_session(self):
        with tempfile.TemporaryDirectory() as td:
            h=pathlib.Path(td);(h/'.hermes').mkdir()
            with sqlite3.connect(h/'.hermes/state.db') as c:
                c.execute('create table session_turn_leases(conversation_id,holder,acquired_at,expires_at)')
                c.execute('create table sessions(id,title,model,billing_base_url,ended_at)')
                c.execute("insert into session_turn_leases values('missing','pid=42:turn=x',90,110)")
                c.execute("insert into sessions values('other','不能冒认的标题','flash','http://127.0.0.1:18434/v1',NULL)")
            rows,clients=p.native_lease_rows(h,{18434},100,lambda home,pid:1,lambda port:{42})
            self.assertEqual(rows,[]);self.assertNotIn('不能冒认的标题',json.dumps(clients))

    def test_macos_sshd_process_title_is_not_mistaken_for_executable_path(self):
        with patch.object(p,'socket_pids',side_effect=[{60897},{60897,67353}]),patch.object(p,'run',return_value='p60897\nftxt\nn/usr/libexec/sshd-session\n'):
            self.assertEqual(p.direct_connections(18434),{67353})

    def test_a_process_named_sshd_without_system_executable_is_rejected(self):
        with patch.object(p,'socket_pids',return_value={42}),patch.object(p,'run',return_value='sshd-session: example-user\nn/tmp/sshd-session\n'):
            self.assertEqual(p.direct_connections(18434),set())

    def test_cli_optional_flags_do_not_drop_valid_caller(self):
        proc=SimpleNamespace(info=dict(pid=9,exe='/Applications/Hermes.app/python',cmdline=['python','chat','--provider','llamacpp','--model','flash']),
            net_connections=lambda **kw:[SimpleNamespace(raddr=SimpleNamespace(ip='127.0.0.1',port=18434),status='ESTABLISHED')])
        with patch.object(activity.psutil,'process_iter',return_value=[proc]):
            rows,note=activity.hermes_cli_activity(pathlib.Path('/tmp'),models())
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['title'],'Hermes CLI 任务')

    def test_refresh_wires_cli_reader_with_home_and_models(self):
        import remote_activity
        m=models();home=pathlib.Path('/tmp')
        def source(actual_home,actual_models):
            self.assertEqual(actual_home,home);self.assertIs(actual_models,m)
            return [dict(model_key=m[0]['id'],agent='Hermes CLI',session_id='cli-1',title='任务',phase='处理任务',source='真实连接',confidence='live')],''
        with patch.object(activity,'hermes_activity',return_value=([],'')),patch.object(activity,'dsh_activity',return_value=([],'')),patch.object(activity,'hermes_cli_activity',side_effect=source),patch.object(remote_activity,'remote_activity',return_value=([],'')):
            activity.attach(m,home)
        self.assertEqual(m[0]['activities'][0]['agent'],'Hermes CLI')

    def test_request_ending_between_snapshots_does_not_leave_false_unknown_busy(self):
        m=models();m[0].update(activities=[],activity_state='unknown',activity_note='有请求，调用方未确认',can_release=False)
        with patch.object(local_controller,'llama_snapshot',return_value={'models':[{'id':'flash','value':'loaded','busy':False}]}),patch.object(activity,'attach') as again:
            local_controller.recheck_unattributed_llama(m,pathlib.Path('/tmp'))
        self.assertFalse(m[0]['busy']);self.assertEqual(m[0]['activity_state'],'idle');again.assert_not_called()

    def test_unattributed_still_busy_gets_only_one_fresh_source_read(self):
        m=models();m[0]['activities']=[]
        with patch.object(local_controller,'llama_snapshot',return_value={'models':[{'id':'flash','value':'loaded','busy':True}]}),patch.object(activity,'attach') as again:
            local_controller.recheck_unattributed_llama(m,pathlib.Path('/tmp'))
        again.assert_called_once_with(m,pathlib.Path('/tmp'));self.assertTrue(m[0]['busy'])

    def test_failed_resample_keeps_busy_protection_and_does_not_guess_idle(self):
        m=models();m[0].update(activities=[],can_release=False)
        with patch.object(local_controller,'llama_snapshot',return_value={'error':'unreadable','models':[]}),patch.object(activity,'attach') as again:
            local_controller.recheck_unattributed_llama(m,pathlib.Path('/tmp'))
        self.assertTrue(m[0]['busy']);self.assertFalse(m[0]['can_release']);again.assert_not_called()

if __name__=='__main__':unittest.main()
