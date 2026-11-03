import pytest
from sqlalchemy import select

from easylabel.core import db
from easylabel.core.experiments import create_experiment, freeze, load_config, set_status
from easylabel.core.stimuli import ManifestError

from conftest import PAIR_CONFIG, pair_lists, write_csv


def _config(tmp_path, **kw):
    params = dict(id='x', scale=3, block=20, rep=0.05, gold=0.05, followup='false', strategy='coverage', gap=1)
    params.update(kw)
    root = tmp_path / 'cfg'
    root.mkdir(exist_ok=True)
    pair_lists(root)
    (root / 'config.yaml').write_text(PAIR_CONFIG.format(**params))
    return root


def test_config_normalises_question_into_arm(tmp_path):
    config, _ = load_config(_config(tmp_path) / 'config.yaml')
    assert config.question is None and [a.id for a in config.arms] == ['default']
    # The stored form re-validates unchanged (frozen configs are reloaded on every request).
    assert type(config).model_validate(config.model_dump(mode='json')) == config


@pytest.mark.parametrize('patch, message', [
    ('response_scale: 4', 'response_scale'),
    ('unknown_field: 1', 'unknown_field'),
    ('display: {css_long_edge: 81}', '偶数'),
])
def test_config_rejects(tmp_path, patch, message):
    root = _config(tmp_path)
    text = (root / 'config.yaml').read_text()
    key = patch.split(':')[0]
    lines = [l for l in text.splitlines() if not l.startswith(key + ':')] + [patch]
    (root / 'config.yaml').write_text('\n'.join(lines))
    with pytest.raises(ManifestError, match=message):
        load_config(root / 'config.yaml')


def test_item_list_validation(imported, tmp_path):
    root = _config(tmp_path)
    write_csv(root / 'pairs.csv', ['scene_id', 'version_a', 'version_b'],
              [['s1', 'A', 'A'], ['s1', 'A', 'B'], ['s1', 'B', 'A'], ['s1', 'A', 'zz'], ['nope', 'A', 'B']])
    with pytest.raises(ManifestError) as e:
        create_experiment(imported, root / 'config.yaml')
    text = str(e.value)
    assert '禁止自比' in text and '重复题目' in text and '没有版本' in text and '场景不存在' in text


def test_missing_derivative_size_is_reported(imported, tmp_path):
    root = _config(tmp_path)
    text = (root / 'config.yaml').read_text().replace('css_long_edge: 80', 'css_long_edge: 64')
    (root / 'config.yaml').write_text(text)
    with pytest.raises(ManifestError, match='easylabel derive'):
        create_experiment(imported, root / 'config.yaml')


def test_lifecycle_and_hash(imported, tmp_path):
    root = _config(tmp_path)
    exp_id, counts = create_experiment(imported, root / 'config.yaml')
    assert counts == dict(main=32, practice=2, gold=4)
    create_experiment(imported, root / 'config.yaml', replace=True)  # drafts may be replaced
    with pytest.raises(ValueError):
        set_status(imported, exp_id, 'live')  # must freeze first
    digest = freeze(imported, exp_id)
    assert len(digest) == 64
    with pytest.raises(ValueError, match='已存在'):
        create_experiment(imported, root / 'config.yaml', replace=True)
    with pytest.raises(ValueError):
        freeze(imported, exp_id)
    set_status(imported, exp_id, 'live')
    set_status(imported, exp_id, 'closed')
    with imported.connect() as conn:
        row = conn.execute(select(db.experiments).where(db.experiments.c.id == exp_id)).first()
    assert row.status == 'closed' and row.config_hash == digest
