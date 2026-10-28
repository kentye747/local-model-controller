import test_support  # Isolate settings before importing any application module.
import contextlib,csv,datetime as dt,json,pathlib,sqlite3,tempfile,unittest,os
from unittest.mock import patch
import usage_history as h

FLASH=dict(id='hermes:flash',name='Flash',engine='Hermes · llama.cpp',model_id='flash',busy=True,
           activities=[dict(agent='Hermes · mini',confidence='live',title='PRIVATE TASK')])
TF='[tensorfold] done chatcmpl-{id} prompt=100 cached=60 tokens=40 finish={finish} ttft=0.5 decode=2 elapsed=2.5 queued=0.1 tok/s=19.5\n'
LOAD="[8] 0.00.000.001 I srv load_model: loading model '/models/flash.gguf'\n"
def timing(kind,seconds,tokens,task=1,rate=20):
    return f'[8] 0.01.000.123 I slot print_timing: id 0 | task {task} | {kind} = {seconds*1000} ms / {tokens} tokens (1 ms per token, {rate} tokens per second)\n'

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name)/'stats';self.home=pathlib.Path(self.temp.name)/'home';self.home.mkdir()
        self.c=h.connect(self.root);h.register(self.c,[FLASH]);self.c.commit()
    def tearDown(self):self.c.close();self.temp.cleanup()
    def event(self,key,**values):
        h.insert(self.c,dict(event_key=key,model_key=FLASH['id'],observed_at=100,source='fixture',**values));self.c.commit()
    def rows(self):return [dict(x) for x in self.c.execute('select * from events')]
    def test_weighted_speed_is_not_arithmetic_mean(self):
        self.event('a',output_tokens=20,output_seconds=1,output_tps=20,total_seconds=1)
        self.event('b',output_tokens=540,output_seconds=9,output_tps=60,total_seconds=9)
        s=h.summary(self.rows());self.assertEqual(s['avg_output_tps'],56);self.assertEqual(s['max_output_tps'],60);self.assertEqual(s['output_tokens'],560)
    def test_quantiles_and_missing_values(self):
        for i,v in enumerate([1,3,5,7]):self.event(str(i),total_seconds=v)
        s=h.summary(self.rows());self.assertEqual(s['p50_seconds'],4);self.assertAlmostEqual(s['p95_seconds'],6.7);self.assertIsNone(s['output_tokens']);self.assertIsNone(s['avg_output_tps'])
    def test_tensorfold_cache_and_no_ttft_to_prefill_conversion(self):
        e=h.tf_line(self.c,TF.format(id='one',finish='length'),None,100);self.assertEqual(e['input_tokens'],40);self.assertEqual(e['cached_tokens'],60);self.assertNotIn('input_tps',e);self.assertEqual(e['ttft_seconds'],.5)
        h.insert(self.c,e);s=h.summary(self.rows());self.assertEqual(s['cache_ratio'],.6);self.assertEqual(s['truncated'],1)
    def test_llama_request_assembly_ignores_prompt_and_unknown_pid(self):
        state={};self.assertIsNone(h.llama_line(self.c,timing('eval time',2,40),state,None,100))
        for line in [LOAD,'PRIVATE PROMPT\n',timing('prompt eval time',1,100,rate=100),timing('eval time',2,40),timing('total time',3,140)]:
            e=h.llama_line(self.c,line,state,None,100)
            if e:h.insert(self.c,e)
        row=self.rows()[0];self.assertEqual(row['model_key'],'hermes:flash');self.assertEqual(row['input_tokens'],100);self.assertEqual(row['output_tps'],20);self.assertIsNone(row['completed_at']);self.assertNotIn('PRIVATE PROMPT','\n'.join(self.c.iterdump()))
    def test_two_interleaved_slots_keep_independent_counts(self):
        st={};h.llama_line(self.c,LOAD,st,None,100)
        for t,n in [(1,40),(2,80)]:h.llama_line(self.c,timing('eval time',2,n,t),st,None,100)
        a=h.llama_line(self.c,timing('total time',2,80,2),st,None,100);b=h.llama_line(self.c,timing('total time',2,40,1),st,None,100)
        self.assertEqual((a['output_tokens'],b['output_tokens']),(80,40));self.assertNotEqual(a['event_key'],b['event_key'])
    def test_native_lm_usage_without_stats_is_retained(self):
        v=dict(id='request1',created=1792729899,model='mini',usage=dict(prompt_tokens=7200,completion_tokens=342),choices=[dict(finish_reason='stop',message=dict(content='PRIVATE REPLY'))])
        e=h.lm_record(self.c,v,'mini',None,1792900000,'file1');self.assertEqual(e['input_tokens'],7200);self.assertEqual(e['output_tokens'],342);self.assertEqual(e['completed_at'],1792729899);self.assertIsNone(e['output_tps']);self.assertEqual(e['finish_reason'],'stop');h.insert(self.c,e);self.assertNotIn('PRIVATE REPLY','\n'.join(self.c.iterdump()))
    def test_idempotent_capture_and_unknown_time_not_assigned_today(self):
        p=self.home/'.local/share/tensorfold/logs/server.log';p.parent.mkdir(parents=True);p.write_text(TF.format(id='old',finish='stop'))
        now=1792990000
        with patch.object(h.time,'time',return_value=now):h.capture(self.root,self.home,[FLASH],{})
        with patch.object(h.time,'time',return_value=now+4):h.capture(self.root,self.home,[FLASH],{})
        self.assertEqual(h.report(self.root,now=now)['summary']['requests'],1);self.assertEqual(h.report(self.root,days=1,now=now)['summary']['requests'],0)
        with p.open('a') as f:f.write(TF.format(id='new',finish='length'))
        with patch.object(h.time,'time',return_value=now+8):h.capture(self.root,self.home,[FLASH],{})
        self.assertEqual(h.report(self.root,now=now+8)['summary']['requests'],2);self.assertEqual(h.report(self.root,days=1,now=now+8)['summary']['requests'],1)
    def test_log_rotation_and_truncation_do_not_double_count(self):
        p=self.home/'native.log';p.write_text(TF.format(id='same',finish='stop'));h.ingest_file(self.c,p,'tf',h.LIMIT,100);self.c.commit()
        renamed=self.home/'rotated.log';p.rename(renamed);h.ingest_file(self.c,renamed,'tf',h.LIMIT,104);self.c.commit();self.assertEqual(len(self.rows()),1)
        p.write_text(TF.format(id='same',finish='stop'));h.ingest_file(self.c,p,'tf',h.LIMIT,108);self.c.commit();self.assertEqual(len(self.rows()),1)
        p.write_text('');h.ingest_file(self.c,p,'tf',h.LIMIT,112);p.write_text(TF.format(id='different',finish='stop'));h.ingest_file(self.c,p,'tf',h.LIMIT,116);self.c.commit();self.assertEqual(len(self.rows()),2)
    def test_partial_llama_request_survives_cursor_boundary(self):
        p=self.home/'native.log';p.write_text(LOAD+timing('prompt eval time',1,10)+timing('eval time',2,40))
        h.ingest_file(self.c,p,'llama',h.LIMIT,100);self.c.commit();self.assertEqual(len(self.rows()),0)
        with p.open('a') as f:f.write(timing('total time',3,50).rstrip('\n'))
        h.ingest_file(self.c,p,'llama',h.LIMIT,104);self.assertEqual(len(self.rows()),0)
        with p.open('a') as f:f.write('\n')
        h.ingest_file(self.c,p,'llama',h.LIMIT,108);self.assertEqual(len(self.rows()),1)
    def test_partial_lm_json_preserves_native_timestamp_and_not_body(self):
        p=self.home/'lm.log';value=json.dumps(dict(id='lm1',model='mini',usage=dict(prompt_tokens=10,completion_tokens=20),choices=[dict(message=dict(content='PRIVATE BODY'))]))
        prefix='[2026-10-25 12:00:00] [mini] Generated prediction: '
        p.write_text(prefix+value[:30]);os.utime(p,(1793000000,1793000000));h.ingest_file(self.c,p,'lm',h.LIMIT,1793000000);self.c.commit();self.assertEqual(self.c.execute('select offset from cursors').fetchone()[0],0)
        with p.open('a') as f:f.write(value[30:]+'\n')
        h.ingest_file(self.c,p,'lm',h.LIMIT,1793000004);self.c.commit();row=self.rows()[0];self.assertEqual(row['completed_at'],dt.datetime(2026,10,25,12,tzinfo=h.TZ).timestamp());self.assertNotIn('PRIVATE BODY','\n'.join(self.c.iterdump()))
    def test_stale_truncated_lm_record_does_not_stall_import_forever(self):
        p=self.home/'lm.log';p.write_text('[mini] Generated prediction: {"id":"incomplete"');os.utime(p,(1,1))
        h.ingest_file(self.c,p,'lm',h.LIMIT,100);self.c.commit();self.assertEqual(len(self.rows()),0)
        self.assertEqual(self.c.execute('select offset from cursors').fetchone()[0],p.stat().st_size)
        self.assertEqual(h.report(self.root,now=100)['skipped_records'],1)
    def test_pause_gap_does_not_forge_completion_time(self):
        p=self.home/'tf.log';p.write_text(TF.format(id='a',finish='stop'));h.ingest_file(self.c,p,'tf',h.LIMIT,100);self.c.commit()
        with p.open('a') as f:f.write(TF.format(id='b',finish='stop'))
        h.ingest_file(self.c,p,'tf',h.LIMIT,200);self.c.commit();self.assertTrue(all(r['completed_at'] is None for r in self.rows()))
    def test_sampling_does_not_count_closed_app_gap_or_save_task_title(self):
        memory=dict(used=100,swap=10)
        for now in (1000,1004,1100):h.sample(self.c,[FLASH],memory,now)
        self.c.commit();s=h.report(self.root,now=1100)['summary'];self.assertEqual(s['active_seconds'],4);self.assertEqual(s['peak_system_memory'],100);self.assertNotIn('PRIVATE TASK','\n'.join(self.c.iterdump()))
    def test_stream_speeds_do_not_double_count_log_token_usage(self):
        key=h.historical_model(self.c,'LM Studio','mini');self.c.commit();self.event('dummy',output_tokens=1)
        h.insert(self.c,dict(event_key='lm-count',model_key=key,observed_at=100,output_tokens=200));self.c.commit()
        e=dict(timestamp=100000,data=dict(type='llm.prediction.output',modelIdentifier='mini',stats=dict(tokensPerSecond=50,predictedTokensCount=200),content='PRIVATE'))
        h.observe_lm_event(self.root,e);h.observe_lm_event(self.root,e)
        report=h.report(self.root,model=key,now=101);self.assertEqual(report['summary']['requests'],1);self.assertEqual(report['summary']['output_tokens'],200);self.assertEqual(report['summary']['avg_output_tps'],50);self.assertEqual(self.c.execute('select count(*) from rate_samples').fetchone()[0],1)
    def test_csv_exports_full_numbers_and_blocks_spreadsheet_formula(self):
        self.c.execute('update models set name=? where id=?',('=SENSITIVE()',FLASH['id']));self.c.commit();self.event('one',input_tokens=12345,output_tokens=7)
        p=self.home/'history.csv';h.export_csv(self.root,'all',0,p,now=100)
        with p.open(encoding='utf-8-sig') as f:rows=list(csv.reader(f))
        self.assertEqual(rows[1][0],'时间不详');self.assertEqual(rows[1][2],"'=SENSITIVE()");self.assertEqual(rows[1][3],'12345');self.assertEqual(rows[1][6],'')
    def test_missing_or_invalid_numbers_do_not_become_zero_or_nan(self):
        self.event('bad',input_tokens=float('nan'),output_tps=float('inf'),total_seconds=-1)
        r=self.rows()[0];self.assertIsNone(r['input_tokens']);self.assertIsNone(r['output_tps']);self.assertIsNone(r['total_seconds'])
    def test_local_day_boundaries_respect_selected_timezone(self):
        now=dt.datetime(2026,10,25,16,30,tzinfo=dt.timezone.utc).timestamp()
        for zone,day in [(dt.timezone(dt.timedelta(hours=8)),26),(dt.timezone.utc,25)]:
            with self.subTest(zone=zone),patch.object(h,'TZ',zone):
                self.assertEqual(h.bounds(1,now),dt.datetime(2026,10,day,tzinfo=zone).timestamp())
        with self.assertRaises(ValueError):h.bounds(2,now)
    def test_import_progress_includes_files_not_read_yet(self):
        p=self.home/'.hermes/logs/llama-server.log';p.parent.mkdir(parents=True);p.write_text('x'*100+'\n')
        q=self.home/'.local/share/tensorfold/logs/server.log';q.parent.mkdir(parents=True);q.write_text(TF.format(id='unread',finish='stop'))
        h.capture(self.root,self.home,[FLASH],{},max_bytes=50);report=h.report(self.root,now=100)
        self.assertGreaterEqual(report['remaining_bytes'],q.stat().st_size)
    def test_pagination_does_not_change_totals_or_repeat_rows(self):
        for i in range(85):self.event(str(i),output_tokens=1,completed_at=1000+i)
        a=h.report(self.root,now=2000);b=h.report(self.root,now=2000,offset=80)
        self.assertEqual(a['summary']['requests'],85);self.assertEqual(b['summary']['requests'],85)
        self.assertEqual(len(a['recent']),80);self.assertEqual(len(b['recent']),5)
        self.assertFalse({r['id'] for r in a['recent']} & {r['id'] for r in b['recent']})

if __name__=='__main__':unittest.main()
