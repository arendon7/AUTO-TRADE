# OSS-3D3H — D2K replay-readiness seal

## Purpose

D3H closes the last **non-holdout** durability gap before any possible D2K one-shot predictive FINAL_HOLDOUT evaluation. It reconstructs the exact concrete replay inputs already frozen by D3A/D3C, rebinds them to the certified D3G/D2J protocol, and proves that the current exact Qlib runtime can reproduce the winner's frozen D2G environment.

D3H is not an authorization stage. A successful D3H seal means only that replay inputs and runtime identities are reproducible for later human/governance review.

## Certified upstream lineage

D3H consumes only:

1. the certified D2X real-history artifact (99 BTCUSDT/ETHUSDT/SOLUSDT monthly descriptors, offline reverified through D2W);
2. the certified D3C durable DEVELOPMENT outcome;
3. the certified D3G public preregistration artifact and read-only D2J registry.

It does **not** download or read D3F private Q1 material. D3G contributes only the value-opaque Q1 commitment identity and D2J receipt.

## Qlib-free preparation

Before Qlib exists in the environment, D3H deterministically reconstructs:

- D2W sealed TRAIN/DEVELOPMENT raw split;
- D2R TRAIN `FactorMatrixArtifact`;
- D2R TRAIN `SupervisedLabelArtifact`;
- D2R `TrainingBundleArtifact`;
- D2S DEVELOPMENT `FactorMatrixArtifact` **without DEVELOPMENT labels**;
- the frozen D2F plan/request set;
- exactly the `linear-ridge-a10` winner `DevelopmentInferenceRequest` selected previously by D2I.

Every identity must equal the corresponding D3C root. The request, bundle and concrete artifacts are serialized with their existing canonical `write/read` contracts and immediately round-trip verified.

The source campaign identity is frozen locally as `oss3d3a-real-development-campaign-v1`. D3H deliberately does not import the executable D3A campaign module during preparation, because that module transitively loads the Qlib/pandas runner surface. This keeps the preparation import graph core-only while preserving the exact already-certified campaign identity.

## Runtime-only seal

Only after replay preparation is complete does CI install exact `pyqlib==0.9.7`. D3H then collects the model-neutral/candidate environment attestation and requires exact equality with the D2I winner's frozen D2G environment and runtime fingerprints. It also freezes `evaluator_semantic_hash()` for the current D2K implementation.

No `LinearModel` is constructed; no fit/predict is executed.

## Hard boundary

D3H must never:

- construct `ProtectedOSS3FinalHoldout`;
- construct or issue a `HoldoutPermit`;
- initialize or write a D2K evaluation registry;
- invoke `SQLiteOSS3FinalHoldoutEvaluationRegistry.evaluate()`;
- load Q1 feature rows, label rows, label values or outcomes;
- materialize DEVELOPMENT labels;
- rerank, retune, replace or reselect the DEVELOPMENT winner;
- claim predictive validation or profitability;
- authorize promotion, PAPER, execution, capital or LIVE.

Frozen authority remains:

- FINAL_HOLDOUT private material loaded: **false**
- D2K evaluator invoked: **false**
- holdout permit issued/consumed: **false / false**
- FINAL_HOLDOUT checkout authorized: **false**
- predictive validation passed: **false**
- profitability/promotion/execution/PAPER authorized: **false**
- capital authority: **NONE**
- LIVE: **BLOCKED**

## Next frontier

A later stage may review whether to authorize the already-existing D2K one-shot evaluator. That later decision must be explicit and separately certified. D3H itself cannot cross the Q1 boundary.
