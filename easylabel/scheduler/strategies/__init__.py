"""Pluggable item-selection strategies. A strategy only ranks candidate items; the planner owns
constraints (no repeats per rater, scene gap, QC insertion) and reservations."""
from dataclasses import dataclass, field


@dataclass
class Context:
    counts: dict            # item_id -> active (answered + live reserved) count, normal role only
    target: int
    rng: object             # random.Random
    model: dict = field(default_factory=dict)  # strategy-specific state, e.g. per-item information


def coverage(ctx, candidates):
    """Below-target items first, fewest judgements first; over-target items last (extension)."""
    keyed = [(ctx.counts.get(i.id, 0) >= ctx.target, ctx.counts.get(i.id, 0), ctx.rng.random(), i)
             for i in candidates]
    return [k[-1] for k in sorted(keyed, key=lambda k: k[:3])]


def core_first(rest):
    def strategy(ctx, candidates):
        core = [i for i in candidates if i.is_core]
        ctx.rng.shuffle(core)
        return core + rest(ctx, [i for i in candidates if not i.is_core])
    return strategy


def active(ctx, candidates):
    """Information-weighted extension. ctx.model['information'][item_id] is the expected
    information of one more judgement (see scheduler.active); unfitted items rank first."""
    info = ctx.model.get('information', {})
    keyed = []
    for i in candidates:
        n = ctx.counts.get(i.id, 0)
        value = info.get(i.id)
        below = n < ctx.model.get('minimum', 1)
        keyed.append((not below, -(value if value is not None else float('inf')) / (1 + n), ctx.rng.random(), i))
    return [k[-1] for k in sorted(keyed, key=lambda k: k[:3])]


STRATEGIES = {
    'coverage': coverage,
    'core_then_coverage': core_first(coverage),
    'core_then_active': core_first(active),
}
