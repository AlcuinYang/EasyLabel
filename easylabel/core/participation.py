"""Rater-side workflow: invite → consent → profile → display check → practice → blocks.
Every state change is an appended event; assignment.status is a cache of the event log."""
import re
import secrets
from collections import Counter

from sqlalchemy import func, select, update

from easylabel.core import db
from easylabel.core.experiments import config_of, get_experiment
from easylabel.scheduler.planner import PLAN_LOCK, append_followups, plan_block, refresh_reservations

AGE_BANDS = ('18-24', '25-34', '35-44', '45-54', '55+')
EXPERIENCE = ('none', 'casual', 'frequent', 'professional')
COLOR_VISION = ('normal', 'deficient', 'unsure')
PAIR_CHOICES = {3: ('left', 'tie', 'right'), 5: ('left_strong', 'left', 'tie', 'right', 'right_strong')}
REGION_TYPES = ('tone', 'separation', 'disharmony', 'other', 'unsure')
FOLLOWUP_SOURCES = ('tone', 'color', 'both', 'other', 'cannot_tell')
FOLLOWUP_QUESTION = '刚才这两张的差别主要来自哪里？'
SESSION_ID = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
SAFE_ROLES = ('practice', 'followup')  # the only roles the client may learn


class Rejected(ValueError):
    """Client error; message is shown to the rater."""


# ---------------------------------------------------------------- invites & enrollment

def create_invites(engine, experiment_id, tier, count, arm=None, max_uses=1, note=None):
    with engine.begin() as conn:
        exp = get_experiment(conn, experiment_id)
        config = config_of(exp)
        if tier not in config.tiers:
            raise ValueError(f'实验不接受档位 {tier}')
        if arm and arm not in {a.id for a in config.arms}:
            raise ValueError(f'不存在 arm {arm}')
        tokens = [secrets.token_urlsafe(12) for _ in range(count)]
        conn.execute(db.invites.insert(), [dict(token=t, experiment_id=experiment_id, tier=tier, arm=arm,
                                                max_uses=max_uses, uses=0, note=note, created_at=db.now())
                                           for t in tokens])
    return tokens


def _balanced_arm(conn, experiment_id, tier, arms):
    counts = Counter(dict(conn.execute(
        select(db.enrollments.c.arm, func.count()).where(db.enrollments.c.experiment_id == experiment_id,
                                                         db.enrollments.c.tier == tier)
        .group_by(db.enrollments.c.arm)).all()))
    return min(arms, key=lambda a: (counts[a], arms.index(a)))


def join(engine, token, secret=None, external_source=None, external_pid=None):
    """Return (rater secret, experiment id). Re-uses the rater identified by `secret` so the same
    anonymous person is tracked across experiments."""
    with engine.begin() as conn:
        invite = conn.execute(select(db.invites).where(db.invites.c.token == token)).first()
        if not invite:
            raise Rejected('邀请链接无效')
        exp = get_experiment(conn, invite.experiment_id)
        if exp.status != 'live':
            raise Rejected('该实验当前未开放')
        rater = conn.execute(select(db.raters).where(db.raters.c.secret == secret)).first() if secret else None
        if rater:
            existing = conn.execute(select(db.enrollments).where(
                db.enrollments.c.rater_id == rater.id, db.enrollments.c.experiment_id == exp.id)).first()
            if existing:
                return rater.secret, exp.id
        if invite.uses >= invite.max_uses:
            raise Rejected('邀请链接已用完')
        if not rater:
            secret = secrets.token_urlsafe(24)
            rater_id = conn.execute(db.raters.insert().values(
                anon_id='R' + secrets.token_hex(4).upper(), secret=secret, created_at=db.now(),
                external_source=(external_source or None) and external_source[:64],
                external_pid=(external_pid or None) and external_pid[:128])).inserted_primary_key[0]
        else:
            rater_id, secret = rater.id, rater.secret
        config = config_of(exp)
        arm = invite.arm or _balanced_arm(conn, exp.id, invite.tier, [a.id for a in config.arms])
        conn.execute(db.enrollments.insert().values(
            rater_id=rater_id, experiment_id=exp.id, tier=invite.tier, arm=arm, invite_token=token,
            display_passed=False, practice_done=False, created_at=db.now()))
        conn.execute(update(db.invites).where(db.invites.c.token == token).values(uses=db.invites.c.uses + 1))
    return secret, exp.id


