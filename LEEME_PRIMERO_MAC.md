# AUTO-TRADE R6 — LÉEME PRIMERO EN MAC

Este paquete **FULL/STANDALONE** está preparado para instalar y ensayar AUTO-TRADE de forma segura antes de cualquier orden PAPER real.

## La forma más fácil

1. Descomprime `AUTO-TRADE-R6-MAC-FULL.zip`.
2. Abre la carpeta `AUTO-TRADE-R6-MAC-FULL`.
3. Haz doble clic en **`AUTO_TRADE_MAC.command`**.

Si macOS bloquea el primer doble clic por Gatekeeper:

1. haz clic derecho sobre `AUTO_TRADE_MAC.command`;
2. selecciona **Abrir**;
3. confirma **Abrir** una sola vez.

No desactives Gatekeeper globalmente y no uses comandos para eliminar cuarentena del sistema completo.

## Qué incluye el paquete FULL/STANDALONE

No depende de Homebrew, de un Python instalado previamente ni de PyPI durante el primer arranque. Incluye:

- CPython 3.12.13 relocatable para **Apple Silicon (arm64)**;
- CPython 3.12.13 relocatable para **Intel (x86_64)**;
- wheelhouse offline con AUTO-TRADE y todas las dependencias requeridas por runtime/rehearsal;
- manifiestos SHA-256 para runtimes y wheels;
- launcher Finder + Safe Console;
- código, tests, boundaries y runbook del mismo checkpoint R6.

El launcher detecta `uname -m`, verifica hashes, extrae únicamente el runtime correspondiente y crea `.venv` localmente. Si el paquete se mueve después, el bootstrap detecta el cambio de ruta y recrea `.venv` desde el runtime embebido.

## Qué ocurre en el primer arranque

Si falta `.venv`, el launcher ejecuta el bootstrap seguro:

- usa el Python 3.12.13 **embebido**;
- verifica SHA-256 de los dos runtimes y del wheelhouse;
- instala dependencias con `pip --no-index` exclusivamente desde `vendor/wheels`;
- ejecuta boundaries R6/Mac;
- ejecuta tests focalizados;
- prueba Capital Safety localmente;
- no carga credenciales Alpaca;
- no llama al broker;
- fuerza `R6_EXTERNAL_PAPER_WRITE=DISABLED`;
- no envía órdenes;
- LIVE permanece bloqueado.

La conexión a Internet no es necesaria para instalar el runtime ni las dependencias del paquete FULL. Naturalmente, los preflights PAPER GET-only posteriores sí requieren red y credenciales PAPER cuando llegue esa fase.

## Primer ensayo recomendado

Desde el menú de doble clic:

1. **Doctor local** — confirma entorno y protecciones.
2. **Ensayo offline completo** — ejecuta el rehearsal local.
3. **Probar Capital Safety** — prueba una orden candidata local; el RiskDecision lo crea el Capital Safety Kernel real.
4. **Crear workspace privado** — usa por defecto `$HOME/AUTO-TRADE-R6/workspace-001`.
5. **Estado pre-canary** — el workspace nuevo debe indicar `ACCOUNT_PREFLIGHT_REQUIRED`.

Hasta aquí no necesitas cuenta Alpaca ni credenciales.

## Qué significa “Probar Capital Safety”

Es un ejercicio local, no una estrategia rentable y no una orden real.

El candidato pasa por `CapitalSafetyKernel.evaluate(...)` con límites fijos de rehearsal. La salida puede ser `APPROVED` o `REJECTED`, pero incluso un `APPROVED` conserva:

- `external_execution_authorized=false`;
- `capital_authority=NONE`;
- `broker_network_used=false`;
- `broker_write_performed=false`;
- `profitability_claim=false`;
- LIVE bloqueado.

## Cuando queramos probar la cuenta Alpaca PAPER

Lo haremos como una etapa separada y acompañada. El orden seguro es:

1. account preflight — un GET de cuenta;
2. asset preflight — un GET del instrumento/venue;
3. flat-account preflight — GET posiciones + GET órdenes abiertas;
4. market preflight — un GET IEX del símbolo;
5. `pre-canary-status` otra vez.

La cuenta debe demostrar **0 posiciones y 0 órdenes abiertas** para el primer canary y esa evidencia es deliberadamente de corta duración.

## STOP antes de cualquier ejecución

Este paquete y el launcher de doble clic **no ofrecen un botón para enviar una orden**.

El runtime one-shot existe en R6 como parte del sistema certificado, pero se mantiene fuera de Finder/Safe Console. No debe usarse hasta tomar una decisión humana explícita separada después de revisar la cuenta, el asset, mercado, Safety, bracket, receipt y ambas decisiones humanas del canary exacto.

## Estado seguro del paquete

- External PAPER order enviado por el proyecto: **0**.
- Capital authority desde Finder/Safe Console: **NONE**.
- LIVE trading: **BLOCKED**.
- R6 sigue en PR DRAFT.
- Un canary de conectividad no es prueba de rentabilidad.

Para detalle técnico: `docs/MAC_PAPER_RUNBOOK.md`.
