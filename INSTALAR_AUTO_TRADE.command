#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

export R6_EXTERNAL_PAPER_WRITE=DISABLED
unset APCA_API_KEY_ID 2>/dev/null || true
unset APCA_API_SECRET_KEY 2>/dev/null || true

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: Este instalador FULL/OFFLINE es exclusivamente para macOS." >&2
  exit 2
fi
if [[ "${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED" ]]; then
  echo "ERROR: R6_EXTERNAL_PAPER_WRITE no puede estar ENABLED durante instalación." >&2
  exit 2
fi

if [[ -x "$ROOT/VERIFICAR_PAQUETE.command" ]]; then
  bash "$ROOT/VERIFICAR_PAQUETE.command"
fi

PYTHON_PKG="$ROOT/runtime/python-3.12.10-macos11.pkg"
WHEELHOUSE="$ROOT/runtime/wheels"
PYTHON_PKG_MIN_BYTES=43000000

has_py312() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

find_python() {
  local c
  for c in \
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12" \
    "$(command -v python3.12 2>/dev/null || true)" \
    "$(command -v python3 2>/dev/null || true)"; do
    [[ -n "$c" && -x "$c" ]] || continue
    if has_py312 "$c"; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(find_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ ! -f "$PYTHON_PKG" ]]; then
    echo "ERROR: falta el runtime offline: runtime/python-3.12.10-macos11.pkg" >&2
    echo "Descarga el paquete AUTO-TRADE-R6-MAC-FULL completo; el instalador no descargará componentes de Internet." >&2
    exit 2
  fi
  BYTES="$(/usr/bin/stat -f%z "$PYTHON_PKG")"
  if [[ "$BYTES" -lt "$PYTHON_PKG_MIN_BYTES" ]]; then
    echo "ERROR: el runtime Python incluido parece truncado ($BYTES bytes)." >&2
    exit 2
  fi
  echo "Verificando firma del runtime Python incluido..."
  /usr/sbin/pkgutil --check-signature "$PYTHON_PKG"
  echo "Instalando Python 3.12.10 universal2 incluido en el paquete. macOS puede pedir tu contraseña."
  /usr/bin/sudo /usr/sbin/installer -pkg "$PYTHON_PKG" -target /
  PYTHON_BIN="$(find_python || true)"
  [[ -n "$PYTHON_BIN" ]] || { echo "ERROR: Python 3.12 no quedó disponible." >&2; exit 2; }
fi

echo "Python seleccionado: $PYTHON_BIN"
"$PYTHON_BIN" -c 'import sys; print("Python", sys.version.split()[0])'

if [[ -d .venv && ! -x .venv/bin/python ]]; then
  echo "Eliminando entorno virtual incompleto..."
  rm -rf .venv
fi
if [[ ! -x .venv/bin/python ]]; then
  echo "Creando entorno aislado AUTO-TRADE..."
  "$PYTHON_BIN" -m venv .venv
fi

VENV_PY="$ROOT/.venv/bin/python"
if ! "$VENV_PY" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
then
  echo "ERROR: el entorno AUTO-TRADE no usa Python 3.12+." >&2
  exit 2
fi

WHEEL="$WHEELHOUSE/websockets-16.1.1-py3-none-any.whl"
if [[ ! -f "$WHEEL" ]]; then
  echo "ERROR: falta dependencia offline: $WHEEL" >&2
  exit 2
fi

echo "Instalando dependencias desde el wheelhouse OFFLINE..."
"$VENV_PY" -m pip install \
  --disable-pip-version-check \
  --no-index \
  --find-links "$WHEELHOUSE" \
  --only-binary=:all: \
  "websockets==16.1.1"

SITE_PACKAGES="$($VENV_PY - <<'PY'
import site
print(site.getsitepackages()[0])
PY
)"
printf '%s\n' "$ROOT/src" > "$SITE_PACKAGES/auto_trade_r6_source.pth"

"$VENV_PY" - <<'PY'
import autotrade, websockets
assert websockets.__version__ == "16.1.1", websockets.__version__
print("AUTO-TRADE core import: OK")
print("websockets:", websockets.__version__)
PY

chmod +x "$ROOT/ABRIR_AUTO_TRADE.command" "$ROOT/AUTO_TRADE_MAC.command" "$ROOT/INSTALAR_AUTO_TRADE.command" "$ROOT/VERIFICAR_PAQUETE.command"
chmod +x "$ROOT/scripts/mac_bootstrap.sh" "$ROOT/scripts/mac_rehearsal.sh" "$ROOT/scripts/mac_start.sh"
"$VENV_PY" -m compileall -q src scripts

echo "Ejecutando controles de instalación y autoridad..."
"$VENV_PY" scripts/check_contract_registry.py
"$VENV_PY" scripts/check_debt_register.py
"$VENV_PY" scripts/check_r6_authority.py
"$VENV_PY" scripts/check_r6_live_deny_boundary.py
"$VENV_PY" scripts/check_r6_readiness_boundary.py
"$VENV_PY" scripts/check_r6_precanary_status_boundary.py
"$VENV_PY" scripts/check_mac_rehearsal_boundary.py
"$VENV_PY" scripts/check_mac_safe_console_boundary.py
"$VENV_PY" scripts/check_mac_safety_rehearsal_boundary.py
"$VENV_PY" scripts/check_mac_click_launcher_boundary.py
"$VENV_PY" scripts/check_mac_dashboard_boundary.py
"$VENV_PY" scripts/mac_doctor.py

SOURCE_HEAD="$($VENV_PY - <<'PY'
from pathlib import Path
root=Path.cwd()
value="UNKNOWN"
p=root/"MAC_BUILD_INFO.txt"
if p.is_file():
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("source_head="):
            value=line.split("=",1)[1].strip()
            break
print(value)
PY
)"

SOURCE_HEAD="$SOURCE_HEAD" "$VENV_PY" - <<'PY'
from __future__ import annotations
import json, os, pathlib, platform, sys
import websockets
root = pathlib.Path.cwd()
receipt = {
    "schema": "autotrade.mac.full_install.v2",
    "package_mode": "FULL_OFFLINE",
    "source_head": os.environ.get("SOURCE_HEAD", "UNKNOWN"),
    "python": sys.version.split()[0],
    "python_executable": sys.executable,
    "machine": platform.machine(),
    "macos": platform.mac_ver()[0],
    "websockets": websockets.__version__,
    "external_paper_write": "DISABLED",
    "capital_authority": "NONE",
    "live_trading": "BLOCKED",
    "dashboard": "LOCALHOST_ONLY",
    "external_order_submitted_by_install": False,
}
(root / "MAC_FULL_INSTALL_RECEIPT.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

cat <<'TXT'

============================================================
AUTO-TRADE R6 FULL/OFFLINE INSTALL: OK
============================================================
Runtime local: OK
Dependencias offline: OK
Dashboard local: OK
PAPER write: DISABLED
Capital authority: NONE
LIVE: BLOCKED
Orden PAPER enviada por instalación: NO

Ahora haz doble clic en:
  ABRIR_AUTO_TRADE.command

Se abrirá el Control Center en el navegador. Mantén la pequeña ventana de Terminal abierta mientras usas la plataforma.
TXT
