#!/bin/bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "$0")" && pwd -P)"
# Keep the historical install path so an upgrade preserves the existing workspace
# contract and does not create a second parallel app tree.
INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"
ACTIVE_ROOT="$SOURCE_ROOT"
STANDALONE_MODE="NO"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "AUTO-TRADE FULL INSTALL: este instalador es exclusivamente para macOS." >&2
  exit 2
fi
if [[ "${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED" ]]; then
  echo "AUTO-TRADE FULL INSTALL BLOCKED: R6_EXTERNAL_PAPER_WRITE=ENABLED." >&2
  exit 2
fi

unset APCA_API_KEY_ID 2>/dev/null || true
unset APCA_API_SECRET_KEY 2>/dev/null || true
export R6_EXTERNAL_PAPER_WRITE=DISABLED

verify_standalone_assets() {
  local root="$1"
  [[ -f "$root/MAC_STANDALONE_MANIFEST.txt" ]] || { echo "ERROR: falta MAC_STANDALONE_MANIFEST.txt en $root" >&2; return 1; }
  [[ -f "$root/vendor/runtime/SHA256SUMS" ]] || { echo "ERROR: falta el manifiesto SHA-256 del runtime." >&2; return 1; }
  [[ -f "$root/vendor/wheels/SHA256SUMS" ]] || { echo "ERROR: falta el manifiesto SHA-256 del wheelhouse." >&2; return 1; }
  (cd "$root/vendor/runtime" && shasum -a 256 -c SHA256SUMS)
  (cd "$root/vendor/wheels" && shasum -a 256 -c SHA256SUMS)
}

read_source_head() {
  local root="$1"
  awk -F= '$1 == "source_head" {print $2; exit}' "$root/MAC_BUILD_INFO.txt" 2>/dev/null || true
}

prune_real_paper_surface_if_disabled() {
  local root="$1"
  local manifest="$root/MAC_STANDALONE_MANIFEST.txt"
  [[ -f "$manifest" ]] || return 1
  if grep -Fq 'real_paper_surface=SEPARATE_EXACT_ONE_SHOT' "$manifest"; then return 0; fi
  rm -f \
    "$root/ABRIR_PRIMER_CANARY_PREPARAR.command" \
    "$root/ABRIR_PRIMER_CANARY_REAL_PAPER.command" \
    "$root/LEEME_PRIMER_CANARY_REAL_PAPER.md" \
    "$root/web/mac_first_canary_real_paper.html" \
    "$root/scripts/mac_first_canary_restart_safe_dashboard.py" \
    "$root/scripts/mac_first_canary_real_paper_dashboard.py" \
    "$root/scripts/mac_crypto_first_canary_execute_real_paper.py" \
    "$root/scripts/check_r6_first_canary_restart_safe_dashboard.py" \
    "$root/scripts/check_r6_first_canary_real_paper_delegate.py" \
    "$root/scripts/check_r6_first_canary_real_paper_dashboard.py"
}

