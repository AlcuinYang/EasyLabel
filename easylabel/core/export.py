"""Experiment export: tidy CSV tables + the raw event log + frozen config, with a hash manifest.
The analysis package reads only this directory."""
import csv
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from easylabel.core import db
from easylabel.core.experiments import get_experiment
from easylabel.core.participation import active_responses, canonical_pair

RESPONSE_FIELDS = [
    'experiment_id', 'arm', 'tier', 'rater', 'enrollment_id', 'block_seq', 'block_kind', 'position', 'role',
    'assignment_id', 'repeat_of', 'item_key', 'item_kind', 'is_core', 'scene_id', 'template',
    'stimulus_a', 'stimulus_b', 'stimuli', 'layout', 'choice', 'outcome', 'strength', 'expected', 'correct',
    'accepted', 'none', 'best', 'overall', 'regions', 'source', 'other_text', 'rt_ms', 'server_ts',
    'session_id', 'dpr', 'inner_width', 'inner_height', 'undo_count',
]


def response_rows(conn, experiment_id):
    """One row per active response (undone answers excluded; see events.csv for the full log)."""
    exp = get_experiment(conn, experiment_id)
    a, i, b, en, r = db.assignments, db.items, db.blocks, db.enrollments, db.raters
    rows = conn.execute(
        select(a, i.c.item_key, i.c.kind.label('item_kind'), i.c.is_core, i.c.scene_id, i.c.stimuli, i.c.expected,
               b.c.seq.label('block_seq'), b.c.kind.label('block_kind'), en.c.arm, en.c.tier, r.c.anon_id)
        .join(i, i.c.id == a.c.item_id).join(b, b.c.id == a.c.block_id)
        .join(en, en.c.id == a.c.enrollment_id).join(r, r.c.id == en.c.rater_id)
        .where(en.c.experiment_id == experiment_id).order_by(a.c.enrollment_id, b.c.seq, a.c.position)).all()
    responses = active_responses(conn, [x.id for x in rows])
    undo_counts = {}
    for (aid,) in conn.execute(select(db.events.c.assignment_id).where(db.events.c.type == 'undo')):
        undo_counts[aid] = undo_counts.get(aid, 0) + 1
    out = []
    for x in rows:
        ev = responses.get(x.id)
        if ev is None:
            continue
        p, env = ev.payload, ev.payload.get('env') or {}
        row = dict(experiment_id=experiment_id, arm=x.arm, tier=x.tier, rater=x.anon_id, enrollment_id=x.enrollment_id,
                   block_seq=x.block_seq, block_kind=x.block_kind, position=x.position, role=x.role,
                   assignment_id=x.id, repeat_of=x.repeat_of, item_key=x.item_key, item_kind=x.item_kind,
                   is_core=int(bool(x.is_core)), scene_id=x.scene_id,
                   template='followup' if x.role == 'followup' else exp.template,
                   stimuli=';'.join(x.stimuli), layout=';'.join(x.layout), rt_ms=ev.rt_ms,
                   server_ts=ev.server_ts.isoformat() if ev.server_ts else '', session_id=ev.session_id,
                   dpr=env.get('dpr'), inner_width=env.get('inner_width'), inner_height=env.get('inner_height'),
                   undo_count=undo_counts.get(x.id, 0), expected=x.expected)
        if exp.template == 'pair':
            row.update(stimulus_a=x.stimuli[0], stimulus_b=x.stimuli[1])
        if x.role == 'followup':
            row.update(source=p['source'], other_text=p.get('other_text'))
        elif exp.template == 'pair':
            outcome = canonical_pair(x.layout, x.stimuli, p['choice'])
            row.update(choice=p['choice'], outcome=outcome,
                       strength=2 if p['choice'].endswith('_strong') else (0 if outcome == 'tie' else 1))
            if x.expected:
                row['correct'] = int(outcome == x.expected)
        elif exp.template == 'grid':
            row.update(accepted=';'.join(x.layout[s] for s in p['accepted']), none=int(p['none']),
                       best=x.layout[p['best']])
        else:
            row.update(overall=p['overall'], regions=json.dumps(p['regions'], ensure_ascii=False))
        out.append(row)
    return out


