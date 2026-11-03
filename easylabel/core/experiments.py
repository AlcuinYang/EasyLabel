"""One experiment = one config file. Draft → frozen (hashed, immutable) → live → closed."""
import hashlib
import subprocess
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import delete, select, update

from easylabel.core import db
from easylabel.core.stimuli import IDENT, ManifestError, dump_json, read_csv, scene_versions

PROTOCOL_VERSION = '1.0'
TEMPLATES = ('grid', 'pair', 'region')


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Arm(Strict):
    id: str
    question: str


class Display(Strict):
    css_long_edge: int = Field(gt=0)
    zoom_css_long_edge: Optional[int] = Field(default=None, gt=0)  # grid enlargement; never upsampled
    allowed_dpr: list[float] = [1, 1.5, 2]
    pixel_test_required: list[Literal['lab', 'remote']] = ['remote']
    min_black_steps: int = Field(default=4, ge=1, le=5)  # of 0/3/6/9/12; pilot-adjustable
    min_white_steps: int = Field(default=4, ge=1, le=5)  # of 243/246/249/252/255


class QC(Strict):
    hidden_repeat_rate: float = Field(default=0.05, ge=0, le=0.3)
    swap_rate: float = Field(default=0.05, ge=0, le=0.3)
    gold: Optional[str] = None
    gold_rate: float = Field(default=0.0, ge=0, le=0.3)
    gold_arms: Optional[list[str]] = None  # None = every arm


class Followup(Strict):
    enabled: bool = False
    rate: float = Field(default=0.2, gt=0, le=1)
    non_tie_only: bool = True


class Scheduler(Strict):
    strategy: Literal['coverage', 'core_then_coverage', 'core_then_active'] = 'core_then_coverage'
    scene_gap: int = Field(default=3, ge=0)
    reservation_minutes: int = Field(default=30, gt=0)
    active_refit_every: int = Field(default=200, gt=0)


class Exclusion(Strict):
    gold_accuracy_min: Optional[float] = 0.8
    self_consistency_min: Optional[float] = None
    swap_consistency_min: Optional[float] = None
    rt_fast_ms: Optional[int] = 300
    rt_fast_share_max: Optional[float] = 0.1


class Consent(Strict):
    version: str
    text: str


class Region(Strict):
    overall_levels: int = 5
    max_regions: int = 3
    low_label: str = '很差'
    high_label: str = '很好'


class ExperimentConfig(Strict):
    id: str
    title: str
    protocol_version: str = PROTOCOL_VERSION
    template: Literal['grid', 'pair', 'region']
    question: Optional[str] = None
    arms: list[Arm] = []
    response_scale: Literal[3, 5] = 3
    grid_size: int = 6
    items: str
    practice: Optional[str] = None
    target_per_item: int = Field(default=5, gt=0)
    block_size: int = Field(default=100, gt=0)
    tiers: list[Literal['lab', 'remote']] = ['lab', 'remote']
    display: Display
    qc: QC = QC()
    followup: Followup = Followup()
    scheduler: Scheduler = Scheduler()
    exclusion: Exclusion = Exclusion()
    consent: Consent
    region: Region = Region()

    @field_validator('id')
    @classmethod
    def _ident(cls, v):
        if not IDENT.match(v):
            raise ValueError('id 只能含字母、数字、_ . -')
        return v

    @model_validator(mode='after')
    def _coherent(self):
        if not self.arms:
            if not self.question:
                raise ValueError('没有 arms 时必须给出 question')
            # Normalised form: the stored config always lists arms, so it re-validates unchanged.
            self.arms = [Arm(id='default', question=self.question)]
            self.question = None
            return self._coherent()
        elif self.question:
            raise ValueError('有 arms 时问题文本写在各 arm 中，不要同时给出 question')
        ids = [a.id for a in self.arms]
        if len(set(ids)) != len(ids):
            raise ValueError('arm id 重复')
        if self.qc.gold_arms is not None and set(self.qc.gold_arms) - set(ids):
            raise ValueError('qc.gold_arms 引用了不存在的 arm')
        if self.qc.gold and self.template != 'pair':
            raise ValueError('金标题目前只用于成对比较')
        if self.qc.gold_rate and not self.qc.gold:
            raise ValueError('gold_rate > 0 时必须提供 qc.gold 清单')
        if self.followup.enabled and self.template != 'pair':
            raise ValueError('差别来源追问只用于成对比较')
        if self.template != 'pair' and (self.qc.hidden_repeat_rate or self.qc.swap_rate):
            # Hidden repeats and swaps are pair-specific QC; keep the config explicit.
            raise ValueError('非成对模板请把 qc.hidden_repeat_rate 与 qc.swap_rate 设为 0')
        if self.display.css_long_edge % 2 or (self.display.zoom_css_long_edge or 0) % 2:
            raise ValueError('CSS 长边必须为偶数')
        return self


