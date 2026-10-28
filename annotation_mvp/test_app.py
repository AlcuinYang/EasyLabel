import csv
import io
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zlib

from annotation_mvp.app import Store, ThreadingHTTPServer, make_handler


def png(value):
    def chunk(tag, payload):
        return struct.pack('!I', len(payload)) + tag + payload + struct.pack('!I', zlib.crc32(tag + payload) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\0' + bytes([value, 80, 100]))) + chunk(b'IEND', b'')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.folder = self.root / 'input'
        self.folder.mkdir()
        self.store = Store(self.root / 'data')

    def tearDown(self):
        self.temp.cleanup()

    def images(self, n=1):
        for i in range(n):
            (self.folder / ('%03d.png' % i)).write_bytes(png(i))

    def manifest(self, rows):
        p = self.root / 'manifest.csv'
        with p.open('w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['path','group_id','source','split'])
            writer.writerows(rows)
        return str(p)

    def ready(self, n=1):
        self.images(n)
        self.store.import_images(str(self.folder))
        self.store.freeze()

    def test_undecidable_separate_export_undo_and_validation(self):
        self.ready(3)
        session=self.store.start('a')
        tid=session['task']['id']
        for kwargs in ({'undecidable':True,'undecidable_reason':123}, {'undecidable':True,'undecidable_reason':'x'*2001}):
            with self.assertRaises(ValueError):
                self.store.answer('a',tid,None,False,False,1,**kwargs)
        with self.assertRaises(ValueError):
            self.store.answer('a',tid,0,False,False,1,undecidable=True)
        with self.assertRaises(ValueError):
            self.store.answer('a',tid,None,False,True,1,undecidable=True)
        result=self.store.answer('a',tid,None,True,False,1,comment='观察备注',undecidable=True,undecidable_reason='缺少参照')
        self.assertEqual(result['done'],1)
        with self.store.connect() as c:
            row=dict(c.execute('select * from tasks where id=?',(tid,)).fetchone())
        self.assertEqual((row['status'],row['label'],row['uncertain']),('undecidable',None,0))
        labels=list(csv.DictReader(io.StringIO(self.store.export('labels').decode('utf-8-sig'))))
        vote=next(r for r in labels if r['id']==tid)
        self.assertEqual((vote['undecidable_reason'],vote['comment']),('缺少参照','观察备注'))
        training=list(csv.DictReader(io.StringIO(self.store.export('training').decode('utf-8-sig'))))
        vote=next(r for r in training if r['image_id']==row['image_id'])
        self.assertEqual((vote['n_undecidable'],vote['n_skipped'],vote['n_negative'],vote['n_raters']),('1','0','0','0'))
        self.store.undo('a')
        self.store.answer('a',tid,None,False,True,1)
        with self.store.connect() as c:
            row=c.execute('select * from tasks where id=?',(tid,)).fetchone()
            self.assertEqual((row['status'],row['undecidable_reason']),('skipped',''))
        self.store.undo('a')
        self.store.answer('a',tid,None,False,False,1,undecidable=True)

    def test_import_dedup_snapshot_and_freeze(self):
        self.images(2)
        self.assertEqual(self.store.import_images(str(self.folder))['imported'], 2)
        self.assertEqual(self.store.import_images(str(self.folder))['duplicates'], 2)
        copied = list((self.store.root / 'images').iterdir())
        (self.folder / '000.png').write_bytes(b'changed')
        self.assertTrue(all(p.read_bytes().startswith(b'\x89PNG') for p in copied))
        self.store.freeze()
        with self.assertRaises(ValueError):
            self.store.import_images(str(self.folder))

    def test_group_split_conflict_is_atomic(self):
        self.images(2)
        p = self.manifest([[self.folder/'000.png','same','A','train'],[self.folder/'001.png','same','A','test']])
        with self.assertRaises(ValueError):
            self.store.import_images(p)
        self.assertEqual(self.store.status()['count'],0)

    def test_duplicate_alias_cannot_leak_across_splits(self):
        self.images(2)
        self.store.import_images(self.manifest([[self.folder/'000.png','A','src','train']]))
        self.store.import_images(self.manifest([[self.folder/'000.png','B','src','']]))
        with self.assertRaises(ValueError):
            self.store.import_images(self.manifest([[self.folder/'001.png','B','src','test']]))

    def test_repeat_spacing_and_blind_payload(self):
        self.ready(40)
        s = self.store.start('甲')
        self.assertEqual(s['total'],44)
        self.assertEqual(set(s['task']),{'id','image_url','comment'})
        with self.store.connect() as c:
            positions = {r['id']:r['position'] for r in c.execute('SELECT * FROM tasks')}
            repeats = c.execute('SELECT * FROM tasks WHERE repeat_of IS NOT NULL').fetchall()
            self.assertEqual(len(repeats),4)
            for r in repeats:
                self.assertGreaterEqual(r['position']-positions[r['repeat_of']],11)

    def test_resume_idempotent_answer_undo_history(self):
        self.ready(2)
        s=self.store.start('甲'); tid=s['task']['id']
        s=self.store.answer('甲',tid,1,False,False,100)
        self.store.answer('甲',tid,0,False,False,100)
        reopened=Store(self.store.root)
        self.assertEqual(reopened.start('甲')['done'],1)
        self.assertEqual(self.store.undo('甲')['task']['id'],tid)
        self.store.answer('甲',tid,0,True,False,200,['grey_veil'])
        events=list(csv.DictReader(io.StringIO(self.store.export('events').decode('utf-8-sig'))))
        self.assertEqual([r['action'] for r in events],['answer','undo','answer'])

    def test_other_rater_cannot_write_task(self):
        self.ready()
        s=self.store.start('甲');self.store.start('乙')
        with self.assertRaises(ValueError):
            self.store.answer('乙',s['task']['id'],1,False,False,100)

    def test_consensus_excludes_uncertain_and_test_requires_three(self):
        self.images()
        self.store.import_images(self.manifest([[self.folder/'000.png','one','src','test']]))
        self.store.freeze()
        def vote(name,label,uncertain=False,skip=False):
            s=self.store.start(name)
            self.store.answer(name,s['task']['id'],label,uncertain,skip,100,['grey_veil'] if label==0 else [])
        def row():
            return next(csv.DictReader(io.StringIO(self.store.export('training').decode('utf-8-sig'))))
        vote('a',1);vote('b',0)
        self.assertEqual(row()['label'],'')
        vote('c',1,True)
        self.assertEqual(row()['n_raters'],'2')
        vote('d',None,False,True)
        self.assertEqual(row()['n_skipped'],'1')
        vote('e',1)
        self.assertEqual(row()['eligible'],'1')
        self.assertEqual(row()['label'],'1')
        self.assertEqual(row()['split'],'test')

    def test_repeats_do_not_count_as_extra_raters(self):
        self.ready(20)
        s=self.store.start('a')
        while s['task']:
            s=self.store.answer('a',s['task']['id'],1,False,False,100)
        rows=list(csv.DictReader(io.StringIO(self.store.export('training').decode('utf-8-sig'))))
        self.assertEqual(len(rows),20)
        self.assertTrue(all(r['n_raters']=='1' for r in rows))

    def test_invalid_image_rolls_back(self):
        self.images()
        (self.folder/'bad.jpg').write_bytes(b'not an image')
        with self.assertRaises(ValueError):self.store.import_images(str(self.folder))
        self.assertEqual(self.store.status()['count'],0)

    def test_negative_reason_required_and_exported(self):
        self.ready();s=self.store.start('a');tid=s['task']['id']
        for reasons,note in [([],''),(['other'],''),(['unsure','blur'],''),(['invented'],'text')]:
            with self.assertRaises(ValueError):self.store.answer('a',tid,0,False,False,100,reasons,note)
        self.assertEqual(self.store.next('a')['done'],0)
        self.store.answer('a',tid,0,True,False,100,['grey_veil','weak_separation'],'远处楼房像蒙了一层')
        row=next(csv.DictReader(io.StringIO(self.store.export('labels').decode('utf-8-sig'))))
        self.assertEqual(json.loads(row['negative_reasons']),['grey_veil','weak_separation'])
        self.assertEqual(row['negative_note'],'远处楼房像蒙了一层')
        self.assertEqual(row['reason_status'],'provided')
        self.store.undo('a')
        self.store.answer('a',tid,1,False,False,100,['grey_veil'],'stale draft')
        row=next(csv.DictReader(io.StringIO(self.store.export('labels').decode('utf-8-sig'))))
        self.assertEqual(row['negative_reasons'],'[]');self.assertEqual(row['negative_note'],'')

    def test_free_text_and_unsure_reason(self):
        self.ready(2);s=self.store.start('a')
        s=self.store.answer('a',s['task']['id'],0,False,False,100,[],'整体显得闷')
        s=self.store.answer('a',s['task']['id'],0,False,False,100,['unsure'])
        self.assertIsNone(s['task'])

    def test_comment_all_outcomes_export_and_undo(self):
        self.ready(3);s=self.store.start('notes')
        for label,skip in [(1,False),(0,False),(None,True)]:
            s=self.store.answer('notes',s['task']['id'],label,False,skip,100,
                ['grey_veil'] if label==0 else [],'原因文字' if label==0 else '',comment='观察想法\n下一行')
        rows=list(csv.DictReader(io.StringIO(self.store.export('labels').decode('utf-8-sig'))))
        self.assertTrue(all(r['comment']=='观察想法\n下一行' for r in rows))
        negative=next(r for r in rows if r['label']=='0')
        self.assertEqual(negative['negative_note'],'原因文字')
        restored=self.store.undo('notes')
        self.assertEqual(restored['task']['comment'],'观察想法\n下一行')
        reopened=Store(self.store.root)
        self.assertEqual(reopened.next('notes')['task']['comment'],'观察想法\n下一行')
        reopened.answer('notes',restored['task']['id'],1,False,False,100,comment='更正后的想法')
        with reopened.connect() as c:
            self.assertIn('更正后的想法',json.loads(c.execute('SELECT payload FROM events ORDER BY id DESC LIMIT 1').fetchone()[0])['comment'])

    def test_comment_does_not_replace_required_reason(self):
        self.ready();s=self.store.start('notes');tid=s['task']['id']
        with self.assertRaises(ValueError):self.store.answer('notes',tid,0,False,False,100,comment='仅记录一个想法')
        for value in [42,'x'*5001]:
            with self.assertRaises(ValueError):self.store.answer('notes',tid,1,False,False,100,comment=value)
        self.assertEqual(self.store.next('notes')['done'],0)

    def test_legacy_backfill_preserves_vote_and_timestamp(self):
        self.ready();s=self.store.start('a');tid=s['task']['id']
        # Simulate a genuine pre-upgrade negative vote, without changing its original history.
        with self.store.connect() as c:
            c.execute("UPDATE tasks SET status='answered',label=0,uncertain=1,saved_at=123,duration_ms=777 WHERE id=?",(tid,))
        reopened=Store(self.store.root)
        s=reopened.start('a');self.assertTrue(s['task']['reason_only']);self.assertEqual(s['done'],1)
        s=reopened.answer('a',tid,0,False,False,999,['color_muddy'],'颜色混在一起')
        self.assertIsNone(s['task']);self.assertEqual(s['done'],1)
        with reopened.connect() as c:
            row=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
            self.assertEqual((row['label'],row['uncertain'],row['saved_at'],row['duration_ms']),(0,1,123,777))
            self.assertEqual(c.execute('SELECT action FROM events').fetchone()[0],'reason_added')

    def test_http_export_backup_and_csrf(self):
        self.ready()
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.store))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            with urlopen(base+'/api/status') as r:s=json.load(r)
            req=Request(base+'/api/start',data=b'{"name":"http"}',headers={'Content-Type':'application/json'})
            with self.assertRaises(HTTPError) as e:urlopen(req)
            self.assertEqual(e.exception.code,403)
            req.add_header('X-Local-Token',s['token'])
            with urlopen(req) as r:task=json.load(r)['task']
            with urlopen(base+task['image_url']) as r:self.assertEqual(r.read(),png(0))
            with urlopen(base+'/export/training.csv') as r:self.assertIn(b'eligible',r.read())
            with urlopen(base+'/backup') as r:data=r.read()
            backup=self.root/'backup.sqlite3';backup.write_bytes(data)
            c=sqlite3.connect(str(backup))
            self.assertEqual(c.execute('SELECT count(*) FROM images').fetchone()[0],1);c.close()
            with self.assertRaises(HTTPError):urlopen(base+'/../../app.py')
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