class Ctx:
    def __init__(self, conn, secret, experiment_id, session_id=None):
        self.conn = conn
        self.rater = conn.execute(select(db.raters).where(db.raters.c.secret == secret)).first() if secret else None
        if not self.rater:
            raise Rejected('身份无效，请使用邀请链接或个人链接进入')
        self.exp = get_experiment(conn, experiment_id)
        self.config = config_of(self.exp)
        self.enrollment = conn.execute(select(db.enrollments).where(
            db.enrollments.c.rater_id == self.rater.id, db.enrollments.c.experiment_id == experiment_id)).first()
        if not self.enrollment:
            raise Rejected('你未参加该实验')
        self.session_id = None
        if session_id:
            if not SESSION_ID.match(session_id):
                raise Rejected('会话标识无效')
            self.session_id = session_id
            row = conn.execute(select(db.sessions).where(db.sessions.c.id == session_id)).first()
            if row and row.enrollment_id != self.enrollment.id:
                raise Rejected('会话标识冲突')
            if row:
                conn.execute(update(db.sessions).where(db.sessions.c.id == session_id).values(last_seen=db.now()))
            else:
                conn.execute(db.sessions.insert().values(id=session_id, enrollment_id=self.enrollment.id,
                                                         started_at=db.now(), last_seen=db.now()))

    def reload(self):
        self.enrollment = self.conn.execute(select(db.enrollments).where(
            db.enrollments.c.id == self.enrollment.id)).first()
        self.rater = self.conn.execute(select(db.raters).where(db.raters.c.id == self.rater.id)).first()

    def event(self, type_, payload, assignment_id=None, rt_ms=None, client_ts=None):
        return self.conn.execute(db.events.insert().values(
            enrollment_id=self.enrollment.id, session_id=self.session_id, assignment_id=assignment_id,
            type=type_, payload=payload, rt_ms=rt_ms, client_ts=client_ts, server_ts=db.now())).inserted_primary_key[0]


# ---------------------------------------------------------------- response bookkeeping

def active_responses(conn, assignment_ids=None, enrollment_id=None):
    """{assignment_id: event row} of the latest response per assignment not cancelled by an undo."""
    e = db.events
    q = select(e).where(e.c.type.in_(('response', 'undo'))).order_by(e.c.id)
    if enrollment_id is not None:
        q = q.where(e.c.enrollment_id == enrollment_id)
    if assignment_ids is not None:
        q = q.where(e.c.assignment_id.in_(list(assignment_ids)))
    out = {}
    for row in conn.execute(q):
        if row.type == 'response':
            out[row.assignment_id] = row
        elif out.get(row.assignment_id) is not None and out[row.assignment_id].id == row.payload.get('target_event'):
            out.pop(row.assignment_id)
    return out


def _current_block(ctx):
    return ctx.conn.execute(select(db.blocks).where(db.blocks.c.enrollment_id == ctx.enrollment.id,
                                                    db.blocks.c.completed_at.is_(None))
                            .order_by(db.blocks.c.seq.desc())).first()


def _block_rows(ctx, block_id):
    return ctx.conn.execute(select(db.assignments).where(db.assignments.c.block_id == block_id)
                            .order_by(db.assignments.c.position)).all()


# ---------------------------------------------------------------- display check

def evaluate_display(payload, config, tier):
    d, reasons = config.display, []
    dpr = payload.get('dpr')
    if not isinstance(dpr, (int, float)) or not any(abs(dpr - x) < 0.01 for x in d.allowed_dpr):
        reasons.append(f'设备像素比 {dpr} 不在允许范围 {d.allowed_dpr}；请把系统/浏览器缩放调回默认')
    scale = payload.get('visual_viewport_scale')
    if isinstance(scale, (int, float)) and abs(scale - 1) > 0.01:
        reasons.append('页面处于双指缩放状态；请恢复 100%')
    if tier in d.pixel_test_required and payload.get('pixel_test') != 'uniform':
        reasons.append('像素条纹测试未通过：屏幕正在缩放显示，图片会被重采样')
    if int(payload.get('black_steps_seen', 0)) < d.min_black_steps:
        reasons.append(f'近黑台阶只看见 {payload.get("black_steps_seen")} 级（至少 {d.min_black_steps}）')
    if int(payload.get('white_steps_seen', 0)) < d.min_white_steps:
        reasons.append(f'近白台阶只看见 {payload.get("white_steps_seen")} 级（至少 {d.min_white_steps}）')
    # Lab screens are calibrated by the experimenter: record problems but do not block.
    return (not reasons or tier == 'lab'), reasons


