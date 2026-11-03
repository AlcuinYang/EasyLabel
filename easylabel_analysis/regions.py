"""E3 region agreement at two levels: segmentation blocks, and coarse subject / background / global.
For each image: per-location marking rate and mean pairwise Jaccard between raters' marked sets."""
import itertools
import json
from collections import defaultdict


def _coarse_maps(segmentations):
    out = {}
    for row in segmentations or []:
        regions = json.loads(row['regions']) if isinstance(row['regions'], str) else row['regions']
        out[row['stimulus_sha']] = {r['id']: r['coarse'] for r in regions}
    return out


def _jaccard(sets):
    pairs = list(itertools.combinations(sets, 2))
    if not pairs:
        return None
    scores = [len(a & b) / len(a | b) if (a | b) else 1.0 for a, b in pairs]
    return sum(scores) / len(scores)


def region_agreement(rows, excluded=(), segmentations=None):
    coarse_of = _coarse_maps(segmentations)
    by_image = defaultdict(list)
    for r in rows:
        if r['template'] == 'region' and r['block_kind'] == 'main' and r['rater'] not in excluded:
            by_image[r['stimuli']].append(r)
    out = {}
    for sha, rs in by_image.items():
        mapping = coarse_of.get(sha, {})
        fine, coarse, overall = [], [], []
        for r in rs:
            marks = json.loads(r['regions']) if isinstance(r['regions'], str) else (r['regions'] or [])
            ids = {m['region'] for m in marks}
            fine.append(ids)
            coarse.append({'global' if i == 'global' else mapping.get(i, 'unknown') for i in ids})
            overall.append(r['overall'])
        n = len(rs)
        rate = lambda sets: {k: sum(k in s for s in sets) / n for k in sorted(set().union(*sets))} if sets else {}
        out[sha] = dict(n=n, overall_mean=sum(overall) / n, block_rate=rate(fine), coarse_rate=rate(coarse),
                        block_jaccard=_jaccard(fine), coarse_jaccard=_jaccard(coarse))
    return out
