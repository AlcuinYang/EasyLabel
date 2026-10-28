"""Within-scene analysis for imported lists. No between-scene scale is fitted."""
from collections import Counter, defaultdict
from pathlib import Path
import json

from scipy.stats import spearmanr
from annotation_mvp.protocol import write_csv, atomic_json
from annotation_mvp.scaling import fit, reliability, wilson, connectivity


def analyze_pairs(store):
    if store.next_task()['stage'] != 'finished':
        raise ValueError('请先完成当前实验的全部题目')
    if store.config['experiment_mode']=='attribution':
        counts = Counter(r['choice'] for r in store.answers.values())
        report = dict(experiment_mode='attribution', n=len(store.answers), choices=dict(counts))
        atomic_json(store.root/'analysis.json', report)
        (store.root/'report.md').write_text('# 原因归属结果\n\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        store.export()
        return {}, report
    groups = defaultdict(list)
    for task in store.config['tasks']:
        groups[(task['question_id'],task['scene_id'])].append(task)
    scores, reports, pairs, ties = [], [], [], []
    for (question, scene), tasks in sorted(groups.items()):
        answers = {t['task_id']:store.answers[t['task_id']] for t in tasks}
        rows = list(answers.values())
        names = sorted({n for t in tasks for n in (t['img_left'],t['img_right'])})
        davidson, bt = fit(names, rows), fit(names, rows, half_ties=True)
        counts = Counter(n for t in rows for n in (t['img_left'],t['img_right']))
        for label, model in [('Davidson',davidson),('BT_half_ties',bt)]:
            for i,n in enumerate(names):
                scores.append(dict(scene_id=scene,question_id=question,image=n,model=label,
                                   score=float(model['scores'][i]) if model['ok'] else '',
                                   se=float(model['se'][i]) if model['ok'] else '',
                                   n_comparisons=counts[n],status='ok' if model['ok'] else model['reason']))
        swap_pairs = [(answers[t['repeat_of_task_id']],answers[t['task_id']]) for t in tasks
                      if t['repeat_of_task_id'] and t['is_swap_subset'] and not t['is_hidden_repeat']]
        swap = wilson(sum(a['chosen_image']==b['chosen_image'] for a,b in swap_pairs),len(swap_pairs))
        rel = reliability(tasks,answers)
        correlation = None
        if davidson['ok'] and bt['ok']:
            value = float(spearmanr(davidson['scores'],bt['scores']).statistic)
            if __import__('math').isfinite(value):correlation=value
        reports.append(dict(scene_id=scene,question_id=question,n_images=len(names),n_answers=len(rows),
                            davidson_ok=davidson['ok'],davidson_reason=davidson.get('reason'),
                            bt_ok=bt['ok'],bt_reason=bt.get('reason'),rank_correlation=correlation,
                            self_consistency=rel,swap_consistency=swap,connectivity=connectivity(names,rows)))
        for r in rows:
            entry=dict(scene_id=scene,question_id=question,img_a=r['img_left'],img_b=r['img_right'],winner=r['chosen_image'])
            (ties if r['choice']=='tie' else pairs).append(entry)
    report = dict(experiment_mode='limpidity',scale_scope='within_scene_and_question_only',
                  global_scale_fitted=False,scenes=reports,ties_excluded_from_pairs_human=len(ties),
                  note='未收敛或不可辨识的场景不伪造分数与标准误；样本不足的重复一致率记为空。各场景分数不可跨场景比较。')
    write_csv(store.root/'scene_scores.csv',['scene_id','question_id','image','model','score','se','n_comparisons','status'],scores)
    write_csv(store.root/'bt_scores.csv',['image','score','se','n_comparisons','scene_id','question_id'],
              (r for r in scores if r['model']=='Davidson'))
    fields=['img_a','img_b','winner','scene_id','question_id']
    write_csv(store.root/'pairs_human.csv',fields,pairs)
    write_csv(store.root/'ties.csv',fields,ties)
    atomic_json(store.root/'analysis.json',report)
    text=['# v0 按场景分析','',report['note'],'','不拟合跨场景全局量表。','',
          '|问题|场景|图片|判断|Davidson|BT|隐藏重复一致率|左右互换一致率|',
          '|---|---|---:|---:|---|---|---|---|']
    def rate(value):
        return '无配对样本' if not value['n'] else f"{value['success']}/{value['n']} ({value['rate']:.3f})"
    for r in reports:
        text.append(f"|{r['question_id']}|{r['scene_id']}|{r['n_images']}|{r['n_answers']}|{r['davidson_reason'] or '成功'}|{r['bt_reason'] or '成功'}|{rate(r['self_consistency']['strict'])}|{rate(r['swap_consistency'])}|")
    text += ['',f'pairs_human.csv 排除了 {len(ties)} 条平局。详细数字、置信区间和连通分量见 analysis.json。',
             'scene_scores.csv 包含 Davidson 和平局各计半胜的 BT 分数及标准误，bt_scores.csv 为 Davidson 结果。']
    (store.root/'report.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    return {}, report
