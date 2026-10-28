"""Reproducible three-round, single-observer annotation protocol."""
import csv
import hashlib
import io
import json
import math
import os
import random
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import numpy as np
from PIL import Image, ImageCms
from scipy.stats import rankdata

BINS = {'T': '通透', 'M': '中间', 'N': '不通透', 'NA': '不适用', 'U': '拿不准'}
SCENES = ('户外日景', '室内人造光', '夜景', '其他')
GENRES = {'scene': SCENES, 'tone_type': ('低调', '常规', '高调'), 'chroma_strength': ('弱', '中', '强')}
R1_FIELDS = ['image', 'bin', 'rt_ms', 'timestamp', 'session_id', 'free_text']
CMP_FIELDS = ['task_id', 'session_id', 'round', 'img_left', 'img_right', 'choice', 'chosen_image',
              'rt_ms', 'timestamp', 'is_hidden_repeat', 'repeat_of_task_id', 'is_swap_subset', 'pair_key']

def stamp():
    return datetime.now(timezone.utc).isoformat()

def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def write_csv(path, fields, rows):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def pair_key(a, b):
    return json.dumps(sorted([a, b]), ensure_ascii=False, separators=(',', ':'))

def pixel_features(path):
    with Image.open(path) as im:
        im.load()
        if im.format != 'JPEG' or im.mode != 'RGB':
            raise ValueError(f'必须是可解码的三通道 RGB JPEG：{path.name}')
        icc = im.info.get('icc_profile')
        profile = '无ICC；输入协议声明为sRGB'
        if icc:
            profile = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip()
            if 'srgb' not in profile.lower().replace(' ', ''):
                raise ValueError(f'ICC不是sRGB，不能静默转换：{path.name} ({profile})')
        rgb = np.asarray(im, dtype=np.float64) / 255
        width, height = im.size
    linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
    lms = np.einsum('...j,ij->...i', linear, np.array([[.4122214708,.5363325363,.0514459929],
                            [.2119034982,.6806995451,.1073969566],
                            [.0883024619,.2817188376,.6299787005]]))
    lab = np.einsum('...j,ij->...i', np.cbrt(lms), np.array([[.2104542553,.793617785,-.0040720468],
                                  [1.9779984951,-2.428592205,.4505937099],
                                  [.0259040371,.7827717662,-.808675766]]))
    if not np.all(np.isfinite(lab)): raise ValueError('色彩转换产生非有限值')
    ab = lab[...,1:]
    return dict(width=width, height=height, profile=profile,
                lightness=float(np.median(lab[...,0])), chroma=float(np.linalg.norm(ab,axis=-1).mean()),
                cast=float(np.linalg.norm(ab.mean(axis=(0,1)))))

def tertiles(values, labels):
    cuts = np.quantile(values, [1/3, 2/3])
    # Equal values stay together; small cells must be reported, never forced apart by filename.
    return [labels[int(np.searchsorted(cuts, v, side='left'))] for v in values], cuts.tolist()

def quota_deficits(images, bins, genres):
    eligible = [x for x in images if bins[x] != 'NA']
    cells = []
    for field, levels in GENRES.items():
        for level in levels:
            full = sum(genres[x][field] == level for x in images)
            core = sum(genres[x][field] == level for x in eligible)
            if core < 20:
                cells.append(dict(variable=field, level=level, total=full, eligible=core, missing=20-core))
    return cells

