"""Validation and export of immutable, scene-labelled pair manifests."""
import csv
import hashlib
from pathlib import Path, PurePosixPath
from PIL import Image

LIST_FIELDS = ['image_a', 'image_b', 'scene_id', 'pair_id', 'question_id']
EXTRA_FIELDS = ['scene_id', 'pair_id', 'question_id', 'question_text', 'experiment_mode']
ATTRIBUTION_QUESTION = '这两张在通透上的差别主要来自哪里？'
ATTRIBUTION_CHOICES = ('tone', 'color', 'both', 'other', 'no_difference')


def read_pairs(path, inputs):
    """Validate the whole list and every image before any experiment is created."""
    inputs = Path(inputs).resolve()
    if not inputs.is_dir():
        raise ValueError('图片目录不存在')
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if not {'image_a', 'image_b', 'scene_id'} <= set(reader.fieldnames or []):
            raise ValueError('清单必须包含 image_a、image_b、scene_id 列')
        source = list(reader)
    if not source:
        raise ValueError('图对清单不能为空')
    rows, paths, hashes, seen, ids, image_scenes = [], {}, {}, set(), set(), {}
    for index, row in enumerate(source, 2):
        scene = (row.get('scene_id') or '').strip()
        if not scene:
            raise ValueError(f'第{index}行缺少 scene_id')
        names = []
        for field in ('image_a', 'image_b'):
            name = (row.get(field) or '').strip()
            relative = PurePosixPath(name)
            if not name or relative.is_absolute() or '..' in relative.parts or '\\' in name:
                raise ValueError(f'第{index}行 {field} 必须为 input 内的相对路径')
            name = relative.as_posix()
            image = inputs / name
            if not image.is_file():
                raise ValueError(f'第{index}行图片路径不存在：{name}')
            # Source symlinks are permitted, as in the formal experiment.
            if name not in paths:
                try:
                    with Image.open(image) as im:
                        im.load()
                        if im.mode != 'RGB' or im.format != 'JPEG':
                            raise ValueError(f'图片必须为三通道 JPEG：{name}')
                except (OSError, SyntaxError) as e:
                    raise ValueError(f'图片无法打开：{name}') from e
                paths[name] = image
                hashes[name] = hashlib.sha256(image.read_bytes()).hexdigest()
            if name in image_scenes and image_scenes[name] != scene:
                raise ValueError(f'图片 {name} 被分到不同 scene_id；请明确所属场景')
            image_scenes[name] = scene
            names.append(name)
        if names[0] == names[1]:
            raise ValueError(f'第{index}行不能自比')
        question = (row.get('question_id') or '').strip() or 'limpidity'
        key = (scene, question, tuple(sorted(names)))
        if key in seen:
            raise ValueError(f'第{index}行重复无序图对；质控重复应由系统生成')
        seen.add(key)
        pid = (row.get('pair_id') or '').strip() or f'pair-{index-1:05d}'
        if pid in ids:
            raise ValueError(f'第{index}行 pair_id 重复')
        ids.add(pid)
        rows.append(dict(zip(LIST_FIELDS, [*names, scene, pid, question])))
    if len({n.casefold() for n in paths}) != len(paths):
        raise ValueError('图片相对路径存在大小写重名')
    return rows, paths, hashes


def write_pairs(path, rows):
    from annotation_mvp.protocol import write_csv
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(path, LIST_FIELDS, rows)


def schedule_list(edges, seed, round_number=2, scene_gap=3, quality_control=True):
    """Scene cooldown replaces image cooldown ONLY for explicit list experiments.

    A gap is the number of intervening questions (position difference > gap).
    Hidden and swapped repeats use disjoint base pairs. Attribution does not
    introduce new repeats: its inputs are precisely the selected source pairs.
    """
    import math
    import random
    from collections import Counter
    from annotation_mvp.protocol import pair_key
    if type(scene_gap) is not int or scene_gap < 0:
        raise ValueError('scene-gap 必须是非负整数')
    if not edges:
        raise ValueError('没有可安排的图对')
    rng = random.Random(seed)
    tasks = []
    for i, edge in enumerate(edges):
        if not edge.get('scene_id'):
            raise ValueError('清单调度缺少 scene_id')
        a, b = edge['a'], edge['b']
        if a == b:
            raise ValueError('清单不允许自比')
        if rng.randrange(2):
            a, b = b, a
        task = dict(task_id=f'r{round_number}-{i:05d}', round=round_number,
                    img_left=a, img_right=b, pair_key=pair_key(a, b),
                    is_hidden_repeat=0, is_swap_subset=0, repeat_of_task_id='',
                    scope='core', kind='imported')
        task.update({key: edge.get(key, '') for key in EXTRA_FIELDS})
        task['question_id'] = edge.get('question_id') or 'limpidity'
        tasks.append(task)
    n = len(tasks)
    repeats = max(1, math.ceil(n * .1)) if quality_control else 0
    swaps = repeats
    if n < repeats + swaps:
        raise ValueError('清单太小，不能抽取互不重叠的10%隐藏重复和10%互换子集')
    for i, original in enumerate(rng.sample(tasks, repeats + swaps)):
        duplicate = dict(original)
        duplicate['repeat_of_task_id'] = original['task_id']
        if i < repeats:
            duplicate['task_id'] += '-repeat'
            duplicate['is_hidden_repeat'] = 1
        else:
            duplicate['task_id'] += '-swap'
            original['is_swap_subset'] = duplicate['is_swap_subset'] = 1
            duplicate['img_left'], duplicate['img_right'] = original['img_right'], original['img_left']
        tasks.append(duplicate)
    gap = min(100, len(tasks) // 3)
    scene_counts = Counter(t['scene_id'] for t in tasks)
    if any((count - 1) * (scene_gap + 1) + 1 > len(tasks) for count in scene_counts.values()):
        raise ValueError(f'无法满足同场景间隔 {scene_gap} 题；未放宽约束，也未生成部分队列。单场景须显式设置 --scene-gap 0')
    origins = {t['repeat_of_task_id'] for t in tasks if t['repeat_of_task_id']}
    hidden = [t['task_id'] for t in tasks if t['is_hidden_repeat']]
    targets = {tid: gap + 1 + int(i * max(0, int(.85 * len(tasks)) - gap - 1) / max(1, len(hidden)-1))
               for i, tid in enumerate(hidden)}
    for attempt in range(1000):
        remaining = tasks[:]
        rng.shuffle(remaining)
        output, last, positions = [], {}, {}
        while remaining:
            pos = len(output)
            candidates = [t for t in remaining if pos - last.get(t['scene_id'], -100000) > scene_gap
                          and (not t['repeat_of_task_id'] or
                               (t['repeat_of_task_id'] in positions and pos - positions[t['repeat_of_task_id']] > gap))]
            if not candidates:
                break
            counts = Counter(t['scene_id'] for t in remaining)
            def priority(t):
                due = t['is_hidden_repeat'] and pos >= targets[t['task_id']]
                return (due, t['task_id'] in origins and pos <= gap,
                        counts[t['scene_id']] + rng.random() * 2)
            task = max(candidates, key=priority)
            remaining.remove(task)
            positions[task['task_id']] = pos
            last[task['scene_id']] = pos
            output.append(task)
        if not remaining and all(positions[x] < .95 * len(tasks) for x in hidden):
            return output
    raise ValueError(f'无法满足场景间隔{scene_gap}题和重复间隔{gap}题；未放宽约束，也未生成部分队列')
