# OSS-3D3C — Durable DEVELOPMENT Outcome Bundle

## Purpose

OSS-3D3C turns the completed real OSS-3D3A DEVELOPMENT campaign into a portable, canonical and hash-bound research handoff without rerunning the model family inside D3C and without advancing into FINAL_HOLDOUT.

D3A already produced the scientific result. D3C does not create another result. It binds two distinct identities that must not be conflated:

1. the **certified D3A baseline** from the original successful campaign; and
2. the **complete provenance identity of the current D3A replay**.

Those two full fingerprints may differ when the hosted Python environment contains different transitive distributions. D3C therefore requires a third identity: a stable scientific-outcome fingerprint that must match exactly between the certified baseline and the current replay.

The scientific boundary is:

```text
immutable certified D3A baseline
  + current unchanged D3A replay
    -> exact stable scientific-outcome equality
      + six frozen current D2G candidate artifact families
      + append-only current D2S preregistration
      + current terminal D2E ledger
        -> reconstruct current D2H provenance identity
          -> verify persisted D2E tournament only
            -> reproduce current exact D2I provenance seal
              -> D3C durable outcome bundle
                -> next frontier: D2J protocol preregistration only
```

D3C does **not** create or consume any FINAL_HOLDOUT commitment, permit, checkout or result.

## Certified baseline

D3C remains stacked on the exact certified OSS-3D3A head:

`d6a66390e86559d945b10cc1cd53825d20337640`

The exact certified D3A result is now source-controlled as immutable evidence at:

`knowledge/20_RESEARCH/OSS3D3A_CERTIFIED_BASELINE_d6a66390.json`

Its complete original fingerprint is:

`ce703916e83161cc71e73e56e4a9ffab5c0989bf630cfbae260a90a5846ca9cc`

Its original D2I provenance seal is:

`eacb62b1e506a4386b5f3fd71e0d4109f37a716d825473242fd3a94228386416`

The stable scientific-outcome fingerprint derived from that baseline is:

`5b4261b186e30cbdc30ec2c3025305fbb96650247a066cdf470ce53e80f86ea7`

Canonical real scientific outcome:

- status: `COMPLETED`;
- selected DEVELOPMENT trial: `linear-ridge-a10`;
- primary metric: `0.02431784450279712`;
- raw exact-sign-test p-value: `0.000903437382079215`;
- Holm-adjusted p-value: `0.00542062429247529`;
- exact six prediction artifact hashes unchanged;
- exact structural profiles unchanged;
- exact candidate DEVELOPMENT metrics/p-values unchanged;
- FINAL_HOLDOUT unobserved and unauthorized;
- no execution or capital authority.

These values are DEVELOPMENT research evidence, not a profitability or trading-authority claim.

## Why the original full D3A fingerprint is not the scientific replay gate

The original D3A fingerprint intentionally included complete environment provenance. Candidate environment attestations include the installed distribution set and therefore change if the hosted runner receives different transitive packages, even when:

- `pyqlib==0.9.7` remains exact;
- model family/configuration is unchanged;
- source data is unchanged;
- feature and label artifacts are unchanged;
- all six prediction artifact hashes are unchanged;
- structural profiles are unchanged;
- every DEVELOPMENT metric and p-value is unchanged;
- the same winner is selected.

A later replay demonstrated precisely this case: the complete provenance fingerprint changed while the scientific output remained byte-identical on all scientific surfaces.

D3C therefore adopts the explicit policy:

`D3A_STABLE_SCIENTIFIC_OUTPUT_EXCLUDING_RUNTIME_PROVENANCE_V1`

This is not a weakening of provenance. D3C retains **both** full provenance objects in the final artifact. The stable identity is only the equality gate used to decide whether two different runtime attestations produced the same scientific result.

## Stable scientific identity

The stable projection includes:

- D2W/raw TRAIN and DEVELOPMENT lineage;
- research split;
- D2R TRAIN bundle lineage;
- TRAIN feature and label artifact hashes;
- DEVELOPMENT feature and label artifact hashes;
- frozen D2F plan and request-set identities;
- structural policy and all six structural profiles;
- exact prediction artifact hash for every candidate;
- exact evaluable candidate set;
- terminal candidate status;
- primary metric and raw p-value for each candidate where applicable;
- selected winner;
- winner primary metric, raw p-value and Holm-adjusted p-value;
- no-retune/no-fallback/no-reselection state;
- every authority denial relevant to holdout, promotion, PAPER, capital and LIVE.

It deliberately excludes only container identities transitively bound to runtime/environment attestation, including:

- complete candidate-output hashes;
- D2S/D2H container fingerprints;
- D2E tournament container fingerprint;
- D2I container fingerprint;
- D2D evaluation artifact hashes that include environment attestation identity.

Those excluded provenance identities are still preserved separately and fully reverified for the **current** replay.

Any change to prediction hashes, structural evidence, metrics, p-values, winner, source lineage or authority state changes the stable scientific fingerprint and fails closed.

## Why D3C is necessary

The compact D3A result records fingerprints of D2H and D2I, but a future D2J preregistration needs a complete, durable lineage object rather than merely the winner id or a metric value.

Re-running D3A later merely to recreate those objects is undesirable because it would:

