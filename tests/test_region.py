import json

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import select

from easylabel.core import db, participation as P
from easylabel.core.experiments import create_experiment, freeze, set_status
from easylabel.core.export import export_experiment
from easylabel.core.segmentation import import_segmentations
from easylabel.core.stimuli import ManifestError
from easylabel_analysis.io import read_export
from easylabel_analysis.regions import region_agreement

from conftest import GOOD_DISPLAY, write_csv

CONFIG = """id: e3
title: region
template: region
question: 哪里最需要改？
items: single.csv
target_per_item: 2
block_size: 4
display: {css_long_edge: 80}
qc: {hidden_repeat_rate: 0, swap_rate: 0}
scheduler: {scene_gap: 0}
consent: {version: v1, text: 同意}
"""


def _label_map(path, n=10, size=(120, 80)):
    labels = (np.arange(size[0])[None, :] * n // size[0] + 1).repeat(size[1], axis=0).astype(np.uint8)
    Image.fromarray(labels, 'L').save(path)
    return {k: 'subject' if k <= 3 else 'background' for k in range(1, n + 1)}


@pytest.fixture
def segmented(imported, tmp_path):
    root = tmp_path / 'seg'
    root.mkdir()
    coarse = _label_map(root / 'map.png')
    text = ';'.join(f'{k}={v}' for k, v in coarse.items())
    write_csv(root / 'seg.csv', ['scene_id', 'version_label', 'label_map', 'coarse'],
              [['s1', '*', 'map.png', text], ['s2', 'A', 'map.png', text]])
    assert import_segmentations(imported, tmp_path / 'data', root / 'seg.csv', root) == 13
    return root


def test_segmentation_validation(imported, tmp_path):
    root = tmp_path / 'seg'
    root.mkdir()
    _label_map(root / 'few.png', n=4)
    _label_map(root / 'ok.png')
    Image.new('L', (100, 100), 1).save(root / 'square.png')
    write_csv(root / 'seg.csv', ['scene_id', 'version_label', 'label_map', 'coarse'], [
        ['s1', 'A', 'few.png', '1=subject;2=subject;3=background;4=background'],
        ['s1', 'B', 'ok.png', '1=subject'],
        ['s1', 'C', 'ok.png', ';'.join(f'{k}=sky' for k in range(1, 11))],
        ['s1', 'D', 'square.png', '1=subject'],
        ['nope', 'A', 'ok.png', '1=subject'],
    ])
    with pytest.raises(ManifestError) as e:
        import_segmentations(imported, tmp_path / 'data', root / 'seg.csv', root)
    text = str(e.value)
    assert '区域数 4' in text and '覆盖' in text and 'subject / background' in text and '场景不存在' in text


def test_region_flow_export_and_agreement(imported, segmented, tmp_path):
    cfg = tmp_path / 'cfg3'
    cfg.mkdir()
    write_csv(cfg / 'single.csv', ['scene_id', 'version', 'item_id'],
              [['s1', 'A', 'i1'], ['s1', 'cam', 'i2'], ['s2', 'A', 'i3']])
    (cfg / 'config.yaml').write_text(CONFIG)
    create_experiment(imported, cfg / 'config.yaml')
    freeze(imported, 'e3')
    set_status(imported, 'e3', 'live')

    for n, picks in enumerate([['1', '5'], ['1', 'global']]):
        secret, _ = P.join(imported, P.create_invites(imported, 'e3', 'lab', 1)[0])
        sid = f'session-r{n:03d}'
        P.submit_consent(imported, secret, 'e3', sid, 'v1')
        P.submit_profile(imported, secret, 'e3', sid, dict(age_band='25-34', photo_experience='none',
                                                                retouch_experience='none', color_vision='normal'))
        P.submit_display_check(imported, secret, 'e3', sid, GOOD_DISPLAY)
        st = P.start_block(imported, secret, 'e3', sid)
        task = st['task']
        assert task['template'] == 'region' and len(task['regions']) == 10 and task['label_map'].startswith('/api/labelmap/')
        with pytest.raises(P.Rejected, match='至少点选'):
            P.answer(imported, secret, 'e3', sid, task['token'], dict(overall=3, regions=[]))
        with pytest.raises(P.Rejected, match='方向'):
            P.answer(imported, secret, 'e3', sid, task['token'], dict(overall=3, regions=[dict(region='1', type='tone')]))
        with pytest.raises(P.Rejected, match='区域无效'):
            P.answer(imported, secret, 'e3', sid, task['token'], dict(overall=3, regions=[dict(region='99', type='unsure')]))
        while st['stage'] == 'task':
            marks = [dict(region=r, type='tone', direction='down') for r in picks]
            st = P.answer(imported, secret, 'e3', sid, st['task']['token'], dict(overall=4, regions=marks), rt_ms=2000)

    out = tmp_path / 'export3'
    export_experiment(imported, 'e3', out)
    experiment, rows, tables = read_export(out)
    agreement = region_agreement(rows, segmentations=tables['segmentations'])
    some = next(iter(agreement.values()))
    assert some['n'] == 2 and some['overall_mean'] == 4
    assert some['block_rate']['1'] == 1.0 and some['block_rate']['5'] == 0.5
    # Coarse: rater 1 marked subject (1) + background (5); rater 2 subject + global.
    assert some['coarse_rate'] == {'background': 0.5, 'global': 0.5, 'subject': 1.0}
    assert some['block_jaccard'] == pytest.approx(1 / 3)
    with imported.connect() as conn:
        seg = conn.execute(select(db.segmentations)).first()
    with Image.open(tmp_path / 'data' / 'media' / seg.label_map_path) as im:
        assert im.mode == 'L' and 'gamma' not in im.info and 'icc_profile' not in im.info