def load_config(path):
    path = Path(path).resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
        config = ExperimentConfig.model_validate(raw)
    except ValidationError as e:
        raise ManifestError([f'{".".join(map(str, err["loc"]))}: {err["msg"]}' for err in e.errors()])
    return config, path.parent


def _flag(value):
    return value.strip().lower() in ('1', 'true', 'yes', 'y')


def parse_items(path, template, kind, versions, grid_size=6):
    """Return item dicts; stimuli are listed in canonical (file) order, expected in canonical terms."""
    columns = dict(pair=['scene_id', 'version_a', 'version_b'], grid=['scene_id', 'versions'],
                   region=['scene_id', 'version'])[template]
    if kind == 'gold':
        columns = columns + ['expected']
    errors, items, keys, contents = [], [], set(), set()
    for line, row in read_csv(path, columns):
        where = f'{Path(path).name} 第{line}行'
        sid = row['scene_id']
        if sid not in versions:
            errors.append(f'{where} 场景不存在：{sid}'); continue
        if template == 'pair':
            labels = [row['version_a'], row['version_b']]
        elif template == 'grid':
            labels = [v for v in row['versions'].split(';') if v]
        else:
            labels = [row['version']]
        missing = [v for v in labels if v not in versions[sid]]
        if missing:
            errors.append(f'{where} 场景 {sid} 中没有版本：{", ".join(missing)}'); continue
        if len(set(labels)) != len(labels):
            errors.append(f'{where} 同一题内版本重复（禁止自比）'); continue
        if template == 'grid' and len(labels) != grid_size:
            errors.append(f'{where} 网格须恰好 {grid_size} 个版本，实际 {len(labels)}'); continue
        content = (sid, tuple(sorted(labels)))
        if content in contents:
            errors.append(f'{where} 重复题目；质控重复由系统生成'); continue
        contents.add(content)
        key = row.get('item_id') or f'{kind}-{line - 1:05d}'
        if not IDENT.match(key) or key in keys:
            errors.append(f'{where} item_id 非法或重复：{key}'); continue
        keys.add(key)
        expected = row.get('expected') or None
        allowed = ('a', 'b') if kind == 'gold' else ('a', 'b', 'tie')
        if expected and (template != 'pair' or expected not in allowed):
            errors.append(f'{where} expected 须为 {"/".join(allowed)}（仅成对比较）'); continue
        items.append(dict(item_key=key, kind=kind, scene_id=sid,
                          stimuli=[versions[sid][v].sha256 for v in labels],
                          is_core=kind == 'main' and _flag(row.get('is_core', '')), expected=expected))
    return items, errors


