"""Scene / stimulus manifest import. The whole batch is validated before anything is written."""
import csv
import json
import os
from concurrent.futures import ProcessPoolExecutor
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select

from easylabel.core import db
from easylabel.core.imaging import load_master, render_derivatives, sha256_bytes, sha256_file

SOURCE_TYPES = {  # source_type -> is_real
    'expert': True, 'camera_default': True, 'retouch_clear': True, 'retouch_flat': True,
    'synth_ladder': False, 'local_edit': False,
}
SPLITS = ('train', 'val', 'test', 'calib')
TONE_KEYS = ('high', 'mid', 'low')
DYNAMIC_RANGES = ('small', 'mid', 'large')
CHROMA_LEVELS = ('low', 'mid', 'high')
SUBJECT_TAGS = ('outdoor_day', 'indoor_artificial', 'night', 'portrait', 'other')
E2_CLASSES = ('film_flat', 'oversaturated', 'real_fog', 'soft_highkey')
DIRECTIONS = ('down', 'up')
REGIONS = ('subject', 'background')
FORMAL_VERSIONS, FORMAL_MIN_REAL = 12, 4
IDENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')

SCENE_FIELDS = ['scene_id', 'split', 'tone_key', 'dynamic_range', 'subject_tags', 'chroma_level', 'e2_class']
STIMULUS_FIELDS = ['scene_id', 'version_label', 'path', 'source_type', 'operator', 'direction', 'strength',
                   'region', 'parent_version', 'origin_dataset', 'origin_id', 'license_id', 'regen_recipe']


class ManifestError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        shown = '\n'.join(errors[:50]) + (f'\n……另有 {len(errors) - 50} 条' if len(errors) > 50 else '')
        super().__init__(f'清单校验失败（{len(errors)} 条）：\n{shown}')


def read_csv(path, required):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise ManifestError([f'{Path(path).name} 缺少列：{", ".join(missing)}'])
        return [(i, {k: (v or '').strip() for k, v in row.items() if k}) for i, row in enumerate(reader, 2)]


def _choice(errors, where, field, value, allowed, optional=False):
    if optional and not value:
        return
    if value not in allowed:
        errors.append(f'{where} {field}={value!r} 不在 {"/".join(allowed)} 中')


def parse_scenes(path):
    errors, scenes = [], {}
    required = ['scene_id', 'split', 'tone_key', 'dynamic_range', 'subject_tags', 'chroma_level']
    for line, row in read_csv(path, required):
        where = f'scenes.csv 第{line}行'
        sid = row['scene_id']
        if not IDENT.match(sid):
            errors.append(f'{where} scene_id 只能含字母、数字、_ . -'); continue
        if sid in scenes:
            errors.append(f'{where} scene_id 重复：{sid}'); continue
        _choice(errors, where, 'split', row['split'], SPLITS)
        _choice(errors, where, 'tone_key', row['tone_key'], TONE_KEYS)
        _choice(errors, where, 'dynamic_range', row['dynamic_range'], DYNAMIC_RANGES)
        _choice(errors, where, 'chroma_level', row['chroma_level'], CHROMA_LEVELS)
        _choice(errors, where, 'e2_class', row.get('e2_class', ''), E2_CLASSES, optional=True)
        tags = sorted({t for t in row['subject_tags'].split(';') if t})
        if not tags:
            errors.append(f'{where} subject_tags 不能为空')
        for t in tags:
            _choice(errors, where, 'subject_tags', t, SUBJECT_TAGS)
        scenes[sid] = dict(scene_id=sid, split=row['split'], tone_key=row['tone_key'],
                           dynamic_range=row['dynamic_range'], subject_tags=tags,
                           chroma_level=row['chroma_level'], e2_class=row.get('e2_class') or None)
    return scenes, errors


