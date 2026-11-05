#!/usr/bin/env bash
# RefreshSizer launcher (Linux/macOS). No admin rights needed: everything lives in ./.venv.
#   ./run.sh            -> start the local web UI (127.0.0.1) and open the browser
#   ./run.sh <command>  -> CLI, e.g. ./run.sh run inventory.xlsx --out reports --yes
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
STAMP=".venv/.requirements.lock"
install() {
  if command -v uv >/dev/null 2>&1; then
    [ -x ".venv/bin/python" ] || uv venv .venv
    uv pip install -p .venv/bin/python -r requirements.lock
    uv pip install -p .venv/bin/python --no-deps -e .
  else
    [ -x ".venv/bin/python" ] || python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.lock
    .venv/bin/python -m pip install --no-deps -e .
  fi
  cp requirements.lock "$STAMP"
}
# create the venv, or refresh it when requirements.lock changed since the last install
if [ ! -x ".venv/bin/python" ] || ! cmp -s requirements.lock "$STAMP"; then
  install
fi
if [ "$#" -eq 0 ]; then
  exec .venv/bin/python -m refreshsizer ui
fi
exec .venv/bin/python -m refreshsizer "$@"
