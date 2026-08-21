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
  if ! grep -Fq 'r7_paper_operations_surface=GET_ONLY' "$SOURCE_ROOT/MAC_STANDALONE_MANIFEST.txt"; then
    echo "AUTO-TRADE: este paquete no declara la superficie R7 PAPER Operations GET-only." >&2
    exit 2
  fi
  EXPECTED_HEAD="$(read_source_head "$SOURCE_ROOT")"
  INSTALLED_HEAD="$(read_source_head "$INSTALL_ROOT")"
  if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
    echo "AUTO-TRADE: source_head inválido en el paquete." >&2
    exit 2
  fi
  if [[ ! -x "$INSTALL_ROOT/.venv/bin/python" || "$EXPECTED_HEAD" != "$INSTALLED_HEAD" || ! -f "$INSTALL_ROOT/scripts/mac_r7_paper_operations_dashboard.py" || ! -f "$INSTALL_ROOT/web/mac_r7_paper_operations.html" ]]; then
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
# R7 is an operator/read-model overlay only. Certified entry authority remains
# inherited from the R6 unified auto-settlement session. R7 adds Portfolio/Safety
# broker truth via GET and an exposure interlock before preparing any new BUY.
DASHBOARD="$ROOT/scripts/mac_r7_paper_operations_dashboard.py"
PAGE="$ROOT/web/mac_r7_paper_operations.html"

if [[ ! -x "$PYTHON" ]]; then
  echo "AUTO-TRADE: falta el runtime Python. Ejecuta INSTALAR_AUTO_TRADE.command." >&2
  exit 2
fi
if [[ ! -f "$DASHBOARD" || ! -f "$PAGE" ]]; then
  echo "AUTO-TRADE: falta la superficie R7 PAPER Operations certificada." >&2
  exit 2
fi

printf '%s\n' \
  "AUTO-TRADE · R7 PAPER OPERATIONS · UNA SOLA APP" \
  "Portfolio y Safety: broker truth GET-only. Una posición u orden abierta bloquea preparar otro BUY." \
  "Entrada heredada R6: Conectar -> Preparar -> Revisar/Aprobar -> Ejecutar una vez -> Reconciliar por GET." \
  "Attempt IDs, challenges, TTL y recovery se gestionan internamente." \
  "Cierre de posición: WRITE DISABLED en esta build." \
  "PAPER ONLY · BTC/USD BUY LIMIT IOC · USD 10-12 · TARGET ~USD 10.50 · LIVE BLOCKED · RETRY POST FALSE"

exec "$PYTHON" "$DASHBOARD"
