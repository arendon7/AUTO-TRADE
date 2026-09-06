# OSS-3D2C — Qlib Effective Environment Attestation

Status: DEVELOPMENT / RESEARCH ONLY

## Objective

OSS-3D2C closes the reproducibility gap left intentionally after OSS-3D2B. D2B certifies a real, isolated `pyqlib==0.9.7` Ridge training/inference path. D2C records the **effective resolved runtime environment** in which that certified path executes.

This is an environment attestation, not a claim that a single Linux package lock is portable across every platform.

## Frozen parent

D2C is stacked on the certified OSS-3D2B head:

```text
89db149d2cbbbb931da5c3e6a2bfb849a1e03c20
```

The five semantic D2B files remain untouched:

- `model_contract.py`
- `dataset_adapter.py`
- `network_guard.py`
- `runner.py`
- `requirements.txt`

Dedicated CI diffs those files against the pull-request base SHA and fails if any changed.

## Evidence captured

The canonical artifact records only reproducibility-relevant, non-secret facts:

- Python implementation and `major.minor.patch` version;
- OS system and release;
- machine architecture;
- certified D2B `runner_code_hash`;
- frozen `model_config_hash`;
- SHA-256 of `requirements.txt`;
- effective `pyqlib` version;
- exact, canonically sorted installed distribution names and versions;
- distribution count and manifest SHA-256;
- installed presence of packages that remain runtime-forbidden (`mlflow`, `redis`);
- explicit no-execution/no-capital/LIVE-blocked authority fields.

It does **not** record:

- environment variable names or values;
- API keys, tokens, passwords or broker credentials;
- username or home directory;
- hostname/FQDN;
- working directory or filesystem paths;
- IP/MAC/network identity;
- broker/account identity.

## Distribution identity

Distribution names use a PEP-503-like canonical form:

```text
lowercase + collapse [-_.]+ to '-'
```

The final manifest must be sorted and unique after normalization. `importlib.metadata` may enumerate the same installed distribution more than once when multiple metadata discovery paths describe the **same canonical package and same version**; D2C treats that as enumeration noise and collapses those identical records deterministically.

The rule is deliberately asymmetric:

```text
same canonical name + same version  -> collapse to one manifest row
same canonical name + different versions -> FAIL CLOSED
```

Thus D2C does not hide an ambiguous effective environment. If conflicting versions are observed for one canonical package name, attestation stops and names the conflicting package.

The primary runtime remains a hard contract:

```text
pyqlib == 0.9.7
```

Required supporting distributions must be present:

- numpy
- pandas
- scikit-learn
- scipy
- joblib

Their exact effective versions are **captured**, not hard-coded as universal policy. If dependency resolution changes one of them, the package manifest and complete environment attestation hashes change.

## Installed does not mean authorized

The real `pyqlib==0.9.7` dependency graph currently installs packages including MLflow and Redis. D2B does not import or use them. D2C records their presence when installed but keeps them in the `RUNTIME_FORBIDDEN_EVEN_IF_INSTALLED` set.

Therefore:

```text
PACKAGE_PRESENT != RUNTIME_AUTHORIZED
```

D2B and D2C boundary checkers continue to prohibit the corresponding runtime surfaces.

## Canonical identity

The artifact has two nested fingerprints:

```text
installed_manifest_hash
    = SHA256(canonical installed [{name,version}, ...])

attestation_hash
    = SHA256(canonical attestation payload excluding attestation_hash itself)
```

Any change to Python/platform identity, D2B runner code, model config, requirements bytes, installed package names/versions, or authority fields changes the final attestation hash.

## Determinism check

Dedicated CI captures the environment twice in the same job:

```text
environment-a.json
environment-b.json
```

It requires byte-for-byte equality and then independently reads both artifacts and checks `same_effective_environment()`.

The canonical `environment-a.json` is retained as a GitHub Actions artifact for 30 days.

## Certification workflow

The dedicated workflow performs, in order:

1. checkout with full history;
2. install AUTO-TRADE core only;
3. prove no D2B semantic file changed from the PR base;
4. compile D2C evidence/checker;
5. static authority/privacy boundary before external runtime install;
6. install certified `pyqlib==0.9.7` lab requirements;
7. verify effective primary Qlib version;
8. run D2C tests, including duplicate-metadata collapse and conflicting-version denial;
9. capture the environment twice and require exact equality;
10. verify the captured evidence;
11. re-run D2C boundary after external dependencies exist;
12. execute real D2B Qlib Ridge regression;
13. re-prove D2A/D1/C/B/A, Research Authority, W83 and OSS-2H;
14. retain the canonical attestation artifact.

Core Safety runs independently and remains Qlib-free.

## Authority boundary

OSS-3D2C has no authority to:

- import or execute Qlib models;
- use MLflow or Redis;
- access network/process surfaces;
- read environment variable values or secrets;
- access brokers;
- construct `OrderIntent` or `RiskDecision`;
- invoke OMS or Capital Safety;
- promote a candidate;
- authorize PAPER;
- reserve or deploy capital;
- enable LIVE.

Canonical authority state:

```text
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

## Scientific interpretation

An equal D2C attestation hash means the recorded effective software/platform environment is equal under this contract. A different hash means results should not be treated as same-environment reproductions without explicit analysis.

Neither equality nor certification proves predictive alpha, economic profitability, robustness, or suitability for capital deployment.
