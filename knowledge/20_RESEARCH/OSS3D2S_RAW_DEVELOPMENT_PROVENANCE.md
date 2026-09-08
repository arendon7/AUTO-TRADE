# OSS-3D2S — Raw DEVELOPMENT provenance before label materialization

Status: **research-only / stacked DRAFT boundary**.

D2S closes the remaining raw-data provenance gap in the DEVELOPMENT model-selection path.  Its purpose is not to add a new model, metric or execution path.  Its purpose is to prove, in an auditable sequence, that the DEVELOPMENT features and labels used by D2G/D2D/D2E come from one immutable raw OHLCV path, while preventing the supervised label artifact from existing before the six predictions and statistical policy have been frozen durably.

## Why D2S exists

The earlier D2H boundary correctly guarantees that label **values are not semantically used** before D2E preregistration.  However, D2H historically receives a complete `SupervisedLabelArtifact` during plan construction and stores its artifact hash.  That means the label artifact must already have been materialized.

For synthetic fixtures that is acceptable.  For a raw-market scientific chain we want a stronger statement:

```text
before the candidate family is frozen:
    raw market is committed
    causal DEVELOPMENT features may exist
    predictions may exist
    label formula/support metadata may exist
    a DEVELOPMENT label artifact must NOT exist
```

D2S therefore adds an earlier, append-only preregistration in front of D2H.  It does not replace D2H or D2E.

## Canonical sequence

```text
raw TRAIN OHLCV
  -> D2R TRAIN features + TRAIN labels + TrainingBundle
    -> D2F exact six-model requests

raw DEVELOPMENT warmup + raw DEVELOPMENT OHLCV
  -> D2S causal DEVELOPMENT features only
    -> six D2G Qlib predictions
      -> D2S durable raw/statistical preregistration
        -> D2S DEVELOPMENT label materialization
          -> ordinary D2H/D2E preregistration
            -> D2D metrics
              -> D2E tournament
```

The crucial ordering is:

```text
D2S durable preregistration
    BEFORE
SupervisedLabelArtifact.build(... DEVELOPMENT ...)
```

and then:

```text
D2H/D2E durable preregistration
    BEFORE
D2D metric computation
```

These are two distinct gates.

## Raw DEVELOPMENT source

`RawDevelopmentMarketSource` contains:

- exactly 20 pre-partition warmup bars per symbol;
- the complete raw DEVELOPMENT path;
- one stable research-universe identity;
- no label artifact;
- no prediction values;
- no DEVELOPMENT metrics;
- no FINAL_HOLDOUT or economic-holdout material;
- no execution authority.

D2S explicitly records:

```text
full_development_path_loaded = true
supervised_label_artifact_materialized = false
```

The full path exists because D2S must commit the exact raw data that will later generate the labels.  This does **not** mean D2G receives that path.  D2G receives only the resulting OSS-3B DEVELOPMENT feature artifact.

## Stable universe identity vs material identity

As established by D2R, `source_universe_hash` used across TRAIN and DEVELOPMENT represents stable instrument semantics:

- symbol;
- venue;
- quote currency;
- price tick;
- quantity step;
- timeframe.

It deliberately excludes period-specific bars and dataset hashes.

D2S separately commits material identities:

- warmup material universe hash;
- warmup dataset-set hash;
- DEVELOPMENT material universe hash;
- DEVELOPMENT dataset-set hash;
- combined raw DEVELOPMENT source hash.

This preserves exact lineage without making TRAIN/DEVELOPMENT schema compatibility impossible.

## DEVELOPMENT feature formula family

D2S reuses the exact canonical D2Q/D2R feature semantics:

```text
momentum_20 = close_t / close_(t-20) - 1

volatility_20 = population stddev of the twenty one-bar simple
                close returns ending at t
```

Properties:

- input field: CLOSE only;
- lookback: 20 bars;
- Decimal precision 50;
- exact formula hashes;
- exact formula registry hash;
- no parameter fitting;
- no adaptive feature search.

For each feature row at `as_of=t`, the computation receives only:

```text
20-bar warmup + DEVELOPMENT bars through t
```

and the selected 21-bar history must satisfy:

```text
bar.ended_at <= t
```

A future DEVELOPMENT bar can never change an earlier feature row.

## DEVELOPMENT support

With `N` raw DEVELOPMENT bars and `S` symbols, D2S defines:

```text
sample_count = (N - 1) * S
```

The terminal raw DEVELOPMENT bar is not a prediction origin.  It exists only as the one-bar target endpoint for the final supervised label.

The support is precomputed without label values as ordered `(timestamp, symbol)` keys and frozen in:

```text
evaluation_keyset_hash
```

That hash must equal every D2A/D2G prediction receipt keyset before D2S preregistration can be created.

## D2S preregistration

`OSS3RawDevelopmentPreregistration` freezes, before any label artifact exists:

### Raw material

- raw DEVELOPMENT source hash;
- DEVELOPMENT material universe hash;
- DEVELOPMENT dataset-set hash.

### Feature evidence

- DEVELOPMENT feature artifact hash;
- feature schema hash;
- feature row payload hash;
- canonical feature formula registry hash.

### Future label semantics, but not values

