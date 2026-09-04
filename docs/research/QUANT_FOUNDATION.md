# AUTO-TRADE R7 — Quant Research Foundation

## Purpose

R7 expands the existing audited AUTO-TRADE research layer without weakening the R6 PAPER/LIVE governance boundary. The objective is not to import a large third-party trading bot into the product. The objective is to keep AUTO-TRADE as the control plane and use selected external projects as pinned research, validation, analytics or connector references.

No strategy in this document is assumed profitable. Every candidate must earn promotion through the existing DEVELOPMENT -> HOLDOUT -> PAPER evidence chain after costs, slippage and risk constraints.

## Architecture decision

```text
External research sources (pinned, non-versioned)
        |
        +-- LEAN ---------------- independent engine validation
        +-- Qlib ---------------- ML / research workflow reference
        +-- Hummingbot ----------- crypto connector/execution reference
        +-- Zipline Reloaded ----- independent backtest reference
        +-- gs-quant ------------- risk/statistics reference
        +-- NumerAPI ------------- optional external model/data source
        |
        v
AUTO-TRADE research strategy library
        |
        v
BacktestEngine -> DEVELOPMENT trials -> Strategy Tournament
        |
        v
Frozen HOLDOUT -> robustness/stress evidence
        |
        v
R6 PAPER gateway and reconciliation
        |
        v
LIVE remains blocked by the existing human/risk governance gates
```

The external repositories are cloned under `.external/quant/` by `scripts/bootstrap_quant_sources.py`. That directory is intentionally ignored by Git. Third-party source is therefore not silently vendored into AUTO-TRADE.

## Audited source registry — 2026-09-03

| Source | Pinned commit | License | R7 policy |
|---|---|---|---|
| QuantConnect/Lean | `cfc7e8ac451e384b08b697465e33016ab26c1263` | Apache-2.0 | Clone; independent engine/architecture validation |
| microsoft/qlib | `79633dd9506ea689e5400dea0197717b5b3d74b7` | MIT | Clone; isolated ML/research reference |
| hummingbot/hummingbot | `2bfaccc48dd49e71a5b6d9b3011808e127dd00cd` | Apache-2.0 | Clone; connector/execution reference |
| stefan-jansen/zipline-reloaded | `943010b9da848e317fc520de87edade2b884d329` | Apache-2.0 | Clone; independent backtest reference |
| goldmansachs/gs-quant | `ccbd4ae780f51be4e01ecbf834c7b93583fec57f` | Apache-2.0 | Clone; quantitative analytics reference |
| numerai/numerapi | `ab54eef18f54d0244199cb8bffd4da647621191f` | MIT | Clone; optional Numerai integration |
| freqtrade/freqtrade | not vendored | GPL-3.0 | Ideas/reference only; no source copied into core |
| polakowo/vectorbt | not vendored | Apache-2.0 + Commons Clause | Reference only for commercial core |
| StockSharp/StockSharp | not vendored | StockSharp custom/EULA | Metadata/reference only |
| wilsonfreitas/awesome-quant | not vendored | curated index | Discovery only; audit each downstream license separately |

### Source-governance rules

1. Every external source used by automated research must have an immutable commit pin.
2. License review precedes code reuse.
3. GPL, Commons-Clause and custom-license code is not copied into AUTO-TRADE core by this track.
4. External engines cannot bypass AUTO-TRADE risk, approval, reconciliation, shadow or kill-switch layers.
5. No external repository receives broker secrets from the bootstrap process.
6. A source upgrade is a reviewed change: update the pin, review upstream changes and rerun the relevant validation suite.

## Strategy library v1

`src/autotrade/research/strategy_library.py` initially contains four independent, deterministic implementations. They use only the Python standard library and AUTO-TRADE research interfaces.

### 1. Time-series momentum

Signal: compare the most recent fully closed price with the close `N` bars ago. Positive momentum targets long exposure; negative momentum targets short or flat according to `position_mode`.

Rationale: time-series momentum/trend is one of the most extensively documented systematic effects across equity-index, currency, commodity and bond futures. Moskowitz, Ooi and Pedersen (Journal of Financial Economics, 2012) document return persistence over horizons up to roughly 12 months across 58 liquid instruments.

