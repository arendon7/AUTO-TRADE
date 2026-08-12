#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
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
STANDALONE_MARKER="$ROOT/MAC_STANDALONE_MANIFEST.txt"
STANDALONE_MODE="NO"

if [[ -f "$STANDALONE_MARKER" ]]; then
  STANDALONE_MODE="YES"
  EXPECTED_INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"
  if [[ "$ROOT" != "$EXPECTED_INSTALL_ROOT" ]]; then
    echo "ERROR: FULL/STANDALONE runtime execution is allowed only from:" >&2
    echo "  $EXPECTED_INSTALL_ROOT" >&2
    echo "Run INSTALAR_AUTO_TRADE.command from the downloaded package; it verifies and relocates the bundle before CPython executes." >&2
    exit 2
  fi

  case "$(uname -m)" in
    arm64) RUNTIME_ARCH="arm64" ;;
    x86_64) RUNTIME_ARCH="x86_64" ;;
    *)
      echo "ERROR: unsupported Mac architecture for FULL/STANDALONE package: $(uname -m)" >&2
      exit 1
      ;;
  esac

  RUNTIME_ARCHIVE="$ROOT/vendor/runtime/cpython-3.12.13-20260718-${RUNTIME_ARCH}.tar.gz"
  RUNTIME_SUMS="$ROOT/vendor/runtime/SHA256SUMS"
  WHEELHOUSE="$ROOT/vendor/wheels"
  WHEEL_SUMS="$WHEELHOUSE/SHA256SUMS"
  RUNTIME_ROOT="$ROOT/.runtime/$RUNTIME_ARCH"

  [[ -f "$RUNTIME_ARCHIVE" ]] || { echo "ERROR: standalone runtime archive is missing for $RUNTIME_ARCH." >&2; exit 1; }
  [[ -f "$RUNTIME_SUMS" ]] || { echo "ERROR: standalone runtime SHA256 manifest is missing." >&2; exit 1; }
  [[ -d "$WHEELHOUSE" ]] || { echo "ERROR: standalone wheelhouse is missing." >&2; exit 1; }
  [[ -f "$WHEEL_SUMS" ]] || { echo "ERROR: standalone wheel SHA256 manifest is missing." >&2; exit 1; }

  (
    cd "$ROOT/vendor/runtime"
    shasum -a 256 -c SHA256SUMS
  )
  (
    cd "$WHEELHOUSE"
    shasum -a 256 -c SHA256SUMS
  )

  if [[ ! -x "$RUNTIME_ROOT/python/bin/python3" ]]; then
    TMP_RUNTIME="$ROOT/.runtime/.extract-${RUNTIME_ARCH}-$$"
    rm -rf "$TMP_RUNTIME" "$RUNTIME_ROOT"
    mkdir -p "$TMP_RUNTIME" "$ROOT/.runtime"
    tar -xzf "$RUNTIME_ARCHIVE" -C "$TMP_RUNTIME"
    [[ -x "$TMP_RUNTIME/python/bin/python3" ]] || {
      rm -rf "$TMP_RUNTIME"
      echo "ERROR: embedded runtime archive did not contain python/bin/python3." >&2
      exit 1
    }
    mv "$TMP_RUNTIME" "$RUNTIME_ROOT"
  fi

  PYTHON_BIN="$RUNTIME_ROOT/python/bin/python3"
  set +e
  "$PYTHON_BIN" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info[:3] == (3, 12, 13) else 1)
PY
  PYTHON_PROBE_STATUS=$?
  set -e
  if [[ $PYTHON_PROBE_STATUS -ne 0 ]]; then
    echo "ERROR: embedded CPython probe failed with status $PYTHON_PROBE_STATUS." >&2
    echo "Runtime path: $PYTHON_BIN" >&2
    echo "The installed FULL copy did not pass trusted execution; installation remains fail-closed." >&2
    exit 2
  fi

  EXPECTED_VENV_STAMP="${RUNTIME_ARCH}|3.12.13|20260718|${ROOT}"
  VENV_STAMP="$ROOT/.venv/.autotrade-standalone-runtime"
  if [[ -d "$ROOT/.venv" ]]; then
    CURRENT_STAMP="$(cat "$VENV_STAMP" 2>/dev/null || true)"
    if [[ "$CURRENT_STAMP" != "$EXPECTED_VENV_STAMP" ]]; then
      rm -rf "$ROOT/.venv"
    fi
  fi

  if [[ ! -d "$ROOT/.venv" ]]; then
    "$PYTHON_BIN" -m venv "$ROOT/.venv"
  fi
  VENV_PY="$ROOT/.venv/bin/python"
  VENV_PIP="$ROOT/.venv/bin/pip"
  "$VENV_PY" -m pip install \
    --no-index \
    --disable-pip-version-check \
    --find-links "$WHEELHOUSE" \
    'auto-trade-core[dev]==0.4.0.dev0'
  printf '%s\n' "$EXPECTED_VENV_STAMP" > "$VENV_STAMP"
else
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
  [[ -n "$PYTHON_BIN" ]] || { echo "ERROR: Python 3.12+ is required for source/rehearsal checkout. Use the FULL/STANDALONE package to avoid this host requirement." >&2; exit 1; }

  if [[ ! -d .venv ]]; then "$PYTHON_BIN" -m venv .venv; fi
  VENV_PY="$ROOT/.venv/bin/python"
  VENV_PIP="$ROOT/.venv/bin/pip"
  "$VENV_PY" -m pip install --upgrade pip
  "$VENV_PIP" install -e '.[dev]'
fi

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
"$VENV_PY" scripts/check_mac_standalone_boundary.py

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

cat <<EOF

MAC BOOTSTRAP: PASS
standalone_mode=${STANDALONE_MODE}
Recommended single safe entry point:
  bash scripts/mac_start.sh
Try the real Capital Safety Kernel locally:
  bash scripts/mac_start.sh safety-rehearsal
  bash scripts/mac_start.sh safety-rehearsal --kill-switch
Create the first private workspace outside the repository:
  bash scripts/mac_start.sh init-workspace \"\$HOME/AUTO-TRADE-R6/workspace-001\"
Repeatable offline rehearsal after this first installation:
  bash scripts/mac_start.sh rehearsal
Safe diagnostics:
  bash scripts/mac_start.sh doctor
  bash scripts/mac_start.sh readiness \"\$HOME/AUTO-TRADE-R6/workspace-001\"
No Alpaca credential was read by this bootstrap.
No broker endpoint was called by this bootstrap.
No PAPER order was submitted.
This bootstrap ran with R6_EXTERNAL_PAPER_WRITE=DISABLED.
LIVE trading remains BLOCKED.
Read next:
  docs/MAC_PAPER_RUNBOOK.md
EOF
