import csv
import hashlib
import importlib.util
import itertools
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from annotation_mvp.pair_lists import write_pairs, ATTRIBUTION_QUESTION
from annotation_mvp.pair_protocol import PairStore
from annotation_mvp.pair_analysis import analyze_pairs
from annotation_mvp.protocol import ProtocolStore, schedule
from annotation_mvp.tools.make_retest_list import make_retest

class PairListTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.inputs=self.root/'input';self.inputs.mkdir()
    def tearDown(self):self.temp.cleanup()
    def manifest(self,count=40,scenes=8):
        rows=[]
        for i in range(count):
            a,b=f's{i%scenes}/v{i*2}.jpg',f's{i%scenes}/v{i*2+1}.jpg'
            for n in (a,b):
                p=self.inputs/n;p.parent.mkdir(exist_ok=True);Image.new('RGB',(16,12),(i%255,70,100)).save(p)
            rows.append(dict(image_a=a,image_b=b,scene_id=f's{i%scenes}',pair_id=f'p{i}',question_id='limpidity'))
        file=self.root/'pairs.csv';write_pairs(file,rows);return file,rows
    def complete(self,store,choice='left'):
        s=store.begin()
        while s['stage']=='comparison':
            s=store.submit(dict(session_id=s['session_id'],task_id=s['task_id'],revision=s['revision'],choice=choice,rt_ms=1200,note='测试备注'))
        return s
    def check_schedule(self,store,count,gap):
        tasks=store.config['tasks'];positions={t['task_id']:i for i,t in enumerate(tasks)};last={};expected=__import__('math').ceil(count*.1)
        hidden=[t for t in tasks if t['is_hidden_repeat']];swaps=[t for t in tasks if t['is_swap_subset'] and t['repeat_of_task_id']]
        self.assertEqual(len(hidden),expected);self.assertEqual(len(swaps),expected);self.assertEqual(len(tasks),count+expected*2)
        self.assertFalse({t['repeat_of_task_id'] for t in hidden}&{t['repeat_of_task_id'] for t in swaps})
        for i,t in enumerate(tasks):
            self.assertGreater(i-last.get(t['scene_id'],-10000),gap);last[t['scene_id']]=i
            if t['repeat_of_task_id']:
                self.assertGreater(i-positions[t['repeat_of_task_id']],min(100,len(tasks)//3))
                origin=tasks[positions[t['repeat_of_task_id']]]
                expected_pair=(origin['img_left'],origin['img_right']) if t['is_hidden_repeat'] else (origin['img_right'],origin['img_left'])
                self.assertEqual((t['img_left'],t['img_right']),expected_pair)
        self.assertLess(max(positions[t['task_id']] for t in hidden),.95*len(tasks))
    def test_import_40_and_300_scene_spacing(self):
        for count,scenes in [(40,8),(300,30)]:
            with self.subTest(count=count):
                file,rows=self.manifest(count,scenes);store=PairStore(self.inputs,self.root/f'r{count}',pairs=file,seed=19)
                self.check_schedule(store,count,3);self.assertEqual(store.next_task()['stage'],'comparison')
                self.assertEqual(store.config['tasks'],PairStore(self.inputs,store.root,read_only=True).config['tasks'])
    def test_missing_scene_and_missing_path_are_atomic(self):
        file,rows=self.manifest()
        for bad in [dict(rows[0],scene_id=''),dict(rows[0],image_a='missing.jpg')]:
            write_pairs(file,[bad]);dest=self.root/'invalid'
            with self.assertRaisesRegex(ValueError,'scene_id|路径不存在'):PairStore(self.inputs,dest,pairs=file)
            self.assertFalse(dest.exists())
        file.write_text('image_a,image_b\na.jpg,b.jpg\n')
        with self.assertRaisesRegex(ValueError,'scene_id'):PairStore(self.inputs,self.root/'invalid',pairs=file)
    def test_unsatisfiable_scene_gap_never_relaxed(self):
        file,rows=self.manifest(15,1)
        with self.assertRaisesRegex(ValueError,'无法满足'):PairStore(self.inputs,self.root/'invalid',pairs=file)
        self.assertFalse((self.root/'invalid').exists())
        self.check_schedule(PairStore(self.inputs,self.root/'valid',pairs=file,scene_gap=0),15,0)
    def test_question_ui_record_and_immutable_configuration(self):
        file,rows=self.manifest();store=PairStore(self.inputs,self.root/'results',pairs=file,question='哪张层次更清楚？')
        s=store.begin();self.assertEqual(s['question_text'],'哪张层次更清楚？');self.assertNotIn('scene_id',s);self.assertNotIn('chosen_image',s)
        tid=s['task_id'];store.submit(dict(session_id=s['session_id'],task_id=tid,choice='tie',rt_ms=700))
        for key in ('scene_id','pair_id','question_id','question_text','experiment_mode'):self.assertTrue(store.answers[tid][key])
        exported=list(csv.DictReader((store.root/'comparisons.csv').read_text().splitlines()));self.assertEqual(exported[0]['question_text'],'哪张层次更清楚？');self.assertEqual(exported[0]['chosen_image'],'')
        with self.assertRaisesRegex(ValueError,'冻结'):PairStore(self.inputs,store.root,question='新问题')
        before=store.journal.read_bytes();file.write_text(file.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'清单已改变'):PairStore(self.inputs,store.root,pairs=file)
        self.assertEqual(store.journal.read_bytes(),before)
    def test_snapshot_mutation_and_legacy_directory_guard(self):
        file,rows=self.manifest();store=PairStore(self.inputs,self.root/'results',pairs=file);n=rows[0]['image_a'];(self.inputs/n).write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'输入图片'):PairStore(self.inputs,store.root)
        recovered=PairStore(None,store.root,read_only=True);self.assertEqual(len(recovered.paths),80);recovered.paths[n].write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'快照'):PairStore(None,store.root,read_only=True)
        legacy=self.root/'legacy';legacy.mkdir();(legacy/'events.jsonl').write_text('{"type":"session_start"}\n')
        with self.assertRaisesRegex(ValueError,'旧实验'):PairStore(self.inputs,legacy,pairs=file)
    def test_retest_export_reimports_and_does_not_change_source(self):
        file,rows=self.manifest();source=PairStore(self.inputs,self.root/'source',pairs=file)
        with self.assertRaisesRegex(ValueError,'尚未完成'):make_retest(source.root,.5,7,self.root/'retest.csv')
        self.complete(source,choice='tie');before=source.journal.read_bytes();retest=make_retest(source.root,.5,7,self.root/'retest.csv')
        self.assertEqual(len(retest),20);self.assertEqual(len({r['pair_id'] for r in retest}),20)
        new=PairStore(self.inputs,self.root/'retest',pairs=self.root/'retest.csv',scene_gap=0);self.assertEqual(len(new.config['pairs']),20)
        self.assertEqual(source.journal.read_bytes(),before)
        with self.assertRaisesRegex(ValueError,'不能写入来源'):make_retest(source.root,.5,7,source.root/'oops.csv')
    def test_attribution_other_required_and_export(self):
        file,rows=self.manifest();source=PairStore(self.inputs,self.root/'source',pairs=file);s=source.begin()
        while s['stage']=='comparison':
            task=source.task_map[s['task_id']];choice='tie' if task['pair_id']=='p0' else 'left'
            s=source.submit(dict(session_id=s['session_id'],task_id=s['task_id'],choice=choice,rt_ms=1200))
        before=source.journal.read_bytes();store=PairStore(self.inputs,self.root/'attribution',attribution=True,source_results=source.root,scene_gap=0)
        self.assertEqual(len(store.config['tasks']),39);self.assertTrue(all(not t['repeat_of_task_id'] for t in store.config['tasks']))
        s=store.begin();self.assertEqual(s['question_text'],ATTRIBUTION_QUESTION);payload=dict(session_id=s['session_id'],task_id=s['task_id'],choice='other',rt_ms=1900);prior=store.journal.read_bytes()
        with self.assertRaisesRegex(ValueError,'必须填写'):store.submit(payload)
        self.assertEqual(store.journal.read_bytes(),prior);store.submit(dict(payload,attribution_text='材质差异',note='备注'))
        exported=list(csv.DictReader((store.root/'attributions.csv').read_text().splitlines()));self.assertEqual(exported[0]['attribution_text'],'材质差异');self.assertEqual(exported[0]['experiment_mode'],'attribution');self.assertEqual(exported[0]['chosen_image'],'')
        self.assertEqual(len(list(csv.DictReader((store.root/'comparisons.csv').read_text().splitlines()))),0)
        resumed=PairStore(self.inputs,store.root);self.assertEqual(len(resumed.answers),1);s=resumed.begin();resumed.undo(dict(session_id=s['session_id'],revision=s['revision']));self.assertEqual(len(resumed.answers),0);self.assertEqual(source.journal.read_bytes(),before)
    def test_scene_analysis_has_scores_se_and_no_global_scale(self):
        rows=[]
        for scene in ('a','b','c','d'):
            for i in range(6):Image.new('RGB',(12,12),(30*i,50,90)).save(self.inputs/f'{scene}{i}.jpg')
            for a,b in itertools.combinations(range(6),2):rows.append(dict(image_a=f'{scene}{a}.jpg',image_b=f'{scene}{b}.jpg',scene_id=scene,pair_id=f'{scene}-{a}-{b}',question_id='limpidity'))
        file=self.root/'pairs.csv';write_pairs(file,rows);store=PairStore(self.inputs,self.root/'analysis',pairs=file,scene_gap=0);s=store.begin()
        while s['stage']=='comparison':
            t=store.task_map[s['task_id']];a=int(t['img_left'][-5]);b=int(t['img_right'][-5]);delta=(b-a)%6;choice='tie' if delta==3 else 'left' if delta in (1,2) else 'right'
            s=store.submit(dict(session_id=s['session_id'],task_id=s['task_id'],choice=choice,rt_ms=800))
        from annotation_mvp.scaling import fit
        with patch('annotation_mvp.pair_analysis.fit',wraps=fit) as fitting:
            _,report=analyze_pairs(store);self.assertTrue(all(len(call.args[0])==6 for call in fitting.call_args_list))
        self.assertFalse(report['global_scale_fitted']);self.assertEqual(len(report['scenes']),4)
        exported=list(csv.DictReader((store.root/'scene_scores.csv').read_text().splitlines()));self.assertEqual(len(exported),48);self.assertTrue(all(r['score']!='' and float(r['se'])>0 for r in exported))
        for r in report['scenes']:
            if r['self_consistency']['strict']['n']:self.assertEqual(r['self_consistency']['strict']['rate'],1)
            if r['swap_consistency']['n']:self.assertEqual(r['swap_consistency']['rate'],1)
    def test_http_question_and_attribution_rejection(self):
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        from annotation_mvp.measurement_app import handler
        file,rows=self.manifest();source=PairStore(self.inputs,self.root/'source',pairs=file,question='测试问题？')
        def serve(store,action):
            server=ThreadingHTTPServer(('127.0.0.1',0),handler(store));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base+'/api/status') as r:token=json.load(r)['token']
                def post(path,data):
                    req=Request(base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Local-Token':token})
                    with urlopen(req) as r:return json.load(r)
                action(post,base)
            finally:server.shutdown();server.server_close();thread.join()
        serve(source,lambda post,base:self.assertEqual(post('/api/start',{'annotator':'A'})['question_text'],'测试问题？'))
        self.complete(source)
        attr=PairStore(self.inputs,self.root/'attr',attribution=True,source_results=source.root,scene_gap=0)
        def reject(post,base):
            state=post('/api/start',{'annotator':'A'});before=attr.journal.read_bytes()
            with self.assertRaises(HTTPError) as error:post('/api/answer',dict(session_id=state['session_id'],task_id=state['task_id'],choice='other',rt_ms=1000))
            self.assertEqual(error.exception.code,400);self.assertIn('必须填写',json.loads(error.exception.read())['error'])
            self.assertEqual(before,attr.journal.read_bytes())
            post('/api/answer',dict(session_id=state['session_id'],task_id=state['task_id'],choice='other',attribution_text='材质',rt_ms=1000))
            with urlopen(base+'/api/results') as response:
                self.assertIn('attributions.csv',json.load(response)['files'])
            with urlopen(base+'/download/attributions.csv') as response:
                rows=list(csv.DictReader(response.read().decode().splitlines()))
            self.assertEqual(rows[0]['attribution_text'],'材质')
        serve(attr,reject)

    def test_attribution_notes_follow_append_log_and_invalid_identity_is_rejected(self):
        file,rows=self.manifest(15,1)
        source=PairStore(self.inputs,self.root/'source',pairs=file,scene_gap=0);self.complete(source)
        store=PairStore(self.inputs,self.root/'attr',attribution=True,source_results=source.root,scene_gap=0)
        state=store.begin();tid=state['task_id'];sid=state['session_id']
        store.save_note(dict(session_id=sid,task_id=tid,note='先保存的备注'))
        store.submit(dict(session_id=sid,task_id=tid,choice='tone',rt_ms=1000))
        self.assertEqual(store.answers[tid]['note'],'先保存的备注')
        store.navigate(dict(session_id=sid,direction=-1))
        store.save_note(dict(session_id=sid,task_id=tid,note='更新的备注'))
        for loaded in (store,PairStore(self.inputs,store.root)):
            rows=list(csv.DictReader((loaded.root/'attributions.csv').read_text().splitlines()))
            self.assertEqual(rows[0]['note'],'更新的备注')
        before=store.journal.read_bytes()
        with self.assertRaisesRegex(ValueError,'标注者代号'):store.begin(None)
        self.assertEqual(store.journal.read_bytes(),before)

    def test_optional_columns_and_resume_from_journal(self):
        file,rows=self.manifest(15,1)
        import io
        stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=['image_a','image_b','scene_id']);writer.writeheader()
        for r in rows:writer.writerow({k:r[k] for k in writer.fieldnames})
        file.write_text(stream.getvalue())
        store=PairStore(self.inputs,self.root/'results',pairs=file,scene_gap=0)
        self.assertEqual(store.config['question_text'],'哪张更通透？')
        self.assertTrue(all(t['question_id']=='limpidity' for t in store.config['tasks']))
        state=store.begin();store.submit(dict(session_id=state['session_id'],task_id=state['task_id'],choice='left',rt_ms=1000))
        expected=store.next_task();(store.root/'protocol.json').unlink();(store.root/'comparisons.csv').write_text('deliberately not authoritative')
        recovered=PairStore(self.inputs,store.root)
        self.assertEqual(recovered.next_task(),expected);self.assertEqual(len(recovered.answers),1)
        with store.journal.open('ab') as f:f.write(b'{"incomplete"')
        before=store.journal.read_bytes()
        with self.assertRaisesRegex(ValueError,'日志损坏'):PairStore(self.inputs,store.root,read_only=True)
        self.assertEqual(store.journal.read_bytes(),before)
        recovered=PairStore(self.inputs,store.root)
        self.assertEqual(recovered.next_task(),expected);self.assertEqual(len(recovered.answers),1)

    def test_scene_analysis_reports_unidentifiable_without_fake_se(self):
        file,rows=self.manifest(15,1)
        store=PairStore(self.inputs,self.root/'results',pairs=file,scene_gap=0);self.complete(store,'left')
        _,report=analyze_pairs(store)
        self.assertFalse(report['scenes'][0]['davidson_ok'])
        rows=list(csv.DictReader((store.root/'scene_scores.csv').read_text().splitlines()))
        self.assertTrue(all(r['score']=='' and r['se']=='' for r in rows))

    def test_legacy_read_only_and_real_baseline(self):
        baseline_path=os.environ.get('ANNOTATION_LEGACY_BASELINE')
        if not baseline_path:self.skipTest('设置 ANNOTATION_LEGACY_BASELINE 以核对真实正式实验备份')
        baseline_path=Path(baseline_path);baseline=json.loads(baseline_path.read_text());backup=baseline_path.parent;inputs=backup/'input';results=backup/'results'
        legacy_code=backup/baseline.get('legacy_code_dir','code')
        before={p.relative_to(results).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in results.rglob('*') if p.is_file()};store=ProtocolStore(inputs,results,read_only=True)
        digest=lambda x:hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(store.next_task(),baseline['next_task']);self.assertEqual(digest(dict(r1=store.r1,scenes=store.scenes,answers=store.answers,notes=store.notes)),baseline['state_sha256'])
        if 'comparison_done' in baseline:self.assertEqual(len(store.answers),baseline['comparison_done'])
        self.assertEqual(digest(schedule(store.plan(2)['edges'],store.plan(2)['seed'])),baseline['schedule_sha256']);self.assertEqual(hashlib.sha256((results/'events.jsonl').read_bytes()).hexdigest(),baseline['events_sha256'])
        self.assertEqual(before,{p.relative_to(results).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in results.rglob('*') if p.is_file()})
        copied=self.root/'old_results';shutil.copytree(results,copied);spec=importlib.util.spec_from_file_location('old_protocol',legacy_code/'protocol.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old);previous=old.ProtocolStore(inputs,copied)
        self.assertEqual(previous.next_task(),store.next_task());self.assertEqual(previous.answers,store.answers);self.assertEqual(previous.r1,store.r1);self.assertEqual(previous.scenes,store.scenes)
        self.assertEqual(old.schedule(previous.plan(2)['edges'],previous.plan(2)['seed']),schedule(store.plan(2)['edges'],store.plan(2)['seed']))
        self.assertEqual(schedule(store.plan(2)['edges'],store.plan(2)['seed']),store.plan(2)['tasks'])
        from annotation_mvp.scaling import audit,reliability
        self.assertEqual(audit(previous),audit(store))
        self.assertEqual(reliability(previous.plan(2)['tasks'],previous.answers),reliability(store.plan(2)['tasks'],store.answers))
        self.assertEqual(hashlib.sha256((legacy_code/'scaling.py').read_bytes()).hexdigest(),hashlib.sha256((Path(__file__).parent/'scaling.py').read_bytes()).hexdigest())
        new_copy=self.root/'new_results';shutil.copytree(results,new_copy);ProtocolStore(inputs,new_copy)
        for name in ('round1.csv','genre.csv','comparisons.csv','notes.csv','sessions.csv'):
            self.assertEqual((copied/name).read_bytes(),(new_copy/name).read_bytes())

if __name__=='__main__':unittest.main()
