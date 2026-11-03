"""Per-rater quality control and the pre-registered exclusion rules from the frozen config.
Works on response rows as produced by easylabel.core.export.response_rows (or io.read_export)."""
from collections import defaultdict
from statistics import median

from easylabel_analysis.bt import wilson


def rater_qc(rows, exclusion):
    """exclusion: dict with gold_accuracy_min, self_consistency_min, swap_consistency_min, rt_fast_ms,
    rt_fast_share_max (None disables a rule). Returns {rater: metrics}."""
    by_rater = defaultdict(list)
    for r in rows:
        if r['block_kind'] != 'practice' and r['role'] != 'followup':
            by_rater[r['rater']].append(r)
    out = {}
    for rater, rs in sorted(by_rater.items()):
        by_assignment = {r['assignment_id']: r for r in rs}
        pairs = defaultdict(list)
        for r in rs:
            if r['role'] in ('hidden_repeat', 'swap') and r['repeat_of'] in by_assignment:
                pairs[r['role']].append((by_assignment[r['repeat_of']], r))
        repeat = pairs['hidden_repeat']
        strict = sum(a['outcome'] == b['outcome'] for a, b in repeat)
        loose = sum(a['outcome'] == b['outcome'] or 'tie' in (a['outcome'], b['outcome']) for a, b in repeat)
        swap = pairs['swap']
        gold = [r for r in rs if r['role'] == 'gold' and r.get('correct') is not None]
        rts = [r['rt_ms'] for r in rs if r.get('rt_ms') is not None]
        fast_ms = exclusion.get('rt_fast_ms')
        fast_share = (sum(t < fast_ms for t in rts) / len(rts)) if (rts and fast_ms) else None
        m = dict(
            arm=rs[0]['arm'], tier=rs[0]['tier'],
            n_main=sum(r['role'] == 'normal' for r in rs),
            self_consistency=wilson(strict, len(repeat)),
            self_consistency_loose=wilson(loose, len(repeat)),
            swap_consistency=wilson(sum(a['outcome'] == b['outcome'] for a, b in swap), len(swap)),
            gold_accuracy=wilson(sum(r['correct'] for r in gold), len(gold)),
            rt_median_ms=median(rts) if rts else None,
            rt_fast_share=fast_share,
            left_share=_side_share(rs),
        )
        m['excluded'], m['exclusion_reasons'] = _apply(m, exclusion)
        out[rater] = m
    return out


def _side_share(rs):
    """Share of decisive pair answers given to the left image: a position-bias indicator."""
    decisive = [r for r in rs if r.get('choice') and r['choice'] != 'tie']
    if not decisive:
        return None
    return sum(r['choice'].startswith('left') for r in decisive) / len(decisive)


def _apply(m, ex):
    reasons = []
    rules = [('gold_accuracy_min', 'gold_accuracy', '金标题正确率'),
             ('self_consistency_min', 'self_consistency', '自一致率'),
             ('swap_consistency_min', 'swap_consistency', '互换一致率')]
    for key, metric, label in rules:
        threshold = ex.get(key)
        rate = m[metric]['rate']
        if threshold is not None and rate is not None and rate < threshold:
            reasons.append(f'{label} {rate:.2f} < {threshold}')
    share_max = ex.get('rt_fast_share_max')
    if share_max is not None and m['rt_fast_share'] is not None and m['rt_fast_share'] > share_max:
        reasons.append(f'过快作答占比 {m["rt_fast_share"]:.2f} > {share_max}')
    return bool(reasons), reasons
