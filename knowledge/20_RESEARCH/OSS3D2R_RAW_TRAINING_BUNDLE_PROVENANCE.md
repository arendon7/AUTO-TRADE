# OSS-3D2R · Raw Training Bundle Provenance

Status: RESEARCH ONLY · STACKED DRAFT

## 1. Purpose

D2Q proved the inference-side relationship:

```text
raw economic OHLCV -> canonical causal features -> D2O feature artifact
```

D2R closes the corresponding fit-side gap. It reconstructs the complete
supervised TRAIN material from raw market bars:

```text
raw TRAIN OHLCV + exact 20-bar warmup
  -> canonical TRAIN feature rows
  + fixed one-bar forward TRAIN labels
    -> exact OSS-3B FactorMatrixArtifact
    -> exact OSS-3C SupervisedLabelArtifact
      -> exact OSS-3D1 TrainingBundleArtifact
```

The output bundle is the same contract consumed by D2F/D2G.

## 2. Three identities must not be conflated

D2R explicitly separates three kinds of identity that older synthetic fixtures
could safely blur but a material provenance chain cannot.

### 2.1 Schema semantics

Feature `FactorDefinition` values carry stable semantics:

```text
source_id = AUTO-TRADE/OSS3D2Q_CANONICAL_CLOSE_FACTORS
source_hash = canonical D2Q formula registry hash
```

The exact raw TRAIN dataset hash is **not** embedded in `feature_schema_hash`.
That is required so a later DEVELOPMENT partition using the same formulas can
have the same schema while containing different bytes.

The label definition follows the same principle:

```text
source_id = AUTO-TRADE/OSS3D2R_CANONICAL_FORWARD_LABEL
source_hash = canonical label formula hash
```

### 2.2 Research-universe identity

D2A requires one common `source_universe_hash` across TRAIN and DEVELOPMENT.
`AlignedMarketUniverse.universe_hash` cannot serve that purpose because it
contains dataset hashes and therefore changes by partition.

D2R defines:

```text
OSS3D2R_RESEARCH_UNIVERSE_IDENTITY_V1
```

from stable instrument semantics only:

- symbol;
- venue;
- quote currency;
- price tick;
- quantity step;
- timeframe.

It deliberately excludes:

- bar values;
- dataset hashes;
- partition-specific universe name.

### 2.3 Material lineage

Concrete bytes remain fully bound through:

- warmup `AlignedMarketUniverse.universe_hash`;
- warmup dataset-set hash;
- TRAIN material `AlignedMarketUniverse.universe_hash`;
- TRAIN dataset-set hash;
- combined D2R `raw_training_source_hash`;
- feature/label manifest `source_dataset_hash`.

This separation prevents both false equivalence and false incompatibility.

## 3. Canonical TRAIN features

D2R reuses the exact D2Q formula registry:

```text
momentum_20 = close_t / close_(t-20) - 1
volatility_20 = population stddev of 20 one-bar simple close returns ending at t
```

Properties:

- close-only;
- exact 20-bar lookback;
- Decimal precision 50;
- final float64-compatible values;
- fixed formula hashes;
- no adaptive feature search.

For each feature origin `t`, the calculator receives only:

```text
20 warmup/prior bars + current closed bar
```

and enforces:

```text
bar.ended_at <= t
```

Thus:

```text
feature_causal_prefix_enforced = true
feature_future_values_used = false
```

## 4. Canonical supervised label

D2R v1 defines exactly one label:

```text
forward_return_1 = close_(t+1) / close_t - 1
```

with:

```text
horizon_bars = 1
availability = next bar close
return convention = SIMPLE_CLOSE_RETURN
```

No alternative horizon, threshold or transformation is available at runtime.

Supervised labels necessarily use future TRAIN data. D2R records this rather
than hiding it:

```text
label_future_values_used = true
label_future_values_confined_to_explicit_horizon = true
```

The target horizon must remain entirely inside TRAIN.

## 5. Terminal-bar rule

If raw TRAIN contains N bars, D2R creates sample origins for bars:

```text
0 .. N-2
```

The final raw bar N-1 is used only as the next-bar target endpoint for the last
label.

It is never emitted as a new feature/label origin.

This guarantees the exact same `(timestamp, symbol)` keyset for features and
labels while preserving a valid future target.

## 6. Half-open TRAIN partition

OSS-3C requires:

```text
available_at < partition_end
```

