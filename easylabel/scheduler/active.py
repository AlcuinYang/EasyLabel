"""Active extension for pair experiments: refit within-scene BT on the current pooled data (per arm)
every `active_refit_every` new judgements and score each pair by the expected reduction in the
variance of its score difference. Scenes that cannot be fitted yet return no score, which the
`active` strategy treats as top priority (explore first)."""
import threading
from collections import defaultdict

from easylabel.core.export import response_rows
from easylabel_analysis.bt import fit, pair_information

_cache, _lock = {}, threading.Lock()


def information_by_item(rows, items):
    by_scene = defaultdict(list)
    for r in rows:
        if r['role'] == 'normal' and r['block_kind'] == 'main' and r.get('outcome'):
            by_scene[r['scene_id']].append(dict(a=r['stimulus_a'], b=r['stimulus_b'], outcome=r['outcome']))
    models = {}
    for scene, rs in by_scene.items():
        names = sorted({n for r in rs for n in (r['a'], r['b'])})
        models[scene] = fit(names, rs, half_ties=True)
    info = {}
    for item in items:
        model = models.get(item.scene_id)
        a, b = item.stimuli
        if not model or not model['ok'] or a not in model['names'] or b not in model['names']:
            continue
        k = {n: i for i, n in enumerate(model['names'])}
        cov = model['covariance']
        var = max(0.0, cov[k[a], k[a]] + cov[k[b], k[b]] - 2 * cov[k[a], k[b]])
        pq = pair_information(model, a, b)
        # One more Bernoulli observation with information pq reduces var to var / (1 + var·pq).
        info[item.id] = var - var / (1 + var * pq)
    return info


def active_model(conn, exp, arm, config, items):
    if config.template != 'pair':
        return {}
    rows = [r for r in response_rows(conn, exp.id) if r['arm'] == arm]
    key = (exp.id, arm)
    with _lock:
        cached = _cache.get(key)
        if cached and len(rows) - cached[0] < config.scheduler.active_refit_every:
            return cached[1]
    model = dict(information=information_by_item(rows, [i for i in items if i.kind == 'main']), minimum=1)
    with _lock:
        _cache[key] = (len(rows), model)
    return model
