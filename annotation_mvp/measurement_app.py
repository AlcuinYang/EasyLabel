#!/usr/bin/env python3
"""Local single-observer measurement UI. All metadata stays server-side."""
import argparse
import fcntl
import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from annotation_mvp.protocol import ProtocolStore
from annotation_mvp.scaling import adaptive, analyze
from annotation_mvp.pair_protocol import PairStore
from annotation_mvp.pair_analysis import analyze_pairs

STATIC=Path(__file__).resolve().parent/'measurement_static'
RESULT_FILES=('report.md','analysis.json','comparisons.csv','attributions.csv','scene_scores.csv','bt_scores.csv','pairs_human.csv','ties.csv','notes.csv','sessions.csv')

def handler(store):
    token=secrets.token_urlsafe(32)
    job={'running':False,'message':''}
    def run_analysis():
        try:
            with store.lock:
                if isinstance(store, PairStore):
                    analyze_pairs(store)
                    job['message']='分析完成，已按场景输出结果。'
                    return
                model,report=analyze(store)
                if not store.plan(3):
                    adaptive(store,model)
                    job['message']='第二轮分析完成，已生成定向补判。'
                else: job['message']=f'分析完成，结果已写入 {store.root}。'
        except Exception as e: job['message']='分析停止：'+str(e)
        finally: job['running']=False
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def send(self,code,data,kind='application/json; charset=utf-8'):
            raw=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
            self.send_response(code); self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(raw)
        def valid_host(self): return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
        def do_GET(self):
            if not self.valid_host(): return self.send(403,{'error':'Host rejected'})
            if self.path=='/api/status':
                return self.send(200,dict(token=token,image_count=len(store.paths),job=job))
            if self.path=='/api/results':
                report=store.root/'report.md'
                return self.send(200,dict(report=report.read_text(encoding='utf-8') if report.exists() else '',
                                          files=[name for name in RESULT_FILES if (store.root/name).is_file()]))
            if self.path.startswith('/download/'):
                name=self.path.removeprefix('/download/')
                if name not in RESULT_FILES or not (store.root/name).is_file():
                    return self.send(404,{'error':'结果文件不存在'})
                kind='text/csv; charset=utf-8' if name.endswith('.csv') else 'text/plain; charset=utf-8'
                raw=(store.root/name).read_bytes()
                self.send_response(200); self.send_header('Content-Type',kind)
                self.send_header('Content-Disposition',f'attachment; filename="{name}"')
                self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(raw)
                return
            if self.path.startswith('/image/'):
                name=store.tokens.get(self.path.split('/')[-1])
                if not name: return self.send(404,{'error':'图片不存在'})
                return self.send(200,store.paths[name].read_bytes(),'image/jpeg')
            files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
            if self.path in files:
                name,kind=files[self.path]; return self.send(200,(STATIC/name).read_bytes(),kind)
            return self.send(404,{'error':'不存在'})
        def do_POST(self):
            if not self.valid_host() or self.headers.get('X-Local-Token')!=token: return self.send(403,{'error':'请刷新页面'})
            try:
                length=int(self.headers.get('Content-Length',0))
                if not 0<length<65536: raise ValueError('请求长度无效')
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict): raise ValueError('请求内容必须为对象')
                if job['running']: raise ValueError('分析正在运行，请稍候')
                if self.path=='/api/start': result=store.begin(data.get('annotator','A'))
                elif self.path=='/api/answer': result=store.submit(data)
                elif self.path=='/api/undo': result=store.undo(data)
                elif self.path=='/api/navigate': result=store.navigate(data)
                elif self.path=='/api/note': result=store.save_note(data)
                elif self.path=='/api/pause':
                    with store.lock:
                        sid=data.get('session_id')
                        if sid in store.sessions:
                            store.append(dict(type='session_pause',session_id=sid,timestamp=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()))
                            del store.sessions[sid]
                    result={'ok':True}
                elif self.path=='/api/prepare': result=store.generate_round2()
                elif self.path=='/api/analyze':
                    job.update(running=True,message='正在拟合、校验与生成报告…')
                    threading.Thread(target=run_analysis,daemon=True).start(); result=dict(job)
                else: return self.send(404,{'error':'不存在'})
                self.send(200,result)
            except (ValueError,KeyError,TypeError,OSError) as e: self.send(400,{'error':str(e)})
    return Handler

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True,help='输入图片目录')
    parser.add_argument('--results',type=Path,required=True,help='独立实验结果目录')
    parser.add_argument('--seed',type=int,default=None)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--open',action='store_true')
    parser.add_argument('--analyze',action='store_true',help='完成第二轮后生成分析结果')
    parser.add_argument('--round3',action='store_true',help='分析后生成一次有界定向补判')
    parser.add_argument('--pairs',type=Path,help='导入独立图对清单')
    parser.add_argument('--question',help='本实验问题，清单默认：哪张更通透？')
    parser.add_argument('--scene-gap',type=int,default=None,help='清单模式同场景间隔题数，默认3；单场景显式设0')
    parser.add_argument('--attribution',action='store_true',help='原因归属实验')
    parser.add_argument('--source-results',type=Path,help='已完成的来源比较实验结果目录')
    args=parser.parse_args()
    configured=args.results/'protocol.json'
    existing_mode=json.loads(configured.read_text()).get('experiment_mode') if configured.exists() else None
    if not configured.exists() and (args.results/'events.jsonl').exists():
        with (args.results/'events.jsonl').open() as journal:
            first=json.loads(journal.readline())
        existing_mode=first.get('config',{}).get('experiment_mode')
    list_mode=args.pairs is not None or args.attribution or existing_mode in ('limpidity','attribution')
    if list_mode and (not any(x=='--input' or x.startswith('--input=') for x in sys.argv) or not any(x=='--results' or x.startswith('--results=') for x in sys.argv)):
        parser.error('独立实验必须显式指定 --input 和 --results，不能使用正式实验默认目录')
    if list_mode and args.round3:parser.error('清单实验不使用跨场景定向补判 --round3')
    if not list_mode and (args.question is not None or args.scene_gap is not None or args.source_results is not None):
        parser.error('新增配置用于独立 --pairs / --attribution 实验，不修改正式实验')
    args.results.mkdir(parents=True,exist_ok=True)
    process_lock=(args.results/'.process.lock').open('a')
    try: fcntl.flock(process_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('同一题库已有服务运行，请使用网页分析入口或先停止服务。')
    try:
        store=(PairStore(args.input,args.results,seed=args.seed,pairs=args.pairs,question=args.question,
                         scene_gap=args.scene_gap,attribution=args.attribution,source_results=args.source_results)
               if list_mode else ProtocolStore(args.input,args.results,args.seed if args.seed is not None else 20260916))
    except (ValueError,OSError) as e:parser.error(str(e))
    if args.analyze or args.round3:
        model,report=analyze_pairs(store) if isinstance(store,PairStore) else analyze(store)
        if args.round3: print(adaptive(store,model))
        print('分析输出：'+str(store.root/'report.md')); return
    try:server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(store))
    except OSError as e:
        if e.errno==48:raise SystemExit(f'端口 {args.port} 已被其他程序占用。请改用 --port 8870，或选择其他空闲端口。') from None
        raise
    print(f'通透标注台：http://127.0.0.1:{args.port}\n图片：{len(store.paths)}\n结果：{store.root}',flush=True)
    if args.open: webbrowser.open(f'http://127.0.0.1:{args.port}')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
