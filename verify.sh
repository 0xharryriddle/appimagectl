#!/usr/bin/env bash
# verify.sh — CI mirror for appimagectl. Must exit 0 when everything is green.
set -euo pipefail
cd "$(dirname "$0")"

PY="${VENV_PY:-./.venv/bin/python}"
RUFF="${RUFF:-$(command -v ruff || echo ~/.local/bin/ruff)}"

echo "== 1/4 ruff =="
"$RUFF" check src tests

echo "== 2/4 pytest =="
"$PY" -m pytest -q

echo "== 3/4 package import =="
"$PY" -c "import appimagectl, appimagectl.cli; print('import ok')"

echo "== 4/4 CLI smoke (real files, read-only) =="
store="${APPIMAGECTL_SMOKE_STORE:-$HOME/Applications}"
if [ -d "$store" ]; then
  app=$(ls "$store"/*.AppImage 2>/dev/null | head -1)
  if [ -n "$app" ]; then
    "$PY" -m appimagectl inspect --shallow "$app" >/dev/null
    "$PY" -m appimagectl doctor >/dev/null
    echo "smoke ok on $(basename "$app") (inspect shallow + doctor)"
  else
    echo "no AppImage in $store; smoke skipped (doctor only)"
    "$PY" -m appimagectl doctor >/dev/null
  fi
else
  echo "no store dir; smoke skipped"
fi

echo "ALL GATES GREEN"