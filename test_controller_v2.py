import test_support  # Isolate settings before importing any application module.
import copy, importlib, json, os, pathlib, tempfile, unittest
from unittest.mock import patch
import local_controller as c

def model(key='tensorfold:27b', **extra):
    m=c.compact_model(key,'Test model','TensorFold','test','http://127.0.0.1:18080/v1',65536,20*2**30)
    m.update(extra)
    return m

class LifecycleTests(unittest.TestCase):
    def test_distinct_checkpoints_with_similar_size_remain_visible(self):
        inventory=[{'modelKey':key,'sizeBytes':100000001+i,'architecture':'same','quantization':{'name':'4bit'}} for i,key in enumerate(['base','fine-tune','base'])]
        with patch.object(c,'lm_inventory',return_value=inventory):
            rows=c.models_snapshot({}, {'models':[],'root':None}, {'models':[]})
        self.assertEqual([m['id'] for m in rows if m['id'].startswith('lmstudio:')],['lmstudio:base','lmstudio:fine-tune'])

    def test_short_probe_requires_final_ok_not_reasoning(self):
        ready=model(state='ready',busy=False)
        for message in [{'reasoning_content':'OK'}, {'content':'not OK'}, {'content':''}]:
            with self.subTest(message=message),patch.object(c,'find_model',return_value=({},ready)),patch.object(c,'request',return_value={'choices':[{'message':message}]}):
                with self.assertRaisesRegex(RuntimeError,'不计为测试通过'):c.test_model(ready['id'])
        with patch.object(c,'find_model',return_value=({},ready)),patch.object(c,'request',return_value={'choices':[{'message':{'content':' OK '}}]}):
            self.assertIn('实测响应：OK',c.test_model(ready['id'])['message'])

    def test_hermes_unreadable_is_unknown_not_stopped(self):
        with patch.object(c,'lm_inventory',return_value=[]):
            models=c.models_snapshot({}, {'models':[dict(id='flash',value='unknown',busy=None,weight=0,vision=False)],'root':'http://127.0.0.1:49309','error':'unreadable'}, {'models':[]})
        flash=next(m for m in models if m['id'].startswith('hermes:'))
        self.assertEqual(flash['state'],'unknown');self.assertFalse(flash['can_load']);self.assertFalse(flash['can_release'])

    def test_lm_resident_is_not_ready_without_api(self):
        inventory=[{'type':'llm','modelKey':'test','sizeBytes':100}]
        with patch.object(c,'lm_inventory',return_value=inventory):
            m=c.models_snapshot({}, {'models':[],'root':None}, {'models':[{'modelKey':'test','identifier':'test'}],'api_ids':[]})[-1]
        self.assertEqual(m['state'],'resident');self.assertTrue(m['can_load']);self.assertTrue(m['can_release'])

    def test_resident_start_api_does_not_reload_or_budget_again(self):
        before=model('lmstudio:test',state='resident');after=model('lmstudio:test',state='ready')
        with patch.object(c,'find_model',side_effect=[({},before),({},after)]),patch.object(c,'run') as run:
            c.load(before['id'])
            self.assertEqual(run.call_count,1);self.assertEqual(run.call_args.args[0][1:],['server','start'])
    def test_foreign_port_is_not_managed(self):
        with patch.object(c,'lm_inventory',return_value=[]):
            m=c.models_snapshot({'pid':123,'health':{'model':'foreign','status':'ok'}},{'models':[],'root':None},{'models':[]})[0]
        self.assertEqual(m['state'],'foreign');self.assertFalse(m['can_load']);self.assertFalse(m['can_release'])

    def test_busy_flash_disables_release(self):
        llama={'models':[{'id':'Flash','value':'loaded','busy':True,'weight':10,'vision':True}],'root':'http://127.0.0.1:49309'}
        with patch.object(c,'lm_inventory',return_value=[]):
            models=c.models_snapshot({},llama,{'models':[]})
        flash=next(m for m in models if m['id']=='hermes:Flash')
        self.assertFalse(flash['can_release']);self.assertEqual(flash['state_text'],'使用中')

    def test_memory_limit_refuses_before_loading(self):
        m=model();s={'models':[m],'memory':{'total':100,'used':90}}
        with patch.object(c,'find_model',return_value=(s,m)),patch.object(c,'preferences',return_value={'memory_limit':90,'evict_idle':False}),patch.object(c,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'加载保护预算'):c.load(m['id'])
            run.assert_not_called()

    def test_eviction_preflight_does_not_partially_release(self):
        m=model();idle=model('hermes:idle',state='ready',busy=False);busy=model('hermes:busy',state='ready',busy=True)
        s={'models':[m,idle,busy],'memory':{'total':10**12,'used':0}}
        with patch.object(c,'find_model',return_value=(s,m)),patch.object(c,'preferences',return_value={'memory_limit':90,'evict_idle':True}),patch.object(c,'release') as release:
            with self.assertRaisesRegex(RuntimeError,'没有释放任何'):c.load(m['id'])
            release.assert_not_called()

    def test_release_rechecks_activity(self):
        stale=model(state='ready',busy=False,can_release=True)
        fresh=model(state='ready',busy=True,can_release=False)
        with patch.object(c,'find_model',return_value=({},fresh)),patch.object(c,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'正在处理请求'):c.release(stale)
            run.assert_not_called()

    def test_unknown_lm_activity_requires_explicit_confirmation(self):
        m=model('lmstudio:sample',state='ready',busy=None,can_release=True)
        with patch.object(c,'find_model',return_value=({},m)),patch.object(c,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'确认释放'):c.release(m)
            run.assert_not_called()

    def test_lm_status_failure_is_not_reported_stopped(self):
        inventory=[{'type':'llm','modelKey':'test','displayName':'test','sizeBytes':100}]
        with patch.object(c,'lm_inventory',return_value=inventory):
            m=c.models_snapshot({}, {'models':[],'root':None}, {'models':[],'error':'unavailable'})[-1]
        self.assertEqual(m['state'],'unknown');self.assertFalse(m['can_load'])

    def test_loopback_only(self):
        with self.assertRaisesRegex(RuntimeError,'本机回环'):c.request('https://example.com','/models')