Reference: `https://doi.org/10.1016/j.jfineco.2011.11.003`

### 2. Donchian breakout

Signal: the current fully closed bar must close outside the high/low channel formed only by prior bars. This makes the information boundary explicit and leaves actual execution to the next-bar execution semantics of the existing backtester.

Purpose: provide a structurally different trend representation from moving-average crossover and time-series return momentum.

### 3. Mean-reversion z-score

Signal: compare the current close with a mean and volatility estimate computed from the prior reference window. Extreme negative deviations target long exposure; extreme positive deviations target short/flat exposure; positions can return to flat near the estimated mean.

Purpose: diversify the candidate universe. It is not presumed to outperform trend strategies and must be rejected when transaction costs, regimes or holdout evidence do not support it.

### 4. Volatility-managed momentum

Signal direction comes from time-series momentum. Position quantity is multiplied by a bounded ratio of target per-bar volatility to observed return volatility.

Rationale: momentum can exhibit severe crash risk. Barroso and Santa-Clara (Journal of Financial Economics, 2015) show that momentum risk varies materially through time and that volatility-based risk management can strongly improve its risk-adjusted behavior in their sample.

Reference: `https://doi.org/10.1016/j.jfineco.2014.11.010`

## Validation doctrine

A candidate is not selected because it has the highest raw backtest return. The research process should progressively require:

1. **Information-boundary correctness** — closed-bar signals, next-bar execution, no look-ahead.
2. **Realistic costs** — spread, fees, slippage, volume participation and market-impact assumptions.
3. **DEVELOPMENT evidence** — deterministic trial ledger and complete frozen candidate universe.
4. **Multiple-testing control** — record every tried configuration; do not hide failed trials.
5. **Cross-validation / walk-forward robustness** — sensitivity across periods and regimes.
6. **Frozen HOLDOUT** — used only after development selection is frozen.
7. **Stress tests** — higher fees/slippage, latency, gaps, adverse volatility and liquidity.
8. **Independent-engine comparison** — selected candidates can be reproduced in LEAN and/or Zipline where practical.
9. **PAPER forward evidence** — use R6 reconciliation, anomalies, SLOs and shadow controls.
10. **LIVE governance** — unchanged; no research module can authorize capital-bearing execution.

## Ranking objectives

The tournament should treat raw return as only one dimension. Candidate reports should include at least:

- net return after costs;
- maximum drawdown;
- Sharpe/Sortino or existing risk-adjusted metrics;
- profit factor and expectancy where supported by the trade ledger;
- turnover and total modeled costs;
- parameter sensitivity;
- regime stability;
- holdout degradation versus development;
- tail/stress behavior;
- statistical evidence adjusted for repeated trials where available.

A target such as a fixed 5% return every day is explicitly **not** an acceptance criterion. Optimization against an extreme fixed daily return target would strongly incentivize leverage, overfitting and hidden tail risk.

## Bootstrap

List the approved source catalog:

```bash
python scripts/bootstrap_quant_sources.py --list
```

Clone all permissive pinned sources:

```bash
python scripts/bootstrap_quant_sources.py
```

Clone only selected sources:

```bash
python scripts/bootstrap_quant_sources.py --source lean --source qlib
```

The bootstrap script does not install or import these projects into AUTO-TRADE. Integration is deliberate and adapter-based.

## Next R7 increments

1. Add a safe declarative catalog/factory for the four strategy kinds without dynamic imports or arbitrary callables.
2. Run DEVELOPMENT campaigns over multiple liquid instruments and timeframes using the existing trial ledger.
3. Add ensemble/portfolio allocation only after individual strategy evidence exists.
4. Add independent LEAN parity fixtures for selected strategies.
5. Add market-regime conditioning and volatility/correlation-aware portfolio sizing.
6. Add a data-provider abstraction for crypto and equities while keeping research datasets immutable and hashed.
7. Promote only validated candidates into R6 PAPER; do not modify R6 LIVE authorization semantics.
