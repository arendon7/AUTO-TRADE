# AUTO-TRADE R7 — Quant Research Foundation

## Purpose

R7 expands the audited AUTO-TRADE research layer without weakening the R6 PAPER/LIVE governance boundary. AUTO-TRADE remains the control plane; selected external projects are pinned research, validation, analytics or connector references rather than a replacement trading bot.

No strategy is assumed profitable. Every candidate must earn promotion through DEVELOPMENT -> protected HOLDOUT -> PAPER evidence after costs, slippage, robustness, multiple-testing and risk constraints.

**Integration baseline:** this R7 track was created from R6 head `f59df20f3f1d28877121a36d03094012ae37e50c`. It must be rebased/retargeted onto the eventual merged R6 commit before R7 can be merged to `main`.

## Architecture

```text
Pinned external research sources (.external/quant, not vendored)
        |
        +-- LEAN ---------------- independent engine validation
        +-- Qlib ---------------- ML/research reference
        +-- Hummingbot ----------- crypto execution/connector reference
        +-- CCXT ----------------- exchange/data normalization reference
        +-- Zipline Reloaded ----- independent backtest reference
        +-- gs-quant ------------- risk/statistics reference
        +-- PyPortfolioOpt ------- portfolio/risk allocation reference
        +-- NumerAPI ------------- optional external model/data source
        |
        v
LibraryStrategySpec (strict finite catalog)
        |
        v
StrategySearchSpace -> StrategyProgram (frozen bounded universe)
        |
        v
DevelopmentResearchAutopilot
        |
        +-- BacktestEngine / explicit costs / next-bar execution
        +-- SQLiteTrialLedger preregistration
        +-- Strategy Tournament
        +-- minimum quality policy
        +-- chronological walk-forward
        +-- adverse cost/latency/liquidity stress
        +-- PBO / CSCV
        +-- Deflated Sharpe
        +-- explicit statistical promotion gate
        |
        v
promotion_ready_trial_id (may remain EMPTY)
        |
        v
Protected HOLDOUT (separate permit-gated path; not accessible to autopilot)
        |
        v
R6 PAPER gateway / reconciliation / shadow controls
        |
        v
LIVE remains governed by existing human/risk authorization gates
```

## Audited source registry — 2026-09-03

| Source | Pinned commit | License | R7 policy |
|---|---|---|---|
| QuantConnect/Lean | `cfc7e8ac451e384b08b697465e33016ab26c1263` | Apache-2.0 | Clone; independent engine/architecture validation |
| microsoft/qlib | `79633dd9506ea689e5400dea0197717b5b3d74b7` | MIT | Clone; isolated ML/research reference |
| hummingbot/hummingbot | `2bfaccc48dd49e71a5b6d9b3011808e127dd00cd` | Apache-2.0 | Clone; connector/execution reference |
| ccxt/ccxt | `420f367bcfbbe8a125b006b0025dce43301cc0dc` | MIT | Clone; exchange/data normalization reference |
| stefan-jansen/zipline-reloaded | `943010b9da848e317fc520de87edade2b884d329` | Apache-2.0 | Clone; independent backtest reference |
| goldmansachs/gs-quant | `ccbd4ae780f51be4e01ecbf834c7b93583fec57f` | Apache-2.0 | Clone; quantitative analytics reference |
| PyPortfolio/PyPortfolioOpt | `a6638d2e06dae6f444fd022cfd4b3c528902a85b` | MIT | Clone; allocation/covariance/risk reference |
| numerai/numerapi | `ab54eef18f54d0244199cb8bffd4da647621191f` | MIT | Clone; optional Numerai integration |
| freqtrade/freqtrade | not vendored | GPL-3.0 | Ideas/reference only; no source copied into core |
| polakowo/vectorbt | not vendored | Apache-2.0 + Commons Clause | Reference only for commercial core |
| StockSharp/StockSharp | not vendored | StockSharp custom/EULA | Metadata/reference only |
| wilsonfreitas/awesome-quant | not vendored | curated index | Discovery only; audit every downstream license |

### Source governance

1. External research sources use immutable commit pins.
2. License review precedes code reuse.
3. GPL, Commons-Clause and custom-license code is not copied into AUTO-TRADE core by this track.
4. External engines cannot bypass AUTO-TRADE risk, approval, reconciliation, shadow or kill-switch layers.
5. Bootstrap never receives broker secrets.
6. Source upgrades require a pin change, upstream review and validation rerun.
7. `.external/quant/` is Git-ignored; third-party histories are not silently incorporated into AUTO-TRADE.

## Strategy library v1