- depend again on the external Qlib runtime;
- repeat six model fits/predictions already frozen;
- increase operational surface without creating new scientific information;
- make later protocol construction depend on ephemeral in-memory state;
- conflate scientific reproducibility with hosted-runner package provenance.

D3C closes that gap using the durable output emitted by one validated current replay and binds it to the immutable certified baseline.

## Durable source material

D3A leaves the following under its external `work_root`.

### Six current candidate artifact families

For every exact D2F candidate:

- `request.json`;
- `training-bundle.json`;
- `development-features.json`;
- `prediction.json`;
- `attestation.json`;
- `run-evidence.json`;
- additional TRAIN/runtime files produced by D3A.

D3C reads and independently revalidates the artifacts required for the frozen current output identity. It rebuilds the D2A prediction receipt from the request, prediction, TRAIN bundle and DEVELOPMENT feature artifact rather than trusting a loose receipt file.

Every reconstructed current `FrozenCandidateOutput.fingerprint` must equal the exact candidate-output hash committed by that same current D3A replay.

### D2S append-only preregistration

`d2s-real.sqlite3`

D3C opens this database using SQLite URI `mode=ro` and `PRAGMA query_only = ON`.

It requires exactly one canonical D3A preregistration and verifies canonical JSON, stored fingerprint, raw DEVELOPMENT identity, D2F plan/request-set identity, D2H code identity, tournament identifiers and statistical-policy lineage.

D3C does not initialize or modify this source database.

### D2E terminal ledger

`d2e-real-trials.sqlite3`

D3C treats the source as immutable evidence. Because the existing `SQLiteTrialLedger` initializes schema on construction, D3C never points that mutable helper at the source file. It copies the ledger to a temporary verification file and performs deterministic tournament verification only on that copy.

The source ledger itself is never mutated.

## No scientific recomputation inside D3C

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

For every completed candidate, D3C requires the ledger's stored primary metric, p-value and D2D artifact hash to equal the current D3A candidate-result accounting. Frozen structural failures must remain the exact D3A failure code and may expose no evaluation values.

## D2H reconstruction

D3C reconstructs the exact **current replay** `FamilyEvaluationPreregistration` from:

- current D2S durable preregistration;
- exact six current candidate artifacts;
- current shared runtime identity;
- exact DEVELOPMENT label artifact **hash only** from D3A;
- exact D2E campaign/tournament metadata;
- exact D2H code version frozen before DEVELOPMENT metrics.

The resulting D2H fingerprint must equal the D3A-recorded current replay D2H preregistration fingerprint.

After terminal-ledger verification, D3C constructs current D2H batch evidence from persisted D2D hashes and the reverified D2E tournament. Its fingerprint must equal the current replay D3A-recorded batch fingerprint.

## D2I reproduction

D3C runs the existing deterministic `seal_development_winner(...)` over the reconstructed **current** D2H objects.

The resulting D2I fingerprint must equal the D3A-recorded current D2I seal exactly. It is not required to equal the historical D2I seal when runtime provenance changed; both current and historical provenance remain preserved through their respective D3A evidence objects.

Any different current selected trial, model config, request, prediction, environment binding, D2G run evidence, D2D artifact, D2E tournament, runtime or metric fails closed relative to the current D3A replay.

D3C never performs a new winner selection from an alternative universe.

## Durable bundle

Contract:

`OSS3D3C_DURABLE_DEVELOPMENT_OUTCOME_BUNDLE_V1`

The final artifact embeds canonical JSON for:

1. immutable certified D3A baseline evidence;
2. complete current D3A replay evidence;
3. reconstructed current D2H preregistration;
4. reconstructed current D2H completed batch evidence;
5. reproduced current D2I DEVELOPMENT winner seal.

It binds:

- certified baseline full fingerprint;
- current replay full fingerprint;
- stable scientific-outcome fingerprint shared by both;
- current D2H preregistration fingerprint;
- current D2H batch fingerprint;
- current D2I fingerprint;
- its own artifact SHA-256.

Serialization rules:

- UTF-8 JSON;
- sorted canonical keys;
- compact separators;
- duplicate JSON keys rejected;
- non-finite numbers rejected;
- bounded to 4 MiB;
- atomic temporary-file replacement;
- complete artifact hash verified on read.

The embedded objects remain research evidence. D3C does not grant them new authority.

## Next frontier

The only permitted next frontier remains:

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

The dedicated real-data run must additionally prove, in order:

1. the source-controlled certified baseline is canonical and still has full fingerprint `ce7039...`;
2. that baseline projects to stable scientific fingerprint `5b4261...`;
3. D2Y/D2Z real data rehydrates with all 99 descriptors verified;
4. `pyqlib==0.9.7` is exact before the scientific replay;
5. the unchanged D3A campaign completes;
6. the current D3A replay projects to the exact same `5b4261...` scientific fingerprint;
7. the current winner and all canonical metrics remain exact;
8. D3C reconstructs current D2H/D2I provenance solely from durable outputs;
9. the portable D3C artifact round-trips and preserves all authority denials.

A change only to runtime provenance may change the current full D3A/D2H/D2I container fingerprints. A change to scientific identity is a certification failure.

D3C must remain a child of the certified D3A branch and must not modify D3A scientific files.