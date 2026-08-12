#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "AUTO-TRADE CONTROL CENTER: este launcher es exclusivamente para macOS." >&2
  exit 2
fi
if [[ "${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED" ]]; then
  echo "AUTO-TRADE CONTROL CENTER BLOCKED: R6_EXTERNAL_PAPER_WRITE=ENABLED." >&2
  exit 2
fi

unset APCA_API_KEY_ID 2>/dev/null || true
unset APCA_API_SECRET_KEY 2>/dev/null || true
export R6_EXTERNAL_PAPER_WRITE=DISABLED

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  bash "$ROOT/INSTALAR_AUTO_TRADE.command"
fi

exec "$ROOT/.venv/bin/python" "$ROOT/scripts/mac_dashboard.py"
