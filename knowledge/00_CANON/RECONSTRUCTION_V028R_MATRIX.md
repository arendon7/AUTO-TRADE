# v0.28R CAPABILITY EQUIVALENCE MATRIX

Fecha: 2026-08-10
Estado: ACTIVE — R1 certified, R2 active
Objetivo: reconstruir todas las capacidades históricamente verificadas sin deuda oculta.

## Status legend
- `PASS`: implementado + certificado en el árbol actual.
- `PARTIAL`: existe base útil, falta equivalencia completa.
- `TODO`: no implementado todavía.
- `BLOCKED`: depende de una capacidad previa.

| Track | Capability | Historical evidence | Current | Evidence / exit requirement |
|---|---|---|---|---|
| R0 | deterministic safety authority separation | v0.1 | PASS | AI has no execution authority; deterministic gates only |
| R0 | tamper-detect durable Event Ledger | v0.1+ | PASS | hash-chain + persistence + verification tests |
| R0 | durable OMS + idempotency + UNKNOWN/reconciliation base | v0.10 lineage | PARTIAL | base PASS; lifecycle completeness closes in R2 |
| R0 | persistent kill switch + stale safety decision invalidation | reconstructed strengthening | PASS | restart + race tests |
| R0 | atomic risk reservations cross-process | reconstructed strengthening | PASS | concurrency tests |
| R1 | canonical market data + hashes/provenance | v0.3–v0.5 lineage | PASS | strict timezone/OHLCV/order/gap handling + reproducible dataset hash |
| R1 | strategy contract / safe DSL | v0.3 | PASS | strict declarative JSON, canonical hash, no eval/import/broker/network/OMS authority |
| R1 | BAR_CLOSE -> NEXT_BAR / no look-ahead | v0.3 | PASS | exact close timestamp + `execution_delay_bars >= 1` + adversarial tests |
| R1 | explicit fees/spread/slippage/latency | v0.5 lineage | PASS | costs explicit; zero-cost opt-in; bar-delay latency scope documented |
| R1 | TRAIN/VALIDATION/protected HOLDOUT | v0.3+ | PASS | chronological split + durable one-use final-validation permit |
| R1 | walk-forward robustness | v0.5 | PASS | chronological rolling/expanding folds + distinct evaluation datasets |
| R1 | moving-block bootstrap | v0.5 | PASS | contiguous block bootstrap + explicit seed/config + failure tests |
| R1 | sample adequacy + immutable validation registry | v0.5 | PASS | adequacy gates + append-only-by-fingerprint evidence |
| R2 | fat-finger and price sanity bands | v0.10 | PARTIAL | complete boundaries + stale/invalid price tests |
| R2 | portfolio/strategy/order/position exposure + leverage | v0.10 | PARTIAL | complete risk-policy matrix + reservation interaction |
| R2 | daily loss + drawdown + circuit state | v0.10 | PARTIAL | durable counters/state + restart/race tests |
| R2 | full partial-fill/cancel/replace lifecycle | implied execution maturity | TODO | state machine + idempotency + reconciliation + chaos tests |
| R2 | versioned machine-readable contracts | v0.10 77 schemas | TODO | schema registry + compatibility/validation CI |
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
| R5 | socket termination -> DEGRADED | v0.22 | TODO | no reconnect that hides gaps |
| R5 | synchronized portfolio shadow | v0.25 | TODO | frozen weights + exact timestamps |
| R5 | forward evidence without HOLDOUT | v0.25 | TODO | post-activation evidence separation |
| R6 | external Alpaca PAPER gateway | v0.26 | TODO | PAPER host fixed + disabled default + no LIVE host |
| R6 | bounded external PAPER canary | v0.26 | TODO | prerequisites + tighter notional cap |
| R6 | PAPER evidence qualification | v0.27 | TODO | terminality/fills/slippage/reconciliation |
| R6 | broker-side equity bracket protection | v0.28 | TODO | parent bracket + exactly 2 validated legs |
| R6 | PAPER trade_updates protection evidence | v0.28 | TODO | event evidence when policy requires |
| R6 | unsupported products fail closed | v0.28 | TODO | crypto bracket unsupported unless separately certified |

## R1 certification
Merged SHA: `ed1c0689299b625e8092bad99814d93a4fb77438`.

Post-merge CI:
- Core Safety Tests: PASS;
- Knowledge Contract: PASS.

Pre-merge functional evidence retained by ADR-0004:
- 161 tests PASS;
- 90.34% total coverage; 85% gate unchanged;
- bootstrap 100%, DSL 96%, gates 96%, market 100%, validation 94%, splits 95%, strategy 100%, backtest 95%.

No known R1 P0/P1 debt at certification. Explicit R1 scope limits are documented and are not represented as completed capabilities outside R1.

## Active target — R2
R2 closes control-plane maturity before any external market/broker integration:
1. complete risk-policy matrix;
2. durable loss/drawdown/circuit semantics;
3. partial-fill/cancel/replace lifecycle;
4. reconciliation/UNKNOWN chaos paths;
5. versioned machine-readable contract registry;
6. raise coverage on persistence/reconciliation hotspots;
7. keep PAPER/LIVE fail-closed.

## Debt policy
A track cannot be PASS with known P0/P1 defects, skipped negative tests, reduced coverage, untracked mismatches, hidden critical TODOs or temporary broker/network bypasses. All actual debt is tracked in `DEBT_REGISTER.md`.

## Final release condition
`v0.28R` exists only when every required row is `PASS`, CI is green on the release SHA, canon/handoff/debt register are synchronized and LIVE remains blocked unless a separate future promotion explicitly changes that state.
