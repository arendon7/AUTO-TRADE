# HANDOFF ACTUAL

Fecha: 2026-08-10
Fase: v0.28R Reconstruction
Track activo: **R2 — Capital Safety + OMS maturity**
Branch: `reconstruction/r2-capital-oms`

## R1 closure
R1 was merged as `ed1c0689299b625e8092bad99814d93a4fb77438`.

Certified capabilities:
- canonical market data + provenance hashes;
- structural anti-look-ahead;
- explicit research costs/capacity + bar-delay latency assumption;
- safe declarative Strategy DSL + canonical hash;
- protected HOLDOUT + one-use final-validation permit;
- chronological walk-forward robustness;
- moving-block bootstrap;
- sample adequacy;
- immutable Experiment and Validation registries;
- documented R1 failure paths.

Evidence:
- 161 tests PASS / 90.34% total coverage before merge;
- Core Safety + Knowledge Contract PASS on merge SHA.

R1 has no known P0/P1 debt. Its explicit single-symbol/bar-based limitations are scope boundaries, not hidden claims.

## Debt registry
`knowledge/00_CANON/DEBT_REGISTER.md` is mandatory from this point forward.

R2 currently owns:
- `TD-R2-001` full partial-fill/cancel/replace lifecycle;
- `TD-R2-002` versioned machine-readable contracts;
- `TD-R2-003` complete risk-policy matrix;
- `TD-R2-004` durable daily-loss/drawdown/circuit state;
- `TD-R2-005` control-plane coverage hotspots.

P0/P1 cannot be waived to close R2.

## R2 next exact work
1. inspect `domain.py`, `safety.py`, `oms.py`, `state.py`, `persistence.py`, `reconciliation.py`, `engine.py` and broker interfaces;
2. write an R2 lifecycle/risk-state design before broad mutation;
3. implement partial-fill/cancel/replace transitions + persistence;
4. make reconciliation recover ambiguous lifecycle states;
5. complete durable circuit/loss/drawdown semantics;
6. add schema/contract registry + compatibility CI;
7. extend adversarial/chaos tests;
8. close debt IDs with evidence, then certify/merge.

## Guardrails
- no broker real or external market-data network in R2;
- no blind retry after ambiguous broker I/O;
- stricter defensive state wins under concurrency/restart;
- no safety threshold is weakened to make tests/strategies pass;
- LIVE remains blocked.

## Startup sequence
`AGENTS.md -> SOURCE_OF_TRUTH -> ESTADO_ACTUAL -> TAREA_ACTIVA -> RECONSTRUCTION_V028R_MATRIX -> DEBT_REGISTER -> latest ADR -> HANDOFF_ACTUAL -> Graphify if fresh -> impacted source`.

## Capital
**LIVE TRADING: BLOQUEADO.**
