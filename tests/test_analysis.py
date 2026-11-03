import itertools
from types import SimpleNamespace

import numpy as np

from easylabel.scheduler.active import information_by_item
from easylabel.scheduler.strategies import Context, active
from easylabel_analysis.bt import connectivity, fit, wilson


def simulate(scores, n_each, rng, nu=0.3):
    names = list(scores)
    rows = []
    for a, b in itertools.combinations(names, 2):
        d = (scores[a] - scores[b]) / 2
        p = np.exp([d, -d, np.log(nu)])
        p /= p.sum()
        for _ in range(n_each):
            rows.append(dict(a=a, b=b, outcome=rng.choice(['a', 'b', 'tie'], p=p)))
    return rows


def test_davidson_recovers_order():
    rng = np.random.default_rng(3)
    truth = {f'v{k}': s for k, s in enumerate([-2, -1, 0, 1, 2.5])}
    model = fit(sorted(truth), simulate(truth, 40, rng))
    assert model['ok'] and model['nu'] > 0
    est = dict(zip(model['names'], model['scores']))
    assert sorted(est, key=est.get) == sorted(truth, key=truth.get)
    assert abs(sum(model['scores'])) < 1e-6  # sum-to-zero identification


def test_unidentifiable_data_is_reported_not_fabricated():
    rows = [dict(a='x', b='y', outcome='a')] * 5
    assert fit(['x', 'y'], rows) == dict(ok=False, reason='响应支持图不强连通；不存在有限的全局无惩罚估计')
    assert fit(['x', 'y'], [dict(a='x', b='y', outcome='tie')] * 3)['ok'] is False
    assert connectivity(['x', 'y'], rows)['strict_strong'] is False


def test_wilson():
    assert wilson(0, 0)['rate'] is None
    w = wilson(8, 10)
    assert w['rate'] == 0.8 and 0.49 < w['ci'][0] < 0.5 and 0.94 < w['ci'][1] < 0.95


def test_active_information_and_ranking():
    import random
    from easylabel_analysis.bt import pair_information
    rng = np.random.default_rng(5)
    truth = {'a': -3, 'b': 0, 'c': 0.2, 'd': 3}
    rows = [dict(role='normal', block_kind='main', scene_id='s', stimulus_a=r['a'], stimulus_b=r['b'],
                 outcome=r['outcome']) for r in simulate(truth, 6, rng)]
    items = [SimpleNamespace(id=k, scene_id='s', stimuli=list(p), is_core=False)
             for k, p in enumerate(itertools.combinations('abcd', 2))]
    info = information_by_item(rows, items)
    # Expected variance reduction of the score difference: var²·pq / (1 + var·pq).
    model = fit(sorted(truth), [dict(a=r['stimulus_a'], b=r['stimulus_b'], outcome=r['outcome']) for r in rows],
                half_ties=True)
    k = {n: i for i, n in enumerate(model['names'])}
    for item in items:
        a, b = item.stimuli
        cov = model['covariance']
        var = cov[k[a], k[a]] + cov[k[b], k[b]] - 2 * cov[k[a], k[b]]
        pq = pair_information(model, a, b)
        assert abs(info[item.id] - var * var * pq / (1 + var * pq)) < 1e-12
    ctx = lambda counts: Context(counts=counts, target=5, rng=random.Random(0), model=dict(information=info, minimum=1))
    ranked = active(ctx({i.id: 3 for i in items}), items)
    assert [i.id for i in ranked] == sorted(info, key=info.get, reverse=True)
    # Unfitted scenes and never-judged items come first (explore before exploit).
    new = SimpleNamespace(id=99, scene_id='t', stimuli=['x', 'y'], is_core=False)
    assert active(ctx({i.id: 3 for i in items}), items + [new])[0] is new
    assert active(ctx({i.id: 3 for i in items if i.id != 5}), items)[0].id == 5
