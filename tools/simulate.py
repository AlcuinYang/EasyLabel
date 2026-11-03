"""Scale and sampling simulation with known ground truth (not research data).

    python tools/simulate.py --scenes 238 --raters 30 --blocks 2 --strategy core_then_active

Builds synthetic scenes (12 versions each, small images), a pair experiment over every within-scene
pair, and simulated raters answering by a Davidson model with known scores. Reports planning / answer
latency and how well within-scene orderings are recovered (mean Spearman ρ with the truth)."""
import argparse
import itertools
import random
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from conftest import GOOD_DISPLAY, LABELS, make_dataset, write_csv  # noqa: E402

from easylabel.core import db, participation as P  # noqa: E402
from easylabel.core.experiments import create_experiment, freeze, set_status  # noqa: E402
from easylabel.core.export import response_rows  # noqa: E402
from easylabel.core.stimuli import import_manifest  # noqa: E402
from easylabel_analysis.scenes import scene_fits  # noqa: E402

CONFIG = """id: sim
title: simulation
template: pair
question: 哪一张差异更能被区分开？
items: pairs.csv
target_per_item: {target}
block_size: {block}
display: {{css_long_edge: 80}}
qc: {{hidden_repeat_rate: 0.05, swap_rate: 0.05}}
scheduler: {{strategy: {strategy}, scene_gap: 3, active_refit_every: 300}}
consent: {{version: v1, text: sim}}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scenes', type=int, default=60)
    ap.add_argument('--raters', type=int, default=10)
    ap.add_argument('--blocks', type=int, default=1)
    ap.add_argument('--block-size', type=int, default=100)
    ap.add_argument('--target', type=int, default=5)
    ap.add_argument('--strategy', default='core_then_coverage')
    ap.add_argument('--seed', type=int, default=0)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix='easylabel-sim-'))
    scenes = [f'sc{k:03d}' for k in range(args.scenes)]
    t0 = time.time()
    src = make_dataset(tmp / 'src', scenes=scenes)
    engine = db.open_engine(tmp / 'data')
    import_manifest(engine, tmp / 'data', src / 'scenes.csv', src / 'stimuli.csv', src, [80])
    print(f'导入 {len(scenes)} 场景 × {len(LABELS)} 版本：{time.time() - t0:.1f}s')

    rng = np.random.default_rng(args.seed)
    labels = list(LABELS)
    truth = {sid: dict(zip(labels, rng.normal(0, 1.5, len(labels)))) for sid in scenes}
    pairs = [[sid, a, b, f'{sid}-{a}-{b}', int(k < 6 and n < max(1, args.scenes // 5))]
             for n, sid in enumerate(scenes) for k, (a, b) in enumerate(itertools.combinations(labels, 2))]
    write_csv(tmp / 'pairs.csv', ['scene_id', 'version_a', 'version_b', 'item_id', 'is_core'], pairs)
    (tmp / 'config.yaml').write_text(CONFIG.format(target=args.target, block=args.block_size, strategy=args.strategy))
    create_experiment(engine, tmp / 'config.yaml')
    freeze(engine, 'sim')
    set_status(engine, 'sim', 'live')
    with engine.connect() as conn:
        from sqlalchemy import select
        label_of = {r.sha256: (r.scene_id, r.version_label) for r in conn.execute(select(db.stimuli))}
    print(f'题目 {len(pairs)} 对')

    py = random.Random(args.seed)
    plan_ms, answer_ms, n_answers = [], [], 0
    for r in range(args.raters):
        secret, _ = P.join(engine, P.create_invites(engine, 'sim', 'remote', 1)[0])
        sid = f'sim-session-{r:04d}'
        P.submit_consent(engine, secret, 'sim', sid, 'v1')
        P.submit_profile(engine, secret, 'sim', sid, dict(age_band='25-34', photo_experience='casual',
                                                           retouch_experience='none', color_vision='normal'))
        P.submit_display_check(engine, secret, 'sim', sid, GOOD_DISPLAY)
        for _ in range(args.blocks):
            t = time.perf_counter()
            st = P.start_block(engine, secret, 'sim', sid)
            plan_ms.append((time.perf_counter() - t) * 1000)
            while st['stage'] == 'task':
                token = st['task']['token']
                with engine.connect() as conn:
                    layout = conn.execute(select(db.assignments.c.layout).where(db.assignments.c.token == token)).scalar()
                (scene, left), (_, right) = label_of[layout[0]], label_of[layout[1]]
                d = (truth[scene][left] - truth[scene][right]) / 2
                p = np.exp([d, -d, np.log(0.3)])
                choice = py.choices(['left', 'right', 'tie'], weights=p / p.sum())[0]
                t = time.perf_counter()
                st = P.answer(engine, secret, 'sim', sid, token, {'choice': choice}, rt_ms=1500)
                answer_ms.append((time.perf_counter() - t) * 1000)
                n_answers += 1

    with engine.connect() as conn:
        rows = response_rows(conn, 'sim')
    fits = scene_fits(rows)
    rhos, failed = [], 0
    for (_, scene), f in fits.items():
        model = f['bt_half_ties']
        if not model['ok']:
            failed += 1
            continue
        est = [model['scores'][model['names'].index(n)] for n in model['names']]
        tru = [truth[scene][label_of[n][1]] for n in model['names']]
        rhos.append(spearmanr(est, tru).statistic)
    q = lambda xs, p: float(np.percentile(xs, p)) if xs else float('nan')
    print(f'作答 {n_answers} 条；开块 中位 {q(plan_ms, 50):.0f}ms / P95 {q(plan_ms, 95):.0f}ms；'
          f'提交 中位 {q(answer_ms, 50):.0f}ms / P95 {q(answer_ms, 95):.0f}ms')
    print(f'场景内排序恢复（{args.strategy}）：{len(rhos)} 个场景可拟合、{failed} 个不可辨识，'
          f'平均 Spearman ρ = {np.mean(rhos) if rhos else float("nan"):.3f}')
    print(f'数据目录：{tmp}')


if __name__ == '__main__':
    main()
