#!/bin/bash
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "AUTO-TRADE MAC START: este launcher solo funciona en macOS." >&2
  exit 2
fi

if [[ "${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED" ]]; then
  echo "SAFE CLICK START BLOCKED: la shell heredó R6_EXTERNAL_PAPER_WRITE habilitado." >&2
  echo "Cierra esta ventana y abre AUTO_TRADE_MAC.command desde una sesión sin autoridad de escritura." >&2
  exit 2
fi

# El launcher de doble clic siempre es broker-inert. Los GET PAPER se ejecutan
# únicamente desde mac_start.sh de forma explícita, nunca automáticamente aquí.
unset APCA_API_KEY_ID || true
unset APCA_API_SECRET_KEY || true
export R6_EXTERNAL_PAPER_WRITE=DISABLED

if [[ ! -x ".venv/bin/python" ]]; then
  clear
  echo "AUTO-TRADE R6 — PRIMER ARRANQUE SEGURO EN MAC"
  echo
  echo "Se ejecutará el bootstrap local. No se leerán credenciales Alpaca y no se enviarán órdenes."
  echo "LIVE permanece BLOQUEADO."
  echo
  bash scripts/mac_bootstrap.sh || {
    code=$?
    echo
    echo "El bootstrap no terminó correctamente (código $code). Revisa el mensaje anterior."
    read -r -p "Presiona ENTER para cerrar..." _
    exit "$code"
  }
fi

run_safe() {
  bash scripts/mac_start.sh "$@"
  local code=$?
  if [[ $code -ne 0 ]]; then
    echo
    echo "La acción segura terminó con código $code. No se habilitó escritura PAPER."
  fi
  return $code
}

show_header() {
  clear
  cat <<'EOF'
AUTO-TRADE R6 — MAC SAFE CONSOLE

Modo actual:
  • External PAPER write: DISABLED
  • LIVE trading: BLOCKED
  • Orden real desde este launcher: IMPOSIBLE

1) Crear workspace privado
2) Doctor local
3) Ensayo offline completo
4) Inspeccionar readiness de un workspace
5) Abrir runbook de Mac
6) Mostrar pasos GET-only de Alpaca PAPER
0) Salir
EOF
}

while true; do
  show_header
  echo
  read -r -p "Selecciona una opción: " choice
  echo
  case "$choice" in
    1)
      default_workspace="$HOME/AUTO-TRADE-R6/workspace-001"
      read -r -p "Ruta del nuevo workspace [$default_workspace]: " workspace
      workspace="${workspace:-$default_workspace}"
      run_safe init-workspace "$workspace"
      ;;
    2)
      run_safe doctor
      ;;
    3)
      run_safe rehearsal
      ;;
    4)
      read -r -p "Ruta del workspace (ej. $HOME/AUTO-TRADE-R6/workspace-001): " workspace
      if [[ -z "$workspace" ]]; then
        echo "Ruta vacía: no se ejecutó ninguna acción."
      else
        run_safe readiness "$workspace"
      fi
      ;;
    5)
      open "$ROOT/docs/MAC_PAPER_RUNBOOK.md" >/dev/null 2>&1 || \
        echo "Runbook: $ROOT/docs/MAC_PAPER_RUNBOOK.md"
      ;;
    6)
      cat <<'EOF'
Pasos de red permitidos desde el Safe Start (GET-only):

  bash scripts/mac_start.sh account-preflight <WORKSPACE> <ALPACA_PAPER_ACCOUNT_ID>
  bash scripts/mac_start.sh market-preflight <WORKSPACE> <SYMBOL>

Estos pasos requieren que configures credenciales PAPER de forma explícita.
Este launcher de doble clic no carga .env, no conserva credenciales y no ofrece ejecución de órdenes.
EOF
      ;;
    0)
      echo "Cierre seguro. External PAPER write sigue DISABLED; LIVE sigue BLOCKED."
      exit 0
      ;;
    *)
      echo "Opción no válida."
      ;;
  esac
  echo
  read -r -p "Presiona ENTER para volver al menú..." _
done
