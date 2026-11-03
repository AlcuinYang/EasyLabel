import csv

import pytest
from sqlalchemy import func, select

from easylabel.core import db
from easylabel.core.stimuli import ManifestError, import_manifest

from conftest import STIM_HEADER, write_csv


def _rows(path):
    with open(path, encoding='utf-8') as f:
        return list(csv.reader(f))[1:]


def _import(engine, tmp_path, dataset, stimuli=None, **kw):
    return import_manifest(engine, tmp_path / 'data', dataset / 'scenes.csv', stimuli or dataset / 'stimuli.csv',
                           dataset, [80], **kw)


def test_import_counts_and_idempotence(engine, dataset, tmp_path):
    r = _import(engine, tmp_path, dataset)
    assert (r['scenes_added'], r['stimuli_added'], r['derivatives_added']) == (4, 48, 144)
    assert r['warnings'] == []  # 12 versions, 8 real per scene
    again = _import(engine, tmp_path, dataset)
    assert again['stimuli_added'] == 0 and again['stimuli_unchanged'] == 48
    with engine.connect() as conn:
        synth = conn.execute(select(db.stimuli).where(db.stimuli.c.version_label == 't1')).first()
        parent = conn.execute(select(db.stimuli).where(db.stimuli.c.sha256 == synth.parent_sha)).first()
    assert synth.is_real is False and parent.version_label == 'A' and parent.scene_id == synth.scene_id


@pytest.mark.parametrize('mutate, message', [
    (lambda r: r.__setitem__(4, ''), '合成版本必须填写 operator'),
    (lambda r: r.__setitem__(3, 'magic'), 'source_type'),
    (lambda r: r.__setitem__(8, 'nope'), '母版本不存在'),
    (lambda r: r.__setitem__(8, r[1]), '不能以自己为母版本'),
    (lambda r: r.__setitem__(11, ''), 'license_id 不能为空'),
    (lambda r: r.__setitem__(2, '../x.png'), '相对路径'),
    (lambda r: r.__setitem__(5, 'sideways'), 'direction'),
])
def test_manifest_errors_reject_whole_batch(engine, dataset, tmp_path, mutate, message):
    rows = _rows(dataset / 'stimuli.csv')
    target = next(r for r in rows if r[1] == 't1')
    mutate(target)
    bad = tmp_path / 'bad.csv'
    write_csv(bad, STIM_HEADER, rows)
    with pytest.raises(ManifestError, match=message):
        _import(engine, tmp_path, dataset, bad)
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.stimuli)).scalar() == 0


def test_parent_cycle_detected(engine, dataset, tmp_path):
    rows = _rows(dataset / 'stimuli.csv')
    for r in rows:
        if r[0] == 's1' and r[1] == 't1':
            r[8] = 't2'
        if r[0] == 's1' and r[1] == 't2':
            r[8] = 't1'
    bad = tmp_path / 'bad.csv'
    write_csv(bad, STIM_HEADER, rows)
    with pytest.raises(ManifestError, match='循环'):
        _import(engine, tmp_path, dataset, bad)


def test_same_file_twice_rejected(engine, dataset, tmp_path):
    rows = _rows(dataset / 'stimuli.csv')
    rows[1][2] = rows[0][2]
    bad = tmp_path / 'bad.csv'
    write_csv(bad, STIM_HEADER, rows)
    with pytest.raises(ManifestError, match='同一文件'):
        _import(engine, tmp_path, dataset, bad)


def test_composition_strict_and_warning(engine, dataset, tmp_path):
    rows = [r for r in _rows(dataset / 'stimuli.csv') if not (r[0] == 's2' and r[1] in ('B', 'C'))]
    small = tmp_path / 'small.csv'
    write_csv(small, STIM_HEADER, rows)
    with pytest.raises(ManifestError, match='场景 s2：10 个版本'):
        _import(engine, tmp_path, dataset, small, strict_composition=True)
    r = _import(engine, tmp_path, dataset, small)
    assert any('s2' in w for w in r['warnings'])


def test_imported_versions_are_immutable(engine, dataset, tmp_path):
    _import(engine, tmp_path, dataset)
    from PIL import Image
    Image.new('RGB', (240, 160), (1, 2, 3)).save(dataset / 's1' / 'A.png')
    with pytest.raises(ManifestError, match='不可替换'):
        _import(engine, tmp_path, dataset)
