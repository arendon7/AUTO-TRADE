# AUTO-TRADE R6 — LÉEME PRIMERO EN MAC

Este paquete está preparado para **ensayar AUTO-TRADE de forma segura antes de cualquier orden PAPER real**.

## La forma más fácil

1. Descomprime `AUTO-TRADE-R6-MAC.zip`.
2. Abre la carpeta `AUTO-TRADE-R6-MAC`.
3. Haz doble clic en **`AUTO_TRADE_MAC.command`**.

Si macOS bloquea el primer doble clic por Gatekeeper:

1. haz clic derecho sobre `AUTO_TRADE_MAC.command`;
2. selecciona **Abrir**;
3. confirma **Abrir** una sola vez.

No desactives Gatekeeper globalmente y no uses comandos para eliminar cuarentena del sistema completo.

## Qué ocurre en el primer arranque

Si falta `.venv`, el launcher ejecuta el bootstrap seguro:

- requiere Python 3.12+;
- instala dependencias locales dentro de `.venv`;
- ejecuta boundaries R6/Mac;
- ejecuta tests focalizados;
- prueba Capital Safety localmente;
- no carga credenciales Alpaca;
- no llama al broker;
- fuerza `R6_EXTERNAL_PAPER_WRITE=DISABLED`;
- no envía órdenes;
- LIVE permanece bloqueado.

## Primer ensayo recomendado

Desde el menú de doble clic:

1. **Doctor local** — confirma entorno y protecciones.
2. **Ensayo offline completo** — ejecuta el rehearsal local.
3. **Probar Capital Safety** — prueba una orden candidata local; el RiskDecision lo crea el Capital Safety Kernel real.
4. **Crear workspace privado** — usa por defecto `$HOME/AUTO-TRADE-R6/workspace-001`.
5. **Inspeccionar readiness** — el workspace nuevo debe indicar `ACCOUNT_PREFLIGHT_REQUIRED`.

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
2. flat-account preflight — GET posiciones + GET órdenes abiertas;
3. market preflight — un GET IEX del símbolo;
4. readiness otra vez.

La cuenta debe demostrar **0 posiciones y 0 órdenes abiertas** para el primer canary y esa evidencia es deliberadamente de corta duración.

## STOP antes de cualquier ejecución

Este paquete y el launcher de doble clic **no ofrecen un botón para enviar una orden**.

El comando de ejecución PAPER existe en el repositorio como parte del sistema certificado, pero se mantiene fuera de Safe Start. No debe usarse hasta tomar una decisión explícita separada después de revisar account/flat/market/Safety/OMS/Health y el canary exacto.

## Estado actual

- External PAPER order enviado por el proyecto: **0**.
- Capital authority actual: **NONE**.
- LIVE trading: **BLOCKED**.
- R6 sigue en PR DRAFT.
- Un canary de conectividad no es prueba de rentabilidad.

Para detalle técnico: `docs/MAC_PAPER_RUNBOOK.md`.