def _write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def _plain(row):
    return {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else
                v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in row._asdict().items()}


def export_experiment(engine, experiment_id, out_dir):
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError(f'导出目录必须为空：{out}')
    with engine.connect() as conn:
        exp = get_experiment(conn, experiment_id)
        responses = response_rows(conn, experiment_id)
        items = conn.execute(select(db.items).where(db.items.c.experiment_id == experiment_id)).all()
        shas = sorted({s for it in items for s in it.stimuli})
        stimuli = conn.execute(select(db.stimuli).where(db.stimuli.c.sha256.in_(shas))).all()
        scene_ids = sorted({s.scene_id for s in stimuli})
        scenes = conn.execute(select(db.scenes).where(db.scenes.c.scene_id.in_(scene_ids))).all()
        enrollments = conn.execute(select(db.enrollments).where(db.enrollments.c.experiment_id == experiment_id)).all()
        enrollment_ids = [e.id for e in enrollments]
        raters = conn.execute(select(db.raters).where(
            db.raters.c.id.in_([e.rater_id for e in enrollments]))).all()
        checks = conn.execute(select(db.display_checks).where(
            db.display_checks.c.enrollment_id.in_(enrollment_ids))).all()
        events = conn.execute(select(db.events).where(db.events.c.enrollment_id.in_(enrollment_ids))
                              .order_by(db.events.c.id)).all()
        blocks = conn.execute(select(db.blocks).where(db.blocks.c.enrollment_id.in_(enrollment_ids))).all()
        assignments = conn.execute(select(db.assignments).where(
            db.assignments.c.enrollment_id.in_(enrollment_ids))).all()
        segmentations = conn.execute(select(db.segmentations).where(db.segmentations.c.stimulus_sha.in_(shas))).all()

    anon = {r.id: r.anon_id for r in raters}
    _write_csv(out / 'responses.csv', RESPONSE_FIELDS, responses)
    tables = {
        'items.csv': items, 'stimuli.csv': stimuli, 'scenes.csv': scenes, 'display_checks.csv': checks,
        'events.csv': events, 'blocks.csv': blocks, 'assignments.csv': assignments,
        'segmentations.csv': segmentations,
    }
    for name, rows in tables.items():
        if rows:
            plain = [_plain(r) for r in rows]
            if name == 'stimuli.csv':
                for p in plain:
                    p.pop('source_path', None)  # local path, not part of the dataset
            _write_csv(out / name, list(plain[0]), plain)
    _write_csv(out / 'raters.csv', ['rater', 'profile', 'external_source', 'external_pid'],
               [dict(rater=r.anon_id, profile=json.dumps(r.profile, ensure_ascii=False),
                     external_source=r.external_source, external_pid=r.external_pid) for r in raters])
    _write_csv(out / 'enrollments.csv', ['enrollment_id', 'rater', 'tier', 'arm', 'consent_version',
                                         'display_passed', 'practice_done', 'completion_code'],
               [dict(enrollment_id=e.id, rater=anon[e.rater_id], tier=e.tier, arm=e.arm,
                     consent_version=e.consent_version, display_passed=int(e.display_passed),
                     practice_done=int(e.practice_done), completion_code=e.completion_code) for e in enrollments])
    licenses = {}
    for s in stimuli:
        licenses.setdefault(s.license_id, []).append(f'{s.origin_dataset}:{s.origin_id}')
    (out / 'experiment.json').write_text(json.dumps(dict(
        id=exp.id, status=exp.status, protocol_version=exp.protocol_version, config_hash=exp.config_hash,
        code_version=exp.code_version, config=exp.config,
        frozen_at=exp.frozen_at and exp.frozen_at.isoformat(),
        licenses={k: sorted(set(v)) for k, v in licenses.items()}), ensure_ascii=False, indent=2), encoding='utf-8')
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir())}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return dict(responses=len(responses), files=len(manifest))
