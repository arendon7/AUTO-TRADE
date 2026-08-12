#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ "${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED" ]]; then
  echo "AUTO-TRADE SAFE OPEN BLOCKED: R6_EXTERNAL_PAPER_WRITE=ENABLED" >&2
  exit 2
fi
export R6_EXTERNAL_PAPER_WRITE=DISABLED
unset APCA_API_KEY_ID 2>/dev/null || true
unset APCA_API_SECRET_KEY 2>/dev/null || true

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  bash "$ROOT/INSTALAR_AUTO_TRADE.command"
fi

exec "$ROOT/.venv/bin/python" "$ROOT/scripts/mac_dashboard.py"
