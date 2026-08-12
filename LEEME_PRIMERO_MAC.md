# AUTO-TRADE R6 — LÉEME PRIMERO EN MAC

Este paquete **FULL/STANDALONE** está preparado para instalar y operar el tramo seguro de AUTO-TRADE desde una interfaz local, antes de cualquier orden PAPER real.

## La forma más fácil

1. Descomprime `AUTO-TRADE-R6-MAC-FULL.zip`.
2. Abre la carpeta `AUTO-TRADE-R6-MAC-FULL`.
3. Haz doble clic en **`INSTALAR_AUTO_TRADE.command`** una sola vez.
4. Cuando termine en `AUTO-TRADE R6 INSTALL: OK`, haz doble clic en **`ABRIR_AUTO_TRADE.command`**.
5. Se abrirá **AUTO-TRADE R6 Control Center** en tu navegador. Mantén abierta la pequeña ventana de Terminal mientras usas la plataforma.

Si macOS bloquea un `.command` por Gatekeeper, haz clic derecho sobre él → **Abrir** → **Abrir** una sola vez. No desactives Gatekeeper globalmente.

`AUTO_TRADE_MAC.command` permanece incluido como consola técnica de respaldo, pero ya no es la interfaz recomendada para el uso cotidiano.

## Qué incluye el FULL/STANDALONE

No depende de Homebrew, de un Python instalado previamente ni de PyPI durante el primer arranque. Incluye:

- CPython 3.12.13 relocatable para **Apple Silicon (arm64)**;
- CPython 3.12.13 relocatable para **Intel (x86_64)**;
- wheelhouse offline con AUTO-TRADE y las dependencias requeridas;
- manifiestos SHA-256 para runtimes y wheels;
- Control Center local + launcher Finder + consola técnica;
- código, tests, boundaries y runbook del mismo checkpoint R6.

El bootstrap detecta `uname -m`, verifica hashes, extrae sólo el runtime correspondiente y crea `.venv` localmente. Si mueves la carpeta, recrea el entorno desde el runtime embebido.

## Qué puedes hacer desde el Control Center sin escribir comandos

- crear/inicializar el workspace privado;
- ejecutar Doctor, rehearsal, Capital Safety y readiness;
- ver `pre-canary-status`, el bloqueo actual y el siguiente gate;
- ingresar temporalmente credenciales Alpaca PAPER;
- ejecutar con botones los cuatro preflights GET-only: Account, Asset, Flat Account e IEX Market;
- construir la candidata `CONNECTIVITY_CANARY` local;
- ejecutar la preparación determinista del bracket;
- generar el review receipt después de la primera decisión humana;
- revisar el historial/log de las acciones y el progreso de la cadena.

Las credenciales PAPER no se escriben en `.env`, archivos, `localStorage` ni `sessionStorage`. Se conservan sólo en los campos de la página mientras está abierta y se pasan al proceso hijo del GET explícito que pulses.

## Frontera que sigue separada

Por diseño, el dashboard **no** contiene acciones para:

- emitir la primera o segunda autoridad humana;
- ejecutar reviewed Final Freshness;
- stagear OMS;
- enviar una orden PAPER;
- habilitar LIVE.

Esas ceremonias siguen separadas porque son parte de la frontera de autoridad. `READY` en el dashboard significa únicamente “listo para el siguiente gate indicado”; nunca significa POST autorizado.

## Estado seguro permanente

- `R6_EXTERNAL_PAPER_WRITE=DISABLED` desde instalador/dashboard;
- External PAPER order enviado por instalación/dashboard: **0**;
- Capital authority desde Control Center: **NONE**;
- LIVE trading: **BLOCKED**;
- `UNKNOWN => RECONCILIATION_ONLY`;
- la primera canary sigue limitada a US-equity bracket;
- conectividad PAPER no es prueba de rentabilidad.

Para detalle técnico y la ceremonia humana posterior: `docs/MAC_PAPER_RUNBOOK.md`.
