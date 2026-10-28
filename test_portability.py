import test_support  # Isolate settings before importing any application module.
import contextlib,importlib.util,json,os,pathlib,subprocess,tempfile,unittest
from unittest.mock import patch
import local_controller as c
import runtime_config as config
import remote_activity

class PortabilityTests(unittest.TestCase):
    def test_remote_is_disabled_and_never_runs_ssh_by_default(self):
        with patch.dict(remote_activity.REMOTE,enabled=False),patch.object(remote_activity.subprocess,'run') as run:
            self.assertEqual(remote_activity.remote_activity(pathlib.Path('/unused'),[]),([],''));run.assert_not_called()

    def test_settings_merge_retains_defaults_and_expands_paths(self):
        with tempfile.TemporaryDirectory() as td,patch.object(config,'DATA_DIR',pathlib.Path(td)):
            (pathlib.Path(td)/'settings.json').write_text(json.dumps({'remote':{'enabled':False},'paths':{'node':'~/custom/node'}}))
            value=config.settings();self.assertEqual(value['remote']['expected_hostname'],'inference-host.local')
            self.assertTrue(value['paths']['node'].endswith('/custom/node'));self.assertFalse(value['tensorfold']['enabled'])

    def test_settings_reject_malformed_sections_boolean_and_ssh_options(self):
        for value in ({'paths':[]},{'remote':{'enabled':'yes'}},{'remote':{'enabled':True,'ssh_host':'-oProxyCommand=bad'}}):
            with self.subTest(value=value),tempfile.TemporaryDirectory() as td,patch.object(config,'DATA_DIR',pathlib.Path(td)):
                (pathlib.Path(td)/'settings.json').write_text(json.dumps(value))
                with self.assertRaises(ValueError):config.settings()

    def test_custom_agent_metadata_roots_are_honored(self):
        with tempfile.TemporaryDirectory() as td,patch.object(config,'DATA_DIR',pathlib.Path(td)):
            folder=pathlib.Path(td);custom=folder/'custom-agent'
            (folder/'settings.json').write_text(json.dumps({'paths':{'dsh_home':str(custom)}}))
            self.assertEqual(config.agent_home(folder/'unused','dsh'),custom)
            self.assertEqual(config.agent_home(folder/'isolated','hermes'),folder/'isolated/.hermes')

    def test_no_phantom_tensorfold_model_for_a_new_install(self):
        with tempfile.TemporaryDirectory() as td,patch.object(c,'HOME',pathlib.Path(td)),patch.object(c,'lm_inventory',return_value=[]):
            self.assertEqual(c.models_snapshot({'disabled':True},{'models':[],'root':None},{'models':[]}),[])

    def test_no_hermes_installation_is_a_supported_status(self):
        with patch.dict(os.environ,LMC_DISABLE_HERMES='1'),patch.dict(c.CONFIG['llama'],base_url=''):
            self.assertIsNone(c.managed());self.assertEqual(c.llama_snapshot()['models'],[])

    def test_unknown_size_is_refused_before_engine_mutation(self):
        m=c.compact_model('lmstudio:test','Test','LM Studio','test','http://127.0.0.1:1234/v1',65536,0)
        with patch.object(c,'find_model',return_value=({'models':[m],'memory':{'total':10**12,'used':0}},m)),patch.object(c,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'无法估算'):c.load(m['id'])
            run.assert_not_called()

    def test_native_config_restore_keeps_browser_and_other_providers(self):
        with tempfile.TemporaryDirectory() as td,patch.object(c,'HOME',pathlib.Path(td)),patch.object(c,'ROOT',pathlib.Path(td)/'data'):
            before={'browser':{'enabled':True},'model':{'default':'prior'},'providers':{'existing':{'url':'https://example.org'}}}
            c.atomic(c.hermes_home()/'config.yaml',c.yaml.safe_dump(before));c.save_hermes_config({'model':{'default':'next'}})
            now=c.hermes_config();self.assertEqual(now['model']['default'],'next');self.assertEqual(now['browser'],before['browser']);self.assertEqual(now['providers'],before['providers'])

