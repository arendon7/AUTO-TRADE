# OSS-3D2Q · Raw-Market Feature Provenance

Status: RESEARCH ONLY · STACKED DRAFT

## 1. Purpose

OSS-3D2Q closes the raw-input provenance gap that remains after OSS-3D2O and
OSS-3D2P.

D2O proves:

```text
committed feature bytes
  -> frozen winner/TRAIN/model/runtime
    -> real Qlib
      -> exact economic prediction bytes
```

D2P proves that the prediction admitted to the economic one-shot is exactly a
replay of those committed feature bytes.

D2Q now proves the upstream relationship:

```text
committed raw OHLCV bars
  + exact pre-partition warmup
  + canonical TRAIN formula declarations
    -> causal deterministic feature calculation
      -> exact D2O EconomicPredictionFeatureArtifact
```

D2Q is not an execution module and cannot promote a strategy.

## 2. Scientific problem

A feature artifact can be perfectly hash-bound and still be scientifically
wrong if its values were not actually calculated from the raw market material
claimed by the research protocol.

A second risk is semantic drift: an inference feature named `momentum_20` is not
enough. The formula used at inference must be the same formula identity declared
by the TRAIN factor schema.

A third risk is look-ahead: an offline evaluator has the full holdout path in
memory. A row at time `t` must nonetheless depend only on bars that closed no
later than `t`.

D2Q treats these as separate invariants.

## 3. Canonical v1 formula family

D2Q v1 deliberately supports exactly two features and no adaptive feature
selection:

### momentum_20

```text
close_t / close_(t-20) - 1
```

Properties:

- input: close only;
- lookback: 20 bar intervals;
- arithmetic: Decimal precision 50;
- final storage: float64-compatible Python float;
- no normalization fit on holdout;
- no cross-sectional leakage;
- no future bar access.

### volatility_20

Twenty one-bar simple close returns ending at `t` are computed:

```text
r_i = close_i / close_(i-1) - 1
```

Then:

```text
sqrt(mean((r_i - mean(r))^2))
```

This is population standard deviation, not sample standard deviation.

Properties are otherwise identical to `momentum_20`.

The complete formula specification is canonical-JSON hashed. The resulting
formula hashes, rather than names alone, must match the TRAIN `FactorDefinition`
objects.

## 4. Exact warmup

The first economic signal occurs at the close of the first economic bar. A
20-bar feature cannot be computed causally from a partition that begins at that
bar without prior history.

D2Q therefore requires exactly:

```text
20 aligned warmup bars per symbol
```

The warmup:

- has the same symbols;
- same quote currency;
- same timeframe;
- exact same instrument metadata;
- no gaps;
- ends exactly when the economic partition starts.

This avoids silently using a shorter expanding window or a future-filled
lookback.

## 5. Raw-market source contract

`OSS3D2Q_RAW_MARKET_FEATURE_SOURCE_V1` contains two immutable aligned universes:

1. `warmup_universe` — exactly 20 bars before the economic partition;
2. `economic_universe` — the exact universe already committed by D2M.

The economic universe must reproduce:

```text
D2M universe_hash
D2M source_dataset_set_hash
D2M symbols
D2M quote_currency
D2M timeframe_seconds
D2M bar_count
D2M partition_start
D2M partition_end
```

The combined D2Q `source_hash` binds both warmup and economic identities.

## 6. Causality rule

D2Q is honest about offline material:

```text
full_economic_path_loaded = true
raw_market_values_loaded = true
```

But each individual formula evaluation receives only:

```text
warmup bars + economic bars through current signal index
```

and then only the latest 21 closed bars are passed to the formula.

For every bar used:

```text
bar.ended_at <= feature_row.as_of
```

The last selected bar must end exactly at `as_of`.

The receipt therefore records:

```text
causal_prefix_enforced = true
future_market_values_used_per_row = false
```

This is stronger and more precise than pretending the offline runner never had
the future path available in memory.

## 7. TRAIN semantic identity