def parse_stimuli(path, root, scene_ids):
    errors, rows, seen = [], [], set()
    root = Path(root).resolve()
    for line, row in read_csv(path, ['scene_id', 'version_label', 'path', 'source_type',
                                     'origin_dataset', 'origin_id', 'license_id']):
        where = f'stimuli.csv 第{line}行'
        sid, label = row['scene_id'], row['version_label']
        if sid not in scene_ids:
            errors.append(f'{where} scene_id 未在 scenes 中定义：{sid}'); continue
        if not IDENT.match(label):
            errors.append(f'{where} version_label 只能含字母、数字、_ . -'); continue
        if (sid, label) in seen:
            errors.append(f'{where} 同一场景内 version_label 重复：{sid}/{label}'); continue
        seen.add((sid, label))
        rel = Path(row['path'])
        if not row['path'] or rel.is_absolute() or '..' in rel.parts:
            errors.append(f'{where} path 必须为 --root 内的相对路径'); continue
        source = (root / rel).resolve()
        if not source.is_file():
            errors.append(f'{where} 文件不存在：{row["path"]}'); continue
        stype = row['source_type']
        if stype not in SOURCE_TYPES:
            errors.append(f'{where} source_type={stype!r} 不在 {"/".join(SOURCE_TYPES)} 中'); continue
        for field in ('origin_dataset', 'origin_id', 'license_id'):
            if not row[field]:
                errors.append(f'{where} {field} 不能为空')
        strength = None
        if row.get('strength'):
            try:
                strength = float(row['strength'])
            except ValueError:
                errors.append(f'{where} strength 必须为数值')
        if not SOURCE_TYPES[stype]:
            for field in ('operator', 'direction', 'strength', 'parent_version'):
                if not row.get(field):
                    errors.append(f'{where} 合成版本必须填写 {field}')
            if row.get('operator') and not IDENT.match(row['operator']):
                errors.append(f'{where} operator 只能含字母、数字、_ . -')
            _choice(errors, where, 'direction', row.get('direction', ''), DIRECTIONS, optional=True)
        if stype == 'local_edit':
            _choice(errors, where, 'region', row.get('region', ''), REGIONS)
        elif row.get('region'):
            errors.append(f'{where} 只有 local_edit 可以填写 region')
        rows.append(dict(scene_id=sid, version_label=label, source=source, source_type=stype,
                         is_real=SOURCE_TYPES[stype], operator=row.get('operator') or None,
                         direction=row.get('direction') or None, strength=strength,
                         region=row.get('region') or None, parent_version=row.get('parent_version') or None,
                         origin_dataset=row['origin_dataset'], origin_id=row['origin_id'],
                         license_id=row['license_id'], regen_recipe=row.get('regen_recipe') or None))
    return rows, errors


def _parent_order(rows, existing, errors):
    """Validate parent references (same scene, no cycles) and return rows parents-first."""
    by_key = {(r['scene_id'], r['version_label']): r for r in rows}
    order, state = [], {}

    def visit(key, trail):
        if state.get(key) == 'done':
            return
        if state.get(key) == 'active':
            errors.append(f'母版本存在循环：{" → ".join("/".join(k) for k in trail + [key])}')
            return
        state[key] = 'active'
        row = by_key[key]
        parent = row['parent_version']
        if parent:
            pkey = (row['scene_id'], parent)
            if pkey == key:
                errors.append(f'{"/".join(key)} 不能以自己为母版本')
            elif pkey in by_key:
                visit(pkey, trail + [key])
            elif pkey not in existing:
                errors.append(f'{"/".join(key)} 的母版本不存在：{parent}')
        state[key] = 'done'
        order.append(row)

    for key in by_key:
        visit(key, [])
    return order


def _render_one(source, css_long_edges):
    """Worker: (profile, width, height, [(css, scale, bytes, w, h)]) or an error string."""
    try:
        im, profile = load_master(source)
        return profile, im.width, im.height, list(render_derivatives(im, css_long_edges))
    except (OSError, ValueError) as e:
        return str(e)


def composition(versions):
    """versions: iterable of (scene_id, is_real) → {scene: (total, real)}."""
    total, real = Counter(), Counter()
    for sid, is_real in versions:
        total[sid] += 1
        real[sid] += bool(is_real)
    return {sid: (total[sid], real[sid]) for sid in total}


