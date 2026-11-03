"""End-to-end over HTTP: invite → consent → profile → display check → practice → block → export."""
import csv
import json

import pytest
from fastapi.testclient import TestClient

from easylabel.core import participation as P
from easylabel.core.export import export_experiment
from easylabel.server.app import create_app
from easylabel_analysis.io import read_export
from easylabel_analysis.qc import rater_qc
from easylabel_analysis.scenes import scene_fits

from conftest import GOOD_DISPLAY

ADMIN = 'admin-token-for-tests'


@pytest.fixture
def client(imported, pair_experiment, tmp_path):
    exp_id = pair_experiment(rep=0.1, gold=0.1, followup='true', block=20)
    app = create_app(tmp_path / 'data', ADMIN)
    return TestClient(app), exp_id, imported


def _join(c, engine, exp_id, tier='remote', **query):
    token = P.create_invites(engine, exp_id, tier, 1)[0]
    r = c.get(f'/i/{token}', params=query, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == f'/x/{exp_id}'
    return {'X-Session': 'browser-session-01'}


def test_full_rater_flow(client, tmp_path):
    c, exp_id, engine = client
    h = _join(c, engine, exp_id, src='prolific', pid='P123')
    api = f'/api/x/{exp_id}'
    st = c.get(f'{api}/state', headers=h).json()
    assert st['stage'] == 'consent'
    assert c.post(f'{api}/consent', json={'version': 'old'}, headers=h).status_code == 400
    st = c.post(f'{api}/consent', json={'version': 'v1'}, headers=h).json()
    assert st['stage'] == 'profile'
    assert c.post(f'{api}/profile', json={'age_band': '25-34'}, headers=h).status_code == 400
    st = c.post(f'{api}/profile', json=dict(age_band='25-34', photo_experience='frequent',
                                            retouch_experience='casual', color_vision='normal'), headers=h).json()
    assert st['stage'] == 'display'

    # Remote tier: fractional zoom and a resampled screen are rejected.
    bad = c.post(f'{api}/display-check', json=dict(GOOD_DISPLAY, dpr=1.25, pixel_test='banding'), headers=h).json()
    assert bad['display_result']['passed'] is False and len(bad['display_result']['reasons']) == 2
    st = c.post(f'{api}/display-check', json=GOOD_DISPLAY, headers=h).json()
    assert st['stage'] == 'practice_intro'

    st = c.post(f'{api}/block/start', headers=h).json()
    assert st['task']['role'] == 'practice'
    feedback = []
    while st['stage'] == 'task':
        st = c.post(f'{api}/answer', json=dict(token=st['task']['token'], payload={'choice': 'tie'}, rt_ms=1000),
                    headers=h).json()
        feedback.append(st['feedback'])
    assert all(f and f['correct'] is False and f['expected'] in ('left', 'right') for f in feedback)
    assert st['stage'] == 'ready'

    st = c.post(f'{api}/block/start', headers=h).json()
    task = st['task']
    assert task['role'] == 'main' and task['choices'] == ['left', 'tie', 'right']
    # No metadata reaches the client: only opaque tokens and CSS sizes.
    blob = json.dumps(st)
    assert 'sha' not in blob and '.png' not in blob and 's1' not in blob
    img = task['images'][0]['main']
    r = c.get(img['src']['2'], headers=h)
    assert r.status_code == 200 and r.headers['content-type'] == 'image/jpeg'
    assert 'content-disposition' not in r.headers
    # Another rater cannot fetch this rater's images.
    other = TestClient(c.app)
    assert other.get(img['src']['2']).status_code == 400

    # Answer, undo, answer again: the undone answer stays in the event log only.
    first = task['token']
    st = c.post(f'{api}/answer', json=dict(token=first, payload={'choice': 'left'}, rt_ms=800,
                                         env={'dpr': 2, 'inner_width': 1800}), headers=h).json()
    assert c.post(f'{api}/answer', json=dict(token=first, payload={'choice': 'left'}), headers=h).status_code == 400
    st = c.post(f'{api}/undo', headers=h).json()
    assert st['task']['token'] == first
    st = c.post(f'{api}/answer', json=dict(token=first, payload={'choice': 'right'}, rt_ms=700), headers=h).json()
    c.post(f'{api}/event', json=dict(type='note', payload={'text': '左图偏暗'}, token=st['task']['token']), headers=h)

    while st['stage'] == 'task':
        t = st['task']
        payload = {'source': 'other', 'other_text': '锐度'} if t['template'] == 'followup' else {'choice': 'left'}
        if t['template'] == 'followup':
            assert t['question'] == P.FOLLOWUP_QUESTION
            assert c.post(f'{api}/answer', json=dict(token=t['token'], payload={'source': 'other'}),
                          headers=h).status_code == 400
        st = c.post(f'{api}/answer', json=dict(token=t['token'], payload=payload, rt_ms=650), headers=h).json()
    assert st['stage'] == 'ready' and len(st['completion_code']) == 8

    me = c.get('/api/me').json()
    assert me['experiments'][0]['completion_code'] == st['completion_code']

    # Admin views.
    auth = {'Authorization': f'Bearer {ADMIN}'}
    assert c.get('/api/admin/experiments').status_code == 401
    listed = c.get('/api/admin/experiments', headers=auth).json()[0]
    assert listed['raters'] == 1 and listed['target'] == 2 and 0 < listed['done'] <= listed['items'] * 2
    assert listed['last_activity'] is not None
    summary = c.get(f'/api/admin/experiments/{exp_id}', headers=auth).json()['summary']
    assert summary['raters'] == summary['raters_started'] == 1 and summary['responses'] > 0
    assert sum(d['n'] for d in summary['daily']) == summary['responses']
    progress = c.get(f'/api/admin/experiments/{exp_id}/progress', headers=auth).json()
    assert progress['target'] == 2 and len(progress['scenes']) == 4
    cards = c.get(f'/api/admin/experiments/{exp_id}/raters', headers=auth).json()
    assert cards[0]['display']['passed'] is True and cards[0]['gold_accuracy']['n'] == 2
    zipped = c.get(f'/api/admin/experiments/{exp_id}/export.zip', headers=auth)
    assert zipped.headers['content-type'] == 'application/zip'

    # Export + offline analysis.
    out = tmp_path / 'export'
    export_experiment(engine, exp_id, out)
    experiment, rows, tables = read_export(out)
    assert experiment['config_hash'] and experiment['licenses'] == {'test-license': experiment['licenses']['test-license']}
    main = [r for r in rows if r['block_kind'] == 'main']
    first_row = next(r for r in main if r['position'] == 0)
    assert first_row['choice'] == 'right' and first_row['undo_count'] == 1
    assert {r['role'] for r in main} == {'normal', 'hidden_repeat', 'swap', 'gold', 'followup'}
    assert tables['raters'][0]['external_pid'] == 'P123'
    with (out / 'stimuli.csv').open() as f:
        assert 'source_path' not in next(csv.reader(f))
    with (out / 'events.csv').open() as f:
        types = [r['type'] for r in csv.DictReader(f)]
    assert types.count('undo') == 1 and 'note' in types and 'display_check' in types
    qc = rater_qc(rows, experiment['config']['exclusion'])
    card = next(iter(qc.values()))
    # Practice was all 'tie' (excluded from QC); gold answered 'left' on random sides → some wrong.
    assert card['n_main'] > 0 and card['self_consistency']['n'] == 2
    fits = scene_fits(rows)
    assert all(arm == 'default' for arm, _ in fits)

    # The manifest guards the export.
    (out / 'responses.csv').write_text('tampered')
    with pytest.raises(ValueError, match='被改动'):
        read_export(out)


def test_lab_tier_records_but_does_not_block(client):
    c, exp_id, engine = client
    h = _join(c, engine, exp_id, tier='lab')
    api = f'/api/x/{exp_id}'
    c.post(f'{api}/consent', json={'version': 'v1'}, headers=h)
    c.post(f'{api}/profile', json=dict(age_band='35-44', photo_experience='none', retouch_experience='none',
                                       color_vision='unsure'), headers=h)
    st = c.post(f'{api}/display-check', json=dict(GOOD_DISPLAY, dpr=1.25), headers=h).json()
    assert st['display_result']['passed'] is True and st['display_result']['reasons']


def test_closed_experiment_and_bad_invite(client):
    c, exp_id, engine = client
    assert c.get('/i/not-a-token').status_code == 400
    token = P.create_invites(engine, exp_id, 'remote', 1)[0]
    assert c.get(f'/i/{token}', follow_redirects=False).status_code == 303
    other = TestClient(c.app)
    assert other.get(f'/i/{token}', follow_redirects=False).status_code == 400  # single use


def test_admin_invites_and_lifecycle_routes(client):
    c, exp_id, _ = client
    auth = {'Authorization': f'Bearer {ADMIN}'}
    urls = c.post(f'/api/admin/experiments/{exp_id}/invites', json={'tier': 'lab', 'count': 2}, headers=auth).json()
    assert len(urls) == 2 and all(u.startswith('/i/') for u in urls)
    assert c.post(f'/api/admin/experiments/{exp_id}/freeze', headers=auth).status_code == 409  # already live
    assert c.post(f'/api/admin/experiments/{exp_id}/close', headers=auth).json() == {'ok': True}


def test_admin_session_cookie(client):
    c, _, _ = client
    assert c.get('/api/admin/session').status_code == 401
    assert c.post('/api/admin/session', json={'token': 'wrong'}).status_code == 401
    assert c.post('/api/admin/session', json={'token': ADMIN}).status_code == 200
    assert c.get('/api/admin/session').status_code == 200
    c.delete('/api/admin/session')
    assert c.get('/api/admin/session').status_code == 401
