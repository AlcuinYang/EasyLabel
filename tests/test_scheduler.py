import random
from collections import Counter
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from easylabel.core import db, participation as P
from easylabel.scheduler.constraints import Slot, check, sequence

from conftest import GOOD_DISPLAY


@pytest.mark.parametrize('seed', range(40))
def test_sequence_never_violates_constraints(seed):
    rng = random.Random(seed)
    n_scenes, gap = rng.randint(1, 12), rng.randint(0, 4)
    slots = [Slot(key=f'm{k}', scene_id=f's{rng.randrange(n_scenes)}', item_id=k) for k in range(rng.randint(1, 120))]
    for k, orig in enumerate(rng.sample(slots, min(len(slots), rng.randint(0, 10)))):
        slots.append(Slot(key=f'r{k}', scene_id=orig.scene_id, item_id=orig.item_id, role='swap', repeat_of=orig.key))
    order, dropped = sequence(slots, gap, seed, repeat_gap=10)
    assert check(order, gap, 10) == []
    assert len(order) + len(dropped) == len(slots)
    assert len({s.key for s in order}) == len(order)


def test_sequence_places_everything_when_feasible():
    slots = [Slot(key=f'm{k}', scene_id=f's{k % 10}', item_id=k) for k in range(100)]
    order, dropped = sequence(slots, 3, 1)
    assert not dropped and check(order, 3) == []


def _rater(engine, exp_id, n):
    token = P.create_invites(engine, exp_id, 'lab', 1)[0]
    secret, _ = P.join(engine, token)
    sid = f'session-{n:04d}'
    P.submit_consent(engine, secret, exp_id, sid, 'v1')
    P.submit_profile(engine, secret, exp_id, sid, dict(age_band='25-34', photo_experience='casual',
                                                        retouch_experience='none', color_vision='normal'))
    P.submit_display_check(engine, secret, exp_id, sid, GOOD_DISPLAY)
    return secret, sid


def _answer_all(engine, secret, exp_id, sid, choice='left'):
    st = P.state(engine, secret, exp_id, sid)
    while st['stage'] == 'task':
        t = st['task']
        payload = dict(source='tone') if t['template'] == 'followup' else dict(choice=choice)
        st = P.answer(engine, secret, exp_id, sid, t['token'], payload, rt_ms=900)
    return st


def test_planner_coverage_no_repeats_and_reservations(imported, pair_experiment):
    exp_id = pair_experiment(rep=0, gold=0, followup='false', block=10)
    raters = [_rater(imported, exp_id, n) for n in range(3)]
    for secret, sid in raters:
        P.start_block(imported, secret, exp_id, sid)  # practice
        _answer_all(imported, secret, exp_id, sid)
    for secret, sid in raters:
        assert P.start_block(imported, secret, exp_id, sid)['stage'] == 'task'
    with imported.connect() as conn:
        rows = conn.execute(select(db.assignments).where(db.assignments.c.role == 'normal')).all()
        items = {r.id: r for r in conn.execute(select(db.items))}
    per_rater = Counter((r.enrollment_id, r.item_id) for r in rows)
    assert max(per_rater.values()) == 1, 'a rater saw the same item twice'
    # Core items (s1, first three) come first for everyone.
    assert all(sum(items[r.item_id].is_core for r in rows if r.enrollment_id == e) == 3
               for e in {r.enrollment_id for r in rows})
    # 3 raters × 10 = 30 slots over 32 items: coverage spreads them (no item reserved twice while
    # others have none, apart from the shared core set).
    counts = Counter(r.item_id for r in rows if not items[r.item_id].is_core)
    assert max(counts.values()) == 1

    # Expired reservations stop counting toward the target.
    with imported.begin() as conn:
        conn.execute(update(db.assignments).values(reserved_until=db.now() - timedelta(minutes=1)))
    from easylabel.scheduler.planner import active_counts
    with imported.connect() as conn:
        assert active_counts(conn, exp_id, db.now()) == {}


def test_block_contains_qc_roles_and_followups(imported, pair_experiment):
    exp_id = pair_experiment(rep=0.1, gold=0.1, followup='true', block=20, gap=1)
    secret, sid = _rater(imported, exp_id, 0)
    P.start_block(imported, secret, exp_id, sid)
    _answer_all(imported, secret, exp_id, sid)
    P.start_block(imported, secret, exp_id, sid)
    st = _answer_all(imported, secret, exp_id, sid)
    assert st['stage'] == 'ready' and st['completion_code']
    with imported.connect() as conn:
        rows = conn.execute(select(db.assignments).join(db.blocks).where(db.blocks.c.kind == 'main')
                            .order_by(db.assignments.c.position)).all()
    roles = Counter(r.role for r in rows)
    assert roles['hidden_repeat'] == 2 and roles['swap'] == 2 and roles['gold'] == 2
    by_id = {r.id: r for r in rows}
    for r in rows:
        if r.role == 'hidden_repeat':
            assert r.layout == by_id[r.repeat_of].layout and r.position - by_id[r.repeat_of].position >= 10
        if r.role == 'swap':
            assert r.layout == list(reversed(by_id[r.repeat_of].layout))
    # Every answer was 'left' (non-tie), so 50% of the normal items get a follow-up, at the end.
    followups = [r for r in rows if r.role == 'followup']
    assert len(followups) == round(roles['normal'] * 0.5)
    assert min(f.position for f in followups) > max(r.position for r in rows if r.role != 'followup')


def test_core_items_are_never_the_ones_dropped():
    # One scene holds most slots: the sequencer must drop, and it must drop non-core items.
    slots = [Slot(key=f'c{k}', scene_id='s1', item_id=k, priority=0) for k in range(3)]
    slots += [Slot(key=f'x{k}', scene_id='s1', item_id=10 + k) for k in range(5)]
    slots += [Slot(key=f'o{k}', scene_id=f's{k + 2}', item_id=20 + k) for k in range(2)]
    for seed in range(20):
        order, dropped = sequence(slots, 1, seed)
        assert dropped and all(not s.key.startswith('c') for s in dropped)
        assert check(order, 1) == []
