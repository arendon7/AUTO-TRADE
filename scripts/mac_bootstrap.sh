#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: scripts/mac_bootstrap.sh is intentionally for macOS (Darwin)." >&2
  exit 1
fi
if [[ "${R6_EXTERNAL_PAPER_WRITE:-}" == "ENABLED" ]]; then
  echo "ERROR: R6_EXTERNAL_PAPER_WRITE is ENABLED. Disable it before bootstrap." >&2
  exit 1
fi
unset APCA_API_KEY_ID || true
unset APCA_API_SECRET_KEY || true
export R6_EXTERNAL_PAPER_WRITE="DISABLED"

PYTHON_BIN=""
for candidate in python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
  then
    PYTHON_BIN="$candidate"
    break
  fi
done
[[ -n "$PYTHON_BIN" ]] || { echo "ERROR: Python 3.12+ is required." >&2; exit 1; }

if [[ ! -d .venv ]]; then "$PYTHON_BIN" -m venv .venv; fi
VENV_PY="$ROOT/.venv/bin/python"
VENV_PIP="$ROOT/.venv/bin/pip"
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PIP" install -e '.[dev]'
"$VENV_PY" -m compileall -q src tests scripts

"$VENV_PY" scripts/check_contract_registry.py
"$VENV_PY" scripts/check_debt_register.py
"$VENV_PY" scripts/check_r6_authority.py
"$VENV_PY" scripts/check_r6_live_deny_boundary.py
"$VENV_PY" scripts/check_r6_asset_boundary.py
"$VENV_PY" scripts/check_r6_connectivity_candidate_boundary.py
"$VENV_PY" scripts/check_r6_flat_account_boundary.py
"$VENV_PY" scripts/check_r6_market_data_boundary.py
"$VENV_PY" scripts/check_r6_operational_lifecycle_boundary.py
"$VENV_PY" scripts/check_r6_operational_execution_boundary.py
"$VENV_PY" scripts/check_r6_readiness_boundary.py
"$VENV_PY" scripts/check_mac_rehearsal_boundary.py
"$VENV_PY" scripts/check_mac_safe_console_boundary.py
"$VENV_PY" scripts/check_mac_safety_rehearsal_boundary.py

"$VENV_PY" -m pytest -q \
  tests/test_r6_paper_asset.py \
  tests/test_r6_asset_evidence.py \
  tests/test_r6_asset_preflight_cli.py \
  tests/test_connectivity_canary_authority.py \
  tests/test_r6_connectivity_candidate.py \
  tests/test_r6_build_connectivity_candidate_cli.py \
  tests/test_r6_flat_account_preflight.py \
  tests/test_r6_flat_account_failclosed.py \
  tests/test_r6_paper_market_data.py \
  tests/test_r6_paper_market_evidence.py \
  tests/test_r6_market_preflight_cli.py \
  tests/test_r6_market_readiness.py \
  tests/test_r6_paper_readiness.py \
  tests/test_r6_paper_readiness_failclosed.py \
  tests/test_r6_readiness_boundary.py \
  tests/test_r6_operational_execute_validation.py \
  tests/test_r6_execute_paper_canary_cli.py \
  tests/test_mac_safe_console.py \
  tests/test_mac_create_workspace.py \
  tests/test_mac_safety_rehearsal.py \
  tests/test_mac_safety_rehearsal_boundary.py

"$VENV_PY" scripts/mac_safety_rehearsal.py >/dev/null
"$VENV_PY" scripts/mac_safety_rehearsal.py --kill-switch >/dev/null
"$VENV_PY" scripts/mac_doctor.py

cat <<'EOF'

MAC BOOTSTRAP: PASS
Recommended single safe entry point:
  bash scripts/mac_start.sh
Try the real Capital Safety Kernel locally:
  bash scripts/mac_start.sh safety-rehearsal
  bash scripts/mac_start.sh safety-rehearsal --kill-switch
Create the first private workspace outside the repository:
  bash scripts/mac_start.sh init-workspace "$HOME/AUTO-TRADE-R6/workspace-001"
Repeatable offline rehearsal after this first installation:
  bash scripts/mac_start.sh rehearsal
Safe diagnostics:
  bash scripts/mac_start.sh doctor
  bash scripts/mac_start.sh readiness "$HOME/AUTO-TRADE-R6/workspace-001"
No Alpaca credential was read by this bootstrap.
No broker endpoint was called by this bootstrap.
No PAPER order was submitted.
This bootstrap ran with R6_EXTERNAL_PAPER_WRITE=DISABLED.
LIVE trading remains BLOCKED.
Read next:
  docs/MAC_PAPER_RUNBOOK.md
EOF
