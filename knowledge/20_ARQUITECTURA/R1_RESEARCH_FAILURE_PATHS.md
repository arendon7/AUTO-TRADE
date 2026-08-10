# R1 — RESEARCH FAILURE-PATH REVIEW

Date: 2026-08-10
Track: v0.28R / R1
Status: pre-merge review

## Purpose
Demostrar que R1 falla de forma explícita frente a leakage, data corruption, optimistic execution, mutable evidence y unsafe strategy configuration.

| Failure path | Guard | Evidence/test class | Residual scope |
|---|---|---|---|
| naive timestamp | Market/Signal validation | market + signal negative tests | none in R1 |
| duplicate/out-of-order bars | MarketDataset strict ordering | market negative tests | none |
| impossible OHLC | Bar invariants | market negative tests | none |
| time gaps | `gap_indexes()` + sample adequacy policy | market/gate tests | policy decides tolerated count; no silent imputation |
| strategy sees future bars | `StrategyContext.history = bars[:index+1]` | backtest assertions | none |
| same-bar fill | `execution_delay_bars >= 1` | future-bar tests | sub-bar realism out of R1 scope |
| signal timestamp forged | exact current close timestamp check | signal negative tests | none |
| zero trading frictions accidentally | explicit zero-cost opt-in | cost-model tests | calibrated real-world costs belong later campaign config |
| impossible volume fill | max volume participation + zero-volume reject | backtest negative tests | no dynamic impact curve yet |
| excessive backtest leverage | max leverage check | backtest negative tests | portfolio/live leverage belongs R2 |
| arbitrary code in strategy config | strict JSON allowlist, no eval/import/callable/module | DSL injection tests | DSL intentionally narrow |
| strategy config mutated semantically without identity change | canonical strategy hash | DSL hash tests | code-version remains separate evidence input |
| initial stop confused with broker protection | explicit ADR/DSL wording | DSL metadata test + ADR | actual protection belongs R6 |
| HOLDOUT reused | durable unique permit consumption | holdout permit tests | governance still required against malicious source edits |
| HOLDOUT used in normal tuning | hidden dataset behind `ProtectedHoldout.checkout()` | split/permit tests | no cryptographic sandbox claimed |
| too little sample | SampleAdequacyPolicy | adequacy tests | thresholds campaign-specific |
| walk-forward dataset duplicated | distinct dataset-hash requirement | robustness negative tests | none |
| serial dependence destroyed by IID bootstrap | moving-block bootstrap | reproducibility tests | block-size calibration is research policy |
| bootstrap nondeterministic/untraceable | explicit seed/config | reproducibility tests | none |
| experiment rerun differs silently | same spec + different result hash => conflict | registry tests | none |
| validation evidence overwritten | append-only-by-fingerprint validation registry | validation conflict tests | none |
| NaN/inf contaminates research evidence | finite-value checks + canonical JSON `allow_nan=False` | negative tests | add new boundary tests when new metrics appear |
| research output reaches broker | package emits ResearchSignal only; no broker dependency in DSL | architecture/code review | broker integration prohibited until later tracks |

## Threat boundary
R1 is not a hostile-code sandbox. A developer with write access can edit Python source. The protection objective is to prevent accidental/dynamic execution from strategy configuration and to make provenance/reproducibility auditable through Git, hashes, tests and CI.

## Latency statement
`execution_delay_bars` is the R1 latency assumption. It is deterministic, explicit and at least one bar. This is intentionally conservative but not a claim of exchange microstructure realism. R6/execution realism may introduce venue-specific latency without weakening the no-same-bar invariant.

## Promotion rule
R1 cannot be merged while:
- a failure path above lacks a guard/evidence;
- CI is red;
- coverage gate is reduced;
- a P0/P1 research-integrity defect is known;
- source-of-truth/ADR/matrix are inconsistent.

## Capital
**LIVE TRADING: BLOQUEADO.**
