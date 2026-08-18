#!/bin/zsh
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "$0")" && pwd -P)"
INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"
ROOT="$SOURCE_ROOT"

if [[ "${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED" ]]; then
  echo "AUTO-TRADE: preparación rechazada porque el write genérico está habilitado."
  read -r "?Presiona Enter para cerrar..."
  exit 2
fi

export R6_EXTERNAL_PAPER_WRITE=DISABLED
unset APCA_API_KEY_ID || true
unset APCA_API_SECRET_KEY || true

read_source_head() {
  local root="$1"
  awk -F= '$1 == "source_head" {print $2; exit}' "$root/MAC_BUILD_INFO.txt" 2>/dev/null || true
}

if [[ -f "$SOURCE_ROOT/MAC_STANDALONE_MANIFEST.txt" ]]; then
  if ! grep -Fq 'real_paper_surface=SEPARATE_EXACT_ONE_SHOT' "$SOURCE_ROOT/MAC_STANDALONE_MANIFEST.txt"; then
    echo "AUTO-TRADE: este paquete no declara el gate dedicado FIRST-CANARY PAPER."
    read -r "?Presiona Enter para cerrar..."
    exit 2
  fi

  EXPECTED_HEAD="$(read_source_head "$SOURCE_ROOT")"
  INSTALLED_HEAD="$(read_source_head "$INSTALL_ROOT")"
  if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
    echo "AUTO-TRADE: el paquete descargado no contiene un source_head válido."
    read -r "?Presiona Enter para cerrar..."
    exit 2
  fi

  if [[ ! -x "$INSTALL_ROOT/.venv/bin/python" || "$EXPECTED_HEAD" != "$INSTALLED_HEAD" || ! -f "$INSTALL_ROOT/scripts/mac_first_canary_restart_safe_dashboard.py" ]]; then
    echo "AUTO-TRADE: preparando/reparando la copia certificada instalada..."
    bash "$SOURCE_ROOT/INSTALAR_AUTO_TRADE.command"
  fi

  INSTALLED_HEAD="$(read_source_head "$INSTALL_ROOT")"
  if [[ "$EXPECTED_HEAD" != "$INSTALLED_HEAD" ]]; then
    echo "AUTO-TRADE: la copia instalada no coincide con la build descargada; inicio bloqueado."
    read -r "?Presiona Enter para cerrar..."
    exit 2
  fi
  ROOT="$INSTALL_ROOT"
fi

cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
DASHBOARD="$ROOT/scripts/mac_first_canary_restart_safe_dashboard.py"
PAGE="$ROOT/web/mac_first_canary.html"

if [[ ! -x "$PYTHON" ]]; then
  echo "AUTO-TRADE: falta el runtime Python instalado. Ejecuta INSTALAR_AUTO_TRADE.command desde el paquete certificado."
  read -r "?Presiona Enter para cerrar..."
  exit 2
fi
if [[ ! -f "$DASHBOARD" || ! -f "$PAGE" ]]; then
  echo "AUTO-TRADE: falta el gate de preparación reiniciable."
  read -r "?Presiona Enter para cerrar..."
  exit 2
fi

printf '%s\n' \
  "AUTO-TRADE · Preparar Primer Canary PAPER" \
  "Esta pantalla NO puede enviar órdenes." \
  "Prepara BTC/USD PAPER, guarda evidencia restart-safe y permite la aprobación humana." \
  "Después, si todo queda READY, usa por separado ABRIR_PRIMER_CANARY_REAL_PAPER.command." \
  "Control Center genérico: WRITE DISABLED · LIVE: BLOCKED."

exec "$PYTHON" "$DASHBOARD"
