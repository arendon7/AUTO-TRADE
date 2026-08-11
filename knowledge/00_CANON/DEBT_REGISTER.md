# DEBT REGISTER — v0.28R

Fecha: 2026-08-10
Estado: ACTIVE

## Authority
The machine-readable authority is:
`knowledge/00_CANON/debt_register.json`.

This Markdown file is the human-readable operational view. If both ever disagree, the JSON register + CI gate wins and the Markdown must be repaired.

## Regla
No track can be certified with a known open P0/P1/P2 assigned to that track. Newly discovered debt must be registered before milestone closure; severity cannot be downgraded merely to satisfy a release.

## Severidad
- `P0`: immediate uncontrolled-loss/execution/corruption risk.
- `P1`: violates a required invariant or blocks safe progression.
- `P2`: important reliability/maintainability gap that must close before the owning track certifies.
- `P3`: lower-risk tooling/documentation improvement that may remain open when explicitly outside the certified invariant.

## Certified tracks
- **R0 — PASS**
- **R1 — PASS**
- **R2 — PASS**
- **R3 — PASS on certified branch; pending PR #10 integration + post-merge `main` recertification**

All R3 P0/P1/P2 items `TD-R3-001..008` are CLOSED with concrete code/test/evidence paths in `debt_register.json`.

## Current open debt

| ID | Sev | Track | Area | Current debt | Close condition |
|---|---|---|---|---|---|
| `TD-OPS-001` | P3 | OPS | Graphify | semantic/deep Graphify output has not been generated for the current tree | run real deep graph in supported runtime and bind it to valid `SOURCE_SHA`; never fabricate graph artifacts |
| `TD-R4-001` | P1 | R4 | Instrument metadata | no authoritative versioned instrument master yet for venue tick/step/min-max quantity/notional/trading status | implement provenance/version/fingerprint-bound instrument master + fail-closed stale/conflict rules + tests before later PAPER/execution use |

`TD-R4-001` is deliberately open because R4 has **not** been certified. It is a blocker for R4 completion, not hidden debt of R3.

## R3 debt closure summary

| IDs | Status | Evidence class |
|---|---|---|
| `TD-R3-001` | CLOSED | deny-by-default public-data boundary + adversarial network tests |
| `TD-R3-002` | CLOSED | canonical external-data intake + immutable campaign/artifact hashes |
| `TD-R3-003` | CLOSED | frozen campaign + preregistration/terminal Trial Ledger |
| `TD-R3-004` | CLOSED | one-use HOLDOUT permit binding + CI research-authority boundary |
| `TD-R3-005` | CLOSED | complete-family Holm + conditional PBO/Deflated Sharpe evidence |
| `TD-R3-006` | CLOSED | bounded two-fetch real-data reproducibility campaign |
| `TD-R3-007` | CLOSED | read-only Research Control Center |
| `TD-R3-008` | CLOSED | deterministic Strategy Tournament, complete DEVELOPMENT universe, no HOLDOUT/cherry-picking |

Latest R3 closure evidence: **272 tests PASS / 87.56% coverage**, Contract Registry PASS, Research Authority PASS, Debt Register PASS, Knowledge Contract PASS.

## R4 capability gaps
R4 work is now explicit, not hidden:
- authoritative instrument master (`TD-R4-001`);
- correlation-aware portfolio research;
- allocation perturbation + leave-one-out robustness;
- TRAIN-only regime calibration;
- Strategy/Portfolio Health & Drift;
- reduce/block-only Defensive Health Bridge;
- deterministic bounded Portfolio Manager/sizing + cross-strategy budgets.

Additional R4 debt IDs must be created immediately if implementation uncovers an invariant gap not already represented above.

## Track closing procedure
Before marking any track PASS:
1. query `debt_register.json` for P0/P1/P2 owned by the track;
2. close each item with concrete code/test/evidence paths;
3. run `scripts/check_debt_register.py` in CI;
4. verify capability matrix rows independently — closing debt does not automatically prove capability equivalence;
5. synchronize ESTADO/TAREA/MATRIX/HANDOFF;
6. merge only with green CI;
7. recertify the merge SHA on `main` before starting the next track.

## Capital
**LIVE TRADING: BLOQUEADO.**
No debt status or research certification grants PAPER/LIVE execution authority.