# ---------------------------------------------------------------- state

def state(engine, secret, experiment_id, session_id):
    with engine.begin() as conn:
        ctx = Ctx(conn, secret, experiment_id, session_id)
        return _state(ctx)


def _state(ctx):
    c, e = ctx.config, ctx.enrollment
    base = dict(experiment=dict(id=ctx.exp.id, title=c.title, template=c.template, status=ctx.exp.status),
                rater=dict(anon_id=ctx.rater.anon_id), tier=e.tier, completion_code=e.completion_code)
    if e.consent_version != c.consent.version:
        return dict(base, stage='consent', consent=dict(version=c.consent.version, text=c.consent.text))
    if not ctx.rater.profile:
        return dict(base, stage='profile', options=dict(age_band=AGE_BANDS, experience=EXPERIENCE,
                                                         color_vision=COLOR_VISION))
    passed = ctx.conn.execute(select(db.display_checks.c.id).where(
        db.display_checks.c.enrollment_id == e.id, db.display_checks.c.session_id == ctx.session_id,
        db.display_checks.c.passed.is_(True))).first()
    if not passed:
        return dict(base, stage='display', display=dict(
            allowed_dpr=c.display.allowed_dpr, pixel_test=e.tier in c.display.pixel_test_required))
    block = _current_block(ctx)
    if block is None and not e.practice_done and _has_practice(ctx):
        return dict(base, stage='practice_intro')
    if block is None:
        if ctx.exp.status != 'live':
            return dict(base, stage='closed')
        done = ctx.conn.execute(select(func.count()).where(db.blocks.c.enrollment_id == e.id,
                                                           db.blocks.c.kind == 'main')).scalar()
        return dict(base, stage='ready', blocks_done=done, block_size=c.block_size)
    return dict(base, stage='task', **_task(ctx, block))


def _has_practice(ctx):
    return ctx.conn.execute(select(db.items.c.id).where(db.items.c.experiment_id == ctx.exp.id,
                                                        db.items.c.kind == 'practice')).first() is not None


def _question(ctx, role):
    if role == 'followup':
        return FOLLOWUP_QUESTION
    return next(a.question for a in ctx.config.arms if a.id == ctx.enrollment.arm)


def _images(ctx, row):
    c = ctx.config
    sizes = [('main', c.display.css_long_edge)] + ([('zoom', c.display.zoom_css_long_edge)]
                                                  if c.display.zoom_css_long_edge else [])
    out = []
    for slot, sha in enumerate(row.layout):
        entry = dict(slot=slot)
        for name, css in sizes:
            d = ctx.conn.execute(select(db.derivatives).where(
                db.derivatives.c.stimulus_sha == sha, db.derivatives.c.css_long_edge == css,
                db.derivatives.c.scale == '1')).first()
            entry[name] = dict(css_width=d.width, css_height=d.height,
                               src={s: f'/api/media/{row.token}/{slot}/{name}/{s}' for s in ('1', '1.5', '2')})
        out.append(entry)
    return out


def _task(ctx, block):
    rows = _block_rows(ctx, block.id)
    current = next((r for r in rows if r.status != 'answered'), None)
    answered = sum(r.status == 'answered' for r in rows)
    in_followup = any(r.role == 'followup' for r in rows)
    number = ctx.conn.execute(select(func.count()).where(
        db.blocks.c.enrollment_id == ctx.enrollment.id, db.blocks.c.kind == 'main',
        db.blocks.c.seq <= block.seq)).scalar()  # 1-based ordinal among main blocks
    progress = dict(seq=block.seq, number=number, kind=block.kind, answered=answered, total=len(rows),
                    can_undo=answered > 0 and not (in_followup and rows[answered - 1].role != 'followup'))
    role = current.role if current.role in SAFE_ROLES else 'main'
    task = dict(token=current.token, role=role, template=ctx.config.template, question=_question(ctx, current.role),
                images=_images(ctx, current))
    if role == 'followup':
        task.update(template='followup', choices=FOLLOWUP_SOURCES)
    elif ctx.config.template == 'pair':
        task['choices'] = PAIR_CHOICES[ctx.config.response_scale]
    elif ctx.config.template == 'region':
        seg = ctx.conn.execute(select(db.segmentations).where(
            db.segmentations.c.stimulus_sha == current.layout[0])).first()
        rc = ctx.config.region
        task.update(levels=rc.overall_levels, max_regions=rc.max_regions, low_label=rc.low_label,
                    high_label=rc.high_label, region_types=REGION_TYPES, label_map=f'/api/labelmap/{current.token}',
                    regions=[r['id'] for r in seg.regions])
    return dict(block=progress, task=task)


