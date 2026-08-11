# HANDOFF ACTUAL

Fecha: 2026-08-10
Fase: v0.28R Reconstruction
Estado: **R3 branch-certified for merge; R4 next**
Branch de integración R3: `reconstruction/r3-real-data-governance`
PR: #10

## Certified through R3
### R0
Durable deterministic control plane: Event Ledger, state/persistence, reservations, kill switch, reconciliation and fail-closed execution base.

### R1
Market-data/backtester/research-integrity foundation:
- canonical market data + provenance hashes;
- structural anti-look-ahead;
- explicit costs/capacity/latency scope;
- safe declarative Strategy DSL;
- protected HOLDOUT + one-use final-validation permit;
- walk-forward, moving-block bootstrap and sample adequacy;
- immutable Experiment/Validation evidence.

### R2
Capital Safety + OMS maturity:
- exact fat-finger/price/risk boundaries;
- order/position/strategy/portfolio/leverage matrix;
- fill-level idempotency;
- partial-fill/cancel/replace lifecycle;
- UNKNOWN/ambiguity/reconciliation/crash recovery;
- durable daily-loss/drawdown circuit state;
- machine-readable contract registry;
- portfolio snapshot/projection integrity.

### R3
Read-only real data + Research Governance:
- bounded deny-by-default public historical-data boundary;
- exact canonical external-data intake + immutable hashes;
- frozen Campaign + preregistered Trial Ledger;
- FINAL_HOLDOUT one-use permit binding;
- complete-family multiple testing / Holm + conditional PBO/Deflated Sharpe;
- deterministic Strategy Tournament over complete DEVELOPMENT universe, no HOLDOUT inputs;
- read-only Research Control Center;
- CI research-authority separation;
- bounded real-data reproducibility campaign.

## R3 certification evidence
Certification basis: `74ca661eeda57ec17e501ba3bf99d1fe0eb7a34a`.

Latest full closure run:
- 272 tests PASS;
- 87.56% branch coverage (85% gate unchanged);
- Contract Registry PASS;
- Research Authority Boundary PASS;
- Debt Register Contract PASS;
- Knowledge Contract PASS.

Immutable certification artifact:
`knowledge/60_EVIDENCE/R3_CERTIFICATION.json`.

Real-data campaign:
- BTCUSDT / 1m / 10 bars;
- two independent fetches equal;
- artifact roundtrip verified;
- no API key, trading endpoint or execution authority;
- source SHA-256 `4bebe7cba7379cce8ac55916433997c333e9e845651333f320a10eba36d84a6d`;
- dataset hash `652ead045ba8bfe92c60aabc32e64913f0b397d9226ed8ac9158d9aa35b5d9a0`;
- manifest fingerprint `4240f9558b4e409c8433b8123dae79a06cb984ecd4e7f3f89d50e757b877ce79`.

This evidence validates intake/reproducibility only; it does **not** prove profitability.

## Debt state
Machine-readable authority: `knowledge/00_CANON/debt_register.json`.

R3 has no open P0/P1/P2 debt.
Known open items outside R3:
- `TD-OPS-001` P3 — real Graphify semantic/deep graph pending supported runtime;
- `TD-R4-001` P1 — authoritative instrument master before later PAPER/execution paths may rely on venue filters.

## Immediate integration sequence
1. run final CI on the fully synchronized R3 branch head;
2. mark PR #10 ready only if every gate is green;
3. merge PR #10;
4. recertify the resulting `main` SHA;
5. only then create `reconstruction/r4-portfolio-health` from that green `main`.

## R4 next exact work
1. close `TD-R4-001` with a versioned authoritative instrument master;
2. audit/reuse existing versioned Portfolio State/reconciliation infrastructure;
3. implement correlation-aware portfolio research + cross-strategy budgets;
4. implement allocation perturbation + leave-one-out robustness;
5. implement TRAIN-calibrated regimes with no HOLDOUT-derived thresholds;
6. implement immutable Strategy/Portfolio Health & Drift evidence;
7. implement reduce/block-only Defensive Health Bridge with explicit recovery policy;
8. implement deterministic bounded Portfolio Manager/sizing;
9. adversarial tests + debt closure + R4 certification before R5.

## Guardrails
- research/Tournament/Control Center have no OMS/broker/capital authority;
- R3 `price_tick`/`quantity_step` values are research serialization precision, not authoritative venue filters;
- no blind retry after ambiguous broker I/O;
- stricter defensive state wins under ambiguity/concurrency/restart;
- no safety threshold is weakened to make tests/strategies pass;
- no external PAPER/LIVE order submission in R4;
- Graphify artifacts are never fabricated.

## Startup sequence
`AGENTS.md -> CONTEXTO_RAPIDO -> ESTADO_ACTUAL -> TAREA_ACTIVA -> RECONSTRUCTION_V028R_MATRIX -> debt_register.json -> latest ADR/evidence -> HANDOFF_ACTUAL -> Graphify if fresh -> impacted source`.

## Capital
**LIVE TRADING: BLOQUEADO.**
