# v0.28R CAPABILITY EQUIVALENCE MATRIX

Fecha: 2026-08-10
Estado: ACTIVE — **R0–R3 certified; R4 next**
Objetivo: reconstruir todas las capacidades históricamente verificadas sin deuda oculta y sin declarar equivalencia por número de versión.

## Status legend
- `PASS`: implementado + certificado en el árbol/track correspondiente.
- `PARTIAL`: existe base útil certificada, falta cerrar la capacidad completa del track.
- `TODO`: no implementado/certificado todavía.
- `BLOCKED`: depende de una capacidad previa.

| Track | Capability | Historical evidence | Current | Evidence / exit requirement |
|---|---|---|---|---|
| R0 | deterministic safety authority separation | v0.1 | PASS | AI has no execution authority; deterministic gates only |
| R0 | tamper-detect durable Event Ledger | v0.1+ | PASS | hash-chain + persistence + verification tests |
| R0 | durable OMS + idempotency + UNKNOWN/reconciliation base | v0.10 lineage | PASS | base plus full lifecycle/ambiguity maturity certified in R2 |
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
| R2 | fat-finger and price sanity bands | v0.10 | PASS | exact boundaries, stale/invalid/crossed/future price rejection |
| R2 | portfolio/strategy/order/position exposure + leverage | v0.10 | PASS | full risk matrix + reservation interaction + snapshot-integrity checks |
| R2 | daily loss + drawdown + circuit state | v0.10 | PASS | durable UTC-session telemetry, persistent circuit, human acknowledgement recovery |
| R2 | full partial-fill/cancel/replace lifecycle | implied execution maturity | PASS | fill-level idempotency, cancel-first replace, reconciliation + crash evidence |
| R2 | versioned machine-readable contracts | v0.10 77 schemas | PASS | real contract registry + payload validation + compatibility CI; no artificial quota |
| R2 | crash/UNKNOWN/reconciliation ambiguity semantics | v0.10 lineage | PASS | broker ambiguity fail-closed + semantic replay + exact-once projection recovery |
| R3 | Binance/public read-only historical intake | v0.15 | PASS | fixed public host/path, GET-only, disabled-by-default/explicit opt-in, provenance |
| R3 | malformed/ambiguous network results fail closed | v0.15 | PASS | redirect/content-type/size/range/time coverage/duplicate/gap negative tests |
| R3 | preregistration + Trial Ledger | v0.16 | PASS | frozen campaign universe + immutable preregistration/terminal accounting |
| R3 | Strategy Tournament | v0.16 | PASS | complete DEVELOPMENT universe, deterministic ranking/tie-break, failures retained, no HOLDOUT |
| R3 | PBO / Deflated Sharpe / multiple testing | v0.16 | PASS | complete-trial accounting + Holm; advanced statistics require explicit prerequisites |
| R3 | Research Control Center read-only | v0.16 | PASS | immutable evidence projection + CI authority separation |
| R3 | FINAL_HOLDOUT authority separation | reconstructed strengthening | PASS | one-use consumed permit bound one-to-one to final trial + research authority gate |
| R3 | bounded real-data reproducibility evidence | reconstructed strengthening | PASS | 2 independent 10-bar fetches + identical hashes + artifact roundtrip |
| R4 | authoritative instrument master | reconstructed safety prerequisite | PASS | versioned venue rules/provenance; unknown/stale/conflicting metadata fail closed (`TD-R4-001`) |
| R4 | versioned Portfolio State / reconciliation infrastructure | v0.18 lineage | PASS | durable/versioned base exists from R0/R2; R4 portfolio-governance invariants still need certification |
| R4 | correlation-aware portfolio research | v0.18 | PASS | common-panel dependence + anti-forgery clusters + exact strategy/cluster/portfolio budgets (`TD-R4-002`) |
| R4 | allocation perturbation + leave-one-out | v0.18 | PASS | deterministic exact-sum Decimal normalization + complete recomputable perturbation/leave-one-out gate (`TD-R4-003`,`TD-R4-011`) |
| R4 | TRAIN-calibrated regimes | v0.18 | PASS | TRAIN/DEVELOPMENT-only calibration + frozen HOLDOUT evaluation + UNKNOWN on stale/missing evidence (`TD-R4-004`) |
| R4 | Strategy/Portfolio Health & Drift | v0.19 | PASS | baseline/policy-bound durable health + retry-safe recovery + tamper-evident ACK-chain anchored in Health state (`TD-R4-005`,`TD-R4-012`,`TD-R4-014`) |
| R4 | Defensive Health Bridge | v0.20 | PASS | reduce/block-only bridge + retry-safe recovery + authoritative unsynced-worsening overlay + Safety/OMS rechecks (`TD-R4-006`,`TD-R4-012`,`TD-R4-013`) |
| R4 | deterministic Portfolio Manager / sizing + cross-strategy budgets | reconstructed strengthening | PASS | advisory-only sizing + base/Health/venue budget+robustness recomputation + authoritative metadata + CI authority gate (`TD-R4-007`) |
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

## Certification ledger
### R1
Merged SHA: `ed1c0689299b625e8092bad99814d93a4fb77438`.
Pre-merge evidence: 161 tests PASS / 90.34% coverage; post-merge Core Safety + Knowledge Contract PASS.

### R2
Certified and integrated on `main` before R3. All R2 P0/P1/P2 reconstruction debt is CLOSED in the machine-readable Debt Register.

### R3
Branch certification basis: `74ca661eeda57ec17e501ba3bf99d1fe0eb7a34a`.
Latest certified closure evidence: **272 tests PASS / 87.56% coverage**, Contract Registry PASS, Research Authority PASS, Debt Register PASS, Knowledge Contract PASS.
Certification artifact: `knowledge/60_EVIDENCE/R3_CERTIFICATION.json`.

Bounded real-data campaign hashes:
- source: `4bebe7cba7379cce8ac55916433997c333e9e845651333f320a10eba36d84a6d`;
- dataset: `652ead045ba8bfe92c60aabc32e64913f0b397d9226ed8ac9158d9aa35b5d9a0`;
- manifest: `4240f9558b4e409c8433b8123dae79a06cb984ecd4e7f3f89d50e757b877ce79`.

This campaign is reproducibility/intake evidence, **not profitability proof**.

## Active target — R4
R4 closes portfolio/regime/health governance before R5 shadow/forward monitoring:
1. authoritative instrument master;
2. audit/reuse existing versioned portfolio state and certify R4 invariants;
3. correlation-aware diversification/concentration research;
4. allocation perturbation + leave-one-out;
5. TRAIN-only regime calibration;
6. Strategy/Portfolio Health & Drift;
7. reduce/block-only Defensive Health Bridge;
8. deterministic sizing + cross-strategy budgets;
9. keep PAPER/LIVE fail-closed.

## Debt policy
A track cannot be PASS with known P0/P1/P2 defects assigned to that track, skipped negative tests, reduced coverage, untracked mismatches, hidden critical TODOs or temporary broker/network bypasses. `knowledge/00_CANON/debt_register.json` is the machine-readable authority; this matrix is the capability view.

## Final release condition
`v0.28R` exists only when every required R0–R6 row is `PASS`, CI is green on the release SHA, canon/handoff/debt register are synchronized and LIVE remains blocked unless a separate future promotion explicitly changes that state.
