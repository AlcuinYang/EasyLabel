"""HTTP service: rater API (cookie identity, opaque media URLs) and admin API (bearer token).
The built frontend is served from server/static/{rater,admin}."""
import io
import secrets
import tempfile
import zipfile
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import func, select

from easylabel.core import db, participation as P
from easylabel.core.experiments import config_of, freeze, get_experiment, set_status
from easylabel.core.export import export_experiment, response_rows
from easylabel_analysis.qc import rater_qc

STATIC = Path(__file__).resolve().parent / 'static'
COOKIE = 'el_rater'
SIZES = ('main', 'zoom')


def create_app(data_dir, admin_token):
    data_dir = Path(data_dir).resolve()
    engine = db.open_engine(data_dir, create=False)
    media = data_dir / 'media'
    app = FastAPI(title='EasyLabel', docs_url='/api/docs', openapi_url='/api/openapi.json', redoc_url=None)

    @app.exception_handler(P.Rejected)
    async def _rejected(_, exc):
        return JSONResponse({'error': str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def _missing(_, exc):
        return JSONResponse({'error': str(exc.args[0]) if exc.args else '不存在'}, status_code=404)

    @app.exception_handler(ValueError)
    async def _invalid(_, exc):
        return JSONResponse({'error': str(exc)}, status_code=409)

    # ------------------------------------------------------------ rater entry points

    def _set_cookie(resp, secret):
        resp.set_cookie(COOKIE, secret, max_age=3600 * 24 * 365, httponly=True, samesite='lax',
                        secure=False)  # TLS is terminated by the reverse proxy in deployment
        return resp

    @app.get('/i/{token}')
    def invite(token: str, request: Request, src: str = None, pid: str = None):
        try:
            secret, exp_id = P.join(engine, token, request.cookies.get(COOKIE), src, pid)
        except P.Rejected as e:
            return _page_message(str(e))
        return _set_cookie(RedirectResponse(f'/x/{exp_id}', status_code=303), secret)

    @app.get('/r/{secret}')
    def personal(secret: str):
        with engine.connect() as conn:
            rater = conn.execute(select(db.raters).where(db.raters.c.secret == secret)).first()
        if not rater:
            return _page_message('个人链接无效')
        return _set_cookie(RedirectResponse('/me', status_code=303), secret)

    @app.get('/api/me')
    def me(request: Request):
        secret = request.cookies.get(COOKIE)
        with engine.connect() as conn:
            rater = conn.execute(select(db.raters).where(db.raters.c.secret == secret)).first() if secret else None
            if not rater:
                raise P.Rejected('身份无效，请使用邀请链接或个人链接进入')
            rows = conn.execute(select(db.enrollments, db.experiments.c.status, db.experiments.c.config)
                                .join(db.experiments, db.experiments.c.id == db.enrollments.c.experiment_id)
                                .where(db.enrollments.c.rater_id == rater.id)).all()
        return dict(anon_id=rater.anon_id, personal_link=f'/r/{rater.secret}',
                    experiments=[dict(id=r.experiment_id, title=r.config['title'], status=r.status,
                                      completion_code=r.completion_code) for r in rows])

    def rater(request: Request, x_session: str = Header(default=None)):
        return request.cookies.get(COOKIE), x_session

    @app.get('/api/x/{exp_id}/state')
    def state(exp_id: str, who=Depends(rater)):
        return P.state(engine, who[0], exp_id, who[1])

    @app.post('/api/x/{exp_id}/consent')
    def consent(exp_id: str, body: dict = Body(...), who=Depends(rater)):
        return P.submit_consent(engine, who[0], exp_id, who[1], body.get('version'))

    @app.post('/api/x/{exp_id}/profile')
    def profile(exp_id: str, body: dict = Body(...), who=Depends(rater)):
        return P.submit_profile(engine, who[0], exp_id, who[1], body)

    @app.post('/api/x/{exp_id}/display-check')
    def display_check(exp_id: str, request: Request, body: dict = Body(...), who=Depends(rater)):
        return P.submit_display_check(engine, who[0], exp_id, who[1], body, request.headers.get('user-agent'))

    @app.post('/api/x/{exp_id}/block/start')
    def block_start(exp_id: str, who=Depends(rater)):
        return P.start_block(engine, who[0], exp_id, who[1])

    @app.post('/api/x/{exp_id}/answer')
    def answer(exp_id: str, body: dict = Body(...), who=Depends(rater)):
        return P.answer(engine, who[0], exp_id, who[1], body.get('token'), body.get('payload'),
                        body.get('rt_ms'), body.get('client_ts'), body.get('env'))

    @app.post('/api/x/{exp_id}/undo')
    def undo(exp_id: str, who=Depends(rater)):
        return P.undo(engine, who[0], exp_id, who[1])

    @app.post('/api/x/{exp_id}/event')
    def event(exp_id: str, body: dict = Body(...), who=Depends(rater)):
        return P.log_event(engine, who[0], exp_id, who[1], body.get('type'), body.get('payload'), body.get('token'))

    def _derivative_file(sha, config, size, scale):
        css = config.display.css_long_edge if size == 'main' else config.display.zoom_css_long_edge
        if size not in SIZES or scale not in ('1', '1.5', '2') or not css:
            raise P.Rejected('图片不存在')
        with engine.connect() as conn:
            d = conn.execute(select(db.derivatives).where(
                db.derivatives.c.stimulus_sha == sha, db.derivatives.c.css_long_edge == css,
                db.derivatives.c.scale == scale)).first()
        if not d:
            raise P.Rejected('图片不存在')
        # No filename, no metadata: just bytes. Private caching keeps repeat views consistent.
        return FileResponse(media / d.path, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=86400'})

    @app.get('/api/media/{token}/{slot}/{size}/{scale}')
    def media_file(token: str, slot: int, size: str, scale: str, request: Request):
        sha, config = P.media_path(engine, request.cookies.get(COOKIE), token, slot, size)
        return _derivative_file(sha, config, size, scale)

    @app.get('/api/labelmap/{token}')
    def label_map(token: str, request: Request):
        rel = P.label_map_path(engine, request.cookies.get(COOKIE), token)
        return FileResponse(media / rel, media_type='image/png', headers={'Cache-Control': 'private, max-age=86400'})

    # ------------------------------------------------------------ admin

    def admin(authorization: str = Header(default=None), request: Request = None):
        supplied = (authorization or '').removeprefix('Bearer ').strip() or request.cookies.get('el_admin', '')
        if not supplied or not secrets.compare_digest(supplied, admin_token):
            raise HTTPException(401, '需要管理员令牌')

    @app.get('/admin/login')
    def admin_login(token: str):
        if not secrets.compare_digest(token, admin_token):
            return _page_message('管理员令牌无效')
        resp = RedirectResponse('/admin', status_code=303)
        resp.set_cookie('el_admin', token, httponly=True, samesite='strict', max_age=3600 * 12)
        return resp

    @app.get('/api/admin/experiments', dependencies=[Depends(admin)])
    def experiments():
        with engine.connect() as conn:
            exps = conn.execute(select(db.experiments).order_by(db.experiments.c.created_at.desc())).all()
            counts = dict(conn.execute(select(db.enrollments.c.experiment_id, func.count())
                                       .group_by(db.enrollments.c.experiment_id)).all())
            answered = dict(conn.execute(
                select(db.enrollments.c.experiment_id, func.count())
                .join(db.assignments, db.assignments.c.enrollment_id == db.enrollments.c.id)
                .where(db.assignments.c.status == 'answered').group_by(db.enrollments.c.experiment_id)).all())
        return [dict(id=e.id, title=e.config['title'], template=e.template, status=e.status,
                     config_hash=e.config_hash, raters=counts.get(e.id, 0), answered=answered.get(e.id, 0),
                     created_at=e.created_at.isoformat()) for e in exps]

    @app.get('/api/admin/experiments/{exp_id}', dependencies=[Depends(admin)])
    def experiment(exp_id: str):
        with engine.connect() as conn:
            e = get_experiment(conn, exp_id)
            kinds = dict(conn.execute(select(db.items.c.kind, func.count()).where(db.items.c.experiment_id == exp_id)
                                      .group_by(db.items.c.kind)).all())
            invites = conn.execute(select(db.invites).where(db.invites.c.experiment_id == exp_id)
                                   .order_by(db.invites.c.created_at)).all()
        return dict(id=e.id, status=e.status, template=e.template, config=e.config, config_hash=e.config_hash,
                    code_version=e.code_version, items=kinds,
                    invites=[dict(url=f'/i/{i.token}', tier=i.tier, arm=i.arm, uses=i.uses, max_uses=i.max_uses,
                                  note=i.note) for i in invites])

    @app.get('/api/admin/experiments/{exp_id}/progress', dependencies=[Depends(admin)])
    def progress(exp_id: str):
        """Scene × judgement-count heatmap data: per item answered count (normal role)."""
        with engine.connect() as conn:
            exp = get_experiment(conn, exp_id)
            config = config_of(exp)
            items = conn.execute(select(db.items).where(db.items.c.experiment_id == exp_id,
                                                        db.items.c.kind == 'main')).all()
            counts = dict(conn.execute(select(db.assignments.c.item_id, func.count())
                                       .join(db.items, db.items.c.id == db.assignments.c.item_id)
                                       .where(db.items.c.experiment_id == exp_id, db.assignments.c.role == 'normal',
                                              db.assignments.c.status == 'answered')
                                       .group_by(db.assignments.c.item_id)).all())
        scenes = {}
        for it in items:
            s = scenes.setdefault(it.scene_id, dict(scene_id=it.scene_id, items=0, core=0, counts=[]))
            s['items'] += 1
            s['core'] += bool(it.is_core)
            s['counts'].append(counts.get(it.id, 0))
        rows = [dict(s, min=min(s['counts']), mean=sum(s['counts']) / len(s['counts']),
                     at_target=sum(c >= config.target_per_item for c in s['counts'])) for s in scenes.values()]
        return dict(target=config.target_per_item, scenes=sorted(rows, key=lambda r: r['scene_id']),
                    total_items=len(items), items_at_target=sum(r['at_target'] for r in rows))

    @app.get('/api/admin/experiments/{exp_id}/raters', dependencies=[Depends(admin)])
    def raters(exp_id: str):
        with engine.connect() as conn:
            exp = get_experiment(conn, exp_id)
            rows = response_rows(conn, exp_id)
            checks = conn.execute(select(db.display_checks, db.raters.c.anon_id)
                                  .join(db.enrollments, db.enrollments.c.id == db.display_checks.c.enrollment_id)
                                  .join(db.raters, db.raters.c.id == db.enrollments.c.rater_id)
                                  .where(db.enrollments.c.experiment_id == exp_id)
                                  .order_by(db.display_checks.c.id)).all()
        qc = rater_qc(rows, exp.config['exclusion'])
        latest = {}
        for c in checks:
            latest[c.anon_id] = dict(passed=c.passed, reasons=c.reasons, dpr=c.payload.get('dpr'),
                                     screen=c.payload.get('screen'), pixel_test=c.payload.get('pixel_test'),
                                     black=c.payload.get('black_steps_seen'), white=c.payload.get('white_steps_seen'))
        return [dict(rater=k, display=latest.get(k), **v) for k, v in qc.items()]

    @app.post('/api/admin/experiments/{exp_id}/invites', dependencies=[Depends(admin)])
    def make_invites(exp_id: str, body: dict = Body(...)):
        tokens = P.create_invites(engine, exp_id, body.get('tier', 'remote'), int(body.get('count', 1)),
                                  body.get('arm') or None, int(body.get('max_uses', 1)), body.get('note'))
        return [f'/i/{t}' for t in tokens]

    # Generic lifecycle route last: it would otherwise capture /invites.
    @app.post('/api/admin/experiments/{exp_id}/{action}', dependencies=[Depends(admin)])
    def transition(exp_id: str, action: str):
        if action == 'freeze':
            return dict(config_hash=freeze(engine, exp_id))
        if action in ('publish', 'close'):
            set_status(engine, exp_id, 'live' if action == 'publish' else 'closed')
            return dict(ok=True)
        raise HTTPException(404, '未知操作')

    @app.get('/api/admin/experiments/{exp_id}/export.zip', dependencies=[Depends(admin)])
    def export(exp_id: str):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / exp_id
            export_experiment(engine, exp_id, out)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as z:
                for p in sorted(out.iterdir()):
                    z.write(p, f'{exp_id}/{p.name}')
        return Response(buffer.getvalue(), media_type='application/zip',
                        headers={'Content-Disposition': f'attachment; filename="{exp_id}-export.zip"'})

    @app.get('/api/admin/experiments/{exp_id}/preview', dependencies=[Depends(admin)])
    def preview(exp_id: str, n: int = 6):
        """A few items rendered as the rater would see them; nothing is recorded."""
        with engine.connect() as conn:
            exp = get_experiment(conn, exp_id)
            config = config_of(exp)
            items = conn.execute(select(db.items).where(db.items.c.experiment_id == exp_id)
                                 .order_by(db.items.c.kind, db.items.c.item_key).limit(max(1, min(n, 50)))).all()
            out = []
            for it in items:
                images = []
                for slot, sha in enumerate(it.stimuli):
                    d = conn.execute(select(db.derivatives).where(
                        db.derivatives.c.stimulus_sha == sha,
                        db.derivatives.c.css_long_edge == config.display.css_long_edge,
                        db.derivatives.c.scale == '1')).first()
                    images.append(dict(slot=slot, main=dict(css_width=d.width, css_height=d.height, src={
                        s: f'/api/admin/media/{exp_id}/{sha}/main/{s}' for s in ('1', '1.5', '2')})))
                out.append(dict(item_key=it.item_key, kind=it.kind, scene_id=it.scene_id, images=images))
        return dict(template=config.template, arms=[a.model_dump() for a in config.arms],
                    choices=P.PAIR_CHOICES.get(config.response_scale), items=out)

    @app.get('/api/admin/media/{exp_id}/{sha}/{size}/{scale}', dependencies=[Depends(admin)])
    def admin_media(exp_id: str, sha: str, size: str, scale: str):
        with engine.connect() as conn:
            config = config_of(get_experiment(conn, exp_id))
        return _derivative_file(sha, config, size, scale)

    # ------------------------------------------------------------ frontend

    def _spa(name):
        index = STATIC / name / 'index.html'
        if not index.exists():
            return _page_message('前端尚未构建：在 web/ 中运行 npm run build')
        return FileResponse(index, headers={'Cache-Control': 'no-store'})

    @app.get('/x/{exp_id}')
    def rater_page(exp_id: str):
        return _spa('rater')

    @app.get('/me')
    def me_page():
        return _spa('rater')

    @app.get('/admin')
    def admin_page():
        return _spa('admin')

    @app.get('/assets/{path:path}')
    def assets(path: str):
        target = (STATIC / 'assets' / path).resolve()
        if not target.is_file() or (STATIC / 'assets').resolve() not in target.parents:
            raise HTTPException(404)
        return FileResponse(target, headers={'Cache-Control': 'public, max-age=31536000, immutable'})

    return app


def _page_message(text):
    html = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<body style="background:#808080;color:#111;font:16px system-ui;display:grid;place-items:center;'
            f'height:100vh;margin:0"><p>{_escape(text)}</p>')
    return Response(html, media_type='text/html; charset=utf-8', status_code=400)


def _escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
