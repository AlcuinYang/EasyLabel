"""Server-side block planning. One block is planned at once and written to `assignments`, so every
scheduling decision is auditable. Callers must hold PLAN_LOCK (single-process deployment)."""
import hashlib
import random
import secrets
import threading
from datetime import timedelta

from sqlalchemy import and_, func, or_, select, update

from easylabel.core import db
from easylabel.scheduler.constraints import Slot, check, sequence
from easylabel.scheduler.strategies import STRATEGIES, Context

PLAN_LOCK = threading.Lock()
REPEAT_GAP = 10


def block_seed(config_hash, enrollment_id, seq):
    digest = hashlib.sha256(f'{config_hash}:{enrollment_id}:{seq}'.encode()).hexdigest()
    return int(digest[:12], 16)


def active_counts(conn, experiment_id, now):
    """Judgements per item that count toward the target: answered, or reserved and not expired."""
    a, i = db.assignments, db.items
    live = or_(a.c.status == 'answered', and_(a.c.status == 'reserved', a.c.reserved_until > now))
    rows = conn.execute(select(a.c.item_id, func.count()).join(i, i.c.id == a.c.item_id)
                        .where(i.c.experiment_id == experiment_id, a.c.role == 'normal', live)
                        .group_by(a.c.item_id)).all()
    return dict(rows)


def _layout(template, stimuli, rng):
    order = list(stimuli)
    if template in ('pair', 'grid'):
        rng.shuffle(order)
    return order


def plan_block(conn, exp, config, enrollment, kind, model=None):
    """Plan and insert the next block for an enrollment. Returns the block id, or None if no items
    remain for this rater."""
    now = db.now()
    a, i = db.assignments, db.items
    seq = conn.execute(select(func.coalesce(func.max(db.blocks.c.seq), -1))
                       .where(db.blocks.c.enrollment_id == enrollment.id)).scalar() + 1
    seed = block_seed(exp.config_hash, enrollment.id, seq)
    rng = random.Random(seed)
    items = {r.id: r for r in conn.execute(select(i).where(i.c.experiment_id == exp.id))}
    seen = {r.item_id for r in conn.execute(select(a.c.item_id).where(a.c.enrollment_id == enrollment.id))}

    slots, diagnostics = [], dict(kind=kind, strategy=None)
    if kind == 'practice':
        for item in sorted((x for x in items.values() if x.kind == 'practice'), key=lambda x: x.item_key):
            slots.append(Slot(key=f'p{item.id}', scene_id=item.scene_id, item_id=item.id, role='practice'))
        gap = 0
    else:
        q, size = config.qc, config.block_size
        n_repeat = round(size * q.hidden_repeat_rate)
        n_swap = round(size * q.swap_rate)
        gold_on = q.gold and (q.gold_arms is None or enrollment.arm in q.gold_arms)
        n_gold = round(size * q.gold_rate) if gold_on else 0
        candidates = [x for x in items.values() if x.kind == 'main' and x.id not in seen]
        ctx = Context(counts=active_counts(conn, exp.id, now), target=config.target_per_item, rng=rng,
                      model=model or {})
        ranked = STRATEGIES[config.scheduler.strategy](ctx, candidates)
        # A scene can fill at most every (gap+1)-th position; taking more would force drops.
        gap = config.scheduler.scene_gap
        cap, per_scene, chosen = (size + gap) // (gap + 1), {}, []
        for item in ranked:
            if len(chosen) >= size - n_repeat - n_swap - n_gold:
                break
            if per_scene.get(item.scene_id, 0) < cap:
                per_scene[item.scene_id] = per_scene.get(item.scene_id, 0) + 1
                chosen.append(item)
        if not chosen:
            return None
        for item in chosen:
            slots.append(Slot(key=f'm{item.id}', scene_id=item.scene_id, item_id=item.id,
                              priority=0 if item.is_core else 1))
        originals = rng.sample(slots, min(len(slots), n_repeat + n_swap))
        for k, orig in enumerate(originals):
            role = 'hidden_repeat' if k < n_repeat else 'swap'
            slots.append(Slot(key=f'r{orig.item_id}', scene_id=orig.scene_id, item_id=orig.item_id,
                              role=role, repeat_of=orig.key))
        gold = [x for x in items.values() if x.kind == 'gold']
        gold.sort(key=lambda x: (x.id in seen, rng.random()))
        for item in gold[:n_gold]:
            slots.append(Slot(key=f'g{item.id}', scene_id=item.scene_id, item_id=item.id, role='gold'))
        diagnostics.update(strategy=config.scheduler.strategy, candidates=len(candidates),
                           below_target=sum(ctx.counts.get(x.id, 0) < ctx.target for x in candidates),
                           core_unseen=sum(x.is_core for x in candidates))

    order, dropped = sequence(slots, gap, seed, REPEAT_GAP)
    problems = check(order, gap, REPEAT_GAP)
    if problems:  # sequence() never violates; this guards against regressions
        raise RuntimeError('调度约束被违反：' + '；'.join(problems[:5]))
    if not order:
        return None
    diagnostics.update(seed=seed, planned=len(order), dropped=[s.key for s in dropped],
                       roles={r: sum(s.role == r for s in order) for r in {s.role for s in order}})
    block_id = conn.execute(db.blocks.insert().values(
        enrollment_id=enrollment.id, seq=seq, kind=kind, seed=seed, plan=diagnostics, planned_at=now)).inserted_primary_key[0]

    reserve = now + timedelta(minutes=config.scheduler.reservation_minutes)
    layouts, ids = {}, {}
    for pos, s in enumerate(order):
        stimuli = items[s.item_id].stimuli
        if s.role == 'hidden_repeat':
            layout = layouts[s.repeat_of]
        elif s.role == 'swap':
            layout = list(reversed(layouts[s.repeat_of]))
        else:
            layout = _layout(exp.template, stimuli, rng)
        layouts[s.key] = layout
        ids[s.key] = conn.execute(a.insert().values(
            block_id=block_id, enrollment_id=enrollment.id, item_id=s.item_id, position=pos, role=s.role,
            repeat_of=ids.get(s.repeat_of), layout=layout, token=secrets.token_urlsafe(16),
            status='reserved', reserved_until=reserve)).inserted_primary_key[0]
    return block_id


