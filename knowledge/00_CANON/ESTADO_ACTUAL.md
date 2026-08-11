# ESTADO ACTUAL

Fecha: 2026-08-10
Fase: v0.28R Reconstruction — **R3 certified for merge; R4 next**

## Estado de certificación
- **R0 — PASS:** foundation durable del control plane.
- **R1 — PASS:** market-data/backtesting/Strategy DSL/research-integrity foundation.
- **R2 — PASS:** Capital Safety + OMS lifecycle/control-plane maturity.
- **R3 — PASS en rama certificada:** real-data read-only + research governance; pendiente únicamente integración de PR #10 y recertificación post-merge de `main`.
- **R4–R6 — NOT CERTIFIED.**

## R0–R2 — base certificada
La base conserva:
- SQLite/WAL durable state;
- hash-chained Event Ledger;
- OMS/idempotency cross-process;
- versioned portfolio + atomic risk reservations;
- persistent kill switch, daily-loss/drawdown circuit + stale safety-decision invalidation;
- full partial-fill/cancel/replace lifecycle;
- DurablePaperBroker interno;
- startup reconciliation + crash/ambiguity recovery;
- strict machine-readable contracts;
- exact risk-policy boundaries and portfolio-snapshot integrity;
- coverage gate >=85%.

## R3 — BRANCH CERTIFIED FOR MERGE
R3 cierra el bloque de datos reales **read-only** y governance de investigación sin crear autoridad sobre capital.

Capacidades certificadas:
- public historical-data boundary deny-by-default, GET-only, HTTPS allowlist y redirect validation before I/O;
- bounded timeout/response/range and strict malformed/ambiguous-data rejection;
- canonical external-data intake reutilizando los contratos R1 `InstrumentMetadata`, `Bar` y `MarketDataset`;
- exact half-open time coverage, no silent truncation/imputation, source/dataset/manifest hashes;
- durable frozen Campaign + Trial Ledger con preregistration and terminal accounting;
- FINAL_HOLDOUT bound one-to-one to a consumed one-use `final_validation` permit;
- CI Research Authority Boundary: research cannot import OMS/broker/safety/execution authority;
- complete-family Holm multiple-testing evidence; PBO/CSCV and Deflated Sharpe only with explicit statistical prerequisites;
- deterministic Strategy Tournament over the **complete DEVELOPMENT universe**, failed trials included, immutable tie-breaking, no HOLDOUT inputs and no promotion/execution authority;
- read-only Research Control Center;
- bounded reproducible real-data campaign.

### R3 technical evidence
Certification basis: `74ca661eeda57ec17e501ba3bf99d1fe0eb7a34a`.

Latest closure evidence:
- **272 tests PASS**;
- **87.56% total branch coverage**; 85% gate unchanged;
- Contract Registry PASS;
- Research Authority Boundary PASS;
- Debt Register Contract PASS;
- Knowledge Contract PASS.

Certification artifact:
`knowledge/60_EVIDENCE/R3_CERTIFICATION.json`.

### Bounded real-data evidence
Campaign: BTCUSDT / 1m / 10 bars / 2026-01-01 00:00–00:10 UTC.

- two independent fetches equal: YES;
- artifact roundtrip verified: YES;
- API keys used: NO;
- trading endpoints used: NO;
- execution authority: NONE;
- source payload SHA-256: `4bebe7cba7379cce8ac55916433997c333e9e845651333f320a10eba36d84a6d`;
- dataset hash: `652ead045ba8bfe92c60aabc32e64913f0b397d9226ed8ac9158d9aa35b5d9a0`;
- manifest fingerprint: `4240f9558b4e409c8433b8123dae79a06cb984ecd4e7f3f89d50e757b877ce79`.

**This proves intake/reproducibility only. It is not profitability evidence.**

## Open debt / future blockers
The machine-readable authority is `knowledge/00_CANON/debt_register.json`.

Current known open items:
- `TD-OPS-001` — P3 — real Graphify semantic/deep graph not yet generated in a supported runtime;
- `TD-R4-001` — P1 — authoritative, versioned instrument master for tick/step/min-max quantity/notional/trading status before any external PAPER/execution path relies on instrument filters.

No R3 P0/P1/P2 debt remains open. R4 cannot certify while `TD-R4-001` remains open.

## R4 — next development track
R4 must reconstruct **Portfolio / Regime / Health governance**, not just basic position bookkeeping:
1. authoritative instrument metadata;
2. correlation-aware portfolio research and deterministic cross-strategy risk budgets;
3. allocation perturbation + leave-one-out robustness;
4. TRAIN-calibrated regimes with no HOLDOUT-derived thresholds;
5. Strategy/Portfolio Health & Drift with immutable baselines/evidence;
6. Defensive Health Bridge that can only reduce/block risk automatically and requires explicit recovery policy;
7. deterministic portfolio sizing/allocation under Capital Safety Kernel constraints.

Existing versioned Portfolio State/reconciliation from R0/R2 is reusable infrastructure, **not proof that R4 is complete**.

## Historical source
The exact historical v0.28 source remains unavailable. It no longer blocks reconstruction. Historical evidence is used to recover invariants/capabilities, never to invent missing source or declare equivalence without tests.

## Graphify + Obsidian
- `knowledge/` remains the human-readable Obsidian canon.
- `debt_register.json`, code, CI and immutable evidence are machine-verifiable truth.
- Graphify semantic/deep build must run in a supported runtime; no synthetic `graphify-out/` is accepted.

## Estado de capital
**LIVE TRADING: BLOQUEADO.**
R3 adds no PAPER/LIVE authority. External broker integration remains a future R6 capability after R4/R5 certification.

## Próximo hito
Integrate PR #10, recertify the merge SHA on `main`, then open R4 from that certified `main` and close R4 debt/capabilities before any shadow/PAPER progression.