The final label is available at the close of the final raw TRAIN bar. D2R sets
the artifact partition's exclusive end to:

```text
final_label_available_at + 1 microsecond
```

This microsecond is a half-open interval sentinel only. It does not represent a
market observation or extra bar.

## 7. Raw source

`OSS3D2R_RAW_TRAINING_MARKET_SOURCE_V1` contains:

1. exactly 20 aligned warmup bars;
2. an aligned TRAIN raw universe with at least three bars.

Warmup and TRAIN must share the exact stable research-universe identity, and the
warmup must end exactly when raw TRAIN starts.

The source structurally denies:

```text
DEVELOPMENT values
FINAL_HOLDOUT values
economic-holdout values
prediction values
```

## 8. Derived artifacts

D2R builds:

### OSS-3B TRAIN features

```text
partition = TRAIN
source_dataset_hash = raw_training_source_hash
source_universe_hash = stable research_universe_identity_hash
feature_schema_hash = stable canonical formula semantics
```

### OSS-3C TRAIN labels

```text
partition = TRAIN
source_dataset_hash = same raw_training_source_hash
source_universe_hash = same stable research_universe_identity_hash
label_definition_hash = fixed one-bar forward formula semantics
```

### OSS-3D1 TrainingBundle

The existing bundle constructor then proves:

- same campaign;
- same frozen split;
- same TRAIN window;
- same stable universe identity;
- exact feature/label keyset;
- exact feature and label artifact hashes.

No new parallel training-bundle contract is invented.

## 9. Receipt

`OSS3D2R_RAW_TRAINING_BUNDLE_PROVENANCE_V1` binds:

- campaign and frozen split;
- stable research-universe identity;
- exact warmup material identity;
- exact TRAIN material identity;
- combined raw source hash;
- D2Q feature formula registry and hashes;
- fixed label formula/definition;
- D2R semantic producer hash;
- exact feature artifact and row-payload hashes;
- exact label artifact and row-payload hashes;
- exact TrainingBundle and manifest hashes;
- sample count;
- causal feature proof;
- bounded label-future proof;
- exact feature/label keyset;
- schema/material and universe/material separation proofs;
- zero execution/promotion authority.

Verification rederives all three artifacts from raw bars and exact-compares them.

## 10. Adversarial requirements

The D2R dedicated suite proves at minimum:

1. raw bars derive an exact complete TrainingBundle;
2. feature schema is identical across TRAIN/DEVELOPMENT despite different material hashes;
3. research-universe identity is stable across partitions but distinct from material universe hash;
4. one-bar forward label value/horizon/availability are exact;
5. terminal raw bar is target endpoint only;
6. changing terminal bar changes labels but not feature row payload;
7. changing a future TRAIN bar cannot change earlier feature rows;
8. the label immediately preceding that bar may legitimately change;
9. warmup length other than 20 fails closed;
10. instrument semantic drift changes universe identity and fails warmup/TRAIN compatibility;
11. feature tampering fails raw rederivation;
12. label tampering fails raw rederivation;
13. receipt cannot be promoted into execution authority;
14. exact six-candidate D2F request family accepts the raw-derived bundle;
15. all six frozen D2G Qlib candidates execute successfully on that bundle.

## 11. Authority boundary

D2R itself imports no Qlib runtime and performs no model inference.

It contains no:

- broker/OMS/Safety/OrderIntent;
- socket/HTTP/subprocess;
- SQLite execution state;
- DEVELOPMENT evaluation;
- FINAL_HOLDOUT evaluator;
- economic evaluator;
- prediction scores;
- PnL or performance gates.

Always:

```text
profitability_claim_authorized = false
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

## 12. Scientific chain after D2R

With D2Q + D2R, the OSS-3 stack can now establish both sides of model material
provenance:

```text
TRAIN raw bars
  -> D2R exact features + labels + TrainingBundle
    -> D2F/D2G frozen Qlib model family

Economic raw bars
  -> D2Q exact causal inference features
    -> D2O exact Qlib prediction
      -> D2P exact admission
        -> D2K predictive one-shot
          -> D2N economic one-shot
```

This still does not authorize PAPER or LIVE.

## 13. Next frontier

After D2R certification, the next useful boundary is not another raw-data
wrapper. It should bind the **selected D2I winner** to a D2R-proven training
bundle and D2Q-proven inference producer as a single end-to-end model lineage
seal, so later D2J/D2L/D2M protocols can require that complete provenance before
any FINAL_HOLDOUT material is opened.
