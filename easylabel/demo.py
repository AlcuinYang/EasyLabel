"""Synthetic demo dataset for trying the workflow end to end. Not research data."""
import csv
import itertools
import random
from pathlib import Path

import numpy as np
from PIL import Image

from easylabel.core.imaging import SRGB_ICC

SCENES = [
    ('demo01', 'train', 'high', 'small', 'outdoor_day', 'mid'),
    ('demo02', 'train', 'mid', 'mid', 'indoor_artificial', 'high'),
    ('demo03', 'val', 'low', 'large', 'night', 'low'),
    ('demo04', 'test', 'mid', 'large', 'portrait', 'mid'),
    ('demo05', 'calib', 'high', 'mid', 'other', 'low'),
    ('demo06', 'calib', 'low', 'small', 'outdoor_day', 'high'),
]
# version label → (source_type, operator, direction, strength, parent, region)
VERSIONS = {
    'A': ('expert', None, None, None, None, None), 'B': ('expert', None, None, None, None, None),
    'C': ('expert', None, None, None, None, None), 'D': ('expert', None, None, None, None, None),
    'E': ('expert', None, None, None, None, None),
    'cam': ('camera_default', None, None, None, None, None),
    'clear': ('retouch_clear', None, None, None, None, None),
    'flat': ('retouch_flat', None, None, None, None, None),
    'tone_d1': ('synth_ladder', 'tone', 'down', 1, 'A', None),
    'tone_d3': ('synth_ladder', 'tone', 'down', 3, 'A', None),
    'veil_2': ('synth_ladder', 'veil', 'down', 2, 'A', None),
    'local_s': ('local_edit', 'tone', 'down', 2, 'A', 'subject'),
}
WIDTH, HEIGHT = 1500, 1000


def _base(seed):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:HEIGHT, 0:WIDTH] / np.array([HEIGHT, WIDTH])[:, None, None]
    hue = rng.random(3)
    img = np.empty((HEIGHT, WIDTH, 3))
    for c in range(3):
        img[..., c] = 0.15 + 0.6 * (1 - y) * hue[c] + 0.2 * x * hue[(c + 1) % 3]
    horizon = 0.55 + 0.1 * np.sin(x * 6 + seed)
    img[y > horizon] *= 0.45
    for _ in range(7):
        cx, cy, r = rng.random(), 0.3 + 0.6 * rng.random(), 0.05 + 0.12 * rng.random()
        mask = ((x - cx) * WIDTH / HEIGHT) ** 2 + (y - cy) ** 2 < r ** 2
        img[mask] = img[mask] * 0.3 + 0.7 * rng.random(3)
    img += rng.normal(0, 0.015, img.shape)
    subject = ((x - 0.5) * WIDTH / HEIGHT) ** 2 / 0.06 + (y - 0.6) ** 2 / 0.05 < 1
    return np.clip(img, 0, 1), subject


def _contrast(img, k):
    mid = img.mean()
    return np.clip(mid + (img - mid) * k, 0, 1)


def _saturation(img, k):
    gray = img.mean(axis=-1, keepdims=True)
    return np.clip(gray + (img - gray) * k, 0, 1)


def _version(img, subject, label, rng):
    if label in 'ABCDE':
        return _saturation(_contrast(img, 1.1 + 0.08 * 'ABCDE'.index(label) + rng.normal(0, .02)), 1.05)
    if label == 'cam':
        return _contrast(img, 0.85)
    if label == 'clear':
        return _saturation(_contrast(img, 1.35), 1.15)
    if label == 'flat':
        return np.clip(_contrast(img, 0.7) + 0.06, 0, 1)
    ref = _version(img, subject, 'A', rng)
    if label.startswith('tone_d'):
        return _contrast(ref, 1 - 0.15 * int(label[-1]))
    if label == 'veil_2':
        return ref * 0.7 + 0.3 * 0.5
    if label == 'local_s':
        out = ref.copy()
        out[subject] = _contrast(ref, 0.6)[subject]
        return out
    raise KeyError(label)


def _segmentation(seed, subject, n=12):
    """Voronoi cells over the frame; a cell is 'subject' when its seed lies in the subject ellipse."""
    rng = np.random.default_rng(seed + 50)
    h, w = HEIGHT // 4, WIDTH // 4
    seeds = rng.random((n, 2)) * [w, h]
    y, x = np.mgrid[0:h, 0:w]
    dist = (x[..., None] - seeds[:, 0]) ** 2 + (y[..., None] - seeds[:, 1]) ** 2
    labels = dist.argmin(axis=-1).astype(np.uint8) + 1
    small = subject[::4, ::4][:h, :w]
    coarse = {k + 1: 'subject' if small[min(h - 1, int(sy)), min(w - 1, int(sx))] else 'background'
              for k, (sx, sy) in enumerate(seeds)}
    return labels, coarse