def import_manifest(engine, data_dir, scenes_csv, stimuli_csv, root, css_long_edges, strict_composition=False,
                    workers=None):
    """Validate everything, render derivatives into staging, then commit in one transaction."""
    css_long_edges = sorted(set(int(x) for x in css_long_edges))
    if not css_long_edges:
        raise ValueError('至少指定一个 CSS 长边')
    media = Path(data_dir).resolve() / 'media'
    scenes, errors = parse_scenes(scenes_csv)
    rows, stim_errors = parse_stimuli(stimuli_csv, root, set(scenes))
    errors += stim_errors
    if errors:
        raise ManifestError(errors)

    with engine.connect() as conn:
        old_scenes = {r.scene_id: r._asdict() for r in conn.execute(select(db.scenes))}
        old_stimuli = {(r.scene_id, r.version_label): r._asdict() for r in conn.execute(select(db.stimuli))}
    for sid, scene in scenes.items():
        if sid in old_scenes:
            old = {k: old_scenes[sid][k] for k in scene}
            if old != scene:
                errors.append(f'场景 {sid} 已存在且元数据不同；场景元数据导入后不可改')
    ordered = _parent_order(rows, old_stimuli, errors)
    if errors:
        raise ManifestError(errors)

    staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=media))
    try:
        shas, todo, seen_sha = {}, [], {}
        for r in ordered:
            key = (r['scene_id'], r['version_label'])
            where = f'{key[0]}/{key[1]}'
            sha = sha256_file(r['source'])
            if sha in seen_sha:
                errors.append(f'{where} 与 {seen_sha[sha]} 是同一文件'); continue
            seen_sha[sha] = where
            shas[key] = sha
            if key in old_stimuli:
                if old_stimuli[key]['sha256'] != sha:
                    errors.append(f'{where} 已存在且文件哈希不同；版本导入后不可替换')
                continue
            todo.append((r, sha, where))
        if errors:
            raise ManifestError(errors)
        # Rendering dominates import time; masters are independent, so fan out across processes.
        sources = [str(r['source']) for r, _, _ in todo]
        if len(todo) > 1 and workers != 1:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(_render_one, sources, [css_long_edges] * len(sources), chunksize=4))
        else:
            results = [_render_one(src, css_long_edges) for src in sources]
        new_rows, derivative_rows = [], []
        for (r, sha, where), result in zip(todo, results):
            if isinstance(result, str):
                errors.append(f'{where}：{result}'); continue
            profile, width, height, rendered = result
            for css, scale, data, w, h in rendered:
                file_sha = sha256_bytes(data)
                (staging / f'{file_sha}.jpg').write_bytes(data)
                derivative_rows.append(dict(stimulus_sha=sha, css_long_edge=css, scale=scale, file_sha=file_sha,
                                            path=f'{file_sha[:2]}/{file_sha}.jpg', width=w, height=h))
            parent = r['parent_version']
            new_rows.append(dict(
                sha256=sha, scene_id=r['scene_id'], version_label=r['version_label'], source_type=r['source_type'],
                is_real=r['is_real'], operator=r['operator'], direction=r['direction'], strength=r['strength'],
                region=r['region'], parent_key=(r['scene_id'], parent) if parent else None,
                origin_dataset=r['origin_dataset'], origin_id=r['origin_id'], license_id=r['license_id'],
                regen_recipe=r['regen_recipe'], source_path=str(r['source']), source_profile=profile,
                width=width, height=height, imported_at=db.now()))
        with engine.connect() as conn:
            existing_shas = {s for (s,) in conn.execute(select(db.stimuli.c.sha256))}
        for row in new_rows:
            if row['sha256'] in existing_shas:
                errors.append(f'{row["scene_id"]}/{row["version_label"]} 的文件已作为其他版本导入')

        versions = [(k[0], v['is_real']) for k, v in old_stimuli.items()]
        versions += [(r['scene_id'], r['is_real']) for r in new_rows]
        comp = composition(versions)
        warnings = []
        for sid in sorted(scenes):
            total, real = comp.get(sid, (0, 0))
            if total != FORMAL_VERSIONS or real < FORMAL_MIN_REAL:
                msg = f'场景 {sid}：{total} 个版本、{real} 个真实版本（正式要求 {FORMAL_VERSIONS} 个、真实 ≥ {FORMAL_MIN_REAL}）'
                (errors if strict_composition else warnings).append(msg)
        if errors:
            raise ManifestError(errors)

        all_shas = {k: v['sha256'] for k, v in old_stimuli.items()} | shas
        for row in new_rows:
            pkey = row.pop('parent_key')
            row['parent_sha'] = all_shas[pkey] if pkey else None
        with engine.begin() as conn:
            new_scene_rows = [dict(s, imported_at=db.now()) for sid, s in scenes.items() if sid not in old_scenes]
            if new_scene_rows:
                conn.execute(db.scenes.insert(), new_scene_rows)
            if new_rows:
                conn.execute(db.stimuli.insert(), new_rows)
            if derivative_rows:
                conn.execute(db.derivatives.insert(), derivative_rows)
            for d in derivative_rows:
                target = media / d['path']
                target.parent.mkdir(exist_ok=True)
                staged = staging / f'{d["file_sha"]}.jpg'
                if not target.exists():
                    os.replace(staged, target)
        return dict(scenes_added=len(new_scene_rows), stimuli_added=len(new_rows),
                    stimuli_unchanged=len(rows) - len(new_rows), derivatives_added=len(derivative_rows),
                    warnings=warnings)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def add_derivatives(engine, data_dir, css_long_edges):
    """Render additional display sizes from the recorded masters, re-verifying their hashes."""
    media = Path(data_dir).resolve() / 'media'
    with engine.connect() as conn:
        stims = conn.execute(select(db.stimuli.c.sha256, db.stimuli.c.source_path)).all()
        have = {(r.stimulus_sha, r.css_long_edge) for r in conn.execute(select(db.derivatives))}
    errors, added = [], []
    for sha, source in stims:
        todo = [c for c in css_long_edges if (sha, int(c)) not in have]
        if not todo:
            continue
        if not Path(source).is_file() or sha256_file(source) != sha:
            errors.append(f'主图缺失或哈希不符：{source}'); continue
        im, _ = load_master(source)
        for css, scale, data, w, h in render_derivatives(im, todo):
            file_sha = sha256_bytes(data)
            path = f'{file_sha[:2]}/{file_sha}.jpg'
            (media / path).parent.mkdir(exist_ok=True)
            if not (media / path).exists():
                (media / path).write_bytes(data)
            added.append(dict(stimulus_sha=sha, css_long_edge=css, scale=scale, file_sha=file_sha,
                              path=path, width=w, height=h))
    if errors:
        raise ManifestError(errors)
    if added:
        with engine.begin() as conn:
            conn.execute(db.derivatives.insert(), added)
    return len(added)


def scene_versions(conn):
    """{scene_id: {version_label: stimulus row}}."""
    out = defaultdict(dict)
    for r in conn.execute(select(db.stimuli)):
        out[r.scene_id][r.version_label] = r
    return out


def dump_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