def refresh_reservations(conn, block_id, minutes):
    conn.execute(update(db.assignments).where(db.assignments.c.block_id == block_id,
                                              db.assignments.c.status != 'answered')
                 .values(status='reserved', reserved_until=db.now() + timedelta(minutes=minutes)))


def append_followups(conn, block, config, responses):
    """B+ block: re-show a sample of this block's non-tie pair judgements, asking for the source
    of the difference. `responses` maps assignment id -> active response payload."""
    a = db.assignments
    rows = conn.execute(select(a).where(a.c.block_id == block.id).order_by(a.c.position)).all()
    if any(r.role == 'followup' for r in rows):
        return 0
    eligible = [r for r in rows if r.role == 'normal' and r.id in responses
                and (not config.followup.non_tie_only or responses[r.id].get('choice') != 'tie')]
    rng = random.Random(block.seed + 1)
    picked = rng.sample(eligible, round(len(eligible) * config.followup.rate)) if eligible else []
    picked.sort(key=lambda r: rng.random())
    start = rows[-1].position + 1 if rows else 0
    reserve = db.now() + timedelta(minutes=config.scheduler.reservation_minutes)
    for k, r in enumerate(picked):
        conn.execute(a.insert().values(
            block_id=block.id, enrollment_id=r.enrollment_id, item_id=r.item_id, position=start + k,
            role='followup', repeat_of=r.id, layout=r.layout, token=secrets.token_urlsafe(16),
            status='reserved', reserved_until=reserve))
    return len(picked)