# ---------------------------------------------------------------- actions

def submit_consent(engine, secret, experiment_id, session_id, version):
    with engine.begin() as conn:
        ctx = Ctx(conn, secret, experiment_id, session_id)
        if version != ctx.config.consent.version:
            raise Rejected('知情同意书版本不符，请刷新页面')
        ctx.event('consent', dict(version=version))
        conn.execute(update(db.enrollments).where(db.enrollments.c.id == ctx.enrollment.id)
                     .values(consent_version=version))
        ctx.reload()
        return _state(ctx)


def submit_profile(engine, secret, experiment_id, session_id, profile):
    rules = dict(age_band=AGE_BANDS, photo_experience=EXPERIENCE, retouch_experience=EXPERIENCE,
                 color_vision=COLOR_VISION)
    clean = {}
    for key, allowed in rules.items():
        if profile.get(key) not in allowed:
            raise Rejected(f'请填写 {key}')
        clean[key] = profile[key]
    with engine.begin() as conn:
        ctx = Ctx(conn, secret, experiment_id, session_id)
        ctx.event('profile', clean)
        conn.execute(update(db.raters).where(db.raters.c.id == ctx.rater.id).values(profile=clean))
        ctx.reload()
        return _state(ctx)


def submit_display_check(engine, secret, experiment_id, session_id, payload, user_agent=None):
    if not isinstance(payload, dict):
        raise Rejected('显示检查数据无效')
    with engine.begin() as conn:
        ctx = Ctx(conn, secret, experiment_id, session_id)
        if not ctx.session_id:
            raise Rejected('缺少会话标识')
        passed, reasons = evaluate_display(payload, ctx.config, ctx.enrollment.tier)
        record = dict(payload, user_agent=(user_agent or '')[:300])
        conn.execute(db.display_checks.insert().values(
            enrollment_id=ctx.enrollment.id, session_id=ctx.session_id, payload=record, passed=passed,
            reasons=reasons, created_at=db.now()))
        ctx.event('display_check', dict(passed=passed, reasons=reasons))
        if passed:
            conn.execute(update(db.enrollments).where(db.enrollments.c.id == ctx.enrollment.id)
                         .values(display_passed=True))
        ctx.reload()
        return dict(_state(ctx), display_result=dict(passed=passed, reasons=reasons))


def start_block(engine, secret, experiment_id, session_id):
    with PLAN_LOCK, engine.begin() as conn:
        ctx = Ctx(conn, secret, experiment_id, session_id)
        st = _state(ctx)
        if st['stage'] not in ('ready', 'practice_intro'):
            return st
        kind = 'practice' if st['stage'] == 'practice_intro' else 'main'
        model = None
        if kind == 'main' and ctx.config.scheduler.strategy == 'core_then_active':
            from easylabel.scheduler.active import active_model  # imports export → participation
            items = conn.execute(select(db.items).where(db.items.c.experiment_id == ctx.exp.id)).all()
            model = active_model(conn, ctx.exp, ctx.enrollment.arm, ctx.config, items)
        block_id = plan_block(conn, ctx.exp, ctx.config, ctx.enrollment, kind, model)
        if block_id is None:
            if kind == 'practice':
                conn.execute(update(db.enrollments).where(db.enrollments.c.id == ctx.enrollment.id)
                             .values(practice_done=True))
                ctx.reload()
                return _state(ctx)
            return dict(st, stage='finished')
        conn.execute(update(db.blocks).where(db.blocks.c.id == block_id).values(session_id=ctx.session_id))
        ctx.event('block_start', dict(block_id=block_id, kind=kind))
        return _state(ctx)


