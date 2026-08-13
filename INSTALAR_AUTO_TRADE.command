#!/bin/bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "$0")" && pwd -P)"
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
  [[ -f "$root/MAC_STANDALONE_MANIFEST.txt" ]] || {
    echo "ERROR: falta MAC_STANDALONE_MANIFEST.txt en $root" >&2
    return 1
  }
  [[ -f "$root/vendor/runtime/SHA256SUMS" ]] || {
    echo "ERROR: falta el manifiesto SHA-256 del runtime." >&2
    return 1
  }
  [[ -f "$root/vendor/wheels/SHA256SUMS" ]] || {
    echo "ERROR: falta el manifiesto SHA-256 del wheelhouse." >&2
    return 1
  }
  (
    cd "$root/vendor/runtime"
    shasum -a 256 -c SHA256SUMS
  )
  (
    cd "$root/vendor/wheels"
    shasum -a 256 -c SHA256SUMS
  )
}

read_source_head() {
  local root="$1"
  awk -F= '$1 == "source_head" {print $2; exit}' "$root/MAC_BUILD_INFO.txt" 2>/dev/null || true
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

    # The downloaded/extracted package may carry Finder quarantine metadata.
    # ditto --norsrc --noqtn deliberately copies only the verified package bytes
    # and normal POSIX metadata, without resource forks, xattrs, ACLs or quarantine.
    ditto --norsrc --noqtn "$SOURCE_ROOT" "$STAGE_ROOT"

    # Never promote transient/local state from a previously attempted source folder.
    rm -rf \
      "$STAGE_ROOT/.venv" \
      "$STAGE_ROOT/.runtime" \
      "$STAGE_ROOT/.git" \
      "$STAGE_ROOT/.pytest_cache" \
      "$STAGE_ROOT/.coverage" \
      "$STAGE_ROOT/coverage.json" \
      "$STAGE_ROOT/.env"
    find "$STAGE_ROOT" -type f \( -name '*.sqlite3' -o -name '*.sqlite3-*' -o -name '*.pyc' \) -delete 2>/dev/null || true
    find "$STAGE_ROOT" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

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
  fi
else
  if [[ ! -d "$SOURCE_ROOT/.git" ]]; then
    echo "ERROR: este paquete no contiene el manifest FULL/STANDALONE." >&2
    echo "Descarga AUTO-TRADE-R6-MAC-FULL; no uses el bundle source/rehearsal para instalar." >&2
    exit 2
  fi
fi

cd "$ACTIVE_ROOT"

clear || true
cat <<EOF
AUTO-TRADE R6 — INSTALACIÓN SEGURA

• FULL/STANDALONE usa el CPython y wheelhouse embebidos.
• No requiere Homebrew.
• No requiere Python del sistema en el paquete FULL.
• No usa PyPI/Internet para instalar el runtime del paquete FULL.
• En FULL descargado, instala en: $INSTALL_ROOT
• PAPER write permanece DISABLED.
• LIVE permanece BLOCKED.
• Esta instalación NO envía órdenes.
EOF

echo
bash "$ACTIVE_ROOT/scripts/mac_bootstrap.sh"

PY="$ACTIVE_ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { echo "ERROR: .venv no quedó instalado." >&2; exit 2; }

"$PY" "$ACTIVE_ROOT/scripts/check_mac_standalone_boundary.py"
"$PY" "$ACTIVE_ROOT/scripts/check_mac_dashboard_boundary.py"
"$PY" "$ACTIVE_ROOT/scripts/mac_doctor.py"

cat <<EOF

============================================================
AUTO-TRADE R6 INSTALL: OK
============================================================
Runtime: OK
Dependencias: OK
Control Center: OK
PAPER write: DISABLED
Capital authority: NONE
LIVE: BLOCKED
Orden enviada por instalación: NO
install_root=$ACTIVE_ROOT
standalone_install_relocated=$([[ "$STANDALONE_MODE" == "YES" && "$SOURCE_ROOT" != "$ACTIVE_ROOT" ]] && echo YES || echo NO)

Ahora abre:
  $ACTIVE_ROOT/ABRIR_AUTO_TRADE.command
============================================================
EOF
