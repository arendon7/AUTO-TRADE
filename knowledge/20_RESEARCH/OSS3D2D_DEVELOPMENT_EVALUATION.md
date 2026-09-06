# OSS-3D2D — DEVELOPMENT Prediction Evaluation

## Status

Implementation candidate. **RESEARCH ONLY.**

Base exacta: OSS-3D2C head certificado `0997860b986eebb006aa230b10ad17a67cc25bbe`.

## Purpose

OSS-3D2D is the first point in the OSS-3 chain where DEVELOPMENT labels may be consumed. They enter only after the prediction artifact has already been produced, cryptographically identified and bound through OSS-3D2A.

The separation is intentional:

```text
TRAIN features + TRAIN labels
        -> frozen training bundle
DEVELOPMENT features
        -> inference request
        -> isolated Qlib prediction
        -> D2A prediction receipt
DEVELOPMENT labels
        -> D2D evaluation only
```

Labels never flow backward into the inference request or Qlib runner.

## Inputs

D2D requires exactly:

1. one `DevelopmentPredictionReceipt` from OSS-3D2A;
2. the exact `QlibPredictionArtifact` referenced by that receipt;
3. one OSS-3C `SupervisedLabelArtifact` with partition `DEVELOPMENT`;
4. one opaque SHA-256 identifying the certified OSS-3D2C environment attestation.

The core does not import the D2C lab module. The environment identity crosses the boundary only as an opaque hash, preserving dependency direction.

## Required identity continuity

Before metrics are computed, D2D requires exact equality for:

- prediction artifact hash;
- prediction manifest hash;
- prediction count;
- campaign ID;
- frozen research split hash;
- source universe hash;
- label definition hash;
- model family;
- model config hash;
- Qlib version;
- producer/runner code hash;
- TRAIN window;
- DEVELOPMENT inference/evaluation window;
- every `(timestamp, symbol)` pair;
- exact D2A keyset hash.

There is no fuzzy join, nearest timestamp match, symbol aliasing or dropped-row tolerance.

## Metric policy

Policy ID:

```text
PREDICTIVE_QUALITY_NO_PNL_V1
```

V1 computes deterministic predictive metrics only:

- global Pearson information coefficient;
- global Spearman/rank information coefficient;
- MAE;
- RMSE;
- sign accuracy;
- per-timestamp cross-sectional Pearson IC;
- per-timestamp cross-sectional rank IC;
- mean and median cross-sectional IC;
- positive cross-sectional IC ratio;
- mean and median cross-sectional rank IC;
- positive cross-sectional rank IC ratio.

A cross-section needs at least three observations and non-zero variance in both scores and targets. Degenerate cross-sections are excluded. At least one valid cross-section is mandatory.

Global Pearson/Spearman require at least three observations and non-zero variance. Undefined correlations fail closed rather than becoming zero or NaN.

## Deliberate exclusions

D2D does **not** compute:

- PnL;
- Sharpe;
- Sortino;
- CAGR;
- executable portfolio returns;
- transaction-cost-adjusted profitability;
- capital allocation;
- model promotion.

Prediction quality and trading profitability are distinct scientific questions. Trading economics remain governed by the existing backtest/cost/promotion pipeline.

## Environment binding

`environment_attestation_hash` is an opaque 64-hex SHA-256 field. D2D binds it into the evaluation artifact but does not inspect the Qlib environment, import lab code or install external dependencies.

This means the same predictions/labels evaluated under a different certified runtime identity yield a different evaluation artifact identity even when metrics happen to be equal.

## FINAL_HOLDOUT boundary

OSS-3C exposes only TRAIN and DEVELOPMENT. D2D explicitly requires DEVELOPMENT. FINAL_HOLDOUT therefore remains outside this evaluator.

Any eventual model comparison, selection or tuning over DEVELOPMENT metrics must happen in a later preregistered campaign/tournament layer with multiple-testing controls. D2D itself evaluates one frozen prediction artifact; it does not search models or thresholds.

## Authority

The D2D manifest permanently asserts:

```text
research_only = true
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

No Qlib runtime, network, subprocess, broker, OMS, Safety or OrderIntent import is allowed.

## Interpretation

A high IC or rank IC is evidence of predictive association on the DEVELOPMENT partition. It is **not** proof of future alpha, executable profitability or promotion readiness.

The next scientific layer after D2D should preregister how multiple candidate evaluations may be compared before any adaptive model expansion.