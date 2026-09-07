# OSS-3D2P — Provenance-Gated Economic Admission

Status: **RESEARCH-ONLY / STACKED DRAFT FRONTIER**

Base: certified OSS-3D2O head `5ae582462df9732a5a13a5593b1d3c0e2a81005c`.

## Purpose

D2O proves that an economic prediction artifact can be reproduced by the exact frozen Qlib winner, original TRAIN bundle, committed point-in-time feature artifact and D2G runtime. D2P converts that provenance primitive into a durable admission condition for a D2P-governed economic campaign.

The problem D2P closes is subtle: a valid D2O receipt that is never bound into the shared one-shot SQLite ordering remains informative evidence, not an admission control. D2P therefore independently replays D2O and persists the exact match **before D2K**. A database trigger then blocks D2N starts lacking that admission.

## Canonical ordering

```text
D2M economic protocol
  -> D2O economic feature artifact
    -> D2O real-Qlib prediction
      -> D2N exact prediction precommit
        -> D2P independent D2O replay
          -> exact replay == durable precommit
            -> D2P append-only provenance admission
              -> D2K predictive FINAL_HOLDOUT
                -> D2N economic one-shot evaluation
```

The ordering contract is:

```text
OSS3D2P_D2O_PRECOMMIT_D2K_D2N_SHARED_SQLITE_ORDERING_V1
```

Replay policy:

```text
INDEPENDENT_D2O_EXACT_PREDICTION_REPLAY_V1
```

## Why D2P replays D2O

D2P does not accept a caller boolean such as `provenance_verified=true` as sufficient evidence. It also does not merely compare a caller-supplied D2O receipt hash.

Admission performs a fresh D2O execution using:

- exact D2M economic protocol;
- exact D2L winner binding;
- exact D2J protocol;
- exact frozen winner request;
- exact original TRAIN feature and label artifacts;
- exact original TRAIN bundle;
- exact D2O economic feature artifact;
- exact D2G environment/runtime identity;
- exact `pyqlib==0.9.7` model execution under D2O `deny_network()`.

The replayed prediction must equal the already-durable D2N precommit on:

```text
prediction_artifact_hash
prediction_payload_hash
prediction_support_hash
```

A feature-value change that changes scores therefore cannot be admitted against the frozen precommit.

## Durable admission

D2P stores one append-only row per economic protocol in:

```text
oss3_economic_prediction_provenance_admissions
```

The durable receipt binds:

- D2M protocol id and receipt hash;
- D2L receipt, binding and strategy semantic hashes;
- D2J protocol id and receipt hash;
- exact D2N prediction-precommit id and receipt hash;
- exact prediction artifact/payload/support hashes;
- recomputed D2O provenance receipt and semantic hashes;
- exact D2O feature artifact, row payload and support hashes;
- feature source and feature-producer code hashes;
- source and observed D2G environment attestations;
- source and observed runtime identities;
- shared D2G model-runner semantic hash;
- D2P admission semantic hash;
- registration time and pre-D2K ordering assertions;
- explicit no-outcome/no-authority state.

The table is protected by no-UPDATE and no-DELETE triggers.

## Database-enforced D2N gate

When the D2P registry initializes a shared SQLite campaign, it first initializes D2N's exact prediction-precommit schema and then installs:

```text
oss3_d2p_d2n_start_requires_provenance_admission
```

The trigger rejects any INSERT into `oss3_economic_holdout_evaluation_starts` unless the exact D2P admission exists for:

- economic protocol id;
- economic protocol receipt;
- D2L receipt;
- strategy semantic hash;
- D2J protocol id/receipt;
- exact economic prediction artifact hash.

Therefore, **inside a D2P-governed SQLite campaign**, calling the lower-level D2N evaluator directly cannot bypass provenance admission.

## Explicit scope of “mandatory”

D2P does not delete or rewrite the historical D2N primitive. A brand-new SQLite file that never initializes D2P still has the older D2N semantics.

Accordingly the precise claim is:

> Provenance admission is mandatory for any campaign initialized and certified under OSS-3D2P.

A later migration may choose to make D2P the only constructor reachable from higher-level campaign orchestration. D2P itself does not silently redefine the already-certified D2N contract.

## Visibility / leakage boundary

D2P must replay inference, so it loads the point-in-time D2O feature artifact and explicitly records:

```text
market_derived_features_loaded = true
```

It does not receive or import:

- economic labels;
- forward economic returns;
- realized PnL;
- D2N Sharpe/PF/DD/net-return metrics;
- D2N gate decisions;
- raw economic `MarketDataset` / `AlignedMarketUniverse` objects.

The receipt therefore requires:

```text
economic_labels_loaded = false
economic_outcomes_loaded = false
```

D2P runs before D2K admission is allowed to commit. The durable transaction rechecks that no D2K start exists and the protected holdout permit has not been consumed.

## Failure semantics

D2P fails closed if any of the following occurs:

- no durable prediction precommit;
- D2M/D2L/D2J lineage drift;
- winner/model/runtime drift;
- feature artifact drift;
- D2O replay artifact differs from precommit;
- D2O replay payload differs from precommit;
- support differs from precommit;
- D2K start already exists;
- D2K permit was already consumed;
- broker/exchange credentials are present during the delegated D2O replay;
- an existing admission conflicts with the new candidate;
- durable prerequisite rows differ from the supplied receipts.

No failed admission creates a durable D2P row.

## Authority boundary

D2P is not a promotion credential and not profitability evidence. Every receipt requires:

```text
profitability_claim_authorized = false
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

D2P imports no broker, OMS, Safety or OrderIntent implementation and does not execute D2K or D2N itself.

## Scientific limitation that remains

D2O/D2P now prove and enforce:

```text
committed feature bytes
  -> frozen TRAIN/model/runtime
    -> real Qlib replay
      -> exact precommitted prediction bytes
        -> mandatory D2P-governed D2N admission
```

They still do not prove that the upstream feature producer correctly transformed raw historical market bars into the D2O feature artifact. The feature artifact binds `feature_source_hash` and `feature_producer_code_hash`, but the raw-market-to-feature derivation is still a separate lineage frontier.

That upstream derivation proof is the logical next research-hardening stage after D2P certification.

## Required tests

The D2P dedicated workflow must prove on one exact head:

1. compile before Qlib installation;
2. D2P authority/outcome/static ordering boundary;
3. D2O, D2N-precommit, D2N, D2M, D2L, D2J, D2I and D2H boundaries remain valid;
4. Research Authority remains valid;
5. exact `pyqlib==0.9.7`;
6. full D2P order: D2O -> precommit -> replay admission -> D2K -> D2N;
7. direct D2N bypass blocked when D2P schema exists but admission does not;
8. changed feature values cannot match frozen prediction precommit;
9. admission after D2K is rejected and not persisted;
10. broker credentials block replay admission;
11. D2P table is append-only and trigger exists;
12. receipt cannot be mutated into promotion/PAPER/capital authority;
13. six-candidate D2G real runtime regression;
14. D2K and D2N regressions;
15. Knowledge Contract and Core Safety green on the same head.
