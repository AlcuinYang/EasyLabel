"""Offline stimulus generation: synthetic ladders and local edits around a reference version.
Operator strengths are research parameters supplied in a spec file, not constants in code. Each output
carries a regen_recipe (JSON) so `stimgen-verify` can rebuild it from the parent and compare hashes.

Operators (all in floating point, clipped once at the end):
  tone    OKLab L about the image median: L' = m + (L - m)·k      (k < 1 compresses, k > 1 expands)
  veil    linear-light mix with sRGB 128 grey: c' = (1 - a)·c + a·g
  chroma  OKLab a, b scaled by s                                  (s < 1 greys, s > 1 oversaturates)
  joint   a sequence of the above, e.g. {tone: 0.8, veil: 0.1}
A local edit applies the same operator inside a mask built from segmentation regions, feathered."""
import csv
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageFilter

from easylabel.core.imaging import SRGB_ICC, load_master, sha256_file, to_linear
from easylabel.core.stimuli import IDENT, ManifestError, read_csv

TOOL_VERSION = 1
GREY = float(to_linear(Image.new('RGB', (1, 1), (128, 128, 128)))[0, 0, 0])
M1 = np.array([[.4122214708, .5363325363, .0514459929], [.2119034982, .6806995451, .1073969566],
               [.0883024619, .2817188376, .6299787005]])
M2 = np.array([[.2104542553, .7936177850, -.0040720468], [1.9779984951, -2.4285922050, .4505937099],
               [.0259040371, .7827717662, -.8086757660]])


def to_oklab(linear):
    return np.cbrt(linear @ M1.T) @ M2.T


def from_oklab(lab):
    return (lab @ np.linalg.inv(M2).T) ** 3 @ np.linalg.inv(M1).T


def apply_operator(linear, operator, params):
    if operator == 'joint':
        for op, value in params.items():
            linear = apply_operator(linear, op, value)
        return linear
    if operator == 'veil':
        return (1 - params) * linear + params * GREY
    lab = to_oklab(np.clip(linear, 0, None))
    if operator == 'tone':
        median = float(np.median(lab[..., 0]))
        lab[..., 0] = median + (lab[..., 0] - median) * params
    elif operator == 'chroma':
        lab[..., 1:] *= params
    else:
        raise ValueError(f'未知算子 {operator}')
    return from_oklab(lab)


def region_mask(label_map_path, region_ids, size, feather_px):
    """Float mask at master size: nearest-neighbour upsampled label membership, Gaussian-feathered."""
    with Image.open(label_map_path) as im:
        labels = np.asarray(im.convert('L'))
    inside = np.isin(labels, [int(r) for r in region_ids]).astype(np.uint8) * 255
    mask = Image.fromarray(inside, 'L').resize(size, Image.Resampling.NEAREST)
    if feather_px:
        mask = mask.filter(ImageFilter.GaussianBlur(feather_px))
    return np.asarray(mask, dtype=np.float64)[..., None] / 255


def render(parent_path, recipe, root):
    im, _ = load_master(parent_path)
    linear = to_linear(im).astype(np.float64)
    edited = apply_operator(linear, recipe['operator'], recipe['params'])
    if recipe.get('region_ids'):
        mask = region_mask(Path(root) / recipe['label_map'], recipe['region_ids'], im.size, recipe.get('feather_px', 0))
        edited = mask * edited + (1 - mask) * linear
    out = np.clip(edited, 0, 1)
    srgb = np.where(out <= .0031308, out * 12.92, 1.055 * out ** (1 / 2.4) - .055)
    image = Image.fromarray(np.round(srgb * 255).astype(np.uint8), 'RGB')
    buffer = io.BytesIO()
    image.save(buffer, 'PNG', icc_profile=SRGB_ICC)
    return buffer.getvalue()


def _params(spec, operator, direction, strength):
    try:
        return spec['operators'][operator][direction][strength]
    except (KeyError, TypeError):
        raise ValueError(f'规格文件中没有 {operator}/{direction}/{strength} 的参数')


JOB_FIELDS = ['scene_id', 'parent_version', 'parent_path', 'version_label', 'operator', 'direction', 'strength',
              'origin_dataset', 'origin_id', 'license_id']


