#!/bin/zsh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PYTHON="$HERE/.venv/bin/python"
DASHBOARD="$HERE/scripts/mac_first_canary_dashboard.py"
PAGE="$HERE/web/mac_first_canary.html"

if [[ ! -x "$PYTHON" ]]; then
  echo "AUTO-TRADE: falta el runtime Python del paquete FULL. Reinstala la build actual."
  read -r "?Presiona Enter para cerrar..."
  exit 2
fi
if [[ ! -f "$DASHBOARD" || ! -f "$PAGE" ]]; then
  echo "AUTO-TRADE: falta el gate Primer Canary PAPER. Reinstala la build actual."
  read -r "?Presiona Enter para cerrar..."
  exit 2
fi
if [[ "${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED" ]]; then
  echo "AUTO-TRADE: este launcher de preparación/aprobación/recuperación se niega a iniciar con R6_EXTERNAL_PAPER_WRITE=ENABLED."
  read -r "?Presiona Enter para cerrar..."
  exit 2
fi

export R6_EXTERNAL_PAPER_WRITE=DISABLED
unset APCA_API_KEY_ID || true
unset APCA_API_SECRET_KEY || true

printf '%s\n' \
  "AUTO-TRADE · Primer Canary BTC/USD PAPER" \
  "Preparar + aprobación humana nueva + recuperación GET-only." \
  "POST real: NO EXPUESTO EN ESTA BUILD · LIVE: BLOCKED."

exec "$PYTHON" "$DASHBOARD"
