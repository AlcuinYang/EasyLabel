#!/usr/bin/env bash
# Builds the synthetic demo once (cached in web/.e2e-data), then serves it for Playwright.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${EASYLABEL_PYTHON:-.venv/bin/python}
DATA=web/.e2e-data
if [ ! -f "$DATA/easylabel.db" ]; then
  "$PY" -m easylabel.cli --data "$DATA" demo --port 8799 > /dev/null
fi
EASYLABEL_ADMIN_TOKEN=e2e-admin exec "$PY" -m easylabel.cli --data "$DATA" serve --port 8799