Before deriving economic features, D2Q requires the frozen TRAIN artifact to
contain exactly:

```text
(momentum_20, volatility_20)
```

in canonical order, with:

- exact formula hash;
- exact lookback 20;
- dtype `float64`;
- role `FEATURE`;
- exact feature-schema hash already bound by D2L.

D2Q therefore proves:

```text
training_formula_identity_verified = true
```

It intentionally records:

```text
training_feature_values_rederived = false
```

D2Q v1 does **not** claim that historical TRAIN values were themselves rebuilt
from their original raw bars. That deeper TRAIN-value provenance must be proved
separately before making a complete raw-to-model historical provenance claim.

## 8. D2O artifact production

D2Q builds the existing D2O `EconomicPredictionFeatureArtifact` directly.

Its provenance fields are:

```text
feature_source_hash = D2Q raw_market_source_hash
feature_producer_code_hash = D2Q producer semantic hash
feature_schema_hash = frozen D2L/TRAIN schema hash
```

Rows cover every nonterminal economic bar close and every symbol exactly, as
required by D2O/D2N.

## 9. D2Q receipt

`OSS3D2Q_RAW_MARKET_FEATURE_PROVENANCE_V1` binds:

- D2M protocol and receipt;
- D2L receipt and binding;
- exact TRAIN feature artifact and schema;
- complete canonical formula registry and hashes;
- warmup universe and dataset-set hashes;
- economic universe and dataset-set hashes;
- combined raw-market source hash;
- D2Q producer semantic hash;
- resulting D2O feature artifact hash;
- resulting feature row-payload hash;
- resulting support hash;
- causal/warmup assertions;
- explicit visibility assertions;
- zero execution/promotion authority.

Verification rederives the entire feature artifact from raw bars and compares the
immutable objects exactly.

## 10. Adversarial requirements

The dedicated suite must prove at minimum:

1. canonical formula family is finite and deterministic;
2. exact D2Q feature artifact is produced from committed raw bars;
3. first-row warmup semantics are correct;
4. perturbing a future bar cannot change any earlier feature row;
5. perturbing a bar when it becomes available changes the relevant row;
6. formula-hash drift fails closed;
7. lookback drift fails closed;
8. warmup length other than 20 fails closed;
9. committed economic-universe drift fails closed;
10. tampered feature values fail raw-market rederivation;
11. receipt cannot be mutated into promotion/execution authority;
12. D2Q output can flow through D2O -> D2P -> D2K -> D2N.

## 11. Authority boundary

D2Q imports no Qlib runtime and performs no model inference.

It consumes no:

```text
supervised labels
economic prediction scores
portfolio allocations
fills
PnL
Sharpe
profit factor
max drawdown
economic PASS/FAIL gates
```

It has no network/process authority and no broker/OMS/Safety/OrderIntent surface.

Always:

```text
profitability_claim_authorized = false
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

## 12. Intended campaign order

For a future fully certified OSS-3 economic campaign:

```text
D2I DEVELOPMENT winner
  -> D2J predictive FINAL_HOLDOUT protocol
    -> D2L predictor/strategy preregistration
      -> D2M economic protocol preregistration
        -> D2Q raw-market feature derivation
          -> D2O real-Qlib economic prediction
            -> D2N exact prediction precommit
              -> D2P independent prediction provenance admission
                -> D2K predictive FINAL_HOLDOUT one-shot
                  -> D2N economic one-shot
```

D2Q does not loosen any prior one-shot ordering rule.

## 13. Remaining frontier

After D2Q, the remaining provenance gap is historical TRAIN-value derivation.
The TRAIN artifact can now be required to declare exact D2Q formula hashes, but
D2Q does not yet reconstruct its historical row values from original raw market
material.

A later frontier should prove:

```text
original TRAIN raw bars
  -> exact canonical formula registry
    -> exact frozen TRAIN FactorMatrixArtifact bytes
```

Only then can AUTO-TRADE claim complete raw-market provenance on both the fit and
inference sides of the OSS-3 model.
