#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

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

if [[ ! -f "$ROOT/MAC_STANDALONE_MANIFEST.txt" && ! -d "$ROOT/.git" ]]; then
  echo "ERROR: este paquete no contiene el manifest FULL/STANDALONE." >&2
  echo "Descarga AUTO-TRADE-R6-MAC-FULL; no uses el bundle source/rehearsal para instalar." >&2
  exit 2
fi

clear || true
cat <<'EOF'
AUTO-TRADE R6 — INSTALACIÓN SEGURA

• FULL/STANDALONE usa el CPython y wheelhouse embebidos.
• No requiere Homebrew.
• No requiere Python del sistema en el paquete FULL.
• No usa PyPI/Internet para instalar el runtime del paquete FULL.
• PAPER write permanece DISABLED.
• LIVE permanece BLOCKED.
• Esta instalación NO envía órdenes.
EOF

echo
bash "$ROOT/scripts/mac_bootstrap.sh"

PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { echo "ERROR: .venv no quedó instalado." >&2; exit 2; }

"$PY" "$ROOT/scripts/check_mac_standalone_boundary.py"
"$PY" "$ROOT/scripts/check_mac_dashboard_boundary.py"
"$PY" "$ROOT/scripts/mac_doctor.py"

cat <<'EOF'

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

Ahora abre:
  ABRIR_AUTO_TRADE.command
============================================================
EOF