def generate(spec_path, jobs_csv, root, out_dir):
    """Render every job; write PNG masters under out_dir and a stimuli-format CSV (paths relative to root)."""
    spec = yaml.safe_load(Path(spec_path).read_text(encoding='utf-8'))
    root, out_dir = Path(root).resolve(), Path(out_dir).resolve()
    if root not in out_dir.parents and out_dir != root:
        raise ValueError('--out 必须位于 --root 之内，生成清单中的路径相对于 root')
    errors, rows = [], []
    for line, job in read_csv(jobs_csv, JOB_FIELDS):
        where = f'{Path(jobs_csv).name} 第{line}行'
        try:
            if not IDENT.match(job['version_label']):
                raise ValueError('version_label 非法')
            strength = int(job['strength'])
            parent = (root / job['parent_path']).resolve()
            if root not in parent.parents:
                raise ValueError('parent_path 必须位于 root 内')
            recipe = dict(tool='easylabel.stimgen', tool_version=TOOL_VERSION, spec_version=spec.get('version'),
                          parent_sha=sha256_file(parent), operator=job['operator'],
                          params=_params(spec, job['operator'], job['direction'], strength))
            region = None
            if job.get('region_ids'):
                region = job.get('region') or ''
                if region not in ('subject', 'background') or not job.get('label_map'):
                    raise ValueError('局部编辑须给出 region（subject/background）与 label_map')
                recipe.update(region_ids=[r for r in job['region_ids'].split(';') if r], label_map=job['label_map'],
                              label_map_sha=sha256_file(root / job['label_map']),
                              feather_px=float(job.get('feather_px') or spec.get('feather_px', 0)))
            data = render(parent, recipe, root)
        except (ValueError, OSError) as e:
            errors.append(f'{where}：{e}'); continue
        target = out_dir / job['scene_id'] / f'{job["version_label"]}.png'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rows.append(dict(scene_id=job['scene_id'], version_label=job['version_label'],
                         path=target.relative_to(root).as_posix(),
                         source_type='local_edit' if region else 'synth_ladder', operator=job['operator'],
                         direction=job['direction'], strength=strength, region=region or '',
                         parent_version=job['parent_version'], origin_dataset=job['origin_dataset'],
                         origin_id=job['origin_id'], license_id=job['license_id'],
                         regen_recipe=json.dumps(dict(recipe, output_sha=hashlib.sha256(data).hexdigest()),
                                                 ensure_ascii=False, sort_keys=True)))
    if errors:
        raise ManifestError(errors)
    out_csv = out_dir / 'stimuli_generated.csv'
    fields = ['scene_id', 'version_label', 'path', 'source_type', 'operator', 'direction', 'strength', 'region',
              'parent_version', 'origin_dataset', 'origin_id', 'license_id', 'regen_recipe']
    with out_csv.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return out_csv, len(rows)


def verify(stimuli_csv, root):
    """Rebuild every row that has a regen_recipe from its parent and compare with the file on disk.
    Returns a list of problems (empty = all byte-identical)."""
    root = Path(root).resolve()
    rows = [r for _, r in read_csv(stimuli_csv, ['scene_id', 'version_label', 'path', 'parent_version'])]
    by_key = {(r['scene_id'], r['version_label']): r for r in rows}
    problems = []
    for r in rows:
        if not r.get('regen_recipe'):
            continue
        where = f'{r["scene_id"]}/{r["version_label"]}'
        recipe = json.loads(r['regen_recipe'])
        parent = by_key.get((r['scene_id'], r['parent_version']))
        if not parent:
            problems.append(f'{where}：清单中缺少母版本'); continue
        parent_path = root / parent['path']
        if sha256_file(parent_path) != recipe['parent_sha']:
            problems.append(f'{where}：母版本文件与配方记录的哈希不符'); continue
        if recipe.get('label_map') and sha256_file(root / recipe['label_map']) != recipe['label_map_sha']:
            problems.append(f'{where}：分割图哈希不符'); continue
        data = render(parent_path, recipe, root)
        on_disk = (root / r['path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != hashlib.sha256(on_disk).hexdigest():
            a = np.asarray(Image.open(io.BytesIO(data)), dtype=int)
            b = np.asarray(Image.open(io.BytesIO(on_disk)), dtype=int)
            diff = int(np.abs(a - b).max()) if a.shape == b.shape else None
            problems.append(f'{where}：重新生成的文件不一致（最大像素差 {diff}）')
    return problems
