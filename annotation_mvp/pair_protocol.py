"""Independent v0 list experiments; legacy ProtocolStore remains the default."""
import hashlib
import json
import os
import random
import secrets
import shutil
import tempfile
import threading
from pathlib import Path

from annotation_mvp.protocol import ProtocolStore, CMP_FIELDS, atomic_json, write_csv, stamp, schedule
from annotation_mvp.pair_lists import (read_pairs, LIST_FIELDS, EXTRA_FIELDS,
                                      ATTRIBUTION_CHOICES, ATTRIBUTION_QUESTION, schedule_list)

PAIR_FIELDS = CMP_FIELDS + EXTRA_FIELDS
ATTR_FIELDS = PAIR_FIELDS + ['attribution_text', 'note']
DEFAULT_QUESTION = '哪张更通透？'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_events(path, read_only=False):
    """Same crash-tail recovery policy as ProtocolStore, with a strict read-only path."""
    path = Path(path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    events, offset = [], 0
    for line in raw.splitlines(keepends=True):
        try:
            event = json.loads(line)
        except (ValueError, UnicodeDecodeError) as e:
            if read_only or offset + len(line) != len(raw):
                raise ValueError('日志损坏；只读操作不会修复日志') from e
            (path.parent / 'interrupted-tail.bin').write_bytes(line)
            with path.open('r+b') as f:
                f.truncate(offset)
            break
        if not isinstance(event, dict) or 'type' not in event:
            raise ValueError('日志事件格式错误')
        events.append(event)
        offset += len(line)
    if raw and offset == len(raw) and not raw.endswith(b'\n') and not read_only:
        with path.open('ab') as f:
            f.write(b'\n'); f.flush(); os.fsync(f.fileno())
    return events


class PairStore(ProtocolStore):
    def __init__(self, inputs, results, seed=None, pairs=None, question=None,
                 scene_gap=None, attribution=False, source_results=None, read_only=False):
        self.root = Path(results).resolve()
        self.inputs = Path(inputs).resolve() if inputs else None
        self.read_only = read_only
        self.lock = threading.RLock()
        self.sessions = {}
        self.journal = self.root / 'events.jsonl'
        # Validate the existing journal without mutation before deciding experiment type.
        try:
            self.events = load_events(self.journal, read_only=True)
        except ValueError:
            # Only recover our own truncated tail, never touch a legacy journal.
            first_line = self.journal.read_bytes().splitlines()[0]
            try:
                is_list = json.loads(first_line).get('type') == 'experiment_created'
            except (ValueError, AttributeError):
                is_list = False
            if read_only or not is_list:
                raise
            self.events = load_events(self.journal, read_only=False)
        if self.events:
            first = self.events[0]
            if first['type'] != 'experiment_created':
                raise ValueError('结果目录属于旧实验；请指定独立的新 --results 目录')
            self.config = first['config']
            if self.config.get('experiment_mode') not in ('limpidity', 'attribution'):
                raise ValueError('不支持的实验模式')
            for key, value in [('seed', seed), ('scene_gap', scene_gap), ('question_text', question)]:
                if value is not None and value != self.config[key]:
                    raise ValueError(f'已有实验的 {key} 已冻结；请使用新的结果目录')
            if attribution and self.config['experiment_mode'] != 'attribution':
                raise ValueError('不能把已有比较实验改成原因归属实验')
            if pairs is not None and sha(pairs) != self.config.get('pairs_sha256'):
                raise ValueError('图对清单已改变；请使用新的结果目录')
            if source_results is not None:
                source = Path(source_results).resolve()
                if str(source) != self.config.get('source_results') or sha(source/'events.jsonl') != self.config.get('source_events_sha256'):
                    raise ValueError('来源实验或其日志已变化，不能悄悄替换归属题库')
            stored = self.root / 'protocol.json'
            if stored.exists() and json.loads(stored.read_text()) != self.config:
                raise ValueError('配置快照与创建日志不一致')
            if not read_only:
                self.events = load_events(self.journal, read_only=False)
        else:
            if read_only:
                raise ValueError('未找到已创建的图对实验')
            if self.root.exists() and any(p.name != '.process.lock' for p in self.root.iterdir()):
                raise ValueError('结果目录非空；不能覆盖或混入已有实验')
            if not self.inputs:
                raise ValueError('新实验必须指定 --input')
            if self.inputs == self.root or self.inputs in self.root.parents or self.root in self.inputs.parents:
                raise ValueError('--input 与 --results 必须是独立、不互相包含的目录')
            seed = 20260916 if seed is None else seed
            scene_gap = 3 if scene_gap is None else scene_gap
            if attribution:
                if pairs is not None or source_results is None:
                    raise ValueError('原因归属需要 --source-results，不同时使用 --pairs')
                source = read_source(source_results)
                if Path(source_results).resolve() == self.root:
                    raise ValueError('原因归属必须使用独立结果目录')
                rows = source_rows(source, decisive_only=True)
                if not rows:
                    raise ValueError('来源实验没有非平局、非重复图对')
                # Validate selected paths with exactly the public CSV contract.
                with tempfile.TemporaryDirectory() as d:
                    manifest = Path(d)/'pairs.csv'
                    write_csv(manifest, LIST_FIELDS, rows)
                    rows, paths, hashes = read_pairs(manifest, self.inputs)
                for n, digest in hashes.items():
                    if digest != source.config['hashes'].get(n):
                        raise ValueError(f'来源图片与归属实验输入内容不一致：{n}')
                mode = 'attribution'
                if question is not None and question != ATTRIBUTION_QUESTION:
                    raise ValueError('原因归属模式的问题固定，不询问哪张更好')
                question = ATTRIBUTION_QUESTION
                provenance = dict(source_results=str(Path(source_results).resolve()),
                                  source_events_sha256=source.source_events_sha256)
                if sha(Path(source_results)/'events.jsonl') != source.source_events_sha256:
                    raise ValueError('来源实验在读取期间发生变化，请暂停来源实验后重试')
            else:
                if pairs is None or source_results is not None:
                    raise ValueError('清单实验需要 --pairs；--source-results 仅用于原因归属')
                rows, paths, hashes = read_pairs(pairs, self.inputs)
                mode = 'limpidity'
                question = DEFAULT_QUESTION if question is None else question
                provenance = dict(pairs_sha256=sha(pairs))
            if not isinstance(question, str) or not question.strip() or len(question) > 500:
                raise ValueError('问题文本需要1至500字')
            edges = [dict(a=r['image_a'], b=r['image_b'], **{k:r[k] for k in ('scene_id','pair_id','question_id')},
                          question_text=question, experiment_mode=mode) for r in rows]
            tasks = (schedule_list(edges, seed, scene_gap=scene_gap, quality_control=False) if attribution else
                     schedule(edges, seed, small_list=True, scene_gap=scene_gap))
            self.config = dict(version=2, experiment_id=secrets.token_hex(16), seed=seed, scene_gap=scene_gap, experiment_mode=mode,
                               question_text=question, hashes=hashes, pairs=rows, tasks=tasks,
                               created_at=stamp(), input_dir=str(self.inputs),
                               repeat_gap=min(100, len(tasks)//3), features={}, **provenance)
            # No queue or result exists until validation and scheduling have succeeded.
            self.root.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix='.snapshot-', dir=self.root))
            try:
                for n, path in paths.items():
                    dest = staging/n
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, dest)
                    if sha(dest) != hashes[n]:
                        raise ValueError('复制期间图片内容改变，未创建实验')
                staging.rename(self.root/'stimuli')
                self.append(dict(type='experiment_created', config=self.config, timestamp=stamp()))
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                if not self.events:
                    shutil.rmtree(self.root/'stimuli', ignore_errors=True)
                raise
        self.paths = {}
        for n, digest in self.config['hashes'].items():
            snapshot = self.root/'stimuli'/n
            if not snapshot.is_file() or sha(snapshot) != digest:
                raise ValueError(f'图片快照缺失或被修改：{n}')
            if self.inputs is not None:
                original = self.inputs/n
                if not original.is_file() or sha(original) != digest:
                    raise ValueError(f'输入图片缺失或已改变：{n}')
            self.paths[n] = snapshot
        self.tokens = {hashlib.sha256(n.encode()).hexdigest()[:24]: n for n in self.paths}
        self.reverse_tokens = {v:k for k,v in self.tokens.items()}
        self.task_map = {t['task_id']:t for t in self.config['tasks']}
        self.rebuild()
        if not read_only:
            atomic_json(self.root/'protocol.json', self.config)
            write_csv(self.root/'pairs.csv', LIST_FIELDS, self.config['pairs'])

    def rebuild(self):
        self.active, self.notes, self.pending_choices = {}, {}, {}
        for index, source in enumerate(self.events):
            e = dict(source, _index=index)
            tid = e.get('task_id')
            if e['type'] in ('comparison', 'attribution'):
                task = self.task_map.get(tid)
                if task is None:
                    raise ValueError('日志含未知任务，停止重建')
                if any(e.get(k) != task[k] for k in ('img_left','img_right','scene_id','question_id','question_text','experiment_mode')):
                    raise ValueError('答案元数据与冻结题库不一致')
                self.active[tid] = e
                if 'note' in e:
                    self.notes[tid] = e
            elif e['type'] == 'note':
                self.notes[tid] = e
            elif e['type'] == 'undo':
                if tid in self.active and self.active[tid]['_index'] == e['target_index']:
                    self.active.pop(tid)
        self.answers = dict(self.active)
        self.r1, self.scenes, self.genres = {}, {}, {}
        if not self.read_only:
            self.export()

    def export(self):
        write_csv(self.root/'comparisons.csv', PAIR_FIELDS,
                  (r for r in self.answers.values() if r['type']=='comparison'))
        if self.config['experiment_mode'] == 'attribution':
            write_csv(self.root/'attributions.csv', ATTR_FIELDS,
                      (dict(r, note=self.notes.get(tid, {}).get('note', r.get('note', '')))
                       for tid, r in self.answers.items()))
        write_csv(self.root/'notes.csv', ['task_id','session_id','annotator','note','timestamp'], self.notes.values())
        write_csv(self.root/'sessions.csv', ['session_id','annotator','timestamp'],
                  (e for e in self.events if e['type']=='session_start'))

    def plan(self, round_number):
        return dict(tasks=self.config['tasks']) if round_number == 2 else None

    def task_sequence(self):
        return [dict(t, stage='comparison') for t in self.config['tasks']]

    def next_task(self):
        return next((dict(t, stage='comparison') for t in self.config['tasks']
                     if t['task_id'] not in self.answers), dict(stage='finished'))

    def public_state(self, session_id):
        state = super().public_state(session_id)
        state.update(experiment_mode=self.config['experiment_mode'], question_text=self.config['question_text'])
        state['experiment_id'] = self.config['experiment_id']
        task = self.task_map.get(state.get('task_id'))
        if task:
            state['question_id'] = task['question_id']
        return state

    def begin(self, annotator='A'):
        if self.read_only:
            raise ValueError('只读实验不能开始会话')
        if not isinstance(annotator, str) or not 1 <= len(annotator.strip()) <= 60:
            raise ValueError('标注者代号需要1至60字')
        owners = {e.get('annotator','A') for e in self.events if e['type'] in ('comparison','attribution')}
        if owners and annotator.strip() not in owners:
            raise ValueError('已有标注者，其他标注者需要独立实验目录')
        return super().begin(annotator)

    def submit(self, data):
        with self.lock:
            sid, tid = data.get('session_id'), data.get('task_id')
            session = self.sessions.get(sid)
            if not session:
                raise ValueError('请开始或恢复标注')
            if tid in self.active and not data.get('revise'):
                return self.public_state(sid)
            if data.get('revision', len(self.events)) != len(self.events):
                raise ValueError('页面状态已变化，请暂停后恢复')
            task = self.viewed_task(sid)
            if task.get('task_id') != tid or session.get('task') != tid:
                raise ValueError('任务已变化，请恢复标注')
            rt = data.get('rt_ms')
            if type(rt) is not int or not 0 <= rt <= 86400000:
                raise ValueError('反应时间格式错误')
            note = data.get('note', self.notes.get(tid, {}).get('note', ''))
            text = data.get('attribution_text','')
            if any(not isinstance(s, str) or len(s)>5000 for s in (note, text)):
                raise ValueError('文字最多5000字')
            choice = data.get('choice')
            attribution = self.config['experiment_mode']=='attribution'
            if choice not in (ATTRIBUTION_CHOICES if attribution else ('left','right','tie')):
                raise ValueError('选项无效')
            if attribution and choice=='other' and not text.strip():
                raise ValueError('选择“其他”时必须填写文字')
            event = {k:task[k] for k in PAIR_FIELDS if k in task}
            event.update(type='attribution' if attribution else 'comparison', choice=choice,
                         chosen_image='' if attribution or choice=='tie' else task['img_'+choice],
                         rt_ms=rt, timestamp=stamp(), session_id=sid, annotator=session['annotator'], note=note)
            if attribution:
                event['attribution_text'] = text.strip()
            revising = tid in self.active
            if revising:
                event['replaces_event_index'] = self.active[tid]['_index']
                self.invalidate_analysis()
            self.append(event)
            self.rebuild()
            return self.navigate(dict(session_id=sid,direction=1)) if revising else self.public_state(sid)

    def generate_round2(self):
        raise ValueError('图对清单已冻结，不使用 make_graph 或粗分')


def read_source(results):
    """Read only completed list experiments; never reconstruct from a mutable CSV."""
    before = sha(Path(results)/'events.jsonl')
    source = PairStore(None, results, read_only=True)
    if source.config['experiment_mode'] != 'limpidity':
        raise ValueError('来源必须为成对比较实验，不是原因归属实验')
    if source.next_task()['stage'] != 'finished':
        raise ValueError('来源实验尚未完成')
    if sha(Path(results)/'events.jsonl') != before:
        raise ValueError('来源实验在读取期间发生变化，请暂停后重试')
    source.source_events_sha256 = before
    return source


def source_rows(source, decisive_only=False):
    rows = []
    for t in source.config['tasks']:
        # The ORIGINAL member of a swap subset is not a repeated presentation.
        if t['repeat_of_task_id'] or t['is_hidden_repeat']:
            continue
        answer = source.answers[t['task_id']]
        if decisive_only and answer['choice']=='tie':
            continue
        rows.append(dict(image_a=t['img_left'],image_b=t['img_right'],scene_id=t['scene_id'],
                         pair_id=t['pair_id'],question_id=t['question_id']))
    return rows
