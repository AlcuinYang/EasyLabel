"""E0: acceptable sets from the multi-select grid. Per stimulus: how often it was shown, accepted
and picked as best; per scene: how often 'none' was chosen."""
from collections import defaultdict

from easylabel_analysis.bt import wilson


def acceptable_sets(rows, excluded=()):
    shown, accepted, best = defaultdict(int), defaultdict(int), defaultdict(int)
    scene_n, scene_none = defaultdict(int), defaultdict(int)
    stimulus_scene = {}
    for r in rows:
        if r['template'] != 'grid' or r['block_kind'] != 'main' or r['rater'] in excluded:
            continue
        scene_n[r['scene_id']] += 1
        scene_none[r['scene_id']] += bool(r['none'])
        for s in r['layout'].split(';'):
            shown[s] += 1
            stimulus_scene[s] = r['scene_id']
        for s in filter(None, (r['accepted'] or '').split(';')):
            accepted[s] += 1
        best[r['best']] += 1
    stimuli = {s: dict(scene_id=stimulus_scene[s], shown=n, accepted=wilson(accepted[s], n), best=wilson(best[s], n))
               for s, n in shown.items()}
    scenes = {sid: dict(n=n, none=wilson(scene_none[sid], n)) for sid, n in scene_n.items()}
    return dict(stimuli=stimuli, scenes=scenes)
