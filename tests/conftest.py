import csv
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from easylabel.core import db
from easylabel.core.imaging import SRGB_ICC

LABELS = {  # 8 real + 4 synthetic, as in the formal composition
    'A': 'expert', 'B': 'expert', 'C': 'expert', 'D': 'expert', 'E': 'expert', 'cam': 'camera_default',
    'clear': 'retouch_clear', 'flat': 'retouch_flat',
    't1': 'synth_ladder', 't2': 'synth_ladder', 'v1': 'synth_ladder', 'loc': 'local_edit',
}
SCENE_HEADER = ['scene_id', 'split', 'tone_key', 'dynamic_range', 'subject_tags', 'chroma_level', 'e2_class']
STIM_HEADER = ['scene_id', 'version_label', 'path', 'source_type', 'operator', 'direction', 'strength', 'region',
               'parent_version', 'origin_dataset', 'origin_id', 'license_id', 'regen_recipe']


def write_csv(path, header, rows):
    with Path(path).open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def make_dataset(root, scenes=('s1', 's2', 's3', 's4'), size=(240, 160)):
    root = Path(root)
    rng = np.random.default_rng(0)
    scene_rows, stim_rows = [], []
    for k, sid in enumerate(scenes):
        scene_rows.append([sid, 'calib' if k == 0 else 'train', 'mid', 'mid', 'outdoor_day;portrait', 'mid', ''])
        base = rng.random((size[1], size[0], 3))
        for j, (label, stype) in enumerate(LABELS.items()):
            path = root / sid / f'{label}.png'
            path.parent.mkdir(parents=True, exist_ok=True)
            img = np.clip(base * (0.5 + j / 20), 0, 1)
            Image.fromarray((img * 255).astype(np.uint8), 'RGB').save(path, icc_profile=SRGB_ICC)
            synth = stype in ('synth_ladder', 'local_edit')
            stim_rows.append([sid, label, f'{sid}/{label}.png', stype, 'tone' if synth else '',
                              'down' if synth else '', '1' if synth else '', 'subject' if stype == 'local_edit' else '',
                              'A' if synth else '', 'test', f'{sid}-{label}', 'test-license', ''])
    write_csv(root / 'scenes.csv', SCENE_HEADER, scene_rows)
    write_csv(root / 'stimuli.csv', STIM_HEADER, stim_rows)
    return root


@pytest.fixture
def dataset(tmp_path):
    return make_dataset(tmp_path / 'src')


@pytest.fixture
def engine(tmp_path):
    return db.open_engine(tmp_path / 'data')


@pytest.fixture
def imported(engine, dataset, tmp_path):
    from easylabel.core.stimuli import import_manifest
    import_manifest(engine, tmp_path / 'data', dataset / 'scenes.csv', dataset / 'stimuli.csv', dataset, [80, 120])
    return engine


PAIR_CONFIG = """id: {id}
title: test pair
template: pair
question: 哪一张差异更能被区分开？
response_scale: {scale}
items: pairs.csv
practice: practice.csv
target_per_item: 2
block_size: {block}
display: {{css_long_edge: 80, allowed_dpr: [1, 1.5, 2]}}
qc: {{hidden_repeat_rate: {rep}, swap_rate: {rep}, gold: gold.csv, gold_rate: {gold}}}
followup: {{enabled: {followup}, rate: 0.5}}
scheduler: {{strategy: {strategy}, scene_gap: {gap}, active_refit_every: 5}}
consent: {{version: v1, text: 同意}}
"""


def pair_lists(root, scenes=('s1', 's2', 's3', 's4'), per_scene=8):
    import itertools
    labels = list(LABELS)
    rows = []
    for sid in scenes:
        for k, (a, b) in enumerate(list(itertools.combinations(labels, 2))[:per_scene]):
            rows.append([sid, a, b, f'{sid}-{a}-{b}', int(sid == 's1' and k < 3)])
    write_csv(root / 'pairs.csv', ['scene_id', 'version_a', 'version_b', 'item_id', 'is_core'], rows)
    write_csv(root / 'practice.csv', ['scene_id', 'version_a', 'version_b', 'item_id', 'expected'],
              [['s1', 'clear', 't2', 'p1', 'a'], ['s2', 'clear', 't2', 'p2', 'a']])
    write_csv(root / 'gold.csv', ['scene_id', 'version_a', 'version_b', 'item_id', 'expected'],
              [[sid, 't2', 'A', f'g-{sid}', 'b'] for sid in scenes])


@pytest.fixture
def pair_experiment(imported, tmp_path):
    def build(id='e1', scale=3, block=20, rep=0.1, gold=0.1, followup='true', strategy='core_then_coverage', gap=1,
              publish=True):
        from easylabel.core.experiments import create_experiment, freeze, set_status
        root = tmp_path / f'cfg-{id}'
        root.mkdir()
        pair_lists(root)
        (root / 'config.yaml').write_text(PAIR_CONFIG.format(id=id, scale=scale, block=block, rep=rep, gold=gold,
                                                             followup=followup, strategy=strategy, gap=gap))
        create_experiment(imported, root / 'config.yaml')
        if publish:
            freeze(imported, id)
            set_status(imported, id, 'live')
        return id
    return build


GOOD_DISPLAY = dict(dpr=2, visual_viewport_scale=1, pixel_test='uniform', black_steps_seen=5, white_steps_seen=5,
                    screen=dict(width=1920, height=1080), inner_width=1800, inner_height=1000)
