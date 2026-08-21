# CONTEXTO RÁPIDO — AUTO-TRADE

Estado actual: **R0–R5 certified; R6 first real Alpaca PAPER canary broker-truth closed; R7 PAPER Operations + Strategy Lab active**.

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md`
5. `knowledge/00_CANON/debt_register.json`
6. `docs/R7_PAPER_OPERATIONS_STRATEGY_LAB.md`
7. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`

## Estado certificado
R0–R5 continúan siendo los tracks canónicos certificados en el machine debt register.

R6 first-canary exact-head certificado: `0cbb782015eeed200b9851b53764ac6389c3d9ff`.
PR #46 permanece separado de R7 para no mezclar la prueba de entrada/recovery con nueva autoridad operacional.

## Broker truth ya observado
Intento `first-canary-57d01d35e8b25f4babc57695ac87d962`:
- BTC/USD BUY LIMIT IOC PAPER;
- exactamente un POST de entrada;
- broker status `filled`;
- fill bruto `0.00014432 BTC`;
- posición neta GET `0.000143959 BTC`;
- recovery `RECOVERED_GET_ONLY`;
- `entry_attempt_count=1`;
- `retry_post=false`;
- credenciales no persistidas;
- LIVE bloqueado;
- lifecycle `ENTRY_FILLED_UNPROTECTED`.

El canary prueba plumbing de broker/idempotencia/recovery. **No prueba rentabilidad.**

## R7 abierto
Branch: `work/r7-paper-operations-strategy-lab`.

Orden de ejecución:
1. Portfolio Truth: account + posiciones + órdenes abiertas por GET;
2. Reduce/Close/Protection PAPER sobre exposición real;
3. Strategy Lab usable sobre R1–R5;
4. Auto-Paper Runner con permisos acotados por estrategia;
5. probation PAPER y evaluación realizada vs esperada.

## Authority
La IA puede investigar, generar hipótesis y programar experimentos. No puede convertir por sí sola una salida model-generated en broker order authority.

Toda orden automática futura debe originarse en estrategia versionada/determinista y cruzar Portfolio + Capital Safety + OMS + writer one-shot + reconciliation.

**LIVE TRADING: BLOQUEADO.**