class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=pathlib.Path(self.temp.name)
        self.patches=[patch.object(c,'HOME',self.home),patch.object(c,'ROOT',self.home/'controller'),patch.object(c,'DSH_HOME',self.home/'.dsh'),patch.dict(os.environ,{'HERMES_HOME':str(self.home/'.hermes')})]
        for p in self.patches:p.start()
        (self.home/'.hermes').mkdir()
        self.original={'model':{'provider':'old','default':'old-model'},'browser':{'cdp_url':'http://127.0.0.1:9222'},'agent':{'environment_hint':'保留桌面与浏览器说明'},'providers':{'lmstudio':{'base_url':'http://127.0.0.1:1234/v1'}}}
        (self.home/'.hermes/config.yaml').write_text(json.dumps(self.original))

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def test_real_hermes_api_preserves_unrelated_config_and_restores(self):
        read_raw_config=c.hermes_config
        m=model(model_id=c.TF_ID)
        c.assign_hermes(m)
        after=read_raw_config()
        self.assertEqual(after['model']['default'],c.TF_ID)
        self.assertEqual(after['browser'],self.original['browser'])
        self.assertEqual(after['agent']['environment_hint'],self.original['agent']['environment_hint'])
        self.assertEqual(after['providers']['lmstudio'],self.original['providers']['lmstudio'])
        c.restore('hermes')
        self.assertEqual(read_raw_config()['model'],self.original['model'])

    def test_dsh_assignment_preserves_other_routes_and_is_independent(self):
        dsh=c.DSH_HOME;dsh.mkdir()
        prior={'agent-default-model':{'provider':'cloud','model':'previous'},'theme':{'mode':'dark'},'llm-pi-ai':{'providers':{'existing':{'baseURL':'https://example.com'}}}}
        c.atomic(dsh/'settings.yaml',c.yaml.safe_dump(prior))
        hermes_before=(self.home/'.hermes/config.yaml').read_bytes()
        c.assign_dsh(model(model_id=c.TF_ID))
        current=c.read_yaml(dsh/'settings.yaml')
        self.assertEqual(current['theme'],prior['theme'])
        self.assertEqual(current['llm-pi-ai']['providers']['existing'],prior['llm-pi-ai']['providers']['existing'])
        self.assertEqual(current['agent-default-model']['model'],c.TF_ID)
        self.assertEqual((self.home/'.hermes/config.yaml').read_bytes(),hermes_before)
        self.assertEqual((dsh/'.env').stat().st_mode&0o777,0o600)
        c.restore('dsh')
        self.assertEqual(c.read_yaml(dsh/'settings.yaml')['agent-default-model'],prior['agent-default-model'])

    def test_hermes_native_route_resolves_the_authenticated_runtime_key(self):
        resolve_runtime_provider=lambda **kw:c.hermes_config()['model']
        native=model('hermes:flash',model_id='flash')
        with patch.object(c,'managed',return_value=('http://127.0.0.1:18435','test-native-runtime-key')):
            c.assign_hermes(native)
        route=resolve_runtime_provider(requested='llamacpp',target_model='flash')
        self.assertEqual(route['base_url'],'http://127.0.0.1:18435/v1')
        self.assertEqual(route['api_key'],'test-native-runtime-key')

    def test_dsh_same_engine_switch_and_restore_keeps_catalog(self):
        first=model('lmstudio:first',model_id='first');second=model('lmstudio:second',model_id='second')
        c.assign_dsh(first);c.assign_dsh(second);c.restore('dsh')
        config=c.read_yaml(c.DSH_HOME/'settings.yaml')
        selected=config['agent-default-model']
        ids={m['id'] for m in config['llm-pi-ai']['providers'][selected['provider']]['models']}
        self.assertEqual(selected['model'],'first');self.assertIn('first',ids);self.assertIn('second',ids)