def _code_version():
    try:
        out = subprocess.run(['git', 'describe', '--always', '--dirty'], cwd=Path(__file__).parent,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def create_experiment(engine, config_path, replace=False):
    config, base = load_config(config_path)
    with engine.connect() as conn:
        versions = scene_versions(conn)
        existing = conn.execute(select(db.experiments).where(db.experiments.c.id == config.id)).first()
    if existing and (not replace or existing.status != 'draft'):
        raise ValueError(f'实验 {config.id} 已存在（状态 {existing.status}）；只有草稿可以用 --replace 覆盖')
    errors, all_items = [], []
    sources = [('main', config.items), ('practice', config.practice), ('gold', config.qc.gold)]
    for kind, rel in sources:
        if not rel:
            continue
        path = (base / rel).resolve()
        if not path.is_file():
            errors.append(f'{kind} 清单不存在：{rel}'); continue
        found, errs = parse_items(path, config.template, kind, versions, config.grid_size)
        all_items += found; errors += errs
    if not any(i['kind'] == 'main' for i in all_items) and not errors:
        errors.append('正式题目清单为空')
    errors += _check_display(engine, config, all_items)
    if errors:
        raise ManifestError(errors)
    with engine.begin() as conn:
        if existing:
            conn.execute(delete(db.items).where(db.items.c.experiment_id == config.id))
            conn.execute(delete(db.experiments).where(db.experiments.c.id == config.id))
        conn.execute(db.experiments.insert(), dict(
            id=config.id, protocol_version=config.protocol_version, template=config.template,
            config=config.model_dump(mode='json'), status='draft', created_at=db.now()))
        conn.execute(db.items.insert(), [dict(i, experiment_id=config.id) for i in all_items])
    return config.id, {k: sum(i['kind'] == k for i in all_items) for k in ('main', 'practice', 'gold')}


def _check_display(engine, config, items):
    needed = {config.display.css_long_edge} | ({config.display.zoom_css_long_edge} - {None})
    shas = {s for i in items for s in i['stimuli']}
    with engine.connect() as conn:
        have = {(r.stimulus_sha, r.css_long_edge) for r in conn.execute(
            select(db.derivatives.c.stimulus_sha, db.derivatives.c.css_long_edge)
            .where(db.derivatives.c.scale == '1'))}
        segs = {s for (s,) in conn.execute(select(db.segmentations.c.stimulus_sha))}
    errors = []
    lacking = sorted({css for s in shas for css in needed if (s, css) not in have})
    if lacking:
        errors.append(f'部分刺激缺少 CSS 长边 {lacking} 的派生图；先运行 easylabel derive')
    if config.template == 'region' and shas - segs:
        errors.append(f'{len(shas - segs)} 张单图没有分割图；先导入分割')
    return errors


def get_experiment(conn, experiment_id):
    row = conn.execute(select(db.experiments).where(db.experiments.c.id == experiment_id)).first()
    if not row:
        raise KeyError(f'实验不存在：{experiment_id}')
    return row


def config_of(row):
    return ExperimentConfig.model_validate(row.config)


def freeze(engine, experiment_id):
    with engine.begin() as conn:
        exp = get_experiment(conn, experiment_id)
        if exp.status != 'draft':
            raise ValueError(f'只有草稿可以冻结；当前状态 {exp.status}')
        rows = conn.execute(select(db.items).where(db.items.c.experiment_id == experiment_id)
                            .order_by(db.items.c.kind, db.items.c.item_key)).all()
        shas = sorted({s for r in rows for s in r.stimuli})
        derivs = conn.execute(select(db.derivatives).where(db.derivatives.c.stimulus_sha.in_(shas))
                              .order_by(db.derivatives.c.stimulus_sha, db.derivatives.c.css_long_edge,
                                        db.derivatives.c.scale)).all()
        segs = conn.execute(select(db.segmentations).where(db.segmentations.c.stimulus_sha.in_(shas))
                            .order_by(db.segmentations.c.stimulus_sha)).all()
        frozen = dict(config=exp.config,
                      items=[[r.kind, r.item_key, r.scene_id, r.stimuli, r.is_core, r.expected] for r in rows],
                      derivatives=[[d.stimulus_sha, d.css_long_edge, d.scale, d.file_sha] for d in derivs],
                      segmentations=[[g.stimulus_sha, g.label_map_sha, g.regions] for g in segs])
        digest = hashlib.sha256(dump_json(frozen).encode()).hexdigest()
        conn.execute(update(db.experiments).where(db.experiments.c.id == experiment_id).values(
            status='frozen', config_hash=digest, code_version=_code_version(), frozen_at=db.now()))
    return digest


def set_status(engine, experiment_id, target):
    transitions = {'live': ('frozen', 'published_at'), 'closed': ('live', 'closed_at')}
    source, stamp = transitions[target]
    with engine.begin() as conn:
        exp = get_experiment(conn, experiment_id)
        if exp.status != source:
            raise ValueError(f'状态 {exp.status} 不能变为 {target}')
        conn.execute(update(db.experiments).where(db.experiments.c.id == experiment_id)
                     .values(status=target, **{stamp: db.now()}))