`src/autotrade/research/strategy_library.py` contains four original deterministic implementations using only the standard library and AUTO-TRADE research interfaces.

### Time-series momentum

Compares the latest fully closed price with the close `N` bars earlier. Positive momentum targets long exposure; negative momentum targets short or flat according to `position_mode`.

Research basis: Moskowitz, Ooi and Pedersen, *Time Series Momentum*, Journal of Financial Economics (2012). DOI: `10.1016/j.jfineco.2011.11.003`.

### Donchian breakout

The current fully closed bar must close outside a high/low channel formed only by prior bars. Actual execution remains next-bar under the existing backtester.

### Mean-reversion z-score

Uses a prior-bar reference window. Extreme deviations can target contrarian exposure and return to flat near the estimated mean. It is included as a diversifier, not presumed superior.

### Volatility-managed momentum

Momentum supplies direction while bounded inverse-volatility scaling reduces exposure as realized volatility rises.

Research basis: Barroso and Santa-Clara, *Momentum Has Its Moments*, Journal of Financial Economics (2015). DOI: `10.1016/j.jfineco.2014.11.010`.

## Safe automatic search

### `LibraryStrategySpec`

The declarative catalog can instantiate only the four audited strategy classes. Unknown fields, arbitrary callables, module imports, commands, URLs, broker fields and OMS fields fail closed.

### `StrategySearchSpace`

Each family is a finite deterministic grid. Candidate cardinality is known before execution and bounded by `max_candidates`; duplicate parameter values and invalid dimensions are rejected before a campaign exists.

### `StrategyProgram`

Combines multiple families into one frozen candidate universe with a global `max_total_candidates`. Program hashes and trial IDs bind strategy family, version and parameters. This makes the full multiple-testing universe explicit before results are observed.

### `DevelopmentResearchAutopilot`

The autopilot accepts an already-designated DEVELOPMENT dataset and:

1. creates the frozen campaign;
2. preregisters **every** candidate before any result is recorded;
3. runs the existing event-driven backtester with explicit costs and next-bar execution;
4. retains successful and failed trials in durable accounting;
5. ranks the complete DEVELOPMENT universe with Strategy Tournament;
6. applies a separate minimum quality policy without deleting weak trials;
7. evaluates eligible candidates on chronological walk-forward folds;
8. stresses fees, spread, slippage, execution delay, liquidity participation and leverage assumptions;
9. computes PBO/CSCV and Deflated Sharpe when their statistical preconditions hold;
10. records no synthetic p-values;
11. distinguishes a **robust DEVELOPMENT selection** from a **promotion-ready candidate**.

The autopilot imports no broker, OMS, PAPER, LIVE, safety-control or network execution surface. It cannot consume a protected HOLDOUT permit.

## Three separate selection states

R7 intentionally does not use one overloaded concept of “winner”.

1. `tournament_selected_trial_id`: best policy-eligible candidate by the frozen Strategy Tournament metric.
2. `selected_trial_id`: candidate that also survives the configured walk-forward and stress policy.
3. `promotion_ready_trial_id`: candidate that additionally passes the explicit statistical gate.

The third value may and often should be empty. An empty value is a valid safe outcome, not an execution failure.

### Statistical promotion gate

Default public-research policy:

- Deflated Sharpe probability >= `0.95`;
- PBO <= `0.50`;
- both PBO and Deflated Sharpe evidence required;
- robust selection must match the frozen maximum-Sharpe trial to inherit the current Deflated Sharpe evidence binding.

If PBO is unavailable because its preconditions fail, the default gate fails closed. If Deflated Sharpe is unavailable, the default gate fails closed. Diagnostic CLI overrides exist but are not used by the canonical public matrix.

## Walk-forward and stress robustness

`src/autotrade/research/robustness.py` adds DEVELOPMENT-only evidence without opening HOLDOUT.

Walk-forward evaluation:

- chronological folds only;
- strategy warm-up comes from prior training bars;
- warm-up bars cannot emit trades;
- evaluation remains next-bar under the normal backtester;
- minimum fold count is explicit.

Default adverse scenarios:

1. `costs-2x`: doubles fees, spread and slippage;
2. `latency-liquidity`: increases execution delay and reduces participation/leverage;
3. `severe-friction`: 3x fees/spread, 4x slippage, extra delay and reduced liquidity/leverage.

Robustness evidence includes positive-fold ratio, median fold Sharpe, worst fold return/drawdown, stress pass ratio, worst stress return/drawdown and immutable fingerprints.

## Existing market-data foundation

