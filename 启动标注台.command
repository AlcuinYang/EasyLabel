#!/bin/zsh
set -e
SCRIPT_DIR="${0:A:h}"
cd -- "$SCRIPT_DIR"
if [[ ! -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  echo "请先按 README 安装：python3.12 -m venv .venv && .venv/bin/python -m pip install -e ."
  read "?按回车退出。"
  exit 1
fi
exec "$SCRIPT_DIR/.venv/bin/python" -m annotation_mvp.demo
