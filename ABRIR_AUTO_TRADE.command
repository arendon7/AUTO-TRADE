#!/bin/bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "$0")" && pwd -P)"
INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"
ROOT="$SOURCE_ROOT"

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

read_source_head() {
  local root="$1"
  awk -F= '$1 == "source_head" {print $2; exit}' "$root/MAC_BUILD_INFO.txt" 2>/dev/null || true
}

if [[ -f "$SOURCE_ROOT/MAC_STANDALONE_MANIFEST.txt" ]]; then
  EXPECTED_HEAD="$(read_source_head "$SOURCE_ROOT")"
  INSTALLED_HEAD="$(read_source_head "$INSTALL_ROOT")"
  if [[ ! -x "$INSTALL_ROOT/.venv/bin/python" || "$EXPECTED_HEAD" != "$INSTALLED_HEAD" ]]; then
    bash "$SOURCE_ROOT/INSTALAR_AUTO_TRADE.command"
  fi
  ROOT="$INSTALL_ROOT"
fi

cd "$ROOT"
[[ -x "$ROOT/.venv/bin/python" ]] || bash "$ROOT/INSTALAR_AUTO_TRADE.command"

# Cold-start qualification builds add only non-executable evidence/binding layers.
# The newest wrapper can issue a UAT-only human decision and seal a local binding,
# but cannot consume approval, open Final Guard, broker POST or enable LIVE.
SERVER="$ROOT/scripts/mac_dashboard_cold_start_final_guard_binding.py"
[[ -f "$SERVER" ]] || SERVER="$ROOT/scripts/mac_dashboard_cold_start_attestation.py"
[[ -f "$SERVER" ]] || SERVER="$ROOT/scripts/mac_dashboard_cold_start.py"
[[ -f "$SERVER" ]] || SERVER="$ROOT/scripts/mac_dashboard_health_commissioning.py"
[[ -f "$SERVER" ]] || SERVER="$ROOT/scripts/mac_dashboard_execution_gate.py"
[[ -f "$SERVER" ]] || SERVER="$ROOT/scripts/mac_dashboard_one_shot.py"
[[ -f "$SERVER" ]] || SERVER="$ROOT/scripts/mac_dashboard.py"
exec "$ROOT/.venv/bin/python" "$SERVER"
