# TAREA ACTIVA — AUTO-TRADE

Fecha: 2026-08-12

## Objetivo inmediato
**Cerrar la UAT guiada de AUTO-TRADE R6 en Mac real y dejar inequívoco cómo se prueba la plataforma antes de cualquier canary PAPER.**

Base R0–R5: post-R5-green `main` `75dcbef65b061f742745ba7be0665521967e0587`.
R6 base: `reconstruction/r6-external-paper-protection`.
Mac UX branch: `work/r6-mac-control-center`.
PR #29: DRAFT.
Checkpoint UX previo a esta sincronización de canon: `2c2e5ebef9bf01f13cf9bed477e670ba79fa2b29`.

Ese checkpoint tiene 5/5 suites PASS:
- Core Safety `31649170421`;
- Knowledge Contract `31649170446`;
- Mac Rehearsal `31649170429`;
- R6 PAPER Authority `31649170448`;
- Mac FULL Standalone `31649170468`, incluyendo arm64 + x86_64.

## Hallazgo UAT que originó esta iteración
La instalación y el kernel estaban sanos, pero la UI anterior era una consola técnica: permitía pulsar Candidate/Preparation/Review Receipt fuera de orden y mostraba errores como artefactos faltantes sin explicar qué debía hacer el operador.

## Corrección ya implementada
Control Center guiado:
1. `Probar la app sin broker`:
   - crea/inicializa workspace;
   - Doctor;
   - Capital Safety rehearsal;
   - cero credenciales, cero broker I/O, cero POST.
2. `Conectar Alpaca PAPER`:
   - Account;
   - Asset;
   - Flat Account;
   - Market IEX;
   - botones posteriores bloqueados hasta completar el gate anterior.
3. Después:
   - connectivity candidate local;
   - deterministic bracket preparation;
   - STOP deliberado en la primera frontera humana.

La UI ahora presenta explicación operacional primero y traceback sólo como diagnóstico técnico secundario.

## UAT inmediata en Mac real
El ensayo correcto del nuevo FULL es:
1. instalar/abrir desde `~/Applications/AUTO-TRADE-R6`;
2. pulsar `1 · Probar la app sin broker` y exigir PASS;
3. verificar que no se pueda adelantar un gate;
4. opcionalmente ingresar credenciales exclusivamente Alpaca PAPER y completar los 4 GET-only en orden;
5. construir candidate y preparation sólo si status los habilita;
6. detenerse antes de H1 salvo decisión humana separada de continuar con la canary real.

## Alpaca vs TradingView
R6 se prueba con **AUTO-TRADE + Alpaca PAPER**. TradingView no es necesario para esta UAT.

Una futura integración TradingView puede servir como visualización/advisory, nunca como autoridad de ejecución o bypass de Capital Safety/OMS/reconciliation.

## Después de cerrar UAT R6
Abrir como siguiente capa de producto un **Strategy Lab Mac** que integre las capacidades R1–R5 ya certificadas:
- datasets/provenance;
- Strategy DSL;
- backtest con fees/spread/slippage/latency;
- walk-forward y bootstrap;
- TRAIN/VALIDATION/HOLDOUT protegido;
- tournament/multiple-testing evidence;
- portfolio/regime/Health;
- shadow/forward evidence;
- promoción humana controlada hacia PAPER.

No iniciar esa integración grande si la UAT R6 todavía revela defectos operativos críticos.

## Deuda R6
OPEN/blocking y sólo cerrable con evidencia externa PAPER real:
- `TD-R6-001..006`.

CLOSED structurally:
- `TD-R6-007..013`.

OPEN nonblocking:
- `TD-OPS-001`.

## Restricciones permanentes
- Coverage real >=85%, fail-closed.
- No fabricar RiskDecision, Health, Portfolio, DBs o evidence JSON para saltar gates.
- `UNKNOWN => RECONCILIATION_ONLY`; jamás blind retry.
- Primera canary: US equity bracket, bounded, account flat.
- Control Center no expone writer/POST/LIVE.
- PAPER connectivity evidence no es profitability proof.

## Capital
External PAPER order enviado: **0**.
Capital authority desde Control Center: **NONE**.
**LIVE TRADING: BLOQUEADO.**