- one-bar label formula hash;
- label definition hash;
- evaluation keyset hash;
- evaluation start/end;
- observation count.

It does **not** contain:

```text
label_artifact_hash
development_label_artifact_hash
label values
D2D metrics
```

### Candidate family

The exact six D2G outputs are frozen by:

- candidate id;
- request hash;
- prediction artifact hash;
- prediction receipt hash;
- environment attestation hash;
- model config hash;
- shared runner hash;
- model-neutral runtime environment hash.

All six must match the D2F canonical family and D2F request set.

### Statistical policy

D2S freezes the existing D2E policy before labels are materialized:

```text
primary_metric = mean_cross_sectional_rank_ic
multiple_testing_policy = EXACT_SIGN_TEST_PLUS_HOLM_V1
common_support_policy = EXACT_CROSS_SECTION_TIMESTAMP_SUPPORT_V1
```

It also freezes:

- tournament campaign id;
- tournament id;
- current D2H/D2E semantic code hash.

Therefore neither the candidate family nor the statistical policy may be changed after the label artifact appears.

## Durable registry

D2S uses its own SQLite table:

```text
oss3d2s_raw_development_preregistrations
```

The table is append-only.  SQLite triggers reject UPDATE and DELETE.

A repeated write is idempotent only if the same preregistration id maps to the exact same fingerprint and canonical JSON.

A conflicting reuse of an id fails closed.

## Label reveal

`materialize_development_labels_after_preregistration(...)` begins by requiring the exact durable D2S preregistration.

Before any label calculation it rechecks:

- D2S semantic hash;
- D2H/D2E semantic hash;
- raw DEVELOPMENT source hash;
- causal DEVELOPMENT feature reproduction;
- DEVELOPMENT feature artifact hash.

Only then is the DEVELOPMENT label artifact constructed.

The v1 label formula is the same one-bar supervised target used by D2R:

```text
forward_return_1 = close_(t+1) / close_t - 1
```

with:

```text
horizon_bars = 1
available_at = horizon_bar_close
```

The resulting label artifact must reproduce the preregistered:

- label definition;
- support/keyset;
- evaluation window;
- observation count.

The reveal receipt explicitly states:

```text
durable_d2s_preregistration_verified = true
predictions_frozen_before_label_materialization = true
label_values_materialized_after_durable_preregistration = true
label_future_values_confined_to_explicit_one_bar_horizon = true
development_metrics_computed = false
```

## D2H/D2E extension

D2S does not invent a second tournament engine.

After reveal, `prepare_d2h_from_d2s_reveal(...)` invokes the existing certified D2H builder and then verifies that the resulting D2E plan is an exact extension of D2S.

It must match:

- D2F plan fingerprint;
- D2F request-set fingerprint;
- D2H semantic code hash;
- six frozen outputs;
- runtime identity;
- tournament campaign/id;
- source campaign/split/universe;
- label definition;
- revealed label artifact;
- evaluation support/window;
- primary metric;
- multiple-testing policy;
- common-support policy.

D2H then performs its ordinary durable D2E preregistration.  Only after that second durable gate does D2D access label values for metrics.

## Completed evidence

`OSS3RawDevelopmentCompletedEvidence` binds:

```text
D2S preregistration
  -> D2S label reveal
    -> D2H preregistration
      -> D2E plan
        -> D2H batch evidence
          -> D2E tournament evidence / winner
```

It remains research-only and cannot promote or authorize execution.

## Adversarial requirements

The D2S test suite must prove at least:

1. causal raw DEVELOPMENT feature derivation;
2. exact TRAIN/DEVELOPMENT schema compatibility;
3. no label artifact hash/value in D2S preregistration;
4. label materialization fails before durable D2S preregistration;
5. append-only registry rejects UPDATE/DELETE;
6. exact one-bar raw-derived DEVELOPMENT labels after preregistration;
7. six real D2G Qlib outputs are frozen before reveal;
8. D2H/D2E plan is an exact extension of D2S;
9. full D2S -> D2H -> D2D -> D2E tournament succeeds;
10. future-bar perturbations cannot change earlier feature rows;
11. raw material drift after preregistration blocks reveal;
12. statistical policy mutation fails closed;
13. execution/promotion/capital mutations fail closed.

## Boundary distinction

D2S proves a stronger property than D2H alone:

```text
D2H: label values become active only after D2E preregistration.
D2S: the label artifact itself does not exist until after an earlier raw/candidate/statistical preregistration.
```

Both are retained.

## Authority

D2S is explicitly denied:

```text
FINAL_HOLDOUT observation
profitability claims
promotion
broker access
OMS writes
Safety writes
OrderIntent
PAPER execution
capital authority
LIVE trading
```

Every evidence object remains:

```text
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

## Next scientific boundary

Once D2S is certified, the raw TRAIN and DEVELOPMENT paths will both be reproducible and selection-safe.  The next useful boundary should move from synthetic/raw fixture provenance toward a **real historical market-data acquisition and immutable dataset snapshot** with provider/source metadata, corporate-action/timezone rules where applicable, and no broker execution authority.  It should not open FINAL_HOLDOUT, PAPER or LIVE merely because the provenance chain is complete.
