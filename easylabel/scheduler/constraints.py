"""Pure sequencing: order a block so that same-scene presentations are at least `scene_gap` items
apart and every QC repeat comes at least `repeat_gap` items after its original."""
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional


@dataclass
class Slot:
    key: str                      # unique within the block
    scene_id: str
    item_id: int
    role: str = 'normal'          # normal | hidden_repeat | swap | gold | practice
    repeat_of: Optional[str] = None
    priority: int = 1             # lower is placed earlier within its scene (core = 0), so drops hit the rest


def sequence(slots, scene_gap, seed, repeat_gap=10, attempts=20):
    """Greedy cooldown scheduling (most-remaining scene first) with random restarts.
    Returns (ordered slots, dropped slots). Repeats that cannot be placed are dropped rather than
    violating a constraint; the best attempt (fewest drops) is kept."""
    best = None
    for attempt in range(attempts):
        rng = random.Random(seed * 1000 + attempt)
        order, dropped = _greedy(slots, scene_gap, repeat_gap, rng)
        if best is None or len(dropped) < len(best[1]):
            best = (order, dropped)
        if not dropped:
            break
    return best


def _greedy(slots, scene_gap, repeat_gap, rng):
    pending = defaultdict(list)
    for s in slots:
        pending[s.scene_id].append(s)
    for items in pending.values():
        rng.shuffle(items)
        # Priority items first, originals before their repeats; shuffle order breaks the remaining ties.
        items.sort(key=lambda s: (s.repeat_of is not None, s.priority))
    last_scene, placed_at, order = {}, {}, []
    while any(pending.values()):
        pos = len(order)
        eligible = []
        for scene, items in pending.items():
            if not items or pos - last_scene.get(scene, -10**9) <= scene_gap:
                continue
            ready = [s for s in items if s.repeat_of is None
                     or (s.repeat_of in placed_at and pos - placed_at[s.repeat_of] >= repeat_gap)]
            if ready:
                eligible.append((scene, ready))
        if not eligible:
            break  # no filler items exist, so an empty eligible set is terminal
        top = max(len(pending[s]) for s, _ in eligible)
        scene, ready = rng.choice([(s, r) for s, r in eligible if len(pending[s]) == top])
        choice = ready[0]
        pending[scene].remove(choice)
        last_scene[scene] = pos
        placed_at[choice.key] = pos
        order.append(choice)
    # Repeats are only ever placed after their original, so nothing placed can be orphaned.
    return order, [s for items in pending.values() for s in items]


def check(order, scene_gap, repeat_gap=10):
    """Return a list of constraint violations (empty when the order is valid)."""
    problems, last, at = [], {}, {}
    for pos, s in enumerate(order):
        if s.scene_id in last and pos - last[s.scene_id] <= scene_gap:
            problems.append(f'位置 {pos}：场景 {s.scene_id} 间隔不足')
        if s.repeat_of is not None:
            if s.repeat_of not in at:
                problems.append(f'位置 {pos}：重复题出现在原题之前')
            elif pos - at[s.repeat_of] < repeat_gap:
                problems.append(f'位置 {pos}：重复题距原题不足 {repeat_gap}')
        last[s.scene_id] = pos
        at[s.key] = pos
    return problems
