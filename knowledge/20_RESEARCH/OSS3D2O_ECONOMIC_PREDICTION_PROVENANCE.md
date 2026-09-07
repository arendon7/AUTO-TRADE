# OSS-3D2O — Economic Prediction Provenance

Status: **RESEARCH-ONLY / STACKED DRAFT FRONTIER**

Base: certified OSS-3D2N head `6f56b6636af39c3a2f3d2a1ccf1729c67f602087`.

## Why D2O exists

OSS-3D2N freezes the exact economic prediction artifact before D2K and prevents post-hoc score substitution. That closes an important governance gap, but a hash commitment alone does not prove that the frozen scores were materially produced by the frozen Qlib model.

D2O adds that missing software provenance layer.

It proves, for one economic prediction artifact, the following chain:

```text
D2I winner
  -> D2J predictive protocol
    -> D2L frozen predictor/portfolio semantics
      -> D2M economic protocol
        -> exact point-in-time economic feature artifact
          -> exact original TRAIN bundle replay
            -> exact D2G winner runtime attestation
              -> deny_network()
                -> pyqlib==0.9.7 frozen linear model
                  -> exact QlibPredictionArtifact
                    -> D2O provenance receipt
```

D2O does **not** consume D2K or D2N outcomes and does not grant execution authority.

## Explicit visibility model

D2O is not described as "market-value free".

The runner receives numerical, point-in-time feature vectors which may be derived from market observations. Therefore the feature artifact and provenance receipt explicitly assert:

```text
market_derived_features_observed = true
market_derived_features_loaded   = true
```

At the same time D2O structurally excludes:

```text
economic_labels_included = false
economic_outcomes_included = false
economic_labels_loaded = false
economic_outcomes_loaded = false
```

The distinction is deliberate: inference needs contemporaneously available features, but the prediction producer must not see the forward economic outcomes later used to judge the strategy.

## Economic feature artifact

`OSS3D2O_ECONOMIC_FEATURE_ARTIFACT_V1` binds:

- exact D2M protocol id and receipt hash;
- exact D2M economic-holdout commitment fingerprint;
- source universe hash and dataset-set hash;
- independent feature-source and feature-producer code hashes;
- exact D2L feature-schema hash;
- exact ordered feature names;
- exact symbol universe;
- timeframe, partition bounds and bar count;
- exact full inference support;
- every numerical feature row and its `available_at` timestamp;
- canonical row-payload and artifact hashes;
- no labels/outcomes/adaptive feature search;
- no execution/PAPER/capital/LIVE authority.

### Support contract

The canonical support is:

```text
EVERY_NONTERMINAL_ECONOMIC_BAR_CLOSE_FULL_UNIVERSE_V1
```

For a committed economic window with `bar_count = N`, predictions exist at closes 1 through N-1 for every committed symbol. D2N may then apply its already-frozen one-bar delay at the next bar open.

Every feature row must satisfy:

```text
available_at <= as_of
```

and rows must exactly match the committed clock × canonical symbol set.

## Frozen model replay

D2O does not accept estimator, alpha, model family, sign, search space or policy overrides.

It requires:

- exact D2L selected trial;
- exact D2A winner request hash;
- exact D2G `model_config_hash`;
- exact shared model-runner semantic hash;
- exact `pyqlib==0.9.7`;
- exact original TRAIN feature and label artifacts;
- exact rebuilt TRAIN bundle hash;
- exact D2L feature schema.

The model is created only from the frozen D2F candidate contract.

## Runtime provenance

Before model execution D2O collects a fresh D2G candidate environment attestation and requires exact equality with the frozen winner:

```text
observed_environment_attestation_hash == D2L.environment_attestation_hash
observed_runtime_environment_hash     == D2L.runtime_environment_hash
observed_runner_code_hash              == D2L.shared_runner_code_hash
```

Qlib import, fit and predict occur only inside `deny_network()`.

Broker/exchange credential environment variables are rejected before runner execution.

## Provenance receipt

`OSS3D2O_ECONOMIC_PREDICTION_PROVENANCE_V1` binds:

- D2M/D2L/D2J lineage;
- exact winner request;
- exact TRAIN artifacts and bundle;
- model family/config/Qlib version;
- shared D2G model-runner semantic hash;
- D2O provenance-runner semantic hash;
- source and observed runtime attestations;
- economic feature artifact, row-payload and support hashes;
- prediction artifact and prediction-payload hashes;
- explicit Qlib-generation and TRAIN-replay assertions;
- explicit no-economic-label/no-economic-outcome state;
- explicit no-network/no-credential state;
- no adaptive search/HPO;
- no execution authority.

The receipt is deterministic for a fixed code/runtime/input/prediction state.

## What D2O proves

D2O materially strengthens D2N by proving the software-level path:

```text
committed feature bytes
  -> exact frozen TRAIN/model/runtime
    -> real Qlib fit/predict
      -> exact prediction artifact bytes
```

A changed prediction score produces a different prediction artifact/payload hash and is not covered by the original provenance receipt.

A changed feature row produces a different feature artifact and provenance identity.

A non-winner request is rejected before model execution.

A changed runtime is rejected before model execution.

## What D2O does NOT prove

D2O does not yet prove that the upstream feature producer transformed raw market bars into the feature artifact correctly. It hash-binds:

```text
feature_source_hash
feature_producer_code_hash
```

but the correctness of that upstream transformation is a separate lineage frontier.

D2O also does not yet make D2N admission require the provenance receipt. That should be the next stacked admission-hardening step after D2O itself is certified. Keeping production and admission changes separate makes the scientific boundary easier to audit.

## Authority boundary

D2O always keeps:

```text
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

It imports no broker, OMS, Safety or OrderIntent layer and uses no SQLite/holdout permit.

A D2O provenance receipt is research evidence only. It is not profitability evidence and is not a promotion or execution credential.

## Certification gates

The dedicated workflow must prove, on one identical head:

1. source/test compile before Qlib installation;
2. D2O static authority/outcome boundary before Qlib;
3. D2N/D2M/D2L/D2J upstream boundaries remain intact;
4. Research Authority remains intact;
5. exact `pyqlib==0.9.7`;
6. real D2O Qlib provenance test;
7. adversarial tamper/request/support/credential tests;
8. D2G real-runtime regression;
9. D2K/D2N regression;
10. repository Knowledge Contract and Core Safety workflows green on the same head.

Only after those gates pass should D2O be treated as a certified primitive for the next admission-binding frontier.