def _validate(ctx, row, payload):
    if not isinstance(payload, dict):
        raise Rejected('作答格式无效')
    slots = range(len(row.layout))
    if row.role == 'followup':
        source = payload.get('source')
        if source not in FOLLOWUP_SOURCES:
            raise Rejected('请选择差别来源')
        text = (payload.get('other_text') or '').strip()[:500]
        if source == 'other' and not text:
            raise Rejected('选择“其他”时请写明')
        return dict(source=source, other_text=text or None)
    t = ctx.config.template
    if t == 'pair':
        if payload.get('choice') not in PAIR_CHOICES[ctx.config.response_scale]:
            raise Rejected('请选择左、差不多或右')
        return dict(choice=payload['choice'])
    if t == 'grid':
        accepted = payload.get('accepted') or []
        none, best = bool(payload.get('none')), payload.get('best')
        if not isinstance(accepted, list) or any(s not in slots for s in accepted) or len(set(accepted)) != len(accepted):
            raise Rejected('选择无效')
        if none == bool(accepted):
            raise Rejected('请勾选达到应有样子的版本，或选择“都没有”')
        if best not in slots or (accepted and best not in accepted):
            raise Rejected('请再挑出最好的一张' + ('（须在已勾选的版本中）' if accepted else ''))
        return dict(accepted=sorted(accepted), none=none, best=best)
    # region
    overall = payload.get('overall')
    if overall not in range(1, ctx.config.region.overall_levels + 1):
        raise Rejected('请先给整图打分')
    seg = ctx.conn.execute(select(db.segmentations).where(db.segmentations.c.stimulus_sha == row.layout[0])).first()
    valid = {r['id'] for r in seg.regions} | {'global'}
    marks = payload.get('regions') or []
    if not isinstance(marks, list) or len(marks) > ctx.config.region.max_regions:
        raise Rejected(f'最多标 {ctx.config.region.max_regions} 块')
    if not marks:
        raise Rejected('请至少点选一块，或选择“全局 / 说不清位置”')
    clean, used = [], set()
    for m in marks:
        if not isinstance(m, dict) or m.get('region') not in valid or m.get('region') in used:
            raise Rejected('区域无效或重复')
        if m.get('type') not in REGION_TYPES:
            raise Rejected('请为每块选择类型')
        if m.get('type') != 'unsure' and m.get('direction') not in ('down', 'up'):
            raise Rejected('请为每块选择方向')
        text = (m.get('other_text') or '').strip()[:500]
        if m['type'] == 'other' and not text:
            raise Rejected('选择“其他”时请写明')
        used.add(m['region'])
        clean.append(dict(region=m['region'], type=m['type'], direction=m.get('direction'), other_text=text or None))
    return dict(overall=overall, regions=clean)


def canonical_pair(layout, stimuli, choice):
    """Map a displayed choice to canonical terms: 'a' / 'b' are item.stimuli[0] / [1]."""
    if choice == 'tie':
        return 'tie'
    left = choice.startswith('left')
    picked = layout[0] if left else layout[1]
    return 'a' if picked == stimuli[0] else 'b'


def answer(engine, secret, experiment_id, session_id, token, payload, rt_ms=None, client_ts=None, env=None):
    with engine.begin() as conn:
        ctx = Ctx(conn, secret, experiment_id, session_id)
        row = conn.execute(select(db.assignments).where(db.assignments.c.token == token)).first()
        if not row or row.enrollment_id != ctx.enrollment.id:
            raise Rejected('题目不存在')
        block = conn.execute(select(db.blocks).where(db.blocks.c.id == row.block_id)).first()
        rows = _block_rows(ctx, block.id)
        current = next((r for r in rows if r.status != 'answered'), None)
        if block.completed_at or current is None or current.id != row.id:
            raise Rejected('这道题已作答或不是当前题；页面将刷新')
        clean = _validate(ctx, row, payload)
        if env is not None and not isinstance(env, dict):
            env = None
        rt = int(rt_ms) if isinstance(rt_ms, (int, float)) and 0 <= rt_ms < 3_600_000 else None
        ctx.event('response', dict(clean, env=env), assignment_id=row.id, rt_ms=rt,
                  client_ts=str(client_ts)[:40] if client_ts else None)
        conn.execute(update(db.assignments).where(db.assignments.c.id == row.id).values(status='answered'))
        refresh_reservations(conn, block.id, ctx.config.scheduler.reservation_minutes)
        feedback = None
        if row.role == 'practice':
            item = conn.execute(select(db.items).where(db.items.c.id == row.item_id)).first()
            if item.expected and ctx.config.template == 'pair':
                got = canonical_pair(row.layout, item.stimuli, clean['choice'])
                expected_side = 'tie' if item.expected == 'tie' else (
                    'left' if (row.layout[0] == item.stimuli[0]) == (item.expected == 'a') else 'right')
                feedback = dict(correct=got == item.expected, expected=expected_side)
        _maybe_finish_block(ctx, block)
        ctx.reload()
        return dict(_state(ctx), feedback=feedback)