class WeightBudgetTests(unittest.TestCase):
    def test_unloaded_split_model_uses_complete_weight_set(self):
        with tempfile.TemporaryDirectory() as td,patch.object(c,'HOME',pathlib.Path(td)),patch.object(c,'ROOT',pathlib.Path(td)/'data'):
            root=pathlib.Path(td);preset=root/'.hermes/runtimes/llamacpp/presets.ini';preset.parent.mkdir(parents=True)
            first=root/'fixture-00001-of-00002.gguf';second=root/'fixture-00002-of-00002.gguf'
            first.write_bytes(b'a'*11);second.write_bytes(b'b'*19)
            preset.write_text('[fixture]\nmodel = '+str(first)+'\n')
            self.assertEqual(c.llama_load_budget('fixture',0),int(30*1.08+2*2**30))
            second.rename(root/'missing-part')
            self.assertEqual(c.llama_load_budget('fixture',0),0)

    def test_native_weight_metadata_is_reused_without_a_model_specific_guess(self):
        with tempfile.TemporaryDirectory() as td,patch.object(c,'ROOT',pathlib.Path(td)/'data'),patch.object(c,'HOME',pathlib.Path(td)):
            self.assertEqual(c.llama_load_budget('fixture',100),int(100*1.08+2*2**30))
            self.assertEqual(c.llama_load_budget('fixture',0),int(100*1.08+2*2**30))

class LocalizationTests(unittest.TestCase):
    def test_all_source_keys_have_english_translations(self):
        root=pathlib.Path(__file__).parent
        spec=importlib.util.spec_from_file_location('extract',root/'scripts/extract_strings.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        catalog=json.loads((root/'locales/en.json').read_text())
        self.assertEqual([s for s in module.sources() if s not in catalog],[])
        for source,target in catalog.items():
            self.assertTrue(target.strip(),source)
            self.assertEqual(source.count('%@'),target.count('%@'),source)

    def test_each_language_has_a_catalog_and_known_fallback(self):
        root=pathlib.Path(__file__).parent/'locales';languages=json.loads((root/'languages.json').read_text())
        self.assertEqual(len({row['id'] for row in languages}),len(languages))
        for row in languages:self.assertIsInstance(json.loads((root/(row['id']+'.json')).read_text()),dict)

class NativeReaderTests(unittest.TestCase):
    def test_native_reader_accepts_only_loopback_ownership_results(self):
        from types import SimpleNamespace
        for value,expected in [(['http://127.0.0.1:18434','fixture-key'],('http://127.0.0.1:18434','fixture-key')),
                               (['https://example.org','fixture-key'],None),(None,None)]:
            with self.subTest(value=value),tempfile.TemporaryDirectory() as td:
                payload=pathlib.Path(td);exe=payload/'tools/python-example/bin/python3';exe.parent.mkdir(parents=True);exe.write_text('fixture')
                with patch.object(c,'PAYLOAD',payload),patch.object(c,'_native_reader_checked',False),patch.object(c,'_native_reader_cache',None),patch.object(c.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(value))):
                    self.assertEqual(c.native_managed_reader(),expected)

    def test_native_reader_failure_does_not_trust_a_default_port(self):
        with tempfile.TemporaryDirectory() as td:
            payload=pathlib.Path(td);exe=payload/'tools/python-example/bin/python3';exe.parent.mkdir(parents=True);exe.write_text('fixture')
            with patch.object(c,'PAYLOAD',payload),patch.object(c,'_native_reader_checked',False),patch.object(c,'_native_reader_cache',None),patch.object(c.subprocess,'run',side_effect=subprocess.TimeoutExpired('fixture',3)):
                self.assertIsNone(c.native_managed_reader())
