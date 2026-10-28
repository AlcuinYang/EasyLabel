#!/usr/bin/env python3
"""Local photographic limpidity annotation. Python 3.9+, standard library only."""
import argparse
import csv
import hashlib
import io
import json
import mimetypes
import random
import secrets
import sqlite3
import tempfile
import threading
import time
import uuid
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
SPLITS = {'train', 'validation', 'test'}
REASONS = {'grey_veil': '发灰，像蒙了一层', 'dark_blocked': '暗部闷、细节挤在一起',
           'light_glare': '亮部泛白或眩光遮住细节', 'color_muddy': '颜色混浊或偏色',
           'weak_separation': '层次或前后景分不开', 'blur': '模糊、细节不清',
           'processing': '处理痕迹明显，观感不自然', 'other': '其他', 'unsure': '说不清具体原因'}


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'images').mkdir(exist_ok=True)
        self.db = self.root / 'annotations.sqlite3'
        self.lock = threading.RLock()
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS images(
                    id TEXT PRIMARY KEY, original_path TEXT NOT NULL, filename TEXT NOT NULL,
                    media TEXT NOT NULL, group_id TEXT NOT NULL, source TEXT NOT NULL,
                    split TEXT NOT NULL CHECK(split IN ('train','validation','test')),
                    imported_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS annotators(name TEXT PRIMARY KEY, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS groups(group_id TEXT PRIMARY KEY, split TEXT NOT NULL);
                INSERT OR IGNORE INTO groups SELECT group_id,split FROM images;
                CREATE TABLE IF NOT EXISTS tasks(
                    id TEXT PRIMARY KEY, annotator TEXT NOT NULL REFERENCES annotators(name),
                    image_id TEXT NOT NULL REFERENCES images(id), position INTEGER NOT NULL,
                    repeat_of TEXT REFERENCES tasks(id), status TEXT NOT NULL DEFAULT 'pending',
                    label INTEGER, uncertain INTEGER NOT NULL DEFAULT 0, duration_ms INTEGER,
                    saved_at REAL, UNIQUE(annotator,position));
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                    action TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
                INSERT OR IGNORE INTO meta VALUES('schema_version','1');
                INSERT OR IGNORE INTO meta VALUES('frozen','0');
            ''')
            columns = {r['name'] for r in c.execute('PRAGMA table_info(tasks)')}
            for name, definition in [('negative_reasons', "TEXT NOT NULL DEFAULT '[]'"),
                                     ('negative_note', "TEXT NOT NULL DEFAULT ''"),
                                     ('reason_status', "TEXT NOT NULL DEFAULT ''"),
                                     ('comment', "TEXT NOT NULL DEFAULT ''"),
                                     ('undecidable_reason', "TEXT NOT NULL DEFAULT ''")]:
                if name not in columns:
                    c.execute('ALTER TABLE tasks ADD COLUMN ' + name + ' ' + definition)
            c.execute("UPDATE tasks SET reason_status='missing' WHERE status='answered' AND label=0 AND reason_status=''")
            c.execute("UPDATE meta SET value='4' WHERE key='schema_version'")

    @contextmanager
    def connect(self):
        c = sqlite3.connect(str(self.db), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            with c:
                yield c
        finally:
            c.close()

    def status(self):
        with self.connect() as c:
            return {
                'count': c.execute('SELECT count(*) FROM images').fetchone()[0],
                'frozen': c.execute("SELECT value FROM meta WHERE key='frozen'").fetchone()[0] == '1',
                'splits': dict(c.execute('SELECT split,count(*) FROM images GROUP BY split').fetchall()),
                'annotators': [dict(r) for r in c.execute('''SELECT a.name,
                    count(t.id) AS total, sum(CASE WHEN t.status!='pending' THEN 1 ELSE 0 END) AS done
                    FROM annotators a LEFT JOIN tasks t ON a.name=t.annotator GROUP BY a.name''')],
                'data_dir': str(self.root),
            }

    def import_images(self, location, source='', grouping='file'):
        if grouping not in ('file', 'parent'):
            raise ValueError('未知母图分组方式')
        path = Path(location).expanduser().resolve()
        if not path.exists():
            raise ValueError('路径不存在，请填写本机文件夹或 CSV 的完整路径')
        entries = []
        if path.is_file() and path.suffix.lower() == '.csv':
            with path.open(encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                if not {'path', 'group_id'}.issubset(reader.fieldnames or []):
                    raise ValueError('CSV 必須包含 path、group_id；可选 source、split')
                for r in reader:
                    p = Path(r['path']).expanduser()
                    if not p.is_absolute():
                        p = path.parent / p
                    if not r['group_id'].strip():
                        raise ValueError('CSV 的 group_id 不可为空')
                    entries.append((p.resolve(), r['group_id'].strip(), r.get('source', '').strip() or source or path.stem, r.get('split', '').strip()))
        elif path.is_dir():
            for p in sorted(path.rglob('*')):
                if p.is_file() and p.suffix.lower() in EXTENSIONS and not p.name.startswith('.'):
                    group = str(p.parent.resolve()) if grouping == 'parent' else str(p.resolve())
                    entries.append((p.resolve(), group, source or path.name, ''))
        else:
            raise ValueError('请选择图片文件夹或 CSV 清单')
        if not entries:
            raise ValueError('没有找到 JPG、PNG 或 WebP 图片')
        if len(entries) > 20000:
            raise ValueError('单批上限为 20,000 张，请使用较小的文件夹或清单')
        with self.lock, self.connect() as c:
            if c.execute("SELECT value FROM meta WHERE key='frozen'").fetchone()[0] == '1':
                raise ValueError('题库已锁定；下一轮请用 --data 指定新目录，避免修改本轮题库')
            groups = dict(c.execute('SELECT group_id,split FROM groups').fetchall())
            known = {r['id']: r for r in c.execute('SELECT * FROM images')}
            prepared, duplicates = [], 0
            for p, group, src, split in entries:
                if not p.is_file() or p.suffix.lower() not in EXTENSIONS:
                    raise ValueError('图片不存在或格式不支持：' + str(p))
                if p.stat().st_size > 80 * 1024 * 1024:
                    raise ValueError('单张图片超过 80 MB：' + p.name)
                raw = p.read_bytes()
                valid = raw.startswith(b'\xff\xd8\xff') or raw.startswith(b'\x89PNG\r\n\x1a\n') or (raw[:4] == b'RIFF' and raw[8:12] == b'WEBP')
                if not valid:
                    raise ValueError('文件内容不是受支持的图片：' + p.name)
                digest = hashlib.sha256(raw).hexdigest()
                if split and split not in SPLITS:
                    raise ValueError('split 只能是 train、validation 或 test')
                if digest in known:
                    previous = known[digest]
                    if split and split != previous['split']:
                        raise ValueError('完全重复的图片被指定到不同划分：' + p.name)
                    if group != previous['group_id'] and group in groups and groups[group] != previous['split']:
                        raise ValueError('重复图片连接了不同划分的母图组：' + p.name)
                    groups[group] = previous['split']
                    duplicates += 1
                    continue
                number = int(hashlib.sha256(('tongtou-v1:' + group).encode()).hexdigest()[:8], 16) / 0xffffffff
                chosen = split or groups.get(group) or ('train' if number < .60 else 'validation' if number < .75 else 'test')
                if group in groups and chosen != groups[group]:
                    raise ValueError('同一母图组不可跨划分：' + group)
                groups[group] = chosen
                media = digest + p.suffix.lower()
                # Snapshot the exact bytes that were hashed; no edits to source images.
                target = self.root / 'images' / media
                if not target.exists():
                    target.write_bytes(raw)
                row = (digest, str(p), p.name, media, group, src, chosen, time.time())
                prepared.append(row)
                known[digest] = {'group_id': group, 'split': chosen}
            c.executemany('INSERT INTO images VALUES(?,?,?,?,?,?,?,?)', prepared)
            c.executemany('INSERT OR IGNORE INTO groups VALUES(?,?)', groups.items())
        return {'imported': len(prepared), 'duplicates': duplicates, 'status': self.status()}

    def freeze(self):
        with self.lock, self.connect() as c:
            if not c.execute('SELECT count(*) FROM images').fetchone()[0]:
                raise ValueError('先导入图片，再锁定题库')
            c.execute("UPDATE meta SET value='1' WHERE key='frozen'")
        return self.status()

    def start(self, name):
        name = str(name).strip()
        if not name or len(name) > 60:
            raise ValueError('请输入 1–60 字的标注者代号')
        with self.lock, self.connect() as c:
            if c.execute("SELECT value FROM meta WHERE key='frozen'").fetchone()[0] != '1':
                raise ValueError('请先完成导入并锁定题库')
            if not c.execute('SELECT 1 FROM annotators WHERE name=?', (name,)).fetchone():
                images = [r[0] for r in c.execute('SELECT id FROM images ORDER BY id')]
                rng = random.Random(hashlib.sha256(name.encode()).hexdigest())
                rng.shuffle(images)
                c.execute('INSERT INTO annotators VALUES(?,?)', (name, time.time()))
                originals = [(uuid.uuid4().hex, image, None) for image in images]
                queue = list(originals)
                # Small sets cannot provide meaningful delayed repeats; no repeats below 20.
                n = round(len(images) * .1) if len(images) >= 20 else 0
                candidates = originals[:-10]
                selected = rng.sample(candidates, min(n, len(candidates)))
                for task_id, image, _ in selected:
                    original_position = next(i for i, t in enumerate(queue) if t[0] == task_id)
                    position = rng.randint(original_position + 11, len(queue))
                    queue.insert(position, (uuid.uuid4().hex, image, task_id))
                c.executemany('INSERT INTO tasks(id,annotator,image_id,position,repeat_of) VALUES(?,?,?,?,?)',
                              [(tid, name, image, pos, repeat) for pos, (tid, image, repeat) in enumerate(queue)])
        return self.next(name)

    def next(self, name):
        with self.connect() as c:
            total, done = c.execute("SELECT count(*),coalesce(sum(status!='pending'),0) FROM tasks WHERE annotator=?", (name,)).fetchone()
            row = c.execute("SELECT id,comment FROM tasks WHERE annotator=? AND status='pending' ORDER BY position LIMIT 1", (name,)).fetchone()
            missing = c.execute("SELECT count(*) FROM tasks WHERE annotator=? AND reason_status='missing'", (name,)).fetchone()[0]
            reason_only = False
            if not row and missing:
                row = c.execute("SELECT id,comment FROM tasks WHERE annotator=? AND reason_status='missing' ORDER BY position LIMIT 1", (name,)).fetchone()
                reason_only = True
            task = {'id': row['id'], 'image_url': '/image/' + row['id'], 'comment': row['comment']} if row else None
            if reason_only: task['reason_only'] = True
            return {'name': name, 'total': total, 'done': done, 'missing_reasons': missing, 'task': task}

    def answer(self, name, task_id, label, uncertain, skip, duration_ms, negative_reasons=None, negative_note='', comment=None, undecidable=False, undecidable_reason=''):
        if type(skip) is not bool or type(uncertain) is not bool or type(undecidable) is not bool:
            raise ValueError('标注状态格式错误')
        if undecidable and (skip or label is not None):
            raise ValueError('无法判断不能同时选择标签或跳过')
        if not isinstance(undecidable_reason, str) or len(undecidable_reason) > 2000:
            raise ValueError('无法判断原因最多 2000 字')
        undecidable_reason = undecidable_reason.strip() if undecidable else ''
        if undecidable: uncertain = False
        if not skip and not undecidable and (type(label) is not int or label not in (0, 1)):
            raise ValueError('标签只能是 0 或 1')
        if type(duration_ms) is not int or not 0 <= duration_ms <= 86400000:
            raise ValueError('耗时格式错误；单次标注上限 24 小时')
        with self.lock, self.connect() as c:
            row = c.execute('SELECT * FROM tasks WHERE id=? AND annotator=?', (task_id, name)).fetchone()
            if not row:
                raise ValueError('标注题目不存在')
            if comment is None: comment = row['comment']
            if not isinstance(comment, str) or len(comment) > 5000:
                raise ValueError('备注最多 5000 字')
            comment = comment.strip()
            supplement = row['status'] == 'answered' and row['reason_status'] == 'missing'
            if row['status'] != 'pending' and not supplement:
                return self.next(name)  # Idempotent retry cannot overwrite a saved answer.
            reasons = [] if negative_reasons is None else negative_reasons
            if not isinstance(reasons, list) or any(not isinstance(r, str) or r not in REASONS for r in reasons):
                raise ValueError('原因选项不合法')
            if not isinstance(negative_note, str) or len(negative_note) > 2000:
                raise ValueError('补充说明最多 2000 字')
            reasons = list(dict.fromkeys(reasons)); negative_note = negative_note.strip()
            if supplement and (label != 0 or skip):
                raise ValueError('此处只补充原“不通透”答案的原因')
            if not skip and label == 0:
                if not reasons and not negative_note:
                    raise ValueError('请标明为什么不通透：选择原因或填写说明')
                if 'other' in reasons and not negative_note:
                    raise ValueError('选择“其他”时请填写具体说明')
                if 'unsure' in reasons and len(reasons) > 1:
                    raise ValueError('“说不清具体原因”不能与其他原因同时选择')
            else:
                reasons, negative_note = [], ''
            reason_status = 'provided' if not skip and label == 0 else ''
            encoded = json.dumps(reasons, ensure_ascii=False)
            if supplement:
                c.execute('UPDATE tasks SET negative_reasons=?,negative_note=?,reason_status=?,comment=? WHERE id=?',
                          (encoded, negative_note, reason_status, comment, task_id))
                c.execute('INSERT INTO events(task_id,action,payload,created_at) VALUES(?,?,?,?)',
                          (task_id, 'reason_added', json.dumps({'negative_reasons':reasons,'negative_note':negative_note,'comment':comment,'reason_schema':'v1'},ensure_ascii=False), time.time()))
            else:
                self._save_answer(c, row, name, task_id, label, uncertain, skip, duration_ms, reasons, negative_note, reason_status, comment, undecidable, undecidable_reason)
        return self.next(name)

    def _save_answer(self, c, row, name, task_id, label, uncertain, skip, duration_ms, reasons, negative_note, reason_status, comment, undecidable, undecidable_reason):
            first = c.execute("SELECT id,comment FROM tasks WHERE annotator=? AND status='pending' ORDER BY position LIMIT 1", (name,)).fetchone()
            if first['id'] != task_id:
                raise ValueError('题目已更新，请刷新后继续')
            payload = {'label': None if skip or undecidable else label, 'uncertain': uncertain, 'skip': skip, 'duration_ms': duration_ms,
                       'negative_reasons': reasons, 'negative_note': negative_note, 'comment': comment, 'undecidable': undecidable, 'undecidable_reason': undecidable_reason, 'reason_schema': 'v1'}
            c.execute('UPDATE tasks SET status=?,label=?,uncertain=?,duration_ms=?,saved_at=?,negative_reasons=?,negative_note=?,reason_status=?,comment=?,undecidable_reason=? WHERE id=?',
                      ('undecidable' if undecidable else 'skipped' if skip else 'answered', payload['label'], int(uncertain), duration_ms, time.time(), json.dumps(reasons,ensure_ascii=False), negative_note, reason_status, comment, undecidable_reason, task_id))
            c.execute('INSERT INTO events(task_id,action,payload,created_at) VALUES(?,?,?,?)',
                      (task_id, 'answer', json.dumps(payload), time.time()))

    def undo(self, name):
        with self.lock, self.connect() as c:
            row = c.execute("SELECT * FROM tasks WHERE annotator=? AND status!='pending' ORDER BY saved_at DESC LIMIT 1", (name,)).fetchone()
            if not row:
                raise ValueError('没有可以撤销的记录')
            c.execute('INSERT INTO events(task_id,action,payload,created_at) VALUES(?,?,?,?)',
                      (row['id'], 'undo', json.dumps(dict(row)), time.time()))
            c.execute("UPDATE tasks SET status='pending',label=NULL,uncertain=0,duration_ms=NULL,saved_at=NULL,negative_reasons='[]',negative_note='',reason_status='',undecidable_reason='' WHERE id=?", (row['id'],))
        return self.next(name)

    def export(self, kind):
        with self.connect() as c:
            if kind == 'manifest':
                fields = ['id', 'original_path', 'filename', 'media', 'group_id', 'source', 'split', 'imported_at']
                rows = [dict(r) for r in c.execute('SELECT * FROM images ORDER BY id')]
            elif kind == 'labels':
                fields = ['id','annotator','image_id','position','repeat_of','status','label','uncertain','duration_ms','saved_at','negative_reasons','negative_note','reason_status','comment','undecidable_reason','group_id','source','split']
                rows = [dict(r) for r in c.execute('SELECT t.*,i.group_id,i.source,i.split FROM tasks t JOIN images i ON i.id=t.image_id ORDER BY annotator,position')]
            elif kind == 'events':
                fields = ['id','task_id','action','payload','created_at']
                rows = [dict(r) for r in c.execute('SELECT * FROM events ORDER BY id')]
            elif kind == 'training':
                fields = ['image_id','path','group_id','source','split','label','n_raters','n_positive','n_negative','n_uncertain','n_skipped','n_undecidable','agreement','eligible','reason']
                rows = []
                for im in c.execute('SELECT * FROM images ORDER BY id').fetchall():
                    votes = c.execute("SELECT * FROM tasks WHERE image_id=? AND repeat_of IS NULL AND status!='pending'", (im['id'],)).fetchall()
                    valid = [v for v in votes if v['status'] == 'answered' and not v['uncertain']]
                    pos = sum(v['label'] == 1 for v in valid)
                    neg = len(valid) - pos
                    label = '' if pos == neg else int(pos > neg)
                    enough = len(valid) >= (3 if im['split'] == 'test' else 1)
                    eligible = enough and label != ''
                    reason = 'ready' if eligible else 'tie_or_no_votes' if label == '' else 'test_needs_3_certain_raters'
                    rows.append(dict(zip(fields, [im['id'], str(self.root / 'images' / im['media']), im['group_id'], im['source'], im['split'], label,
                        len(valid), pos, neg, sum(v['uncertain'] for v in votes), sum(v['status']=='skipped' for v in votes), sum(v['status']=='undecidable' for v in votes),
                        round(max(pos,neg)/len(valid),4) if valid else '', int(eligible), reason])))
            else:
                raise ValueError('未知导出类型')
        out = io.StringIO(newline='')
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            # Prevent spreadsheet formula execution in user-provided string cells.
            writer.writerow({k: ("'" + str(v) if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')) else v) for k,v in row.items()})
        return ('\ufeff' + out.getvalue()).encode('utf-8')


def make_handler(store):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, code, body, content_type='application/json; charset=utf-8', filename=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
            if filename:
                self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
            self.end_headers()
            self.wfile.write(body)

        def allowed_host(self):
            return self.headers.get('Host') in ('127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port))

        def do_GET(self):
            if not self.allowed_host():
                return self.send(403, {'error': 'Host rejected'})
            try:
                url = urlparse(self.path)
                if url.path in ('/', '/app.js', '/style.css'):
                    name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[url.path]
                    return self.send(200, (HERE / 'static' / name).read_bytes(), mimetypes.guess_type(name)[0] + '; charset=utf-8')
                if url.path == '/api/status':
                    return self.send(200, dict(store.status(), token=token, reason_options=REASONS))
                if url.path == '/api/next':
                    return self.send(200, store.next(parse_qs(url.query).get('name', [''])[0]))
                if url.path.startswith('/image/'):
                    with store.connect() as c:
                        row = c.execute('SELECT i.media FROM images i JOIN tasks t ON i.id=t.image_id WHERE t.id=?', (url.path[7:],)).fetchone()
                    if not row:
                        return self.send(404, {'error': '图片不存在'})
                    p = store.root / 'images' / row['media']
                    return self.send(200, p.read_bytes(), mimetypes.guess_type(p.name)[0])
                if url.path.startswith('/export/'):
                    kind = url.path[8:].removesuffix('.csv')
                    return self.send(200, store.export(kind), 'text/csv; charset=utf-8', kind + '.csv')
                if url.path == '/backup':
                    with tempfile.TemporaryDirectory() as d:
                        p = Path(d) / 'annotations.sqlite3'
                        with store.connect() as c:
                            backup = sqlite3.connect(str(p))
                            try:
                                c.backup(backup)
                            finally:
                                backup.close()
                        return self.send(200, p.read_bytes(), 'application/octet-stream', 'annotations-backup.sqlite3')
                return self.send(404, {'error': '页面不存在'})
            except (ValueError, OSError) as e:
                return self.send(400, {'error': str(e)})

        def do_POST(self):
            if not self.allowed_host() or self.headers.get('X-Local-Token') != token:
                return self.send(403, {'error': '请刷新本地页面后重试'})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size < 65536:
                    raise ValueError('请求大小不合法')
                data = json.loads(self.rfile.read(size))
                if self.path == '/api/import':
                    result = store.import_images(data['path'], data.get('source',''), data.get('grouping','file'))
                elif self.path == '/api/freeze':
                    result = store.freeze()
                elif self.path == '/api/start':
                    result = store.start(data['name'])
                elif self.path == '/api/answer':
                    result = store.answer(data['name'], data['task_id'], data.get('label'), data.get('uncertain', False), data.get('skip', False), data['duration_ms'], data.get('negative_reasons'), data.get('negative_note',''), data.get('comment'), data.get('undecidable',False), data.get('undecidable_reason',''))
                elif self.path == '/api/undo':
                    result = store.undo(data['name'])
                else:
                    return self.send(404, {'error': '接口不存在'})
                return self.send(200, result)
            except (ValueError, KeyError, TypeError, OSError) as e:
                return self.send(400, {'error': str(e)})
            except sqlite3.Error:
                return self.send(500, {'error': '数据库写入失败；当前答案未确认保存，请重试'})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path.cwd() / 'data' / 'round-01')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    store = Store(args.data)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(store))
    url = 'http://127.0.0.1:' + str(server.server_port)
    print('通透标注台：' + url + '\n数据目录：' + str(store.root) + '\n按 Ctrl+C 停止。', flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