class PerformanceTests(unittest.TestCase):
    def test_prometheus_averages_not_zero_or_nan(self):
        import performance as p
        value=p.prometheus('llamacpp:prompt_tokens_seconds 320.5\nllamacpp:predicted_tokens_seconds 72.1\n')
        self.assertEqual(value['input'],320.5);self.assertEqual(value['output'],72.1)
        self.assertIsNone(p.prometheus('llamacpp:predicted_tokens_seconds NaN')['output'])

    def test_lm_stats_never_derive_input_from_ttft(self):
        import performance as p
        text='[2026-10-19 14:00:00][INFO][test] Generated prediction: '+json.dumps({'model':'test','stats':{'tokens_per_second':55,'time_to_first_token':.1},'usage':{'prompt_tokens':100}})
        result=p.lm_log(text)['test']
        self.assertEqual(result['output'],55);self.assertIsNone(result['input'])
        self.assertEqual(set(result),{'input','output','label','at'})

    def test_lm_event_retains_metrics_and_discards_reply(self):
        import performance as p
        mid,rate=p.lm_event({'timestamp':1792389585501,'data':{'type':'llm.prediction.output','modelIdentifier':'local-model','output':'private reply','stats':{'tokensPerSecond':36,'timeToFirstTokenSec':.2,'promptTokensCount':100}}})
        self.assertEqual(mid,'local-model');self.assertEqual(rate['output'],36);self.assertIsNone(rate['input'])
        self.assertNotIn('private reply',json.dumps(rate))

    def test_missing_stats_does_not_invent_values(self):
        import performance as p
        self.assertEqual(p.lm_log('[INFO][test] Generated prediction: {"model":"test"}'),{})

if __name__=='__main__':unittest.main()
