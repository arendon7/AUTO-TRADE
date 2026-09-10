# OSS-3D3C — Durable DEVELOPMENT Outcome Bundle

## Purpose

OSS-3D3C turns the completed real OSS-3D3A DEVELOPMENT campaign into a portable, canonical and hash-bound research handoff without rerunning the model family and without advancing into FINAL_HOLDOUT.

D3A already produced the scientific result. D3C does not create another result. It proves that the durable files and ledgers left by D3A reproduce the exact D2H preregistration, D2H batch evidence and D2I DEVELOPMENT winner that D3A reported, then serializes those identities into one bounded artifact.

The scientific boundary remains:

```text
real D3A DEVELOPMENT result
  + six frozen D2G candidate artifact families
  + append-only D2S preregistration
  + terminal D2E ledger
    -> reconstruct D2H identity
      -> verify persisted D2E tournament only
        -> reproduce exact D2I winner seal
          -> D3C durable outcome bundle
            -> next frontier: D2J protocol preregistration only
```

D3C does **not** create or consume any FINAL_HOLDOUT commitment, permit, checkout or result.

## Exact certified source expected

D3C is stacked on the exact certified OSS-3D3A head:

`d6a66390e86559d945b10cc1cd53825d20337640`

Canonical real D3A outcome currently expected:

- status: `COMPLETED`;
- result fingerprint: `ce703916e83161cc71e73e56e4a9ffab5c0989bf630cfbae260a90a5846ca9cc`;
- selected DEVELOPMENT trial: `linear-ridge-a10`;
- primary metric: `0.02431784450279712`;
- raw exact-sign-test p-value: `0.000903437382079215`;
- Holm-adjusted p-value: `0.00542062429247529`;
- D2I winner seal: `eacb62b1e506a4386b5f3fd71e0d4109f37a716d825473242fd3a94228386416`.

These values are evidence identities, not a profitability or trading-authority claim.

## Why D3C is necessary

The D3A compact result records the fingerprints of D2H and D2I, but a future D2J preregistration needs the complete D2H/D2I lineage object, not merely the winner id or a metric value.

Re-running D3A later merely to recreate those objects would be undesirable because it would:

- depend again on the external Qlib runtime;
- repeat six model fits/predictions that are already frozen;
- increase operational surface without creating new scientific information;
- make later protocol construction depend on reconstruction from an ephemeral in-memory state.

D3C closes that gap using only the durable output already emitted by D3A.

## Durable source material

D3A leaves the following under its external `work_root`:

### Six candidate artifact families

For every exact D2F candidate:

- `request.json`;
- `training-bundle.json`;
- `development-features.json`;
- `prediction.json`;
- `attestation.json`;
- `run-evidence.json`;
- additional TRAIN/runtime files produced by D3A.

D3C reads and independently revalidates the artifacts required for the frozen output identity. It rebuilds the D2A prediction receipt from the request, prediction, TRAIN bundle and DEVELOPMENT feature artifact rather than trusting a loose receipt file.

Every reconstructed `FrozenCandidateOutput.fingerprint` must equal the exact candidate-output hash already committed by D3A.

### D2S append-only preregistration

`d2s-real.sqlite3`

D3C opens this database using SQLite URI `mode=ro` and `PRAGMA query_only = ON`.

It requires exactly the canonical D3A preregistration id and verifies:

- canonical JSON;
- stored fingerprint;
- raw DEVELOPMENT identity;
- D2F plan/request-set identity;
- D2H code identity;
- tournament campaign/id;
- statistical policy lineage.

D3C does not initialize or modify this source database.

### D2E terminal ledger

`d2e-real-trials.sqlite3`

D3C first treats this as immutable source evidence. Because the existing `SQLiteTrialLedger` initializes its schema on construction, D3C never points that mutable helper at the source file. It copies the ledger to a temporary verification file and performs deterministic tournament verification only on the copy.

The source ledger itself is never mutated.

## No scientific recomputation

D3C may recompute only deterministic **identity/ranking verification** from already-persisted terminal D2E records.

It does not:

- fit or predict any model;
- invoke `run_isolated_qlib_family_candidate`;
- materialize DEVELOPMENT labels;
- read DEVELOPMENT label values;
- call `evaluate_development_predictions`;
- recompute D2D metrics from predictions/labels;
- add, remove or replace a candidate;
- retune a model;
- change the ranking rule;
- change exact-sign-test/Holm policy.

For every completed candidate, D3C requires the ledger's stored primary metric, p-value and D2D artifact hash to equal D3A's candidate-result accounting. Frozen structural failures must remain the exact D3A failure code and may expose no evaluation values.

## D2H reconstruction

D3C reconstructs the exact `FamilyEvaluationPreregistration` from:

- D2S durable preregistration;
- exact six candidate artifacts;
- exact shared runtime identity;
- exact DEVELOPMENT label artifact **hash only** from D3A;
- exact D2E campaign/tournament metadata;
- exact D2H code version frozen before DEVELOPMENT metrics.

The resulting D2H fingerprint must equal the D3A-recorded D2H preregistration fingerprint.

After terminal-ledger verification, D3C constructs the D2H batch evidence from persisted D2D hashes and the reverified D2E tournament. Its fingerprint must equal the D3A-recorded batch fingerprint.

## D2I reproduction

D3C runs the existing deterministic `seal_development_winner(...)` over the reconstructed D2H objects.

The resulting D2I fingerprint must equal the D3A-recorded D2I winner seal exactly. Any different selected trial, model config, request, prediction, environment, D2G run evidence, D2D artifact, D2E tournament, runtime or metric fails closed.

D3C never performs a new winner selection from an alternative universe.

## Durable bundle

Contract:

`OSS3D3C_DURABLE_DEVELOPMENT_OUTCOME_BUNDLE_V1`

The final artifact embeds canonical JSON for:

1. completed D3A evidence;
2. D2H preregistration;
3. D2H completed batch evidence;
4. D2I DEVELOPMENT winner seal.

It also binds their four fingerprints and its own artifact SHA-256.

Serialization rules:

- UTF-8 JSON;
- sorted canonical keys;
- compact separators;
- duplicate JSON keys rejected;
- non-finite numbers rejected;
- bounded to 4 MiB;
- atomic temporary-file replacement;
- complete artifact hash verified on read.

The embedded objects remain evidence. D3C does not grant them new authority.

## Next frontier

The only permitted next frontier is the existing D2I declaration:

`OSS3D2J_PROTOCOL_PREREGISTRATION_ONLY`

D3C itself does **not** construct a D2J holdout commitment and does not call the D2J durable registry.

A later stage must separately establish a value-opaque, preregistered FINAL_HOLDOUT identity before any possible one-shot evaluation. That later stage must preserve all existing no-retune/no-reselection/no-second-attempt controls.

## Authority boundary

D3C must always retain:

```text
final_holdout_observed              = false
final_holdout_authorized            = false
holdout_permit_consumed             = false
profitability_claim_authorized      = false
promotion_authorized                = false
execution_authorized                = false
paper_execution_authorized          = false
capital_authority                   = NONE
live_trading                        = BLOCKED
```

D3C has no broker, OMS, Safety, OrderIntent, provider acquisition, trade-write or capital surface.

## Certification requirement

D3C is certifiable only when the same exact PR head passes:

1. dedicated OSS-3D3C workflow;
2. Knowledge Contract;
3. Core Safety Tests.

The dedicated real-data run must additionally reproduce the exact already-certified D3A result fingerprint before accepting any D3C artifact. D3C must remain a child of the certified D3A branch and must not modify D3A scientific files.
