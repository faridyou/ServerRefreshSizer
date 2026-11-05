#!/usr/bin/env bash
# RefreshSizer launcher (Linux/macOS). Creates a user-space venv on first run; no admin rights needed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
if [ ! -x ".venv/bin/python" ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv .venv
    uv pip install -p .venv/bin/python -r requirements.lock
    uv pip install -p .venv/bin/python --no-deps -e .
  else
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.lock
    .venv/bin/python -m pip install --no-deps -e .
  fi
fi
exec .venv/bin/python -m refreshsizer "$@"
