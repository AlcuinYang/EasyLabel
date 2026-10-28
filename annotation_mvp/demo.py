"""Launch an isolated synthetic pairwise demo, never a research dataset."""
import argparse
from pathlib import Path
import subprocess
import sys
from annotation_mvp.tools.make_v0_example import create_example


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path.cwd() / 'demo')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if not workspace.exists():
        create_example(workspace)
    if not (workspace / 'input').is_dir() or not (workspace / 'pairs.csv').is_file():
        parser.error('演示目录不完整；请选择新的 --workspace，不覆盖现有文件。')
    print('EasyLabel 合成演示：仅供流程试用，不是研究标注。', flush=True)
    command = [sys.executable, '-m', 'annotation_mvp.measurement_app',
               '--input', str(workspace / 'input'),
               '--results', str(workspace / 'comparison-results'),
               '--pairs', str(workspace / 'pairs.csv'),
               '--question', '哪张更通透？', '--scene-gap', '0',
               '--seed', '20261008', '--port', str(args.port), '--open']
    try:
        raise SystemExit(subprocess.call(command))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