def build(root):
    root = Path(root).resolve()
    masters = root / 'masters'
    masters.mkdir(parents=True, exist_ok=True)
    with (root / 'scenes.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scene_id', 'split', 'tone_key', 'dynamic_range', 'subject_tags', 'chroma_level', 'e2_class'])
        for s in SCENES:
            w.writerow([*s, ''])
    rows = []
    for n, (sid, *_rest) in enumerate(SCENES):
        img, subject = _base(n + 1)
        rng = np.random.default_rng(100 + n)
        for label, (stype, op, direction, strength, parent, region) in VERSIONS.items():
            out = _version(img, subject, label, rng)
            path = masters / sid / f'{label}.png'
            path.parent.mkdir(exist_ok=True)
            if not path.exists():
                Image.fromarray(np.round(out * 255).astype(np.uint8), 'RGB').save(path, icc_profile=SRGB_ICC)
            rows.append([sid, label, f'masters/{sid}/{label}.png', stype, op or '', direction or '',
                         strength if strength is not None else '', region or '', parent or '',
                         'demo-synthetic', f'{sid}-{label}', 'demo-cc0', ''])
    seg_rows = []
    for n, (sid, *_rest) in enumerate(SCENES):
        labels, coarse = _segmentation(n + 1, _base(n + 1)[1])
        path = root / 'segmentations' / f'{sid}.png'
        path.parent.mkdir(exist_ok=True)
        Image.fromarray(labels, 'L').save(path)
        seg_rows.append([sid, '*', f'segmentations/{sid}.png', ';'.join(f'{k}={v}' for k, v in coarse.items())])
    with (root / 'segmentations.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scene_id', 'version_label', 'label_map', 'coarse'])
        w.writerows(seg_rows)
    with (root / 'stimuli.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scene_id', 'version_label', 'path', 'source_type', 'operator', 'direction', 'strength',
                    'region', 'parent_version', 'origin_dataset', 'origin_id', 'license_id', 'regen_recipe'])
        w.writerows(rows)
    _experiments(root)
    return root


CONSENT = ('这是 EasyLabel 的演示实验，图片为程序合成，作答仅用于试用流程。\n'
           '你可以随时停止；块与块之间可以离开，下次用个人链接继续。')


def _experiments(root):
    lists = root / 'lists'
    lists.mkdir(exist_ok=True)
    rng = random.Random(7)
    with (lists / 'e0_grid.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scene_id', 'versions', 'item_id'])
        for sid, *_ in SCENES:
            w.writerow([sid, 'A;B;C;D;E;cam', f'{sid}-grid'])
    labels = list(VERSIONS)
    with (lists / 'e1_pairs.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scene_id', 'version_a', 'version_b', 'item_id', 'is_core'])
        for sid, split, *_ in SCENES:
            pairs = list(itertools.combinations(labels, 2))
            rng.shuffle(pairs)
            for k, (a, b) in enumerate(pairs[:20]):
                w.writerow([sid, a, b, f'{sid}-{a}-{b}', int(split == 'calib' and k < 6)])
    with (lists / 'e1_practice.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scene_id', 'version_a', 'version_b', 'item_id', 'expected'])
        for sid, *_ in SCENES[:4]:
            w.writerow([sid, 'clear', 'tone_d3', f'{sid}-practice', 'a'])
    with (lists / 'e1_gold.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scene_id', 'version_a', 'version_b', 'item_id', 'expected'])
        for sid, *_ in SCENES:
            w.writerow([sid, 'tone_d3', 'A', f'{sid}-gold', 'b'])
    with (lists / 'e3_single.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['scene_id', 'version', 'item_id'])
        for sid, *_ in SCENES:
            for v in ('A', 'cam', 'flat'):
                w.writerow([sid, v, f'{sid}-{v}'])
    common = f"""consent:
  version: demo-1
  text: |
{chr(10).join('    ' + line for line in CONSENT.splitlines())}
tiers: [lab, remote]
"""
    (root / 'e0.yaml').write_text(f"""id: demo-e0
title: 演示 E0 · 可接受集合
template: grid
question: 哪些版本已经达到这张照片应有的样子？
items: lists/e0_grid.csv
target_per_item: 5
block_size: 6
display: {{css_long_edge: 360, zoom_css_long_edge: 720}}
qc: {{hidden_repeat_rate: 0, swap_rate: 0}}
scheduler: {{scene_gap: 0}}
{common}""", encoding='utf-8')
    (root / 'e1.yaml').write_text(f"""id: demo-e1
title: 演示 E1 · 成对比较
template: pair
question: 哪一张不同明暗、不同物体之间的差异更能被区分开？
response_scale: 3
items: lists/e1_pairs.csv
practice: lists/e1_practice.csv
target_per_item: 3
block_size: 40
display: {{css_long_edge: 720}}
qc: {{hidden_repeat_rate: 0.05, swap_rate: 0.05, gold: lists/e1_gold.csv, gold_rate: 0.05}}
followup: {{enabled: true, rate: 0.2}}
scheduler: {{strategy: core_then_active, scene_gap: 2, active_refit_every: 20}}
{common}""", encoding='utf-8')
    (root / 'e3.yaml').write_text(f"""id: demo-e3
title: 演示 E3 · 单图区域点选
template: region
question: 这张照片整体达到应有的样子了吗？哪里最需要改？
items: lists/e3_single.csv
target_per_item: 5
block_size: 9
display: {{css_long_edge: 720}}
qc: {{hidden_repeat_rate: 0, swap_rate: 0}}
scheduler: {{scene_gap: 1}}
region: {{overall_levels: 5, max_regions: 3, low_label: 差得远, high_label: 已经到位}}
{common}""", encoding='utf-8')
