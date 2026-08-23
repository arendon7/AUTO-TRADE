# CONTEXTO RÁPIDO — AUTO-TRADE

Estado actual: **R0–R5 certified; R6 first real Alpaca PAPER canary broker-truth closed; R7 PAPER Operations activo; W78 execution qualification para Strategy Lab en certificación DRAFT.**

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/debt_register.json`
5. `knowledge/00_CANON/debt_register_r7d_auto_paper.json`
6. `knowledge/30_DECISIONES/ADR-0010-w78-deterministic-paper-execution-model.md`
7. `docs/R7_PAPER_OPERATIONS_STRATEGY_LAB.md`
8. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`

## Estado certificado
R0–R5 continúan siendo los tracks canónicos certificados en el machine debt register.

R6 first-canary exact-head certificado: `0cbb782015eeed200b9851b53764ac6389c3d9ff`.

El first canary demostró broker plumbing/idempotencia/recovery PAPER, no rentabilidad.

## Broker truth observado
Intento `first-canary-57d01d35e8b25f4babc57695ac87d962`:
- BTC/USD BUY LIMIT IOC PAPER;
- exactamente un POST de entrada;
- broker status `filled`;
- fill bruto `0.00014432 BTC`;
- posición neta GET observada `0.000143959 BTC`;
- recovery `RECOVERED_GET_ONLY`;
- `entry_attempt_count=1`;
- `retry_post=false`;
- credenciales no persistidas;
- LIVE bloqueado.

PR #49 mantiene separada la operación real risk-reducing de esa exposición. W78 no modifica su writer, lifecycle ni autorización.

## R7 / W78 abierto

### R7B — PAPER Operations real
- Portfolio Truth por GET;
- risk-reducing close/protection;
- one-shot writer;
- UNKNOWN-before-I/O;
- GET-only reconciliation;
- LIVE bloqueado.

### R7C — Strategy Lab / W78
Branch: `work/w78-realistic-paper-execution`.
PR: #50, DRAFT.
Base exacta R7 close: `1a81a5e7c856064cc170a66e24e87928711eda21`.

W78 añade una capa **sin red y sin broker-write authority** para medir sensibilidad de ejecución reutilizando Capital Safety + OMS + fills + portfolio + reconciliation existentes.

Incluye:
- adverse deterministic slippage;
- partial fills;
- LIMIT fill/no-fill bajo stress;
- deterministic market-quality rejection;
- scenario matrix hash-bound;
- Research cost-model qualification binding;
- scientific measurement hashes separados de runtime trace hashes;
- durable reconciliation contra simulated broker truth;
- permanent no-network/no-writer CI boundary.

Un PASS W78 no es profitability proof ni Auto-Paper authority.

### R7D — Auto-Paper
Bloqueado por evidencia y deuda explícita, entre ella:
- `TD-R7D-001` total execution-cost continuity;
- `TD-R7D-002` fee-complete execution accounting;
- `TD-R7D-003` safe remaining-quantity reservation after partial fills.

No promover una estrategia a Auto-Paper sólo por resultados simulados W78.

## Authority
La IA puede investigar, generar hipótesis, programar experimentos y comparar evidencia. No puede convertir por sí sola una salida model-generated en broker-order authority.

Toda orden automática futura debe originarse en estrategia versionada/determinista y cruzar:

`Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

## No-claims
- canary exitoso != estrategia rentable;
- simulated fill != fill Alpaca futuro;
- W78 sin fees realizadas != P&L completo;
- Strategy Lab != capital authority;
- PAPER qualification != LIVE qualification.

**LIVE TRADING: BLOQUEADO.**
