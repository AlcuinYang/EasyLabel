import json

import numpy as np
import pytest
from PIL import Image

from easylabel.core.imaging import SRGB_ICC, to_linear
from easylabel.core.stimuli import ManifestError
from easylabel.stimgen import apply_operator, from_oklab, generate, to_oklab, verify

from conftest import write_csv

SPEC = """version: 1
feather_px: 2
operators:
  tone: {down: {1: 0.7}, up: {1: 1.3}}
  veil: {down: {1: 0.2}}
  chroma: {up: {1: 1.4}}
  joint: {down: {1: {tone: 0.8, veil: 0.1}}}
"""


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'root'
    (root / 's1').mkdir(parents=True)
    rng = np.random.default_rng(1)
    Image.fromarray((rng.random((60, 90, 3)) * 255).astype(np.uint8)).save(root / 's1' / 'A.png', icc_profile=SRGB_ICC)
    labels = np.repeat((np.arange(90) // 9 + 1)[None, :], 60, axis=0).astype(np.uint8)
    Image.fromarray(labels, 'L').save(root / 's1' / 'seg.png')
    (root / 'spec.yaml').write_text(SPEC)
    header = ['scene_id', 'parent_version', 'parent_path', 'version_label', 'operator', 'direction', 'strength',
              'region_ids', 'region', 'label_map', 'origin_dataset', 'origin_id', 'license_id']
    base = ['s1', 'A', 's1/A.png']
    tail = ['fivek', 'a0001', 'LicenseAdobe']
    write_csv(root / 'jobs.csv', header, [
        base + ['tone_d1', 'tone', 'down', 1, '', '', ''] + tail,
        base + ['veil_1', 'veil', 'down', 1, '', '', ''] + tail,
        base + ['joint_1', 'joint', 'down', 1, '', '', ''] + tail,
        base + ['loc_s', 'tone', 'up', 1, '1;2;3', 'subject', 's1/seg.png'] + tail,
    ])
    return root


def test_oklab_roundtrip_and_operator_directions():
    rng = np.random.default_rng(0)
    lin = rng.random((20, 20, 3))
    assert np.allclose(from_oklab(to_oklab(lin)), lin, atol=1e-9)
    spread = lambda x: np.std(to_oklab(np.clip(x, 0, 1))[..., 0])
    assert spread(apply_operator(lin, 'tone', 0.7)) < spread(lin) < spread(apply_operator(lin, 'tone', 1.3))
    chroma = lambda x: np.hypot(*np.moveaxis(to_oklab(np.clip(x, 0, 1))[..., 1:], -1, 0)).mean()
    assert chroma(apply_operator(lin, 'chroma', 1.4)) > chroma(lin)
    assert np.std(apply_operator(lin, 'veil', 0.2)) < np.std(lin)


def test_generate_records_recipe_and_verifies(source):
    out_csv, n = generate(source / 'spec.yaml', source / 'jobs.csv', source, source / 'generated')
    assert n == 4
    import csv
    rows = list(csv.DictReader(out_csv.open(encoding='utf-8')))
    local = next(r for r in rows if r['version_label'] == 'loc_s')
    assert local['source_type'] == 'local_edit' and local['region'] == 'subject'
    recipe = json.loads(local['regen_recipe'])
    assert recipe['params'] == 1.3 and recipe['region_ids'] == ['1', '2', '3'] and len(recipe['output_sha']) == 64
    # Local edit changes only the masked (left) part, beyond the feather.
    a = to_linear(Image.open(source / 's1' / 'A.png').convert('RGB'))
    b = to_linear(Image.open(source / 'generated' / 's1' / 'loc_s.png').convert('RGB'))
    assert np.abs(a[:, :20] - b[:, :20]).max() > 0.01 and np.abs(a[:, 40:] - b[:, 40:]).max() < 1e-6

    # Combine with the parent row, as an import manifest would, and verify byte-for-byte.
    manifest = source / 'stimuli.csv'
    fields = list(rows[0])
    parent = dict.fromkeys(fields, '') | dict(scene_id='s1', version_label='A', path='s1/A.png')
    write_csv(manifest, fields, [[parent[f] for f in fields]] + [[r[f] for f in fields] for r in rows])
    assert verify(manifest, source) == []
    Image.new('RGB', (90, 60)).save(source / 'generated' / 's1' / 'veil_1.png')
    problems = verify(manifest, source)
    assert len(problems) == 1 and 'veil_1' in problems[0]


def test_generate_rejects_unknown_parameters(source):
    write_csv(source / 'bad.csv', ['scene_id', 'parent_version', 'parent_path', 'version_label', 'operator',
                                   'direction', 'strength', 'origin_dataset', 'origin_id', 'license_id'],
              [['s1', 'A', 's1/A.png', 'x', 'tone', 'down', 3, 'd', 'i', 'l']])
    with pytest.raises(ManifestError, match='tone/down/3'):
        generate(source / 'spec.yaml', source / 'bad.csv', source, source / 'generated')
