# v0.28R CAPABILITY EQUIVALENCE MATRIX

Fecha: 2026-08-10
Estado: ACTIVE
Objetivo: reconstruir todas las capacidades históricamente verificadas sin deuda oculta.

## Status legend
- `PASS`: implementado + certificado en el árbol actual.
- `PARTIAL`: existe una base útil pero falta equivalencia completa.
- `TODO`: no implementado todavía en el árbol actual.
- `BLOCKED`: depende de una capacidad previa.

| Track | Capability | Historical evidence | Current | Exit evidence required |
|---|---|---|---|---|
| R0 | deterministic safety authority separation | v0.1 | PASS | AI has no execution authority; deterministic gates only |
| R0 | tamper-detect durable Event Ledger | v0.1+ | PASS | hash-chain + persistence + verification tests |
| R0 | durable OMS + idempotency + UNKNOWN/reconciliation base | v0.10 lineage | PASS/PARTIAL | complete lifecycle to be rechecked in R2 |
| R0 | persistent kill switch + stale safety decision invalidation | reconstructed strengthening | PASS | restart + race tests |
| R0 | atomic risk reservations cross-process | reconstructed strengthening | PASS | concurrency tests |
| R1 | canonical market data + hashes/provenance | v0.3–v0.5 lineage | PARTIAL | PR #4 implementation audit + merge/rework |
| R1 | strategy contract / safe DSL | v0.3 | PARTIAL | deterministic signals, safe parser/config, hash, no broker authority |
| R1 | BAR_CLOSE -> NEXT_BAR / no look-ahead | v0.3 | PARTIAL | adversarial leakage tests |
| R1 | explicit fees/spread/slippage/latency | v0.5 lineage | PARTIAL | zero-cost requires explicit opt-in; cost stress |
| R1 | TRAIN/VALIDATION/protected HOLDOUT | v0.3+ | PARTIAL | no tuning access + one-way final validation governance |
| R1 | walk-forward robustness | v0.5 | PARTIAL | chronological rolling/expanding folds |
| R1 | moving-block bootstrap | v0.5 | TODO | block bootstrap + reproducible seeds/tests |
| R1 | sample adequacy + immutable validation registry | v0.5 | PARTIAL | explicit gates + immutable evidence |
| R2 | fat-finger and price sanity bands | v0.10 | PARTIAL | boundary + stale/invalid price tests |
| R2 | portfolio/strategy/order/position exposure + leverage | v0.10 | PARTIAL | complete risk-policy matrix |
| R2 | daily loss + drawdown + circuit state | v0.10 | PARTIAL | durable state + restart tests |
| R2 | full partial-fill/cancel/replace lifecycle | implied execution maturity | TODO | state machine + reconciliation + chaos tests |
| R2 | versioned machine-readable contracts | v0.10 77 schemas | TODO | schema registry + validation CI |
| R3 | Binance read-only historical intake | v0.15 | TODO | fixed hosts, GET-only, disabled default, provenance |
| R3 | malformed/ambiguous network results fail closed | v0.15 | TODO | negative network fixtures |
| R3 | preregistration + Trial Ledger | v0.16 | TODO | immutable trial accounting |
| R3 | Strategy Tournament | v0.16 | TODO | deterministic ranking + governance |
| R3 | PBO / Deflated Sharpe | v0.16 | TODO | complete-trial accounting prerequisite |
| R3 | Research Control Center read-only | v0.16 | TODO | no mutation/capital authority |
| R4 | correlation-aware portfolio research | v0.18 | TODO | diversification constraints |
| R4 | allocation perturbation + leave-one-out | v0.18 | TODO | robustness reports |
| R4 | TRAIN-calibrated regimes | v0.18 | TODO | no holdout-derived thresholds |
| R4 | Strategy/Portfolio Health & Drift | v0.19 | TODO | immutable baselines/reports |
| R4 | Defensive Health Bridge | v0.20 | TODO | reduce/block only + human recovery |
| R5 | closed-kline read-only stream | v0.22 | TODO | disabled default + fixed host + stream state |
| R5 | duplicate idempotency + gap fail-closed | v0.22 | TODO | no silent imputation |
| R5 | socket termination -> DEGRADED | v0.22 | TODO | no autonomous reconnect that hides gaps |
| R5 | synchronized portfolio shadow | v0.25 | TODO | frozen weights + exact timestamps |
| R5 | forward evidence without HOLDOUT | v0.25 | TODO | post-activation evidence separation |
| R6 | external Alpaca PAPER gateway | v0.26 | TODO | PAPER host fixed + disabled default + no LIVE host |
| R6 | bounded external PAPER canary | v0.26 | TODO | prerequisites + tighter notional cap |
| R6 | PAPER evidence qualification | v0.27 | TODO | terminality/fills/slippage/reconciliation |
| R6 | broker-side equity bracket protection | v0.28 | TODO | parent bracket + exactly 2 validated legs |
| R6 | PAPER trade_updates protection evidence | v0.28 | TODO | event evidence when policy requires |
| R6 | unsupported products fail closed | v0.28 | TODO | crypto bracket unsupported unless separately certified |

## Debt policy
A track cannot be marked PASS with:
- known P0/P1 defects;
- skipped negative tests;
- reduced coverage gate;
- untracked capability mismatch;
- TODO hidden in code comments without a canonical debt item;
- temporary bypasses to reach broker/network paths.

## Immediate target
**R1** is the active reconstruction track. PR #4 is not merged blindly: its research engine must be audited against every R1 row, then completed before promotion.

## Final release condition
`v0.28R` exists only when every row above is `PASS`, CI is green on the release SHA, source-of-truth/handoff are synchronized and LIVE remains blocked unless a separate future promotion explicitly changes that state.
