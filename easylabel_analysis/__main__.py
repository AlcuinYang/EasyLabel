"""python -m easylabel_analysis EXPORT_DIR → analysis.json + report.md next to the export.
Applies the frozen, pre-registered exclusion rules; excluded raters are reported, not deleted."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

from easylabel_analysis.e0 import acceptable_sets
from easylabel_analysis.io import read_export
from easylabel_analysis.qc import rater_qc
from easylabel_analysis.regions import region_agreement
from easylabel_analysis.scenes import scene_fits


def _plain(value):
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _rate(r):
    return '—' if not r['n'] else f"{r['success']}/{r['n']}（{r['rate']:.2f}）"


def analyze(export_dir):
    experiment, rows, tables = read_export(export_dir)
    config = experiment['config']
    qc = rater_qc(rows, config['exclusion'])
    excluded = {k for k, v in qc.items() if v['excluded']}
    labels = {s['sha256']: f"{s['scene_id']}/{s['version_label']}" for s in tables.get('stimuli', [])}
    report = dict(experiment=experiment['id'], config_hash=experiment['config_hash'], template=config['template'],
                  raters=qc, excluded=sorted(excluded))
    lines = [f"# {config['title']}（{experiment['id']}）", '', f"配置哈希 `{experiment['config_hash']}`", '',
             '## 评分者质控', '', '排除规则来自冻结配置：' + json.dumps(config['exclusion'], ensure_ascii=False), '',
             '|评分者|档位|组|正式作答|自一致|互换一致|金标题|过快占比|排除|',
             '|---|---|---|---:|---|---|---|---:|---|']
    for k, v in qc.items():
        fast = '—' if v['rt_fast_share'] is None else f"{v['rt_fast_share']:.2f}"
        lines.append(f"|{k}|{v['tier']}|{v['arm']}|{v['n_main']}|{_rate(v['self_consistency'])}|"
                     f"{_rate(v['swap_consistency'])}|{_rate(v['gold_accuracy'])}|{fast}|{'；'.join(v['exclusion_reasons']) or '否'}|")

    if config['template'] == 'pair':
        fits = scene_fits(rows, excluded)
        report['scenes'] = {f'{arm}|{scene}': dict(
            n=f['n'], davidson=_summary(f['davidson'], labels), bt_half_ties=_summary(f['bt_half_ties'], labels),
            connectivity=f['connectivity']) for (arm, scene), f in fits.items()}
        lines += ['', '## 场景内标度（Davidson）', '', '分数只在同一场景、同一组内可比。', '',
                  '|组|场景|判断数|状态|排序（低→高）|', '|---|---|---:|---|---|']
        for (arm, scene), f in fits.items():
            d = f['davidson']
            order = ' < '.join(labels.get(n, n[:8]).split('/')[-1] for n, _ in
                               sorted(zip(d['names'], d['scores']), key=lambda t: t[1])) if d['ok'] else ''
            lines.append(f"|{arm}|{scene}|{f['n']}|{'成功' if d['ok'] else d['reason']}|{order}|")
        followups = [r for r in rows if r['role'] == 'followup']
        if followups:
            counts = {}
            for r in followups:
                counts[r['source']] = counts.get(r['source'], 0) + 1
            report['followup_sources'] = counts
            lines += ['', '## 差别来源追问', '', json.dumps(counts, ensure_ascii=False)]
    elif config['template'] == 'grid':
        sets = acceptable_sets(rows, excluded)
        report['e0'] = sets
        lines += ['', '## E0 可接受集合', '', '|场景|版本|呈现|被选“达到”|被选最好|', '|---|---|---:|---|---|']
        for sha, s in sorted(sets['stimuli'].items(), key=lambda t: labels.get(t[0], t[0])):
            lines.append(f"|{s['scene_id']}|{labels.get(sha, sha[:8]).split('/')[-1]}|{s['shown']}|"
                         f"{_rate(s['accepted'])}|{_rate(s['best'])}|")
        lines += ['', '|场景|作答|选“都没有”|', '|---|---:|---|']
        for sid, s in sorted(sets['scenes'].items()):
            lines.append(f"|{sid}|{s['n']}|{_rate(s['none'])}|")
    elif config['template'] == 'region':
        report['regions'] = region_agreement(rows, excluded, tables.get('segmentations'))
        lines += ['', '## 区域一致性', '', '块级与粗级（主体 / 背景 / 全局）的评分者间一致性见 analysis.json。']

    out = Path(export_dir)
    (out.parent / f'{out.name}-analysis.json').write_text(
        json.dumps(_plain(report), ensure_ascii=False, indent=2), encoding='utf-8')
    (out.parent / f'{out.name}-report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return report


def _summary(model, labels):
    if not model['ok']:
        return dict(ok=False, reason=model['reason'])
    return dict(ok=True, nu=model['nu'], n=model['n_observations'],
                scores={labels.get(n, n): dict(score=float(s), se=float(e))
                        for n, s, e in zip(model['names'], model['scores'], model['se'])})


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m easylabel_analysis')
    p.add_argument('export_dir')
    args = p.parse_args(argv)
    try:
        analyze(args.export_dir)
    except (ValueError, FileNotFoundError) as e:
        print(f'错误：{e}', file=sys.stderr)
        sys.exit(1)
    name = Path(args.export_dir).name
    print(f'已写入 {name}-analysis.json 与 {name}-report.md')


if __name__ == '__main__':
    main()
