"""Read an export directory produced by `easylabel export`, verifying the hash manifest."""
import csv
import hashlib
import json
from pathlib import Path

INT_FIELDS = ('enrollment_id', 'block_seq', 'position', 'assignment_id', 'repeat_of', 'is_core', 'rt_ms',
              'correct', 'none', 'overall', 'strength', 'undo_count')


def _typed(row):
    out = dict(row)
    for k in INT_FIELDS:
        v = out.get(k)
        out[k] = int(float(v)) if v not in (None, '') else None
    return out


def read_export(path, verify=True):
    root = Path(path)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if verify:
        for name, digest in manifest.items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f'导出文件被改动：{name}')
    experiment = json.loads((root / 'experiment.json').read_text(encoding='utf-8'))
    with (root / 'responses.csv').open(encoding='utf-8', newline='') as f:
        responses = [_typed(r) for r in csv.DictReader(f)]
    tables = {}
    for name in ('stimuli', 'scenes', 'raters', 'enrollments', 'display_checks', 'segmentations'):
        p = root / f'{name}.csv'
        if p.exists():
            with p.open(encoding='utf-8', newline='') as f:
                tables[name] = list(csv.DictReader(f))
    return experiment, responses, tables
