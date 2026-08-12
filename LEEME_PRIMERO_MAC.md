# AUTO-TRADE R6 — LÉEME PRIMERO EN MAC

Este paquete **FULL/STANDALONE** está preparado para instalar y ensayar el tramo seguro de AUTO-TRADE desde una interfaz local, antes de cualquier orden PAPER real.

## Qué hacer apenas se abra

No empieces tocando botones técnicos al azar. El Control Center nuevo está pensado en este orden:

1. **Probar la app sin broker** — crea/inicializa el workspace, ejecuta Doctor y prueba el Capital Safety Kernel localmente. No usa credenciales, no llama a Alpaca y no puede enviar órdenes.
2. **Conectar Alpaca PAPER, sólo lectura** — completa las cuatro lecturas guiadas: Account → Asset → Flat Account → Market IEX. La interfaz mantiene bloqueados los pasos que todavía no corresponden.
3. **Construir la connectivity canary local** — sólo cuando las cuatro lecturas anteriores estén completas; después se prepara el bracket offline.
4. La app se detiene antes de la primera autoridad humana. Ese límite es deliberado.

Si intentas adelantarte, la interfaz debe impedirlo. Si una condición cambia y el backend bloquea de todos modos, la pantalla muestra primero una explicación simple y deja el traceback como diagnóstico secundario.

## ¿Alpaca PAPER o TradingView?

Para **R6**, el ensayo actual se hace en **AUTO-TRADE + Alpaca PAPER**. TradingView no es necesario para probar esta etapa.

TradingView puede incorporarse después como capa visual, revisión de gráficos o fuente advisory de señales, pero no debe convertirse en una vía que salte Capital Safety, OMS, reconciliación o las decisiones humanas. La ejecución segura seguirá perteneciendo al pipeline determinista de AUTO-TRADE.

## Qué es y qué todavía no es esta versión

La app Mac actual sí permite comprobar:

- instalación/runtime;
- workspace y estado operativo;
- Capital Safety local;
- conexión PAPER read-only;
- cuenta plana;
- asset/venue;
- mercado IEX;
- construcción local de la connectivity canary;
- preparación determinista del bracket;
- progreso de los gates.

Todavía **no es la experiencia final de estrategia**. R1–R5 ya contienen motores certificados de research, backtest, walk-forward, holdout, shadow/forward y Health, pero esa capacidad aún no está integrada como un **Strategy Lab** sencillo dentro de la app Mac. Esa es la siguiente capa de producto después de estabilizar el ensayo guiado R6.

## Si vienes de un intento anterior que terminó en `Killed: 9`

No reutilices la carpeta FULL anterior. Descarga y descomprime este paquete nuevo. El instalador nuevo no ejecuta el CPython embebido desde Downloads: primero verifica el bundle y crea la instalación operativa en **`~/Applications/AUTO-TRADE-R6`**.

Si ya existe una instalación incompleta en `~/Applications/AUTO-TRADE-R6`, el instalador nuevo la reemplaza sólo después de verificar correctamente una copia nueva en staging. No necesitas ejecutar `xattr`, Homebrew ni comandos manuales.

Este flujo se prueba en CI tanto en Apple Silicon como Intel partiendo de un ZIP y árbol extraído marcados explícitamente con `com.apple.quarantine`: instalación, arranque del Control Center y reconstrucción del runtime después de borrar la copia descargada deben pasar antes de publicar el artifact FULL.

## La forma más fácil

1. Descomprime `AUTO-TRADE-R6-MAC-FULL.zip`.
2. Abre la carpeta `AUTO-TRADE-R6-MAC-FULL`.
3. Haz doble clic en **`INSTALAR_AUTO_TRADE.command`** una sola vez.
4. El instalador verifica primero los manifiestos SHA-256 y después crea una copia operativa limpia en **`~/Applications/AUTO-TRADE-R6`**.
5. Cuando termine en `AUTO-TRADE R6 INSTALL: OK`, abre **`~/Applications/AUTO-TRADE-R6/ABRIR_AUTO_TRADE.command`**. El `ABRIR_AUTO_TRADE.command` de la carpeta descargada también redirige automáticamente a esa instalación.
6. Se abrirá **AUTO-TRADE R6 Control Center** en tu navegador. Mantén abierta la pequeña ventana de Terminal mientras usas la plataforma.
7. Pulsa **`1 · Probar la app sin broker`**. Sólo después de que termine PASS tiene sentido pasar a Alpaca PAPER.