def _maybe_finish_block(ctx, block):
    rows = _block_rows(ctx, block.id)
    if any(r.status != 'answered' for r in rows):
        return
    if block.kind == 'main' and ctx.config.followup.enabled and not any(r.role == 'followup' for r in rows):
        responses = {k: v.payload for k, v in active_responses(ctx.conn, [r.id for r in rows]).items()}
        if append_followups(ctx.conn, block, ctx.config, responses):
            return
    ctx.conn.execute(update(db.blocks).where(db.blocks.c.id == block.id).values(completed_at=db.now()))
    ctx.event('block_end', dict(block_id=block.id))
    values = {}
    if block.kind == 'practice':
        values['practice_done'] = True
    elif not ctx.enrollment.completion_code:
        values['completion_code'] = secrets.token_hex(4).upper()
    if values:
        ctx.conn.execute(update(db.enrollments).where(db.enrollments.c.id == ctx.enrollment.id).values(**values))


def undo(engine, secret, experiment_id, session_id):
    with engine.begin() as conn:
        ctx = Ctx(conn, secret, experiment_id, session_id)
        block = _current_block(ctx)
        if not block:
            raise Rejected('题块已结束，不能撤回')
        rows = _block_rows(ctx, block.id)
        answered = [r for r in rows if r.status == 'answered']
        if not answered:
            raise Rejected('没有可撤回的作答')
        last = answered[-1]
        if last.role != 'followup' and any(r.role == 'followup' for r in rows):
            raise Rejected('追问阶段不能撤回正式题')
        response = active_responses(conn, [last.id])[last.id]
        ctx.event('undo', dict(target_event=response.id), assignment_id=last.id)
        conn.execute(update(db.assignments).where(db.assignments.c.id == last.id).values(status='reserved'))
        refresh_reservations(conn, block.id, ctx.config.scheduler.reservation_minutes)
        return _state(ctx)


def log_event(engine, secret, experiment_id, session_id, type_, payload, token=None):
    """Non-response events from the client: note, pause, resume, zoom, viewport_blocked."""
    if type_ not in ('note', 'pause', 'resume', 'zoom', 'viewport_blocked'):
        raise Rejected('未知事件')
    with engine.begin() as conn:
        ctx = Ctx(conn, secret, experiment_id, session_id)
        assignment_id = None
        if token:
            row = conn.execute(select(db.assignments).where(db.assignments.c.token == token)).first()
            if not row or row.enrollment_id != ctx.enrollment.id:
                raise Rejected('题目不存在')
            assignment_id = row.id
        clean = payload if isinstance(payload, dict) else {}
        if type_ == 'note':
            clean = dict(text=str(clean.get('text', '')).strip()[:2000])
        ctx.event(type_, clean, assignment_id=assignment_id)
        return dict(ok=True)


def label_map_path(engine, secret, token):
    """Label map of a region-template assignment, only for the rater who owns it."""
    with engine.connect() as conn:
        rater = conn.execute(select(db.raters).where(db.raters.c.secret == secret)).first() if secret else None
        row = conn.execute(select(db.assignments, db.enrollments.c.rater_id)
                           .join(db.enrollments, db.enrollments.c.id == db.assignments.c.enrollment_id)
                           .where(db.assignments.c.token == token)).first()
        if not rater or not row or row.rater_id != rater.id:
            raise Rejected('分割图不存在')
        seg = conn.execute(select(db.segmentations).where(db.segmentations.c.stimulus_sha == row.layout[0])).first()
        if not seg:
            raise Rejected('分割图不存在')
        return seg.label_map_path


def media_path(engine, secret, token, slot, size):
    """Resolve an opaque image URL to a derivative, only for the rater who owns the assignment."""
    with engine.connect() as conn:
        rater = conn.execute(select(db.raters).where(db.raters.c.secret == secret)).first() if secret else None
        row = conn.execute(select(db.assignments, db.enrollments.c.rater_id, db.enrollments.c.experiment_id)
                           .join(db.enrollments, db.enrollments.c.id == db.assignments.c.enrollment_id)
                           .where(db.assignments.c.token == token)).first()
        if not rater or not row or row.rater_id != rater.id or not 0 <= slot < len(row.layout):
            raise Rejected('图片不存在')
        return row.layout[slot], config_of(get_experiment(conn, row.experiment_id))
