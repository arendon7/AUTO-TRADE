# ESTADO ACTUAL

Fecha: 2026-08-10
Fase: v0.28R Reconstruction — R2 active

## Certified foundation
R0 Foundation durable permanece como base del control plane:
- SQLite/WAL durable state;
- hash-chained Event Ledger;
- OMS/idempotency cross-process;
- versioned portfolio + atomic risk reservations;
- persistent kill switch + stale safety-decision invalidation;
- DurablePaperBroker;
- startup reconciliation + crash recovery;
- coverage gate 85%.

## R1 — CERTIFIED
R1 Market Data + Strategy DSL + Research Integrity fue fusionado en `main` como:
`ed1c0689299b625e8092bad99814d93a4fb77438`.

Capacidades certificadas:
- canonical market-data contracts + provenance hash;
- structural anti-look-ahead / future-bar execution;
- explicit fees, spread, slippage and bar-delay latency assumption;
- volume/leverage limits in research fills;
- safe declarative Strategy DSL + canonical hash;
- protected chronological HOLDOUT + one-use final-validation permit;
- rolling/expanding walk-forward robustness;
- reproducible moving-block bootstrap;
- sample adequacy gate;
- immutable Experiment + Validation evidence registries;
- explicit failure-path review.

Evidence:
- pre-merge: 161 tests PASS, 90.34% total coverage, compile PASS, Core Safety PASS, Knowledge Contract PASS;
- post-merge `ed1c068...`: Core Safety PASS + Knowledge Contract PASS.

No known R1 P0/P1 debt at certification.

## Active track — R2 Capital Safety + OMS maturity
R2 must close the execution/control-plane debt before R3 introduces real market-data networking.

Primary work:
1. full fat-finger/price sanity certification;
2. complete order/position/strategy/portfolio exposure + leverage matrix;
3. durable daily-loss/drawdown/circuit semantics;
4. full partial-fill/cancel/replace state machine;
5. stronger UNKNOWN/reconciliation/crash/chaos behavior;
6. machine-readable versioned contract/schema registry;
7. raise control-plane critical coverage where meaningful.

## Debt discipline
`knowledge/00_CANON/DEBT_REGISTER.md` is now mandatory. Current R2 P1 items are explicit and therefore cannot be hidden by declaring the track complete early.

Planned R3–R6 capabilities remain scheduled gaps, not completed claims.

## Historical source
The exact v0.28 source remains unavailable and no longer blocks the plan. Historical reports are invariant evidence only. If the ZIP ever appears, it is treated as a forensic comparison artifact, not an automatic replacement for v0.28R.

## Graphify + Obsidian
- Obsidian `knowledge/` remains the human canon.
- Graphify semantic/deep build must run in a supported assistant/runtime.
- Any committed graph must carry a valid `graphify-out/SOURCE_SHA`.
- No graph is treated as current if its SHA differs.

## Estado de capital
**LIVE TRADING: BLOQUEADO.**
R2 is control-plane reconstruction only; no external PAPER/LIVE authority is added.

## Próximo hito
Close all R2 P1 debt, certify the R2 matrix on `main`, then and only then begin R3 real-data/research-governance networking.
