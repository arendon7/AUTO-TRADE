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
  exit 2
fi
unset APCA_API_KEY_ID || true
unset APCA_API_SECRET_KEY || true
export R6_EXTERNAL_PAPER_WRITE=DISABLED

if [[ ! -x ".venv/bin/python" ]]; then
  clear
  echo "AUTO-TRADE R6 — PRIMER ARRANQUE SEGURO EN MAC"
  echo "Se ejecutará el bootstrap local. No se enviarán órdenes; LIVE permanece BLOQUEADO."
  bash scripts/mac_bootstrap.sh || exit $?
fi

run_safe() {
  bash scripts/mac_start.sh "$@"
  local code=$?
  [[ $code -eq 0 ]] || echo "La acción terminó con código $code. PAPER write sigue DISABLED."
  return $code
}

show_header() {
  clear
  cat <<'EOF'
AUTO-TRADE R6 — MAC SAFE CONSOLE

  • External PAPER write: DISABLED
  • LIVE trading: BLOCKED
  • Orden real desde este launcher: IMPOSIBLE
  • READY en estado pre-canary: sólo significa listo para el siguiente gate seguro

1) Crear workspace privado
2) Doctor local
3) Ensayo offline completo
4) Probar Capital Safety local
5) Ver estado pre-canary y siguiente gate
6) Inspeccionar readiness base
7) Abrir runbook de Mac
8) Mostrar secuencia GET-only PAPER
9) Construir candidata CONNECTIVITY_CANARY local
10) Preparar paquete bracket offline
11) Congelar review receipt offline
0) Salir
EOF
}

ask_workspace() {
  local prompt="$1"
  local workspace
  read -r -p "$prompt" workspace
  [[ -n "$workspace" ]] || { echo "Ruta vacía."; return 1; }
  printf '%s' "$workspace"
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
      run_safe init-workspace "${workspace:-$default_workspace}"
      ;;
    2) run_safe doctor ;;
    3) run_safe rehearsal ;;
    4)
      echo "Capital Safety Kernel real: rehearsal local, sin Alpaca, sin operador y sin POST."
      read -r -p "Símbolo [AAPL]: " symbol
      read -r -p "Cantidad [0.25]: " quantity
      run_safe safety-rehearsal --symbol "${symbol:-AAPL}" --quantity "${quantity:-0.25}"
      ;;
    5)
      workspace="$(ask_workspace "Ruta del workspace: ")" && run_safe pre-canary-status "$workspace"
      ;;
    6)
      workspace="$(ask_workspace "Ruta del workspace: ")" && run_safe readiness "$workspace"
      ;;
    7)
      open "$ROOT/docs/MAC_PAPER_RUNBOOK.md" >/dev/null 2>&1 || echo "$ROOT/docs/MAC_PAPER_RUNBOOK.md"
      ;;
    8)
      cat <<'EOF'
Secuencia GET-only obligatoria:
  account -> asset -> flat account -> market

  bash scripts/mac_start.sh account-preflight <WORKSPACE> <ALPACA_PAPER_ACCOUNT_ID>
  bash scripts/mac_start.sh asset-preflight <WORKSPACE> <SYMBOL>
  bash scripts/mac_start.sh flat-account-preflight <WORKSPACE>
  bash scripts/mac_start.sh market-preflight <WORKSPACE> <SYMBOL>

Asset gate: exact us_equity + active + tradable; primer canary limitado a acción entera.
Luego, sin credenciales y sin red:
  bash scripts/mac_start.sh build-connectivity-candidate <WORKSPACE>
  bash scripts/mac_start.sh prepare-connectivity-candidate <WORKSPACE>
  bash scripts/mac_start.sh pre-canary-status <WORKSPACE>

La primera decisión humana, la segunda intención humana y Final Freshness NO se emiten
desde este launcher. El runbook explica la ceremonia separada.
EOF
      ;;
    9)
      workspace="$(ask_workspace "Ruta del workspace con los 4 GET preflights completos: ")"
      if [[ -n "${workspace:-}" ]]; then
        cat <<'EOF'
Este paso elimina credenciales del proceso hijo y crea únicamente:
  • Portfolio baseline de sesión connectivity;
  • RiskDecision real de CapitalSafetyKernel;
  • orden OMS VALIDATED;
  • autoridad durable CONNECTIVITY_CANARY.
No crea Strategy Health, decisión humana ni autoridad POST.
EOF
        run_safe build-connectivity-candidate "$workspace"
      fi
      ;;
    10)
      workspace="$(ask_workspace "Ruta del workspace con candidata válida: ")" && {
        echo "Preparación determinista offline: bracket + bindings. Sin red, sin nueva autoridad humana y sin POST."
        run_safe prepare-connectivity-candidate "$workspace"
      }
      ;;
    11)
      workspace="$(ask_workspace "Ruta del workspace después de la PRIMERA decisión humana: ")" && {
        echo "Se congelará sólo el documento de revisión exacto. Este paso NO autoriza staging ni POST."
        run_safe review-receipt "$workspace"
      }
      ;;
    0)
      echo "Cierre seguro. PAPER write DISABLED; LIVE BLOCKED."
      exit 0
      ;;
    *) echo "Opción no válida." ;;
  esac
  echo
  read -r -p "Presiona ENTER para volver al menú..." _
done