if [[ -f "$SOURCE_ROOT/MAC_STANDALONE_MANIFEST.txt" ]]; then
  STANDALONE_MODE="YES"
  verify_standalone_assets "$SOURCE_ROOT"
  SOURCE_HEAD="$(read_source_head "$SOURCE_ROOT")"
  if [[ ! "$SOURCE_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
    echo "ERROR: el paquete FULL no contiene un source_head SHA-1 válido." >&2
    exit 2
  fi
  if [[ -L "$INSTALL_ROOT" ]]; then
    echo "ERROR: $INSTALL_ROOT es un symlink; instalación bloqueada por seguridad." >&2
    exit 2
  fi
  mkdir -p "$HOME/Applications"

  if [[ "$SOURCE_ROOT" != "$INSTALL_ROOT" ]]; then
    STAGE_ROOT="$HOME/Applications/.AUTO-TRADE-R6.install.$$"
    rm -rf "$STAGE_ROOT"
    echo
    echo "Preparando copia instalada fuera de Downloads/Finder quarantine..."
    echo "Destino: $INSTALL_ROOT"
    ditto --norsrc --noqtn "$SOURCE_ROOT" "$STAGE_ROOT"
    rm -rf "$STAGE_ROOT/.venv" "$STAGE_ROOT/.runtime" "$STAGE_ROOT/.git" "$STAGE_ROOT/.pytest_cache" "$STAGE_ROOT/.coverage" "$STAGE_ROOT/coverage.json" "$STAGE_ROOT/.env"
    find "$STAGE_ROOT" -type f \( -name '*.sqlite3' -o -name '*.sqlite3-*' -o -name '*.pyc' \) -delete 2>/dev/null || true
    find "$STAGE_ROOT" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
    prune_real_paper_surface_if_disabled "$STAGE_ROOT"
    verify_standalone_assets "$STAGE_ROOT"
    STAGED_HEAD="$(read_source_head "$STAGE_ROOT")"
    if [[ "$STAGED_HEAD" != "$SOURCE_HEAD" ]]; then
      rm -rf "$STAGE_ROOT"
      echo "ERROR: source_head cambió durante la copia de instalación." >&2
      exit 2
    fi
    rm -rf "$INSTALL_ROOT"
    mv "$STAGE_ROOT" "$INSTALL_ROOT"
    ACTIVE_ROOT="$INSTALL_ROOT"
  else
    ACTIVE_ROOT="$SOURCE_ROOT"
    prune_real_paper_surface_if_disabled "$ACTIVE_ROOT"
  fi
else
  if [[ ! -d "$SOURCE_ROOT/.git" ]]; then
    echo "ERROR: este paquete no contiene el manifest FULL/STANDALONE." >&2
    echo "Descarga el artefacto Mac certificado; no uses el bundle source/rehearsal para instalar." >&2
    exit 2
  fi
fi

cd "$ACTIVE_ROOT"
clear || true
cat <<EOF
AUTO-TRADE R7 PAPER OPERATIONS — INSTALACIÓN SEGURA

• La autoridad de entrada permanece en la ruta R6 certificada e idempotente.
• R7 añade Portfolio/Safety GET-only y bloqueo de nuevo BUY si existe exposición.
• FULL/STANDALONE usa el CPython y wheelhouse embebidos.
• No requiere Homebrew ni Python del sistema.
• No usa PyPI/Internet para instalar el runtime del paquete FULL.
• En FULL descargado, instala en: $INSTALL_ROOT
• PAPER write genérico permanece DISABLED.
• Cierre de posición write permanece DISABLED en esta build.
• LIVE permanece BLOCKED.
• Esta instalación NO envía órdenes.
EOF

echo
bash "$ACTIVE_ROOT/scripts/mac_bootstrap.sh"

PY="$ACTIVE_ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { echo "ERROR: .venv no quedó instalado." >&2; exit 2; }

if [[ -f "$ACTIVE_ROOT/MAC_STANDALONE_MANIFEST.txt" ]] && grep -Fq 'first_canary_unified_surface=ONE_APP' "$ACTIVE_ROOT/MAC_STANDALONE_MANIFEST.txt"; then
  if ! grep -Fq 'r7_paper_operations_surface=GET_ONLY' "$ACTIVE_ROOT/MAC_STANDALONE_MANIFEST.txt"; then
    echo "ERROR: el artefacto ONE-APP no declara R7 PAPER Operations GET-only." >&2
    exit 2
  fi
  [[ -f "$ACTIVE_ROOT/scripts/mac_r7_paper_operations_dashboard.py" ]] || { echo "ERROR: falta dashboard R7." >&2; exit 2; }
  [[ -f "$ACTIVE_ROOT/web/mac_r7_paper_operations.html" ]] || { echo "ERROR: falta UI R7." >&2; exit 2; }

  # The installed package certifies both layers: historical R6 entry authority
  # and the R7 read-only operator overlay. No close writer is enabled here.
  "$PY" "$ACTIVE_ROOT/scripts/check_r6_first_canary_unified_dashboard.py"
  "$PY" "$ACTIVE_ROOT/scripts/check_r6_live_deny_boundary.py"
  "$PY" "$ACTIVE_ROOT/scripts/check_r6_authority.py"
  "$PY" "$ACTIVE_ROOT/scripts/check_r7_paper_operations_mac_boundary.py"
  "$PY" -m pytest -q \
    "$ACTIVE_ROOT/tests/test_r6_first_canary_unified_dashboard.py" \
    "$ACTIVE_ROOT/tests/test_mac_r7_paper_operations_dashboard.py"
  echo "R7 PAPER Operations + inherited R6 authority installed checks: OK"
else
  "$PY" "$ACTIVE_ROOT/scripts/check_mac_standalone_boundary.py"
  "$PY" "$ACTIVE_ROOT/scripts/check_mac_dashboard_boundary.py"
  "$PY" "$ACTIVE_ROOT/scripts/mac_doctor.py"
fi

cat <<EOF

============================================================
AUTO-TRADE R7 OPERATIONS INSTALL: OK
============================================================
Runtime: OK
Dependencias: OK
R7 Portfolio/Safety read model: GET-ONLY
Entrada first-canary: R6 authority inherited
PAPER write genérico: DISABLED
Close write: DISABLED
Capital authority durante instalación: NONE
LIVE: BLOCKED
Orden enviada por instalación: NO
install_root=$ACTIVE_ROOT
standalone_install_relocated=$([[ "$STANDALONE_MODE" == "YES" && "$SOURCE_ROOT" != "$ACTIVE_ROOT" ]] && echo YES || echo NO)

Ahora abre:
  $ACTIVE_ROOT/ABRIR_AUTO_TRADE.command
============================================================
EOF