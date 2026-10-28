import csv
import json
import tempfile
import unittest
from pathlib import Path
import sys

# Support the documented unittest command from annotation_mvp itself.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import networkx as nx
import numpy as np
from PIL import Image

from annotation_mvp.protocol import ProtocolStore, GENRES, make_graph, schedule, pair_key
from annotation_mvp.scaling import fit, reliability, wilson, analyze
from types import SimpleNamespace

class MeasurementTests(unittest.TestCase):
    def test_reason_can_be_left_empty_after_restart(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            root=Path(d);inputs=root/'input';inputs.mkdir()
            Image.new('RGB',(10,10),'white').save(inputs/'a.jpg')
            store=ProtocolStore(inputs,root/'results');s=store.begin()
            store.submit(dict(session_id=s['session_id'],task_id=s['task_id'],bin='N',rt_ms=900))
            store=ProtocolStore(inputs,root/'results');s=store.begin()
            result=store.submit(dict(session_id=s['session_id'],task_id=s['task_id'],bin=None,rt_ms=5000,free_text=''))
            self.assertEqual(result['stage'],'scene')
            self.assertEqual(store.r1['a.jpg']['bin'],'N')
            self.assertEqual(store.r1['a.jpg']['free_text'],'')
            self.assertEqual(store.r1['a.jpg']['rt_ms'],900)

    def test_undo_reanswer_notes_and_identity_survive_restart(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            root=Path(d);inputs=root/'input';inputs.mkdir()
            for i in range(2):Image.new('RGB',(10,10),(i*100,50,60)).save(inputs/f'{i}.jpg')
            store=ProtocolStore(inputs,root/'results');state=store.begin('研究者')
            tid=state['task_id'];name=store.next_task()['image']
            state=store.submit(dict(session_id=state['session_id'],task_id=tid,bin='T',rt_ms=1000,free_text='自由描述',note='需要记录的想法'))
            state=store.undo(dict(session_id=state['session_id'],revision=state['revision']))
            self.assertEqual(state['task_id'],tid);self.assertEqual(state['note'],'需要记录的想法')
            self.assertNotIn('bin',state);self.assertEqual(len(store.r1),0)
            result=store.submit(dict(session_id=state['session_id'],task_id=tid,revision=state['revision'],bin='N',rt_ms=1500,free_text='新的描述',note='修订备注'))
            self.assertEqual(store.r1[name]['bin'],'N')
            recovered=ProtocolStore(inputs,root/'results')
            self.assertEqual(recovered.r1[name]['bin'],'N');self.assertEqual(recovered.notes[tid]['note'],'修订备注')
            self.assertEqual(sum(e['type']=='round1' for e in recovered.events),2)
            self.assertEqual(sum(e['type']=='undo' for e in recovered.events),1)
            with self.assertRaises(ValueError):recovered.begin('其他人')
            self.assertEqual(recovered.begin('研究者')['annotator'],'研究者')

    def test_reselect_pending_choice_and_stale_undo(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            root=Path(d);inputs=root/'input';inputs.mkdir();Image.new('RGB',(10,10),'white').save(inputs/'a.jpg')
            store=ProtocolStore(inputs,root/'results');state=store.begin()
            result=store.submit(dict(session_id=state['session_id'],task_id=state['task_id'],bin='T',rt_ms=1000))
            with self.assertRaises(ValueError):store.undo(dict(session_id=state['session_id'],revision=state['revision']))
            state=store.undo(dict(session_id=state['session_id'],revision=result['revision']))
            self.assertFalse(state.get('needs_text',False))
            store.submit(dict(session_id=state['session_id'],task_id=state['task_id'],bin='U',rt_ms=2000,free_text='说明'))
            self.assertEqual(store.r1['a.jpg']['bin'],'U')

    def test_graph_and_schedule(self):
        names=[f'{i:03}.jpg' for i in range(200)]
        bins={n:('N','M','T','U')[i%4] for i,n in enumerate(names)}
        genres={n:dict(scene=GENRES['scene'][(i//4)%4],tone_type=GENRES['tone_type'][i%3],
                       chroma_strength=GENRES['chroma_strength'][(i//3)%3]) for i,n in enumerate(names)}
        edges,anchors,na=make_graph(names,bins,genres,123)
        g=nx.Graph();g.add_edges_from((e['a'],e['b']) for e in edges)
        self.assertTrue(nx.is_connected(g));self.assertFalse(list(nx.bridges(g)))
        self.assertGreaterEqual(min(dict(g.degree()).values()),8)
        self.assertEqual(len(edges),len({pair_key(e['a'],e['b']) for e in edges}))
        tasks=schedule(edges,234);positions={t['task_id']:i for i,t in enumerate(tasks)};last={}
        hidden={t['pair_key'] for t in tasks if t['is_hidden_repeat']}
        swap={t['pair_key'] for t in tasks if t['is_swap_subset']}
        self.assertEqual(len(hidden),40);self.assertFalse(hidden&swap);self.assertGreaterEqual(len(swap),100)
        for i,t in enumerate(tasks):
            for x in (t['img_left'],t['img_right']):
                self.assertGreater(i-last.get(x,-1000),5);last[x]=i
            if t['is_hidden_repeat']: self.assertGreaterEqual(i-positions[t['repeat_of_task_id']]-1,100)
        self.assertLess(max(positions[t['task_id']] for t in tasks if t['is_hidden_repeat']),.95*len(tasks))

    def test_journal_recovery_and_privacy(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); inputs=root/'input';inputs.mkdir()
            for i in range(31): Image.new('RGB',(12,12),(i*7,50,70)).save(inputs/f'{i}.jpg')
            store=ProtocolStore(inputs,root/'results');state=store.begin()
            self.assertNotIn('image',state);self.assertNotIn('bin',state)
            task=store.next_task();data=dict(session_id=state['session_id'],task_id=state['task_id'],bin='U',rt_ms=777)
            response=store.submit(data)
            if response.get('needs_text'):
                data.update(free_text='测试自由描述',rt_ms=9999,bin='N');store.submit(data)
            self.assertEqual(store.r1[task['image']]['bin'],'U');self.assertEqual(store.r1[task['image']]['rt_ms'],777)
            store.submit(data);self.assertEqual(len(store.r1),1)
            with store.journal.open('ab') as f:f.write(b'{"broken"')
            recovered=ProtocolStore(inputs,root/'results');self.assertEqual(len(recovered.r1),1)
            self.assertTrue((root/'results/interrupted-tail.bin').exists())

    def test_davidson_recovers_parameters_and_covariance(self):
        rng=np.random.default_rng(1);names=['a','b','c','d'];scores=np.array([-.8,-.2,.3,.7]);rows=[]
        for i in range(4):
            for j in range(i+1,4):
                v=np.array([np.exp(scores[i]),np.exp(scores[j]),.8*np.exp((scores[i]+scores[j])/2)])
                outcomes=rng.choice(3,size=1500,p=v/v.sum())
                for y in outcomes:
                    rows.append(dict(img_left=names[i],img_right=names[j],choice=['left','right','tie'][y],
                                     chosen_image=[names[i],names[j],''][y]))
        model=fit(names,rows)
        self.assertTrue(model['ok'],model);self.assertAlmostEqual(sum(model['scores']),0,places=9)
        self.assertLess(np.max(abs(model['scores']-scores)),.12);self.assertLess(abs(model['nu']-.8),.12)
        self.assertTrue(np.all(model['se']>0));self.assertLess(np.max(abs(model['covariance'].sum(axis=0))),1e-10)
        self.assertTrue(fit(names,rows,True)['ok'])

    def test_separation_and_all_ties_not_fake_scores(self):
        rows=[dict(img_left='a',img_right='b',choice='left',chosen_image='a')]*10
        self.assertFalse(fit(['a','b'],rows)['ok'])
        rows=[dict(img_left='a',img_right='b',choice='tie',chosen_image='')]*10
        self.assertFalse(fit(['a','b'],rows)['ok'])

    def test_keyboard_navigation_and_revision(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            root=Path(d);inputs=root/'input';inputs.mkdir()
            for name in ('a.jpg','b.jpg','c.jpg'):Image.new('RGB',(10,10),'white').save(inputs/name)
            store=ProtocolStore(inputs,root/'results');state=store.begin();sid=state['session_id'];first=state['task_id']
            payload=dict(task_id=first,session_id=sid,bin='T',rt_ms=1000)
            self.assertTrue(store.submit(payload)['needs_text'])
            second=store.submit(dict(payload,free_text=''))
            self.assertEqual(len(store.r1),1)
            previous=store.navigate(dict(session_id=sid,direction=-1))
            self.assertEqual(previous['task_id'],first);self.assertTrue(previous['is_review']);self.assertNotIn('bin',previous)
            self.assertEqual(len(store.r1),1)
            forward=store.navigate(dict(session_id=sid,direction=1))
            self.assertEqual(forward['task_id'],second['task_id'])
            store.navigate(dict(session_id=sid,direction=-1))
            revised=store.submit(dict(payload,bin='M',revise=True))
            self.assertEqual(revised['task_id'],second['task_id'])
            self.assertEqual(len(store.r1),1);self.assertEqual(next(iter(store.r1.values()))['bin'],'M')
            recovered=ProtocolStore(inputs,root/'results')
            self.assertEqual(next(iter(recovered.r1.values()))['bin'],'M')

    def test_pending_free_text_survives_restart(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            root=Path(d);inputs=root/'input';inputs.mkdir()
            Image.new('RGB',(10,10),'white').save(inputs/'a.jpg')
            store=ProtocolStore(inputs,root/'results');state=store.begin()
            data=dict(task_id=state['task_id'],session_id=state['session_id'],bin='NA',rt_ms=1234)
            self.assertTrue(store.submit(data)['needs_text'])
            recovered=ProtocolStore(inputs,root/'results');state=recovered.begin()
            self.assertTrue(state['needs_text']);self.assertNotIn('bin',state)
            recovered.submit(dict(task_id=state['task_id'],session_id=state['session_id'],bin=None,rt_ms=9999,free_text='自由说明'))
            self.assertEqual(recovered.r1['a.jpg']['bin'],'NA');self.assertEqual(recovered.r1['a.jpg']['rt_ms'],1234)

    def test_invalid_input_and_mutation_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            root=Path(d);inputs=root/'input';inputs.mkdir()
            Image.new('L',(10,10),100).save(inputs/'a.jpg')
            with self.assertRaises(ValueError):ProtocolStore(inputs,root/'results')
            Image.new('RGB',(10,10),'white').save(inputs/'a.jpg')
            ProtocolStore(inputs,root/'results')
            Image.new('RGB',(10,10),'black').save(inputs/'a.jpg')
            with self.assertRaises(ValueError):ProtocolStore(inputs,root/'results')

    def test_anchor_shortage_is_not_silently_relaxed(self):
        names=[str(i) for i in range(20)];bins={n:'M' for n in names}
        genres={n:dict(scene='户外日景',tone_type='常规',chroma_strength='中') for n in names}
        with self.assertRaisesRegex(ValueError,'锚点'):
            make_graph(names,bins,genres,1)

    def test_agreement_is_image_based(self):
        tasks=[dict(task_id='repeat',repeat_of_task_id='first',is_hidden_repeat=1)]
        answers={'first':dict(choice='left',chosen_image='a'),'repeat':dict(choice='right',chosen_image='a')}
        self.assertEqual(reliability(tasks,answers)['strict']['rate'],1)
        self.assertEqual(wilson(0,0)['ci'],None)

    def test_full_report_serializes_and_exports(self):
        names=[f'{i:03}.jpg' for i in range(120)]
        bins={n:('N','M','T','U')[i%4] for i,n in enumerate(names)}
        genres={n:dict(scene=GENRES['scene'][(i//4)%4],tone_type=GENRES['tone_type'][i%3],
                       chroma_strength=GENRES['chroma_strength'][(i//3)%3]) for i,n in enumerate(names)}
        edges,anchors,na=make_graph(names,bins,genres,123); tasks=schedule(edges,234);answers={}
        for i,t in enumerate(tasks):
            for j,choice in enumerate(['left','right','tie']):
                tid=t['task_id'] if j==0 else t['task_id']+'-sim-'+str(j)
                r=dict(t);r.update(task_id=tid,choice=choice,chosen_image='' if choice=='tie' else t['img_'+choice],
                                  session_id=str(i//300),rt_ms=1200,timestamp='test');answers[tid]=r
        plan=dict(tasks=tasks,anchors=anchors,na_probe_images=na)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            store=SimpleNamespace(root=root,paths=dict.fromkeys(names),r1={n:{'bin':bins[n]} for n in names},
                genres=genres,answers=answers,config={'seed':1,'quantile_cuts':{}},plan=lambda r:plan if r==2 else None)
            model,report=analyze(store,simulations=2)
            self.assertTrue(model['ok']);self.assertTrue(report['audit']['passed'])
            for filename in ('analysis.json','bt_scores.csv','ties.csv','pairs_human.csv','report.md','diagnostics.png'):
                self.assertTrue((root/filename).is_file(),filename)



if __name__=='__main__':unittest.main()
