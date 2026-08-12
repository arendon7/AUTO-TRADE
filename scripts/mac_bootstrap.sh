#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: scripts/mac_bootstrap.sh is intentionally for macOS (Darwin)." >&2
  exit 1
fi

# A child script cannot modify the caller's shell. Refuse to start when the
# caller already exposes real PAPER write authority, rather than hiding it only
# inside this process and returning to an unsafe parent shell afterwards.
if [[ "${R6_EXTERNAL_PAPER_WRITE:-}" == "ENABLED" ]]; then
  cat >&2 <<'EOF'
ERROR: R6_EXTERNAL_PAPER_WRITE is ENABLED in the calling shell.
For installation/rehearsal, disable it in that terminal first:
  unset R6_EXTERNAL_PAPER_WRITE
Then rerun:
  bash scripts/mac_bootstrap.sh
EOF
  exit 1
fi

# Installation/rehearsal must never use broker credentials or execution authority.
unset APCA_API_KEY_ID || true
unset APCA_API_SECRET_KEY || true
export R6_EXTERNAL_PAPER_WRITE="DISABLED"

PYTHON_BIN=""
for candidate in python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
    then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  cat >&2 <<'EOF'
ERROR: Python 3.12+ is required.
Install a current Python 3.12+ distribution, then rerun:
  bash scripts/mac_bootstrap.sh
EOF
  exit 1
fi

echo "AUTO-TRADE Mac bootstrap"
echo "Repository: $ROOT"
echo "Python: $($PYTHON_BIN --version 2>&1)"
echo "Broker credentials loaded by bootstrap: NO"
echo "External PAPER write enabled in bootstrap: NO"
echo "LIVE trading: BLOCKED"

if [[ ! -d .venv ]]; then
  "$PYTHON_BIN" -m venv .venv
fi

VENV_PY="$ROOT/.venv/bin/python"
VENV_PIP="$ROOT/.venv/bin/pip"

"$VENV_PY" -m pip install --upgrade pip
"$VENV_PIP" install -e '.[dev]'

"$VENV_PY" -m compileall -q src tests scripts

# Permanent deterministic authority boundaries. These commands are local-only.
"$VENV_PY" scripts/check_contract_registry.py
"$VENV_PY" scripts/check_debt_register.py
"$VENV_PY" scripts/check_r6_authority.py
"$VENV_PY" scripts/check_r6_live_deny_boundary.py
"$VENV_PY" scripts/check_r6_market_data_boundary.py
"$VENV_PY" scripts/check_r6_operational_lifecycle_boundary.py
"$VENV_PY" scripts/check_r6_operational_execution_boundary.py
"$VENV_PY" scripts/check_r6_readiness_boundary.py

# Focused local rehearsal: no broker I/O and no credentials.
"$VENV_PY" -m pytest -q \
  tests/test_r6_paper_market_data.py \
  tests/test_r6_paper_market_evidence.py \
  tests/test_r6_market_preflight_cli.py \
  tests/test_r6_market_readiness.py \
  tests/test_r6_paper_readiness.py \
  tests/test_r6_paper_readiness_failclosed.py \
  tests/test_r6_readiness_boundary.py \
  tests/test_r6_operational_execute_validation.py \
  tests/test_r6_execute_paper_canary_cli.py

# Final local diagnostics. This does not load .env or use broker I/O.
"$VENV_PY" scripts/mac_doctor.py

cat <<'EOF'

MAC BOOTSTRAP: PASS

Repeatable offline rehearsal after this first installation:
  bash scripts/mac_rehearsal.sh

Safe diagnostic commands:
  .venv/bin/python scripts/mac_doctor.py
  .venv/bin/python scripts/mac_doctor.py --workspace <WORKSPACE>
  .venv/bin/python scripts/r6_inspect_paper_readiness.py --workspace <WORKSPACE>

No Alpaca credential was read by this bootstrap.
No broker endpoint was called by this bootstrap.
No PAPER order was submitted.
This bootstrap ran with R6_EXTERNAL_PAPER_WRITE=DISABLED.
LIVE trading remains BLOCKED.

Read next:
  docs/MAC_PAPER_RUNBOOK.md
EOF
