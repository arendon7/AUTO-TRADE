# CONTEXTO RÁPIDO — AUTO-TRADE

Estado actual: **R0–R5 certified; R6 first real Alpaca PAPER canary broker-truth closed; R7 PAPER Operations activo; W78 execution qualification certificado; W79 Strategy Promotion Governance + Strategy Lab read-only en certificación DRAFT.**

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/debt_register.json`
5. `knowledge/00_CANON/debt_register_r7d_auto_paper.json`
6. `knowledge/30_DECISIONES/ADR-0010-w78-deterministic-paper-execution-model.md`
7. `knowledge/30_DECISIONES/ADR-0011-w79-strategy-promotion-governance.md`
8. `docs/R7_PAPER_OPERATIONS_STRATEGY_LAB.md`
9. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`

## Estado certificado
R0–R5 continúan siendo los tracks canónicos certificados en el machine debt register.

R6 first-canary exact-head certificado: `0cbb782015eeed200b9851b53764ac6389c3d9ff`.

W78 exact-head certificado: `2924456e33c2cc9e6579301b176267513a90861f`.

W78 demostró execution qualification determinista/no-network bajo supuestos preregistrados. No demostró rentabilidad ni ejecución Alpaca futura.

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

PR #49 mantiene separada la operación real risk-reducing de esa exposición. W78/W79 no modifican su writer, lifecycle ni autorización.

## R7 abierto

### R7B — PAPER Operations real
PR #49 / `work/r7-paper-close-mac-staging`.

Conserva:
- Portfolio Truth por GET;
- close SELL FULL BTC/USD LIMIT IOC risk-reducing;
- Capital Safety + OMS frescos antes del writer;
- durable UNKNOWN antes del único POST;
- GET-only reconciliation ante ambigüedad;
- residual exposure => stop, sin segundo SELL automático;
- LIVE bloqueado.

### R7C — W78 Execution Qualification
PR #50 / `work/w78-realistic-paper-execution`.

W78 reutiliza el control plane existente:

`OrderIntent -> Capital Safety -> OMS -> DeterministicPaperExecutionBroker -> Fill/EventLedger -> Portfolio/Reconciliation`

Incluye slippage adverso determinista, partial fills, LIMIT fill/no-fill, market-quality rejection, scenario matrix hash-bound, Research cost-model binding, execution evidence reproducible y permanent no-network/no-writer boundary.

### R7C — W79 Strategy Promotion Governance
PR #51 / `work/w79-strategy-promotion-evidence`, apilado sobre W78 certificado.

W79 añade gobernanza de promoción sin autoridad de ejecución:
- threshold policy congelada antes de DEVELOPMENT;
- candidato congelado después de Tournament DEVELOPMENT y antes del HOLDOUT final;
- strategy id/version + trial/tournament fingerprints;
- una sola autoridad SQLite;
- gates `DEVELOPMENT_SELECTION`, `EXECUTION_SENSITIVITY`, `FINAL_HOLDOUT`, `MULTIPLE_TESTING`;
- estados PASS / FAIL / MISSING / BLOCKED;
- `EVIDENCE_QUALIFIED != PAPER_CANDIDATE`;
- blockers explícitos preservados.

Strategy Lab ya existe en el Control Center como `/strategy-lab`:
- SQLite `mode=ro` + `query_only`;
- GET-only;
- sin credenciales ni broker network;
- sin `SAFE_ACTIONS` ni POST;
- no crea `OrderIntent`;
- `PAPER candidate=false`;
- `CAPITAL=NONE`;
- `LIVE=BLOCKED`.

W79 **no persiste todavía assessment autoritativo de gates**. La UI debe mostrar `NOT_PERSISTED_BY_W79` y no sintetizar PASS.

## R7D — Auto-Paper
Bloqueado por evidencia y deuda explícita:
- `TD-R7D-001` total execution-cost continuity;
- `TD-R7D-002` fee-complete execution accounting;
- `TD-R7D-003` safe remaining-quantity reservation after partial fills;
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

No promover una estrategia a Auto-Paper sólo por backtest, HOLDOUT aislado o resultados simulados W78/W79.

## Próximo bloque después de cerrar W79
Persistir un assessment de promoción durable, hash-bound y reproducible, vinculando cada gate a evidencia inmutable sin crear PAPER candidate authority.

## Authority
La IA puede investigar, generar hipótesis, programar experimentos y comparar evidencia. No puede convertir por sí sola una salida model-generated en broker-order authority.

Toda orden automática futura debe originarse en estrategia versionada/determinista y cruzar:

`Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

## No-claims
- canary exitoso != estrategia rentable;
- simulated fill != fill Alpaca futuro;
- W78 sin fees realizadas != P&L completo;
- W79 evidence qualified != PAPER candidate;
- Strategy Lab != capital authority;
- PAPER qualification != LIVE qualification.

**LIVE TRADING: BLOQUEADO.**
