#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED" ]]; then
  echo "SAFE START BLOCKED: R6_EXTERNAL_PAPER_WRITE=ENABLED" >&2
  echo "Run: export R6_EXTERNAL_PAPER_WRITE=DISABLED" >&2
  exit 2
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "AUTO-TRADE is not bootstrapped yet. Running safe Mac bootstrap..."
  bash scripts/mac_bootstrap.sh
fi

export R6_EXTERNAL_PAPER_WRITE=DISABLED

cat <<'EOF'
AUTO-TRADE R6 — MAC SAFE START

This entry point has NO order execution option.
Useful commands:

  bash scripts/mac_start.sh doctor
  bash scripts/mac_start.sh rehearsal
  bash scripts/mac_start.sh safety-rehearsal
  bash scripts/mac_start.sh init-workspace "$HOME/AUTO-TRADE-R6/workspace-001"
  bash scripts/mac_start.sh readiness <WORKSPACE>
  bash scripts/mac_start.sh pre-canary-status <WORKSPACE>

GET-only PAPER network steps, only after configuring PAPER credentials:

  bash scripts/mac_start.sh account-preflight <WORKSPACE> <ALPACA_PAPER_ACCOUNT_ID>
  bash scripts/mac_start.sh asset-preflight <WORKSPACE> <SYMBOL>
  bash scripts/mac_start.sh flat-account-preflight <WORKSPACE>
  bash scripts/mac_start.sh market-preflight <WORKSPACE> <SYMBOL>

After those four GET-only gates, credentials are stripped again:

  bash scripts/mac_start.sh build-connectivity-candidate <WORKSPACE>
  bash scripts/mac_start.sh prepare-connectivity-candidate <WORKSPACE>
  bash scripts/mac_start.sh pre-canary-status <WORKSPACE>

The candidate uses a real CapitalSafetyKernel RiskDecision + OMS VALIDATED order for
purpose CONNECTIVITY_CANARY. Preparation freezes the bounded bracket package.
Audited invariants: NO Strategy Health; NO operator authority; NO external POST authority.

After a separately executed interactive FIRST human decision, this safe launcher may
freeze the review document only:

  bash scripts/mac_start.sh review-receipt <WORKSPACE>
  bash scripts/mac_start.sh pre-canary-status <WORKSPACE>

The first-canary safe path exposed here is:
  account -> asset -> flat account -> market -> connectivity candidate -> offline preparation -> status/review

Second human intent, reviewed Final Freshness, staging and any single PAPER POST remain
outside this safe launcher. READY from pre-canary-status means ready only for the named
next gate; it NEVER means POST-authorized.
EOF

case "${1:-}" in
  "")
    exec .venv/bin/python scripts/mac_safe_console.py doctor
    ;;
  init-workspace)
    shift
    [[ $# -eq 1 ]] || { echo "usage: bash scripts/mac_start.sh init-workspace <WORKSPACE>" >&2; exit 2; }
    exec .venv/bin/python scripts/mac_safe_console.py init-workspace --workspace "$1"
    ;;
  doctor)
    shift
    exec .venv/bin/python scripts/mac_safe_console.py doctor "$@"
    ;;
  rehearsal)
    shift
    exec .venv/bin/python scripts/mac_safe_console.py rehearsal "$@"
    ;;
  safety-rehearsal)
    shift
    exec .venv/bin/python scripts/mac_safe_console.py safety-rehearsal "$@"
    ;;
  readiness)
    shift
    [[ $# -eq 1 ]] || { echo "usage: bash scripts/mac_start.sh readiness <WORKSPACE>" >&2; exit 2; }
    exec .venv/bin/python scripts/mac_safe_console.py readiness --workspace "$1"
    ;;
  pre-canary-status)
    shift
    [[ $# -eq 1 ]] || { echo "usage: bash scripts/mac_start.sh pre-canary-status <WORKSPACE>" >&2; exit 2; }
    exec .venv/bin/python scripts/mac_safe_console.py pre-canary-status --workspace "$1"
    ;;
  account-preflight)
    shift
    [[ $# -eq 2 ]] || { echo "usage: bash scripts/mac_start.sh account-preflight <WORKSPACE> <ALPACA_PAPER_ACCOUNT_ID>" >&2; exit 2; }
    exec .venv/bin/python scripts/mac_safe_console.py account-preflight \
      --workspace "$1" --expected-account-id "$2" --allow-paper-account-read
    ;;
  asset-preflight)
    shift
    [[ $# -eq 2 ]] || { echo "usage: bash scripts/mac_start.sh asset-preflight <WORKSPACE> <SYMBOL>" >&2; exit 2; }
    exec .venv/bin/python scripts/mac_safe_console.py asset-preflight \
      --workspace "$1" --symbol "$2" --allow-paper-asset-read
    ;;
  flat-account-preflight)
    shift
    [[ $# -eq 1 ]] || { echo "usage: bash scripts/mac_start.sh flat-account-preflight <WORKSPACE>" >&2; exit 2; }
    exec .venv/bin/python scripts/mac_safe_console.py flat-account-preflight \
      --workspace "$1" --allow-paper-flat-account-read
    ;;
  market-preflight)
    shift
    [[ $# -eq 2 ]] || { echo "usage: bash scripts/mac_start.sh market-preflight <WORKSPACE> <SYMBOL>" >&2; exit 2; }
    exec .venv/bin/python scripts/mac_safe_console.py market-preflight \
      --workspace "$1" --symbol "$2" --allow-paper-market-read
    ;;
  build-connectivity-candidate)
    shift
    [[ $# -eq 1 ]] || { echo "usage: bash scripts/mac_start.sh build-connectivity-candidate <WORKSPACE>" >&2; exit 2; }
    exec .venv/bin/python scripts/mac_safe_console.py build-connectivity-candidate --workspace "$1"
    ;;
  prepare-connectivity-candidate)
    shift
    [[ $# -eq 1 ]] || { echo "usage: bash scripts/mac_start.sh prepare-connectivity-candidate <WORKSPACE>" >&2; exit 2; }
    exec .venv/bin/python scripts/mac_safe_console.py prepare-connectivity-candidate --workspace "$1"
    ;;
  review-receipt)
    shift
    [[ $# -eq 1 ]] || { echo "usage: bash scripts/mac_start.sh review-receipt <WORKSPACE>" >&2; exit 2; }
    exec .venv/bin/python scripts/mac_safe_console.py review-receipt --workspace "$1"
    ;;
  *)
    echo "Unknown safe command: $1" >&2
    exit 2
    ;;
esac
