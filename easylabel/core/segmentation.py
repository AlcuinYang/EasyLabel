"""Precomputed segmentation import for the region template. One label map per stimulus (or one per
scene, applied to every version with version_label '*'), shared by all raters."""
import hashlib
import io
from pathlib import Path

import numpy as np
from PIL import Image
from sqlalchemy import delete, select

from easylabel.core import db
from easylabel.core.stimuli import ManifestError, read_csv

COARSE = ('subject', 'background')
MIN_REGIONS, MAX_REGIONS = 8, 20


def _parse_coarse(text):
    out = {}
    for part in filter(None, (p.strip() for p in text.split(';'))):
        key, _, value = part.partition('=')
        out[int(key)] = value.strip()
    return out


def import_segmentations(engine, data_dir, csv_path, root, replace=False):
    """csv columns: scene_id, version_label ('*' = all versions), label_map (single-channel PNG whose
    pixel value is the region id), coarse ('1=subject;2=background;…' covering every id)."""
    root = Path(root).resolve()
    target_dir = Path(data_dir).resolve() / 'media' / 'segmentations'
    errors, records = [], []
    with engine.connect() as conn:
        stimuli = conn.execute(select(db.stimuli)).all()
        existing = {s for (s,) in conn.execute(select(db.segmentations.c.stimulus_sha))}
    by_scene = {}
    for s in stimuli:
        by_scene.setdefault(s.scene_id, {})[s.version_label] = s
    for line, row in read_csv(csv_path, ['scene_id', 'version_label', 'label_map', 'coarse']):
        where = f'{Path(csv_path).name} 第{line}行'
        versions = by_scene.get(row['scene_id'])
        if not versions:
            errors.append(f'{where} 场景不存在'); continue
        targets = list(versions.values()) if row['version_label'] == '*' else \
            [versions[row['version_label']]] if row['version_label'] in versions else []
        if not targets:
            errors.append(f'{where} 版本不存在：{row["version_label"]}'); continue
        path = (root / row['label_map']).resolve()
        if not path.is_file():
            errors.append(f'{where} 分割图不存在'); continue
        with Image.open(path) as im:
            if im.mode not in ('L', 'I', 'I;16', 'P'):
                errors.append(f'{where} 分割图须为单通道 PNG（像素值 = 区域编号）'); continue
            labels = np.asarray(im.convert('I') if im.mode != 'L' else im).astype(np.int64)
        ids = sorted(int(v) for v in np.unique(labels))
        if ids and (ids[0] < 0 or ids[-1] > 255):
            errors.append(f'{where} 区域编号须在 0–255 之间（浏览器按 8 位读取分割图）'); continue
        try:
            coarse = _parse_coarse(row['coarse'])
        except ValueError:
            errors.append(f'{where} coarse 格式应为 1=subject;2=background'); continue
        if set(coarse) != set(ids):
            errors.append(f'{where} coarse 须覆盖且只覆盖分割图中的区域 {ids}'); continue
        if any(v not in COARSE for v in coarse.values()):
            errors.append(f'{where} coarse 只能是 subject / background'); continue
        if not MIN_REGIONS <= len(ids) <= MAX_REGIONS:
            errors.append(f'{where} 区域数 {len(ids)} 不在 {MIN_REGIONS}–{MAX_REGIONS} 之间'); continue
        h, w = labels.shape
        regions = []
        for rid in ids:
            ys, xs = np.nonzero(labels == rid)
            regions.append(dict(id=str(rid), coarse=coarse[rid], area=round(len(xs) / labels.size, 5),
                                centroid=[round(float(xs.mean()) / w, 4), round(float(ys.mean()) / h, 4)]))
        for s in targets:
            if abs(w / h - s.width / s.height) > 0.01 * s.width / s.height:
                errors.append(f'{where} 分割图宽高比与 {s.scene_id}/{s.version_label} 不符'); continue
            if s.sha256 in existing and not replace:
                errors.append(f'{where} {s.scene_id}/{s.version_label} 已有分割图（用 --replace 覆盖）'); continue
            records.append((s.sha256, labels, regions))
    if errors:
        raise ManifestError(errors)
    target_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for sha, labels, regions in records:
        # Re-encode as a bare 8-bit greyscale PNG (no gAMA / ICC) so browsers read ids unaltered.
        buffer = io.BytesIO()
        Image.fromarray(labels.astype(np.uint8), 'L').save(buffer, 'PNG')
        data = buffer.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        target = target_dir / f'{digest}.png'
        if not target.exists():
            target.write_bytes(data)
        rows.append(dict(stimulus_sha=sha, label_map_path=f'segmentations/{digest}.png', label_map_sha=digest,
                         regions=regions, imported_at=db.now()))
    with engine.begin() as conn:
        if replace:
            conn.execute(delete(db.segmentations).where(db.segmentations.c.stimulus_sha.in_([r['stimulus_sha'] for r in rows])))
        if rows:
            conn.execute(db.segmentations.insert(), rows)
    return len(rows)
