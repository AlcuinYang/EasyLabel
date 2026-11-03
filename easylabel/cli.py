"""Command line: data import, experiment lifecycle, invites, export, serve, demo."""
import argparse
import json
import os
import secrets
import sys
from pathlib import Path

from easylabel.core import db
from easylabel.core.stimuli import ManifestError


def _engine(args, create=False):
    return db.open_engine(args.data, create=create)


def cmd_import_stimuli(args):
    from easylabel.core.stimuli import import_manifest
    result = import_manifest(_engine(args, create=True), args.data, args.scenes, args.stimuli, args.root,
                             args.css_long_edge, args.strict_composition)
    for w in result.pop('warnings'):
        print('警告：' + w)
    print(json.dumps(result, ensure_ascii=False))


def cmd_derive(args):
    from easylabel.core.stimuli import add_derivatives
    print(f'新增派生图 {add_derivatives(_engine(args), args.data, args.css_long_edge)} 个')


def cmd_import_segmentation(args):
    from easylabel.core.segmentation import import_segmentations
    n = import_segmentations(_engine(args), args.data, args.csv, args.root, args.replace)
    print(f'已导入 {n} 张分割图')


def cmd_create(args):
    from easylabel.core.experiments import create_experiment
    exp_id, counts = create_experiment(_engine(args), args.config, args.replace)
    print(f'已创建草稿 {exp_id}：正式 {counts["main"]} 题，练习 {counts["practice"]} 题，金标 {counts["gold"]} 题')


def cmd_freeze(args):
    from easylabel.core.experiments import freeze
    print(f'已冻结 {args.experiment}，配置哈希 {freeze(_engine(args), args.experiment)}')


def cmd_publish(args):
    from easylabel.core.experiments import set_status
    set_status(_engine(args), args.experiment, 'live')
    print(f'{args.experiment} 已发布')


def cmd_close(args):
    from easylabel.core.experiments import set_status
    set_status(_engine(args), args.experiment, 'closed')
    print(f'{args.experiment} 已关闭')


def cmd_invite(args):
    from easylabel.core.participation import create_invites
    tokens = create_invites(_engine(args), args.experiment, args.tier, args.count, args.arm, args.max_uses, args.note)
    for t in tokens:
        print(f'{args.base_url.rstrip("/")}/i/{t}')


def cmd_export(args):
    from easylabel.core.export import export_experiment
    print(json.dumps(export_experiment(_engine(args), args.experiment, args.out), ensure_ascii=False))


def cmd_qc(args):
    from easylabel.core.experiments import get_experiment
    from easylabel.core.export import response_rows
    from easylabel_analysis.qc import rater_qc
    engine = _engine(args)
    with engine.connect() as conn:
        exp = get_experiment(conn, args.experiment)
        rows = response_rows(conn, args.experiment)
    print(json.dumps(rater_qc(rows, exp.config['exclusion']), ensure_ascii=False, indent=2, default=str))


def cmd_stimgen(args):
    from easylabel.stimgen import generate
    out_csv, n = generate(args.spec, args.jobs, args.root, args.out)
    print(f'已生成 {n} 个版本；清单：{out_csv}')


def cmd_stimgen_verify(args):
    from easylabel.stimgen import verify
    problems = verify(args.stimuli, args.root)
    for p in problems:
        print(p)
    if problems:
        sys.exit(3)
    print('全部合成版本可按配方逐字节重新生成')


def admin_token():
    token = os.environ.get('EASYLABEL_ADMIN_TOKEN')
    if not token:
        token = secrets.token_urlsafe(24)
        print('未设置 EASYLABEL_ADMIN_TOKEN，本次随机生成。')
    return token


def cmd_serve(args):
    import uvicorn
    from easylabel.server.app import create_app
    token = admin_token()
    app = create_app(args.data, token)
    print(f'管理端：http://{args.host}:{args.port}/admin/login?token={token}')
    uvicorn.run(app, host=args.host, port=args.port, log_level='warning')