Si macOS bloquea un `.command` por Gatekeeper, haz clic derecho sobre él → **Abrir** → **Abrir** una sola vez. No desactives Gatekeeper globalmente.

### Por qué el FULL se instala fuera de Downloads

Safari/Finder puede marcar un ZIP descargado y los archivos extraídos con `com.apple.quarantine`. Por eso el runtime embebido **nunca se ejecuta directamente desde la carpeta descargada**.

El instalador FULL sigue este orden fail-closed:

1. valida los SHA-256 del runtime y wheelhouse en la carpeta descargada;
2. copia el bundle a un staging bajo `~/Applications` usando `ditto --norsrc --noqtn`, sin propagar quarantine/xattrs/ACLs;
3. elimina cualquier estado transitorio local (`.venv`, `.runtime`, `.env`, SQLite/cache) de ese staging;
4. vuelve a validar los SHA-256;
5. sólo entonces promueve el staging a `~/Applications/AUTO-TRADE-R6` y ejecuta el CPython embebido.

Si alguien intenta ejecutar directamente `scripts/mac_bootstrap.sh` desde un FULL descargado fuera de `~/Applications/AUTO-TRADE-R6`, el bootstrap se detiene antes de ejecutar CPython y pide usar el instalador.

Después de una instalación correcta puedes borrar la carpeta descomprimida de Downloads; la copia de `~/Applications/AUTO-TRADE-R6` conserva los runtimes, wheelhouse, código y launchers necesarios para reconstruir su entorno local.

`AUTO_TRADE_MAC.command` permanece incluido como consola técnica de respaldo, pero ya no es la interfaz recomendada para el uso cotidiano y, en el FULL, también redirige a la copia instalada.

## Qué incluye el FULL/STANDALONE

No depende de Homebrew, de un Python instalado previamente ni de PyPI durante el primer arranque. Incluye:

- CPython 3.12.13 relocatable para **Apple Silicon (arm64)**;
- CPython 3.12.13 relocatable para **Intel (x86_64)**;
- wheelhouse offline con AUTO-TRADE y las dependencias requeridas;
- manifiestos SHA-256 para runtimes y wheels;
- Control Center local + launcher Finder + consola técnica;
- código, tests, boundaries y runbook del mismo checkpoint R6.

El bootstrap detecta `uname -m`, verifica hashes, extrae sólo el runtime correspondiente y crea `.venv` dentro de la copia instalada. Si `.runtime` o `.venv` se eliminan, puede reconstruirlos desde los activos embebidos sin PyPI.

## Qué puedes hacer desde el Control Center sin escribir comandos

- ejecutar un primer ensayo local guiado;
- crear/inicializar el workspace privado;
- ejecutar Doctor, rehearsal, Capital Safety y readiness;
- ver `pre-canary-status`, el bloqueo actual y el siguiente gate;
- ingresar temporalmente credenciales Alpaca PAPER;
- ejecutar guiados y en orden los cuatro preflights GET-only: Account, Asset, Flat Account e IEX Market;
- construir la candidata `CONNECTIVITY_CANARY` local;
- ejecutar la preparación determinista del bracket;
- generar el review receipt después de la primera decisión humana;
- revisar un historial entendible y desplegar el detalle técnico sólo cuando haga falta.

Las credenciales PAPER no se escriben en `.env` ni en archivos de la aplicación. Se conservan sólo en los campos de la página mientras está abierta y se pasan al proceso hijo del GET explícito que pulses.

## Capital Safety sigue siendo el kernel real

El ensayo **Probar la app sin broker** incluye un rehearsal local del Capital Safety Kernel. No fabrica una aprobación ni sustituye el control crítico: la decisión se obtiene mediante `CapitalSafetyKernel.evaluate(...)` con los límites fijos del ensayo.

Aunque el rehearsal resulte `APPROVED`, conserva explícitamente:

- `external_execution_authorized=false`;
- `capital_authority=NONE`;
- `broker_network_used=false`;
- `broker_write_performed=false`;
- LIVE bloqueado.

Esto prueba la ruta del kernel, no una estrategia rentable ni una autorización de broker.

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
- External PAPER order enviado por el proyecto: **0**;
- External PAPER order enviado por instalación/dashboard: **0**;
- Capital authority desde Control Center: **NONE**;
- LIVE trading: **BLOCKED**;
- `UNKNOWN => RECONCILIATION_ONLY`;
- la primera canary sigue limitada a US-equity bracket;
- conectividad PAPER no es prueba de rentabilidad.

Para detalle técnico y la ceremonia humana posterior: `docs/MAC_PAPER_RUNBOOK.md`.
