#!/bin/zsh
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "$0")" && pwd -P)"
INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"
ROOT="$SOURCE_ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "AUTO-TRADE: esta app es exclusivamente para macOS." >&2
  exit 2
fi
if [[ "${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED" ]]; then
  echo "AUTO-TRADE: inicio bloqueado porque el write genérico está habilitado." >&2
  exit 2
fi

export R6_EXTERNAL_PAPER_WRITE=DISABLED
unset APCA_API_KEY_ID 2>/dev/null || true
unset APCA_API_SECRET_KEY 2>/dev/null || true

read_source_head() {
  local root="$1"
  awk -F= '$1 == "source_head" {print $2; exit}' "$root/MAC_BUILD_INFO.txt" 2>/dev/null || true
}

if [[ -f "$SOURCE_ROOT/MAC_STANDALONE_MANIFEST.txt" ]]; then
  if ! grep -Fq 'first_canary_unified_surface=ONE_APP' "$SOURCE_ROOT/MAC_STANDALONE_MANIFEST.txt"; then
    echo "AUTO-TRADE: este paquete no declara la app unificada FIRST-CANARY." >&2
    exit 2
  fi
  EXPECTED_HEAD="$(read_source_head "$SOURCE_ROOT")"
  INSTALLED_HEAD="$(read_source_head "$INSTALL_ROOT")"
  if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
    echo "AUTO-TRADE: source_head inválido en el paquete." >&2
    exit 2
  fi
  if [[ ! -x "$INSTALL_ROOT/.venv/bin/python" || "$EXPECTED_HEAD" != "$INSTALLED_HEAD" || ! -f "$INSTALL_ROOT/scripts/mac_first_canary_unified_queue.py" ]]; then
    echo "AUTO-TRADE: instalando/reparando la copia certificada..."
    bash "$SOURCE_ROOT/INSTALAR_AUTO_TRADE.command"
  fi
  INSTALLED_HEAD="$(read_source_head "$INSTALL_ROOT")"
  if [[ "$EXPECTED_HEAD" != "$INSTALLED_HEAD" ]]; then
    echo "AUTO-TRADE: la copia instalada no coincide con esta build; inicio bloqueado." >&2
    exit 2
  fi
  ROOT="$INSTALL_ROOT"
fi

cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
# Certified authority base remains scripts/mac_first_canary_unified_dashboard.py.
# Operator entrypoint adds GET-only queue recovery without adding POST authority.
DASHBOARD="$ROOT/scripts/mac_first_canary_unified_queue.py"
PAGE="$ROOT/web/mac_first_canary_unified.html"

if [[ ! -x "$PYTHON" ]]; then
  echo "AUTO-TRADE: falta el runtime Python. Ejecuta INSTALAR_AUTO_TRADE.command." >&2
  exit 2
fi
if [[ ! -f "$DASHBOARD" || ! -f "$PAGE" ]]; then
  echo "AUTO-TRADE: falta la app unificada del primer canary." >&2
  exit 2
fi

printf '%s\n' \
  "AUTO-TRADE · Primer Canary PAPER · UNA SOLA APP" \
  "Flujo: Conectar -> Preparar -> Revisar/Aprobar -> Ejecutar una vez -> Resultado." \
  "Attempt IDs, challenges, TTL y recovery se gestionan internamente." \
  "Si hay varios intentos pendientes, se reconcilian por cola GET-only antes de permitir otro canary." \
  "PAPER ONLY · BTC/USD · máximo USD 5 · LIVE BLOCKED · RETRY POST FALSE"

exec "$PYTHON" "$DASHBOARD"
