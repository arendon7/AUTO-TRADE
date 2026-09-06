# OSS-3D2F — Concrete finite Qlib model family

## Status

Research-only preregistration stage layered directly on OSS-3D2E.

D2F converts the abstract D2E finite-family protocol into one concrete, source-frozen set of Qlib 0.9.7 `LinearModel` candidates. It does **not** execute Qlib, inspect DEVELOPMENT labels, observe FINAL_HOLDOUT, calculate PnL or grant PAPER/capital/LIVE authority.

## Why this stage exists

D2E already freezes how a finite family will be compared: one DEVELOPMENT primary metric, common support, exact sign-test p-values and Holm correction. Before producing multi-model results, AUTO-TRADE also needs to freeze **which exact model configurations exist** and the OSS-3D2A request identity for each candidate.

D2F does that before any candidate result is produced.

```text
OSS-3D2E comparison protocol
        |
        v
OSS-3D2F concrete finite model family
        |
        +--> six immutable model configs
        +--> six deterministic config hashes
        +--> six OSS-3D2A DEVELOPMENT requests
        |
        v
future isolated family runner + D2C-compatible candidate attestations
        |
        v
OSS-3D2D evaluations
        |
        v
OSS-3D2E tournament evidence
```

## Exact candidate family

The family is deliberately small and interpretable. Every candidate uses Microsoft Qlib 0.9.7 `qlib.contrib.model.linear.LinearModel`, `fit_intercept=true`, `include_valid=false`, and `prediction_segment=test`.

| candidate_id | estimator | alpha |
|---|---:|---:|
| `linear-lasso-a0p001` | lasso | 0.001 |
| `linear-lasso-a0p01` | lasso | 0.01 |
| `linear-ols` | ols | 0.0 |
| `linear-ridge-a0p1` | ridge | 0.1 |
| `linear-ridge-a1` | ridge | 1.0 |
| `linear-ridge-a10` | ridge | 10.0 |

The set is stored as the source-level `CANONICAL_CANDIDATES` tuple. Runtime callers cannot append, remove, reorder or alter a candidate without creating a new protocol version.

## Why these candidates

Qlib 0.9.7 `LinearModel` natively supports OLS, Ridge, Lasso and NNLS. D2F V1 chooses OLS plus a small logarithmic regularization ladder for Ridge and Lasso. NNLS is intentionally excluded from V1 so the first family stays focused on comparable unconstrained/regularized linear estimators and avoids introducing a separate coefficient-sign constraint as an additional research degree of freedom.

This is **not** an optimization grid. The six configurations are the complete preregistered family; no result-dependent expansion is allowed.

## Canonical model identity

All six candidates share:

```text
model_family = qlib_linear_finite_v1
required_qlib_version = 0.9.7
implementation = qlib.contrib.model.linear.LinearModel
fit_intercept = true
include_valid = false
prediction_segment = test
```

Each candidate has a unique canonical `model_config_hash` over its complete config dictionary.

A later isolated family runner will have one common semantic `runner_code_hash`. D2F receives that hash explicitly and writes the same value into all six OSS-3D2A requests. Therefore candidate differences are configuration differences, not hidden runner-code differences.

## Request construction

`build_concrete_model_request_set()` accepts only:

- one certified OSS-3D1 TRAIN `TrainingBundleArtifact`;
- one OSS-3B DEVELOPMENT `FactorMatrixArtifact`;
- one shared SHA-256 runner-code identity.

For every frozen candidate, it calls the already-certified `DevelopmentInferenceRequest.build()` path from OSS-3D2A.

The result is a six-element request set where every request shares exactly:

- campaign id;
- frozen research split;
- training bundle;
- DEVELOPMENT feature artifact;
- universe;
- feature schema;
- label definition lineage inherited from TRAIN;
- TRAIN window;
- DEVELOPMENT inference window;
- exact inference keyset and row count;
- Qlib version;
- runner-code hash.

Only the canonical model configuration hash differs by candidate.

## Fail-closed verification

`verify_concrete_model_request_set()` rebuilds the complete family from the concrete TRAIN and DEVELOPMENT artifacts and requires identical plan/evidence fingerprints.

It also calls `verify_inputs()` on every contained OSS-3D2A request. This prevents a serialized request set from being trusted solely because its top-level hashes look plausible.

The request-set evidence rejects:

- missing or extra candidates;
- duplicate request hashes;
- runner-code drift;
- common-support drift between candidates;
- TRAIN or DEVELOPMENT artifact substitution;
- claims that DEVELOPMENT labels were loaded;
- claims that FINAL_HOLDOUT was loaded;
- claims that an external Qlib runtime was invoked by D2F;
- any PAPER, capital or LIVE authority.

## Adaptive-search prohibition

D2F has explicit immutable fields:

```text
adaptive_search = false
hyperparameter_optimization = false
development_labels_observable = false
final_holdout_observable = false
```

Changing any candidate after results requires a new protocol/family version and therefore a new scientific campaign. No random search, grid search, Bayesian search or result-conditioned candidate generation exists in this stage.

## Authority boundary

```text
research_only = true
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

D2F contains no Qlib runtime import, no broker/OMS/Safety integration and no order-generation surface.

## Next stage

The next child stage should implement the **isolated finite-family runner** that can consume one of these exact OSS-3D2A requests, map its `model_config_hash` back to the source-frozen D2F configuration, run Qlib 0.9.7 under the existing no-network/no-broker laboratory boundary, and emit:

1. one OSS-3A prediction artifact;
2. one OSS-3D2A prediction receipt;
3. one candidate-specific D2C-compatible environment attestation;
4. the shared model-neutral runtime identity required by D2E.

Only after those artifacts exist should DEVELOPMENT labels be introduced again through OSS-3D2D and the preregistered D2E tournament be evaluated.
