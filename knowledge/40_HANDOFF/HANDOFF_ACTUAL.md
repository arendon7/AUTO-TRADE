# HANDOFF ACTUAL

Fecha: 2026-08-10
Fase: v0.28R Reconstruction
Track activo: R1 — Market Data + Strategy DSL + Research Engine

## Cambio de dirección aprobado
El source histórico v0.28 se considera irrecuperable para el plan de trabajo. En vez de seguir esperando, el proyecto reconstruirá una implementación equivalente **v0.28R** sin deuda técnica oculta.

ADR-0006 define el programa y `RECONSTRUCTION_V028R_MATRIX.md` define cada capacidad que debe volver a quedar PASS.

## Base actual
R0 Foundation durable ya existe en `main`:
- SQLite/WAL state;
- durable hash-chained ledger;
- OMS/idempotency;
- portfolio versioning + atomic risk reservations;
- persistent kill switch;
- DurablePaperBroker;
- startup reconciliation/crash recovery;
- coverage gate >=85%.

## PR #4
Research v0.4 reconstruida contiene una base fuerte y ya certificada en su head anterior:
- canonical bars/instrument metadata/dataset hashes;
- event-driven backtester;
- minimum future-bar execution;
- explicit fees/spread/slippage/leverage/volume participation;
- temporal splits + protected HOLDOUT permits;
- SQLite Experiment Registry;
- walk-forward + robustness gates;
- adversarial tests;
- 122 tests PASS / 89.40% coverage.

PR #4 pasa de fallback a **candidato R1**. No se fusiona todavía: primero se completa contra toda la matriz R1.

## Faltantes R1 explícitos
- safe Strategy DSL/config validation + canonical hash;
- stronger proof that strategy layer has no broker/network/risk authority;
- moving-block bootstrap;
- sample adequacy / validation-completeness gates;
- explicit latency semantics in cost/execution assumptions if not already equivalent;
- final audit of HOLDOUT isolation and experiment immutability;
- sync with current source-of-truth/canon;
- threat/failure-path review.

## Después de R1
R2: Capital Safety + OMS maturity, incluyendo full partial-fill/cancel/replace lifecycle y machine-readable contract registry.
Luego R3→R6 hasta external PAPER + verified broker-side protection.

## Debt rule
No se cierra ningún track con P0/P1 conocido. P2+ solo puede quedar si está explícito, justificado y no degrada un invariant histórico.

## Memoria
Startup recomendado:
`AGENTS.md -> SOURCE_OF_TRUTH -> CONTEXTO_RAPIDO -> ESTADO_ACTUAL -> TAREA_ACTIVA -> RECONSTRUCTION_V028R_MATRIX -> latest ADR -> HANDOFF_ACTUAL -> Graphify if fresh -> impacted source`.

## Capital
**LIVE TRADING: BLOQUEADO.**
