"""Within-scene, within-arm scaling. Scores are never compared across scenes or arms."""
from collections import defaultdict

from easylabel_analysis.bt import connectivity, fit


def pair_rows(rows, excluded=()):
    """Main-role pair judgements in canonical form, excluding QC repeats, gold and excluded raters."""
    return [dict(a=r['stimulus_a'], b=r['stimulus_b'], outcome=r['outcome'], scene_id=r['scene_id'], arm=r['arm'])
            for r in rows if r['role'] == 'normal' and r['block_kind'] == 'main' and r['rater'] not in excluded
            and r.get('outcome')]


def scene_fits(rows, excluded=()):
    groups = defaultdict(list)
    for r in pair_rows(rows, excluded):
        groups[(r['arm'], r['scene_id'])].append(r)
    out = {}
    for key, rs in sorted(groups.items()):
        names = sorted({n for r in rs for n in (r['a'], r['b'])})
        out[key] = dict(n=len(rs), davidson=fit(names, rs), bt_half_ties=fit(names, rs, half_ties=True),
                        connectivity=connectivity(names, rs))
    return out
