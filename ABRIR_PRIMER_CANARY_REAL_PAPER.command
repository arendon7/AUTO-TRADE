#!/bin/zsh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PYTHON="$HERE/.venv/bin/python"
DASHBOARD="$HERE/scripts/mac_first_canary_real_paper_dashboard.py"
PAGE="$HERE/web/mac_first_canary_real_paper.html"

if [[ ! -x "$PYTHON" ]]; then
  echo "AUTO-TRADE: falta el runtime Python instalado. Reinstala la build certificada."
  read -r "?Presiona Enter para cerrar..."
  exit 2
fi
if [[ ! -f "$DASHBOARD" || ! -f "$PAGE" ]]; then
  echo "AUTO-TRADE: falta el gate separado Primer Canary REAL PAPER."
  read -r "?Presiona Enter para cerrar..."
  exit 2
fi
if [[ "${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED" ]]; then
  echo "AUTO-TRADE: este gate se niega a iniciar con el write genérico habilitado."
  read -r "?Presiona Enter para cerrar..."
  exit 2
fi

export R6_EXTERNAL_PAPER_WRITE=DISABLED
unset APCA_API_KEY_ID || true
unset APCA_API_SECRET_KEY || true

printf '%s\n' \
  "AUTO-TRADE · Primer Canary REAL PAPER · execution-only" \
  "Puede enviar UNA orden BTC/USD PAPER ya preparada y aprobada, máximo USD 5." \
  "Requiere un segundo challenge exacto inmediatamente antes del POST." \
  "Después de consentir/iniciar: NO REINTENTAR POST; usar recuperación GET-only." \
  "Control Center genérico: WRITE DISABLED · LIVE: BLOCKED."

exec "$PYTHON" "$DASHBOARD"
