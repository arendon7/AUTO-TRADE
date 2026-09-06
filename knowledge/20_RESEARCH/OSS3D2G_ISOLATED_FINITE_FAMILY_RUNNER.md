# OSS-3D2G — Isolated finite-family Qlib runner

## Status

Research-only execution stage layered on OSS-3D2F.

D2G executes exactly one of the six source-preregistered D2F candidates per invocation under the existing no-network/no-broker Qlib laboratory boundary. It does not select candidates, tune hyperparameters, inspect DEVELOPMENT labels, observe FINAL_HOLDOUT, calculate trading PnL or authorize PAPER/capital/LIVE activity.

## Why D2G exists

OSS-3D2F freezes the exact model family and creates six OSS-3D2A inference requests. D2G makes those requests executable while preserving scientific comparability:

```text
D2F frozen family/request set
        |
        v
D2G exact config-hash resolver
        |
        v
isolated Qlib 0.9.7 LinearModel
        |
        +--> OSS-3A prediction artifact
        +--> OSS-3D2A prediction receipt
        +--> candidate environment attestation
        +--> model-neutral D2E runtime identity
        +--> D2G run evidence
```

## Backward compatibility

D2G does not modify the certified OSS-3D2B single-Ridge canary or OSS-3D2C environment attestation modules.

New modules are parallel:

- `family_model_contract.py`
- `family_runner.py`
- `family_environment_attestation.py`

The original `model_contract.py`, `runner.py` and `environment_attestation.py` remain intact and continue to represent the historical D2B/D2C canary.

## Exact candidate admission

D2G imports `CANONICAL_CANDIDATES` from OSS-3D2F. A request is executable only when all of these match:

1. `model_family == qlib_linear_finite_v1`;
2. `required_qlib_version == 0.9.7`;
3. `expected_runner_code_hash == family_runner_code_hash()`;
4. `model_config_hash` resolves to exactly one frozen D2F candidate.

No CLI or API parameter exists for estimator, alpha or arbitrary model kwargs.

Unknown hashes fail closed before Qlib execution.

## Shared semantic runner identity

Every candidate uses the same D2G runner code hash. The hash covers:

- the D2F concrete-family source;
- D2G family contract;
- shared dataset adapter;
- shared network guard;
- D2G family runner;
- D2G family attestation code;
- pinned Qlib requirements.

Therefore changing the candidate family, execution semantics, network boundary, environment evidence or pinned runtime necessarily changes the common runner identity.

## Runtime model construction

After request verification, D2G resolves the canonical candidate config and invokes Qlib 0.9.7 `LinearModel` using only frozen values:

```python
LinearModel(
    estimator=config["estimator"],
    alpha=config["alpha"],
    fit_intercept=config["fit_intercept"],
    include_valid=config["include_valid"],
)
```

Prediction uses the frozen `prediction_segment=test`.

The complete D2F V1 family is:

| candidate | estimator | alpha |
|---|---:|---:|
| `linear-lasso-a0p001` | lasso | 0.001 |
| `linear-lasso-a0p01` | lasso | 0.01 |
| `linear-ols` | ols | 0.0 |
| `linear-ridge-a0p1` | ridge | 0.1 |
| `linear-ridge-a1` | ridge | 1.0 |
| `linear-ridge-a10` | ridge | 10.0 |

## Data boundary

D2G receives only:

- OSS-3D2A request;
- OSS-3D1 TRAIN bundle;
- concrete TRAIN factor artifact;
- concrete TRAIN supervised-label artifact;
- DEVELOPMENT factor artifact.

It has no argument for DEVELOPMENT labels and no argument for FINAL_HOLDOUT.

The TRAIN bundle is rebuilt from concrete TRAIN artifacts before execution; all D2A lineage checks are repeated.

## Network and secret boundary

Real Qlib imports, fit and prediction occur inside `deny_network()`.

D2G refuses to start when broker/exchange credential variables are present for supported broker/exchange prefixes, including Alpaca, IBKR, Binance, Coinbase, Kraken, Bybit, OKX, Bitget and KuCoin.

There is no broker, OMS, Safety kernel, `OrderIntent`, exchange client or order submission import.

## Candidate-specific environment attestation

D2C V1 is intentionally preserved for the original Ridge canary, so D2G introduces a separately versioned but D2C-compatible reproducibility artifact:

`OSS3D2G_CANDIDATE_ENVIRONMENT_ATTESTATION_V1`

It records the same core reproducibility dimensions:

- Python implementation/version;
- OS/platform machine;
- libc;
- exact `pyqlib==0.9.7`;
- canonical installed-distribution set + hash;
- model family;
- candidate config hash;
- shared D2G runner hash;
- research-only authority flags.

Because `model_config_hash` differs, candidate attestation hashes differ as expected.

## Model-neutral D2E runtime identity

Every candidate attestation deterministically projects to the OSS-3D2E `RuntimeEnvironmentIdentity`, which deliberately excludes model/config/runner-specific fields and retains only actual runtime/environment properties.

When the six candidates execute in the same isolated environment:

```text
candidate attestation hashes -> six distinct hashes
runtime environment hash     -> one common hash
shared runner code hash       -> one common hash
```

This is the fairness model established by OSS-3D2E.

## D2G run evidence

Each invocation emits `OSS3D2G_CANDIDATE_RUN_EVIDENCE_V1`, binding:

- candidate id;
- model config hash;
- shared runner code hash;
- D2A request hash;
- OSS-3A prediction hash;
- D2A receipt fingerprint;
- environment attestation hash;
- model-neutral runtime environment hash.

It also permanently records:

```text
development_labels_loaded = false
final_holdout_loaded = false
broker_credentials_present = false
network_allowed = false
adaptive_search = false
hyperparameter_optimization = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

`verify_family_candidate_outputs()` rebinds request, prediction, receipt, attestation and evidence against the current frozen D2F/D2G contract.

## Scientific boundary

D2G produces predictions only. It does not rank candidates or decide which model is better.

The next stage is to run the complete six-candidate family under a single certified runtime environment, pass each prediction through OSS-3D2D DEVELOPMENT evaluation, and then ingest all six completed/failed trials into the already-preregistered OSS-3D2E tournament.

Only the D2E layer may compare model performance. FINAL_HOLDOUT remains inaccessible.