def cmd_demo(args):
    from easylabel import demo
    from easylabel.core.experiments import create_experiment, freeze, set_status
    from easylabel.core.participation import create_invites
    from easylabel.core.stimuli import import_manifest
    root = demo.build(Path(args.data) / 'demo-source')
    engine = _engine(args, create=True)
    print('导入合成图片并生成派生图……')
    import_manifest(engine, args.data, root / 'scenes.csv', root / 'stimuli.csv', root, [360, 720], True)
    from easylabel.core.segmentation import import_segmentations
    import_segmentations(engine, args.data, root / 'segmentations.csv', root)
    base = f'http://127.0.0.1:{args.port}'
    for config in ('e0.yaml', 'e1.yaml', 'e3.yaml'):
        exp_id, _ = create_experiment(engine, root / config)
        freeze(engine, exp_id)
        set_status(engine, exp_id, 'live')
        for tier in ('lab', 'remote'):
            token = create_invites(engine, exp_id, tier, 1, max_uses=20, note='demo')[0]
            print(f'{exp_id} [{tier}]：{base}/i/{token}')
    print(f'\n启动：easylabel --data {args.data} serve --port {args.port}')


def main(argv=None):
    p = argparse.ArgumentParser(prog='easylabel')
    p.add_argument('--data', default=os.environ.get('EASYLABEL_DATA'), help='数据目录（含 easylabel.db 与 media/）')
    sub = p.add_subparsers(dest='command', required=True)

    s = sub.add_parser('import-stimuli', help='导入场景与版本清单，生成显示用派生图')
    s.add_argument('--scenes', required=True)
    s.add_argument('--stimuli', required=True)
    s.add_argument('--root', required=True, help='stimuli.csv 中 path 的根目录')
    s.add_argument('--css-long-edge', type=int, action='append', required=True, help='可重复，如 1024 与 480')
    s.add_argument('--strict-composition', action='store_true', help='每场景 12 版本、真实 ≥ 4 不满足即报错')
    s.set_defaults(func=cmd_import_stimuli)

    s = sub.add_parser('derive', help='为已导入刺激补充新的 CSS 长边')
    s.add_argument('--css-long-edge', type=int, action='append', required=True)
    s.set_defaults(func=cmd_derive)

    s = sub.add_parser('import-segmentation', help='导入预计算分割图（模板 C）')
    s.add_argument('csv')
    s.add_argument('--root', required=True)
    s.add_argument('--replace', action='store_true')
    s.set_defaults(func=cmd_import_segmentation)

    s = sub.add_parser('create-experiment', help='从 YAML 配置创建实验草稿')
    s.add_argument('config')
    s.add_argument('--replace', action='store_true', help='覆盖同 id 的草稿')
    s.set_defaults(func=cmd_create)

    for name, func, text in (('freeze', cmd_freeze, '冻结配置（不可再改）'), ('publish', cmd_publish, '开放作答'),
                             ('close', cmd_close, '停止作答'), ('qc', cmd_qc, '打印评分者质控')):
        s = sub.add_parser(name, help=text)
        s.add_argument('experiment')
        s.set_defaults(func=func)

    s = sub.add_parser('invite', help='生成邀请链接')
    s.add_argument('experiment')
    s.add_argument('--tier', choices=['lab', 'remote'], required=True)
    s.add_argument('--count', type=int, default=1)
    s.add_argument('--arm')
    s.add_argument('--max-uses', type=int, default=1)
    s.add_argument('--note')
    s.add_argument('--base-url', default='http://127.0.0.1:8765')
    s.set_defaults(func=cmd_invite)

    s = sub.add_parser('export', help='导出实验数据')
    s.add_argument('experiment')
    s.add_argument('out')
    s.set_defaults(func=cmd_export)

    s = sub.add_parser('serve', help='启动服务')
    s.add_argument('--host', default='127.0.0.1')
    s.add_argument('--port', type=int, default=8765)
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser('stimgen', help='按规格生成合成阶梯与局部编辑（离线）')
    s.add_argument('--spec', required=True, help='算子参数 YAML')
    s.add_argument('--jobs', required=True, help='生成任务 CSV')
    s.add_argument('--root', required=True)
    s.add_argument('--out', required=True, help='输出目录（须在 root 内）')
    s.set_defaults(func=cmd_stimgen)

    s = sub.add_parser('stimgen-verify', help='按 regen_recipe 重新生成并逐字节比对')
    s.add_argument('--stimuli', required=True)
    s.add_argument('--root', required=True)
    s.set_defaults(func=cmd_stimgen_verify)

    s = sub.add_parser('demo', help='生成合成演示数据与两个已发布实验')
    s.add_argument('--port', type=int, default=8765)
    s.set_defaults(func=cmd_demo)

    args = p.parse_args(argv)
    if not args.data and args.command not in ('stimgen', 'stimgen-verify'):
        p.error('请用 --data 或环境变量 EASYLABEL_DATA 指定数据目录')
    try:
        args.func(args)
    except ManifestError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    except (ValueError, KeyError, FileNotFoundError) as e:
        print(f'错误：{e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
