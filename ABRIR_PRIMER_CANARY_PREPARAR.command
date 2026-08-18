#!/bin/zsh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PYTHON="$HERE/.venv/bin/python"
DASHBOARD="$HERE/scripts/mac_first_canary_restart_safe_dashboard.py"
PAGE="$HERE/web/mac_first_canary.html"

if [[ ! -x "$PYTHON" ]]; then
  echo "AUTO-TRADE: falta el runtime Python instalado. Reinstala la build certificada."
  read -r "?Presiona Enter para cerrar..."
  exit 2
fi
if [[ ! -f "$DASHBOARD" || ! -f "$PAGE" ]]; then
  echo "AUTO-TRADE: falta el gate de preparación reiniciable."
  read -r "?Presiona Enter para cerrar..."
  exit 2
fi
if [[ "${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED" ]]; then
  echo "AUTO-TRADE: preparación rechazada porque el write genérico está habilitado."
  read -r "?Presiona Enter para cerrar..."
  exit 2
fi

export R6_EXTERNAL_PAPER_WRITE=DISABLED
unset APCA_API_KEY_ID || true
unset APCA_API_SECRET_KEY || true

printf '%s\n' \
  "AUTO-TRADE · Preparar Primer Canary PAPER" \
  "Esta pantalla NO puede enviar órdenes." \
  "Prepara BTC/USD PAPER, guarda evidencia restart-safe y permite la aprobación humana." \
  "Después, si todo queda READY, usa por separado ABRIR_PRIMER_CANARY_REAL_PAPER.command." \
  "Control Center genérico: WRITE DISABLED · LIVE: BLOCKED."

exec "$PYTHON" "$DASHBOARD"