def make_graph(images, bins, genres, seed):
    rng = random.Random(seed)
    eligible = [x for x in images if bins[x] != 'NA']
    anchors = []
    for label in ('T','M','N'):
        candidates = [x for x in eligible if bins[x] == label and genres[x]['scene'] in SCENES[:2]
                      and genres[x]['chroma_strength'] != '强']
        if len(candidates) < 2:
            raise ValueError(f'{label} 档合格锚点只有 {len(candidates)} 张，需要2张。停止生成。')
        anchors.extend(rng.sample(candidates, 2))
    graph = nx.Graph(); graph.add_nodes_from(eligible)
    def add(a,b,kind):
        if a != b and not graph.has_edge(a,b): graph.add_edge(a,b,kind=kind)
    for x in eligible:
        choices = [a for a in anchors if a != x]
        sets = [rng.sample(choices,4) for _ in range(100)]
        chosen = next((s for s in sets if len({bins[a] for a in s}) == 3), None)
        if chosen is None: raise ValueError('无法生成跨三个档位的锚点边')
        for a in chosen: add(x,a,'anchor')
    order = {'N':0,'M':1,'T':2}
    nearby = nx.Graph(); nearby.add_nodes_from(eligible)
    # Each item gets >=4 non-anchor local neighbours. U has its own stratum and also spans T/M/N.
    for _ in range(8):
        pending = sorted(eligible, key=lambda x: nearby.degree(x))
        for x in pending:
            candidates = [y for y in eligible if y != x and not graph.has_edge(x,y)
                          and (bins[x]=='U' or bins[y]=='U' or abs(order[bins[x]]-order[bins[y]])<=1)]
            rng.shuffle(candidates)
            candidates.sort(key=lambda y: (nearby.degree(y)>=4, bins[x]!=bins[y]))
            for y in candidates:
                if nearby.degree(x)>=4 and graph.degree(x)>=8: break
                add(x,y,'nearby'); nearby.add_edge(x,y)
    # Explicitly connect adjacent rough strata, including their interfaces.
    for low,high in [('N','M'),('M','T')]:
        cross = [(a,b) for a in eligible for b in eligible if bins[a]==low and bins[b]==high and not graph.has_edge(a,b)]
        rng.shuffle(cross)
        for a,b in cross[:max(8,len(eligible)//10)]: add(a,b,'boundary')
    for field,levels in GENRES.items():
        for i,a in enumerate(levels):
            for b in levels[i+1:]:
                count = sum({genres[x][field],genres[y][field]}=={a,b} for x,y in graph.edges)
                available = [(x,y) for x in eligible for y in eligible
                             if genres[x][field]==a and genres[y][field]==b and not graph.has_edge(x,y)]
                rng.shuffle(available)
                if len(available)<25-count: raise ValueError(f'{field}: {a}/{b} 无法补足25条跨类别边')
                for x,y in available[:max(0,25-count)]: add(x,y,'cross_genre')
    if min(dict(graph.degree()).values(), default=0)<8:
        raise ValueError('近邻不足，无法达到每图8个不同对手')
    if not nx.is_connected(graph) or list(nx.bridges(graph)):
        raise ValueError('生成图未满足连通且无桥，停止出题')
    edges = [dict(a=a,b=b,kind=d['kind'],scope='core') for a,b,d in graph.edges(data=True)]
    na = rng.sample([x for x in images if bins[x]=='NA'], min(10,sum(bins[x]=='NA' for x in images)))
    for x in na:
        for a in rng.sample(anchors,4): edges.append(dict(a=x,b=a,kind='na_probe',scope='na_probe'))
    return edges, anchors, na

def schedule(edges, seed, round_number=2, hidden_count=40, swap_count=None, *, small_list=False, scene_gap=3):
    if small_list:
        from annotation_mvp.pair_lists import schedule_list
        return schedule_list(edges, seed, round_number, scene_gap)
    rng = random.Random(seed)
    tasks = []
    for k,e in enumerate(edges):
        a,b = (e['a'],e['b']) if rng.randrange(2) else (e['b'],e['a'])
        tasks.append(dict(task_id=f'r{round_number}-{k:05d}',round=round_number,img_left=a,img_right=b,
                          pair_key=pair_key(a,b),is_hidden_repeat=0,repeat_of_task_id='',is_swap_subset=0,
                          scope=e.get('scope','core'),kind=e.get('kind','adaptive')))
    core = [t for t in tasks if t['scope']=='core']
    if swap_count is None: swap_count=max(100,math.ceil(.1*len(core)))
    if len(core)<hidden_count+swap_count: raise ValueError('不同比较对不足以抽取互不重叠的质控子集')
    chosen = rng.sample(core, hidden_count+swap_count)
    for i,t in enumerate(chosen):
        duplicate=dict(t)
        duplicate['task_id']=t['task_id']+('-repeat' if i<hidden_count else '-swap')
        duplicate['repeat_of_task_id']=t['task_id']
        if i<hidden_count:
            duplicate['is_hidden_repeat']=1
        else:
            t['is_swap_subset']=duplicate['is_swap_subset']=1
            duplicate['img_left'],duplicate['img_right']=t['img_right'],t['img_left']
        tasks.append(duplicate)
    # Repeated tasks are spread over the body, excluding the final 10%; pause boundaries remain user-controlled.
    repeat_ids=[t['task_id'] for t in tasks if t['is_hidden_repeat']]
    hidden_origins={t['repeat_of_task_id'] for t in tasks if t['is_hidden_repeat']}
    targets={tid:int(v) for tid,v in zip(repeat_ids,np.linspace(110,max(111,int(.86*len(tasks))),len(repeat_ids)))}
    for attempt in range(160):
        remaining=list(tasks); output=[]; last={}; positions={}
        rng.shuffle(remaining)
        while remaining:
            pos=len(output)
            valid=[t for t in remaining if all(pos-last.get(x,-1000)>5 for x in (t['img_left'],t['img_right']))
                   and (not t['repeat_of_task_id'] or
                        (t['repeat_of_task_id'] in positions and pos-positions[t['repeat_of_task_id']]>100))]
            if not valid: break
            counts={}
            for t in remaining:
                for x in (t['img_left'],t['img_right']): counts[x]=counts.get(x,0)+1
            def priority(t):
                due=t['is_hidden_repeat'] and pos>=targets[t['task_id']]
                origin=t['task_id'] in hidden_origins
                return (due,origin and pos<100, sum(counts[x] for x in (t['img_left'],t['img_right']))+rng.random()*8)
            t=max(valid,key=priority)
            remaining.remove(t); positions[t['task_id']]=pos; output.append(t)
            for x in (t['img_left'],t['img_right']): last[x]=pos
        if not remaining and (not repeat_ids or max(positions[x] for x in repeat_ids)<.95*len(output)):
            return output
    raise ValueError('无法满足5题冷却和100题重复间隔；未放宽约束，也未生成部分队列。')

class ProtocolStore:
    def __init__(self, inputs, results, seed=20260916, read_only=False):
        self.read_only=read_only
        self.root=Path(results).resolve()
        if not read_only:self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock(); self.inputs=Path(inputs).resolve(); self.sessions={}
        self.journal=self.root/'events.jsonl'; self.events=[]
        if self.journal.exists():
            raw=self.journal.read_bytes(); lines=raw.splitlines(keepends=True)
            offset=0
            for line in lines:
                try: event=json.loads(line)
                except (ValueError,UnicodeDecodeError):
                    if offset+len(line)!=len(raw): raise ValueError('日志中段损坏，停止加载')
                    if self.read_only:raise ValueError('只读加载发现日志尾部损坏；未修改日志')
                    # Recover only a crash-truncated final record, retaining the damaged bytes.
                    (self.root/'interrupted-tail.bin').write_bytes(line)
                    with self.journal.open('r+b') as f: f.truncate(offset)
                    break
                self.events.append(event); offset+=len(line)
            if raw and offset==len(raw) and not raw.endswith(b'\n') and not self.read_only:
                with self.journal.open('ab') as f:
                    f.write(b'\n');f.flush();os.fsync(f.fileno())
        config=self.root/'protocol.json'
        paths=sorted([p for p in self.inputs.iterdir() if p.suffix.lower() in ('.jpg','.jpeg')])
        if not paths: raise ValueError('输入目录没有JPEG图片')
        if len({p.name.casefold() for p in paths})!=len(paths): raise ValueError('存在重名图片')
        identities={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        if config.exists():
            self.config=json.loads(config.read_text())
            if identities!=self.config['hashes']: raise ValueError('题库内容改变；不得在已有实验中静默增删或替换图片')
            # Decode and validate every image at every startup.
            for p in paths:
                with Image.open(p) as im:
                    im.load()
                    if im.mode!='RGB' or im.format!='JPEG': raise ValueError('题库包含非三通道JPEG')
        else:
            if self.read_only:raise ValueError('只读加载需要已有实验配置')
            features={p.name:pixel_features(p) for p in paths}
            names=list(features)
            tone,cuts_l=tertiles([features[x]['lightness'] for x in names],GENRES['tone_type'])
            composite=(rankdata([features[x]['chroma'] for x in names],method='average')+
                       rankdata([features[x]['cast'] for x in names],method='average'))/(2*len(names))
            chroma,cuts_c=tertiles(composite,GENRES['chroma_strength'])
            for i,x in enumerate(names): features[x].update(tone_type=tone[i],chroma_strength=chroma[i],composite_rank=float(composite[i]))
            rng=random.Random(seed); order=list(names); rng.shuffle(order)
            scene_order=list(names); rng.shuffle(scene_order)
            self.config=dict(version=1,seed=seed,hashes=identities,features=features,round1_order=order,
                             scene_order=scene_order,free_text_images=rng.sample(names,min(30,len(names))),
                             quantile_cuts=dict(oklab_L=cuts_l,chroma_composite_rank=cuts_c),created_at=stamp(),
                             pair_key_encoding='JSON array of two lexicographically sorted filenames',
                             loose_agreement='same chosen image OR at least one response is tie',
                             swap_denominator='unique core round2 base pairs; excludes NA probes and repeated presentations')
            atomic_json(config,self.config)
        self.paths={p.name:p for p in paths}
        self.tokens={hashlib.sha256(n.encode()).hexdigest()[:24]:n for n in self.paths}
        self.reverse_tokens={v:k for k,v in self.tokens.items()}
        self.rebuild()

    def append(self,event):
        if self.read_only:raise ValueError('只读实验不能写入')
        with self.journal.open('a',encoding='utf-8') as f:
            f.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n'); f.flush(); os.fsync(f.fileno())
        self.events.append(event)

    def rebuild(self):
        active={}; self.pending_choices={}; self.notes={}
        for index,source in enumerate(self.events):
            e=dict(source,_index=index)
            tid=e.get('task_id')
            if not tid and e['type'] in ('round1','scene'):
                tid=('r1:' if e['type']=='round1' else 'scene:')+self.reverse_tokens[e['image']]
                e['task_id']=tid
            if e['type'] in ('round1','scene','comparison'):
                active[tid]=e
                if 'note' in e:self.notes[tid]=e
            elif e['type']=='round1_choice': self.pending_choices[tid]=e
            elif e['type']=='note':self.notes[tid]=e
            elif e['type']=='undo':
                if tid in active and active[tid]['_index']==e['target_index']:active.pop(tid)
                self.pending_choices.pop(tid,None)
            elif e['type']=='choice_cancel':self.pending_choices.pop(tid,None)
        self.active=active
        self.r1={e['image']:e for e in active.values() if e['type']=='round1'}
        self.scenes={e['image']:e for e in active.values() if e['type']=='scene'}
        self.answers={e['task_id']:e for e in active.values() if e['type']=='comparison'}
        self.genres={n:dict(image=n,scene=self.scenes.get(n,{}).get('scene',''),
                           tone_type=f['tone_type'],chroma_strength=f['chroma_strength'])
                     for n,f in self.config['features'].items()}
        if self.read_only:return
        self.export()

    def export(self):
        write_csv(self.root/'round1.csv',R1_FIELDS,self.r1.values())
        write_csv(self.root/'genre.csv',['image','scene','tone_type','chroma_strength'],self.genres.values())
        write_csv(self.root/'comparisons.csv',CMP_FIELDS,self.answers.values())
        write_csv(self.root/'notes.csv',['task_id','session_id','annotator','note','timestamp'],self.notes.values())
        write_csv(self.root/'sessions.csv',['session_id','annotator','timestamp'],
                  [dict(e,annotator=e.get('annotator','A')) for e in self.events if e['type']=='session_start'])

    def plan(self,round_number):
        p=self.root/f'round{round_number}_tasks.json'
        return json.loads(p.read_text()) if p.exists() else None

    def next_task(self):
        for name in self.config['round1_order']:
            if name not in self.r1: return dict(stage='round1',task_id='r1:'+self.reverse_tokens[name],image=name)
        for name in self.config['scene_order']:
            if name not in self.scenes: return dict(stage='scene',task_id='scene:'+self.reverse_tokens[name],image=name)
        for r in (2,3):
            plan=self.plan(r)
            if plan is None: return dict(stage='prepare' if r==2 else 'analyze',round=r)
            for t in plan['tasks']:
                if t['task_id'] not in self.answers: return dict(t,stage='comparison')
        return dict(stage='finished')

    def task_sequence(self):
        tasks=[dict(stage='round1',task_id='r1:'+self.reverse_tokens[n],image=n) for n in self.config['round1_order']]
        tasks += [dict(stage='scene',task_id='scene:'+self.reverse_tokens[n],image=n) for n in self.config['scene_order']]
        for r in (2,3):
            plan=self.plan(r)
            if plan:tasks += [dict(t,stage='comparison') for t in plan['tasks']]
        return tasks

    def viewed_task(self,sid):
        tid=self.sessions[sid].get('view_tid')
        return next((t for t in self.task_sequence() if t['task_id']==tid),self.next_task()) if tid else self.next_task()

    def navigate(self,data):
        with self.lock:
            sid=data.get('session_id')
            if sid not in self.sessions:raise ValueError('请先开始或恢复标注')
            direction=data.get('direction')
            if direction not in (-1,1):raise ValueError('翻页方向无效')
            sequence=self.task_sequence(); frontier=self.next_task()
            limit=next((i for i,t in enumerate(sequence) if t['task_id']==frontier.get('task_id')),len(sequence))
            current=self.viewed_task(sid)
            pos=next((i for i,t in enumerate(sequence) if t['task_id']==current.get('task_id')),limit)
            target=max(0,min(limit,pos+direction))
            self.sessions[sid]['view_tid']=sequence[target]['task_id'] if target<len(sequence) and target<limit else None
            self.append(dict(type='navigation',session_id=sid,from_task=current.get('task_id'),
                             to_task=self.viewed_task(sid).get('task_id'),timestamp=stamp()))
            return self.public_state(sid)

    def public_state(self,session_id):
        with self.lock:
            task=self.viewed_task(session_id); stage=task['stage']
            result=dict(stage=stage,image_count=len(self.paths),round1_done=len(self.r1),genre_done=len(self.scenes),
                        comparison_done=len(self.answers),session_id=session_id,
                        annotator=self.sessions[session_id].get('annotator','A'),
                        revision=len(self.events),can_undo=bool(self.active) or task.get('task_id') in self.pending_choices,
                        break_due=time.monotonic()-self.sessions[session_id]['start']>=2400)
            result['note']=self.notes.get(task.get('task_id'),{}).get('note','')
            result['is_review']=task.get('task_id') in self.active
            if stage in ('round1','scene'):
                result.update(task_id=task['task_id'],image_url='/image/'+self.reverse_tokens[task['image']])
                if stage=='round1' and task['task_id'] in self.pending_choices and not result['is_review']: result['needs_text']=True
            elif stage=='comparison':
                result.update(task_id=task['task_id'],round=task['round'],
                              left_url='/image/'+self.reverse_tokens[task['img_left']],right_url='/image/'+self.reverse_tokens[task['img_right']])
            if stage in ('round1','scene','comparison'):
                self.sessions[session_id]['task']=task['task_id']
                self.sessions[session_id]['served']=time.monotonic()
            if stage=='prepare':
                result['deficits']=quota_deficits(list(self.paths),{x:r['bin'] for x,r in self.r1.items()},self.genres)
            if stage in ('comparison','analyze','finished'):
                result['comparison_total']=sum(len(p['tasks']) for p in (self.plan(2),self.plan(3)) if p)
            return result

    def begin(self,annotator='A'):
        with self.lock:
            if not isinstance(annotator,str) or not 1<=len(annotator.strip())<=60:raise ValueError('标注者代号需要1至60字')
            annotator=annotator.strip()
            owners={e['session_id']:e.get('annotator','A') for e in self.events if e['type']=='session_start'}
            previous={e.get('annotator',owners.get(e.get('session_id'),'A')) for e in self.events
                      if e['type'] in ('round1','round1_choice','scene','comparison')}
            if previous and annotator not in previous:raise ValueError('本题库为单人实验，已使用标注者 '+', '.join(sorted(previous))+'；其他标注者需要独立题库。')
            sid=secrets.token_hex(12); self.sessions[sid]=dict(start=time.monotonic(),task=None,annotator=annotator)
            self.append(dict(type='session_start',session_id=sid,annotator=annotator,timestamp=stamp()))
            self.rebuild()
            return self.public_state(sid)

    def save_note(self,data):
        with self.lock:
            sid=data.get('session_id');session=self.sessions.get(sid);tid=data.get('task_id')
            if not session or tid!=self.viewed_task(sid).get('task_id'):raise ValueError('题目已变化，请重新打开后保存备注')
            note=data.get('note','')
            if not isinstance(note,str) or len(note)>5000:raise ValueError('备注最多5000字')
            self.append(dict(type='note',task_id=tid,session_id=sid,annotator=session['annotator'],note=note,timestamp=stamp()))
            self.rebuild();return dict(ok=True,revision=len(self.events))

    def undo(self,data):
        with self.lock:
            sid=data.get('session_id')
            if sid not in self.sessions:raise ValueError('请先开始或恢复标注')
            if data.get('revision',len(self.events))!=len(self.events):raise ValueError('页面状态已变化，请暂停后恢复，避免撤销错误记录')
            task=self.next_task();tid=task.get('task_id')
            if tid in self.pending_choices:
                self.append(dict(type='choice_cancel',task_id=tid,session_id=sid,timestamp=stamp()))
            else:
                if not self.active:raise ValueError('没有可以回退的答案')
                last=max(self.active.values(),key=lambda e:e['_index']);tid=last['task_id']
                affected=[r for r in (2,3) if self.plan(r) and
                          (last['type'] in ('round1','scene') or last.get('round',3)<r)]
                if any(t['task_id'] in self.answers for r in affected for t in self.plan(r)['tasks']):
                    raise ValueError('后续轮次已开始，不能跨越已使用的抽样依据回退；当前轮次仍可逐题回退。')
                for r in affected:
                    self.append(dict(type='plan_invalidated',round=r,plan=self.plan(r),session_id=sid,timestamp=stamp()))
                    (self.root/f'round{r}_tasks.json').unlink()
                self.append(dict(type='undo',task_id=tid,target_index=last['_index'],session_id=sid,
                                 annotator=self.sessions[sid]['annotator'],timestamp=stamp()))
            self.rebuild()
            self.sessions[sid].pop('view_tid',None)
            self.invalidate_analysis()
            return self.public_state(sid)

    def invalidate_analysis(self):
            report=self.root/'report.md'
            if report.exists():
                text=report.read_text(encoding='utf-8')
                if not text.startswith('> 结果已过期'):
                    report.write_text('> 结果已过期：答案发生修改，请重新完成标注并运行分析。\n\n'+text,encoding='utf-8')
            result_file=self.root/'analysis.json'
            if result_file.exists():
                result=json.loads(result_file.read_text());result['invalidated_by_undo']=True
                atomic_json(result_file,result)

    def submit(self,data):
        with self.lock:
            sid=data.get('session_id'); session=self.sessions.get(sid)
            if not session: raise ValueError('请开始或恢复本场标注')
            tid=data.get('task_id'); task=self.viewed_task(sid)
            revising=tid in self.active and data.get('revise') is True
            # Idempotent delivery after a lost response.
            if tid in self.active and not revising:
                return self.public_state(sid)
            if 'revision' in data and data['revision']!=len(self.events):raise ValueError('页面状态已变化，请暂停后恢复')
            if tid!=task.get('task_id') or tid!=session.get('task'): raise ValueError('任务已变化，请恢复标注')
            rt=data.get('rt_ms')
            if type(rt)!=int or not 0<=rt<=86400000: raise ValueError('反应时间格式错误')
            note=data.get('note',self.notes.get(tid,{}).get('note',''))
            if not isinstance(note,str) or len(note)>5000:raise ValueError('备注最多5000字')
            event=dict(task_id=tid,session_id=sid,annotator=session['annotator'],rt_ms=rt,timestamp=stamp(),note=note)
            if task['stage']=='round1':
                pending=None if revising else self.pending_choices.get(tid)
                label=pending['bin'] if pending else data.get('bin')
                if label not in BINS: raise ValueError('粗分类别无效')
                text=data.get('free_text','')
                if not isinstance(text,str) or len(text)>5000: raise ValueError('说明最多5000字')
                if task['image'] in self.config['free_text_images'] and not text.strip() and not pending and not revising:
                    # Freeze the initial choice and RT before eliciting language.
                    if not pending:
                        pending=dict(type='round1_choice',bin=label,rt_ms=rt,task_id=tid,session_id=sid,timestamp=stamp())
                        self.append(pending); self.pending_choices[tid]=pending
                    return dict(needs_text=True,revision=len(self.events))
                if pending:
                    event.update(bin=pending['bin'],rt_ms=pending['rt_ms'],timestamp=pending['timestamp'],session_id=pending['session_id'])
                else: event['bin']=label
                event.update(type='round1',image=task['image'],free_text=text.strip())
            elif task['stage']=='scene':
                if data.get('scene') not in SCENES: raise ValueError('场景类别无效')
                event.update(type='scene',image=task['image'],scene=data['scene'])
            elif task['stage']=='comparison':
                if data.get('choice') not in ('left','right','tie'): raise ValueError('比较选项无效')
                event.update({k:task[k] for k in CMP_FIELDS if k in task})
                event.update(type='comparison',choice=data['choice'],chosen_image='' if data['choice']=='tie' else task['img_'+data['choice']])
            else: raise ValueError('当前不在作答阶段')
            if revising:
                last=self.active[tid]
                affected=[r for r in (2,3) if self.plan(r) and (last['type'] in ('round1','scene') or last.get('round',3)<r)]
                if any(t['task_id'] in self.answers for r in affected for t in self.plan(r)['tasks']):
                    raise ValueError('后续轮次已使用这个答案，当前只能浏览，不能更改抽样依据。')
                for r in affected:
                    self.append(dict(type='plan_invalidated',round=r,plan=self.plan(r),timestamp=stamp()))
                    (self.root/f'round{r}_tasks.json').unlink()
                event['replaces_event_index']=last['_index']
                if event['type']=='round1' and not event['free_text']:event['free_text']=last.get('free_text','')
                self.invalidate_analysis()
            self.append(event); session.pop('pending',None); self.rebuild()
            if revising:return self.navigate(dict(session_id=sid,direction=1))
            return self.public_state(sid)

    def generate_round2(self):
        with self.lock:
            if self.next_task()['stage']!='prepare': raise ValueError('必须先完成粗分和独立题材标注')
            bins={x:r['bin'] for x,r in self.r1.items()}
            deficits=quota_deficits(list(self.paths),bins,self.genres)
            if deficits: raise ValueError('题材格不足20张：'+json.dumps(deficits,ensure_ascii=False)+'。需要独立补采方案；不自动合并或修改已锁定题库。')
            edges,anchors,na=make_graph(list(self.paths),bins,self.genres,self.config['seed']+2)
            tasks=schedule(edges,self.config['seed']+20)
            value=dict(seed=self.config['seed']+20,anchors=anchors,na_probe_images=na,edges=edges,tasks=tasks)
            atomic_json(self.root/'round2_tasks.json',value)
            return dict(ok=True,total=len(tasks))