AUTO-TRADE already contains a bounded `BinanceSpotHistoricalProvider` for public klines. It is disabled by default, HTTPS/GET-only, host/path allowlisted, range-bounded, exact-coverage checked and produces immutable hashes/provenance artifacts. Therefore CCXT is not required for the first Binance research campaign; it remains useful for later multi-exchange normalization.

## First public multi-market matrix

The canonical workflow `.github/workflows/r7-public-research.yml` executes six fixed DEVELOPMENT campaigns:

- BTCUSDT: 1h and 4h;
- ETHUSDT: 1h and 4h;
- SOLUSDT: 1h and 4h.

Fixed historical request window: `2025-09-01T00:00:00Z` to `2026-06-01T00:00:00Z`.

Each campaign freezes 36 strategy/parameter candidates, writes an immutable source dataset, durable trial ledger and JSON evidence report, and proves that its protected HOLDOUT was not checked out.

### Matrix run #1 — empirical DEVELOPMENT result

Run #1 completed all six campaign jobs plus the aggregate evidence job successfully.

Observed outcome before adding the explicit statistical promotion field:

- BTC 1h: no robust selection;
- BTC 4h: no robust selection;
- ETH 1h: no robust selection;
- ETH 4h: no robust selection;
- SOL 1h: no robust selection;
- SOL 4h: one robust selection.

The SOL 4h robust candidate was `meanrev-bf148de539597391`, parameters:

- `lookback_bars=24`;
- `entry_z=2`;
- `exit_z=0.5`;
- `order_quantity=50`;
- `position_mode=long_flat`.

Its DEVELOPMENT metrics were modest: net return about `0.20%`, Sharpe about `0.256`, maximum drawdown about `2.09%`, profit factor about `1.098`, 36 fills. Walk-forward/stress evidence was stronger than the aggregate DEVELOPMENT Sharpe: median fold Sharpe about `1.323`, positive-fold ratio `0.60`, all three stress scenarios passed, worst fold return about `-1.51%`, worst stress return about `-2.94%`.

However, its Deflated Sharpe probability was `0.0` and PBO was unavailable because a frozen-family zero-variance segment made Sharpe undefined. Under the new default statistical gate this candidate is therefore **not promotion-ready**. This is precisely the distinction R7 is designed to preserve.

No result from matrix run #1 justifies opening HOLDOUT or claiming a production trading edge.

## Validation doctrine

A candidate is never promoted merely because it has the highest raw return.

1. Closed-bar information boundary and next-bar execution.
2. Explicit fees, spread, slippage, leverage and volume participation.
3. Complete frozen DEVELOPMENT trial universe.
4. Multiple-testing controls; failed/weak trials remain visible.
5. Chronological walk-forward robustness.
6. Adverse cost, latency and liquidity stress.
7. PBO/CSCV and Deflated Sharpe where statistically defined.
8. Explicit statistical promotion gate.
9. Protected HOLDOUT only after DEVELOPMENT evidence is promotion-ready.
10. Independent-engine parity in LEAN/Zipline where practical.
11. PAPER forward evidence under R6 reconciliation/shadow controls.
12. LIVE authorization semantics remain unchanged.

## Core evaluation metrics

The existing backtester records net return, annualized volatility, Sharpe, Sortino, maximum drawdown, turnover, hit rate, profit factor, gross exposure, volume participation, fees, fills and rejected signals. Existing R3 statistics include Holm correction, PBO/CSCV and Deflated Sharpe.

A fixed target such as **5% every day is not an acceptance criterion**. Optimizing toward a fixed extreme daily return would reward leverage, overfitting and hidden tail risk. The objective is robust risk-adjusted compounding under explicit drawdown and evidence constraints.

## Bootstrap external sources

```bash
python scripts/bootstrap_quant_sources.py --list
python scripts/bootstrap_quant_sources.py
python scripts/bootstrap_quant_sources.py --source lean --source qlib
```

The bootstrap clones exact commits into `.external/quant/`; it does not install those projects into AUTO-TRADE or grant them execution authority.

## Next R7 increments

1. Confirm public matrix run #2 with the statistical promotion gate explicitly encoded in every report.
2. Improve statistical diagnosability of zero-variance/no-trade candidates without weakening PBO fail-closed semantics.
3. Add independent LEAN parity fixtures for any future promotion-ready finalist.
4. Add regime-conditioned evaluation and cross-market stability checks.
5. Add portfolio/ensemble allocation only after individual strategy evidence exists.
6. Extend immutable data adapters to additional venues/assets.
7. Open protected HOLDOUT only for a candidate that passes the complete DEVELOPMENT promotion gate; never directly promote to PAPER or LIVE.
