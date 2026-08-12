#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: scripts/mac_rehearsal.sh is intentionally for macOS (Darwin)." >&2
  exit 1
fi

if [[ "${R6_EXTERNAL_PAPER_WRITE:-}" == "ENABLED" ]]; then
  cat >&2 <<'EOF'
ERROR: R6_EXTERNAL_PAPER_WRITE is ENABLED in the calling shell.
Offline rehearsal refuses to run while the external PAPER write gate is enabled.
Run:
  unset R6_EXTERNAL_PAPER_WRITE
Then retry:
  bash scripts/mac_rehearsal.sh
EOF
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  cat >&2 <<'EOF'
ERROR: .venv is missing. Run the first-time bootstrap first:
  bash scripts/mac_bootstrap.sh
EOF
  exit 1
fi

# Rehearsal is deliberately broker-inert even if the caller has PAPER keys loaded.
unset APCA_API_KEY_ID || true
unset APCA_API_SECRET_KEY || true
export R6_EXTERNAL_PAPER_WRITE="DISABLED"

PY="$ROOT/.venv/bin/python"

echo "AUTO-TRADE Mac offline rehearsal"
echo "Broker credentials used: NO"
echo "Broker network I/O: NO"
echo "External PAPER write: DISABLED"
echo "LIVE trading: BLOCKED"

"$PY" scripts/mac_doctor.py
"$PY" scripts/check_contract_registry.py
"$PY" scripts/check_debt_register.py
"$PY" scripts/check_r6_authority.py
"$PY" scripts/check_r6_live_deny_boundary.py
"$PY" scripts/check_r6_market_data_boundary.py
"$PY" scripts/check_r6_operational_lifecycle_boundary.py
"$PY" scripts/check_r6_operational_execution_boundary.py
"$PY" scripts/check_r6_readiness_boundary.py
"$PY" scripts/check_mac_rehearsal_boundary.py
"$PY" scripts/check_mac_safe_console_boundary.py

"$PY" -m pytest -q \
  tests/test_r6_paper_market_data.py \
  tests/test_r6_paper_market_evidence.py \
  tests/test_r6_market_preflight_cli.py \
  tests/test_r6_market_readiness.py \
  tests/test_r6_paper_readiness.py \
  tests/test_r6_paper_readiness_failclosed.py \
  tests/test_r6_operational_prepare.py \
  tests/test_r6_operational_execute_validation.py \
  tests/test_r6_execute_paper_canary_cli.py \
  tests/test_mac_safe_console.py \
  tests/test_mac_create_workspace.py

cat <<'EOF'

MAC OFFLINE REHEARSAL: PASS
No credential was used.
No broker endpoint was called.
No PAPER order was submitted.
LIVE trading remains BLOCKED.

Safe local entry point:
  bash scripts/mac_start.sh

Create a private workspace:
  bash scripts/mac_start.sh init-workspace "$HOME/AUTO-TRADE-R6/workspace-001"
EOF
