# OSS-3D3A — Real DEVELOPMENT Campaign

## Purpose

OSS-3D3A is the first stage that carries the **certified real Binance historical campaign** all the way through the frozen six-model Qlib DEVELOPMENT experiment. It does not change the model family, feature formulas, supervised-label formula, primary metric, multiple-testing policy or winner-selection rule already certified in OSS-3D2R through OSS-3D2I.

Its job is scientific execution, not strategy promotion.

## Exact upstream roots

D3A starts only from the already-certified chain:

- OSS-3D2Y certified head: `608a4efc6202eed698f6becd7d9b769fbf46fa6f`
- D2Y evidence seal: `919009bf61a1a3512e99c3b8edb21017b8d0de4f97722dcc3849f5ba1b5f9beb`
- D2Z certified head: `74e8fc61be94cca665ab12f70694421ba5eb5e10`
- D2Z stable 99-material root: `8aef56ea8e96c2c880765440da86e31041d846324f7b9a3ebd79d0a1a470cdaf`
- D2U V2 plan fingerprint: `f43abdcd532f07d37b54f93abafbd3b29a3c3ee07afeedbae95b7aa15153c59d`
- D2Y TRAIN universe: `0e998c27a9636f1005b429f052ec902f0b9fb91d66e235dc0c3b2f090fa3cd69`
- D2Y DEVELOPMENT universe: `5fb5866a084f97cebff3b75f11be9f9ecf667880fc11584ad52b53cfdd820c9d`

The market family remains exactly 99 monthly Binance Spot 1h descriptors: BTCUSDT, ETHUSDT and SOLUSDT from 2023-04 through 2025-12. No 2026 provider archive and no FINAL_HOLDOUT material are part of this campaign.

## Why rehydrate the original artifact now

The original real D2Y Actions artifact remains available until 2026-10-09. It contains the exact first-acquisition D2V evidence root, including the original append-only ledgers and receipts.

Reacquiring Binance immediately would create new `acquired_at` and `sealed_at` timestamps and therefore new audit receipts even if provider bytes were unchanged. D2Z already solves long-term reacquisition by freezing timestamp-independent material identities, but while the exact original evidence still exists, the strongest path is to rehydrate and verify that exact campaign.

D3A therefore does **not** perform provider GETs during rehydration. It verifies:

1. exact D2Y inventory SHA-256;
2. exact D2Y evidence tar SHA-256;
3. safe regular-file-only tar extraction;
4. exact 398-file inventory after extraction;
5. all 99 descriptor materials through the D2Z offline equality gate;
6. the complete D2V seal and D2U partition through D2W with network disabled;
7. exact D2Y TRAIN and DEVELOPMENT universe identities.

The extraction root must be outside the repository and is created atomically through a staging directory.

## Real research sequence

Once D2W has reproduced the certified raw split, D3A uses only existing certified primitives:

```text
D2Y/D2Z exact real evidence
  -> D2W offline raw handoff
    -> D2R raw TRAIN bundle
      -> D2S causal DEVELOPMENT features only
        -> D2F exact six requests
          -> D2G exact six Qlib predictions
            -> pre-label structural prediction gate
              -> durable D2S preregistration
                -> DEVELOPMENT label materialization
                  -> D2H/D2E durable preregistration
                    -> D2D predictive evaluation
                      -> D2E ranking + exact sign test + Holm
                        -> D2I DEVELOPMENT winner seal
```

The **six** candidates remain exactly the frozen D2F LinearModel family. D3A performs no retuning, no alpha search, no candidate replacement and no fallback candidate.

## Pre-label structural gate

A real frozen model may legitimately produce a prediction vector that cannot support the preregistered cross-sectional IC statistic. This is especially relevant to strongly penalized linear models that may collapse toward a constant prediction on small financial returns.

D3A does not repair such a candidate after seeing data. Before DEVELOPMENT labels are materialized it computes only prediction structure:

- prediction is globally nonconstant; and
- every timestamp has a nonconstant cross-section over the exact three symbols.

Policy:

`PRELABEL_REQUIRE_GLOBAL_AND_EVERY_CROSS_SECTION_SCORE_VARIATION_V1`

A structurally invalid candidate remains part of the frozen family but is later recorded as FAILED with:

`OSS3D3A_PRELABEL_STRUCTURAL_UNEVALUABLE`

There is **no retuning** and no substitute model.

If fewer than two candidates are structurally evaluable, D3A terminates as:

`PRELABEL_BLOCKED`

In that state DEVELOPMENT labels are never materialized, DEVELOPMENT metrics are never computed and no D2I winner exists.

## DEVELOPMENT label ordering

When at least two candidates are structurally evaluable, all six prediction artifacts already exist and are hash-bound before the first DEVELOPMENT label artifact can exist.

D3A then:

1. preregisters D2S durably;
2. materializes the fixed one-bar-forward DEVELOPMENT labels;
3. constructs D2H/D2E from those already-frozen six predictions;
4. preregisters the full six-candidate tournament durably;
5. records structurally invalid candidates as FAILED;
6. evaluates structurally valid candidates with D2D;
7. applies the frozen D2E ranking and Holm evidence;
8. seals the ranking winner with D2I.

A D2I winner means only **first place under the preregistered DEVELOPMENT ranking rule**. Raw p-values and Holm-adjusted p-values are retained as evidence but do not authorize a statistical-significance, alpha or profitability claim.

## Candidate failure semantics

Failed structural candidates remain in the original family accounting. They are not silently dropped before preregistration and are not replaced after results.

Holm evidence continues to refer to the frozen campaign. Completed candidates must share exact cross-sectional timestamp support. A winner may exist only among completed/evaluable candidates.

## What D3A does not do

D3A does not:

- observe or load FINAL_HOLDOUT;
- create or consume a HoldoutPermit;
- execute an OSS-3D2J/D2K holdout protocol;
- infer profitability from DEVELOPMENT predictive metrics;
- run a trading backtest as a substitute for the predictive experiment;
- choose new hyperparameters after seeing real data;
- authorize promotion;
- send broker orders;
- authorize PAPER;
- grant capital authority;
- enable LIVE trading.

The next stage is not automatically invoked. A completed D2I winner may be handed to a separately preregistered predictive FINAL_HOLDOUT protocol only after D3A itself is durably certified and reviewed.

## Authority

D3A terminal evidence always requires:

```text
family_retuned = false
fallback_candidate_used = false
reselection_allowed = false
statistical_significance_claim_authorized = false
profitability_claim_authorized = false
final_holdout_observed = false
final_holdout_authorized = false
holdout_permit_consumed = false
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

`PRELABEL_BLOCKED` additionally requires DEVELOPMENT labels and metrics to remain absent.

## Certification expectation

Dedicated D3A CI must prove two phases on the same PR head:

### Core-only rehydration

- Qlib absent;
- exact certified D2Y artifact downloaded by fixed run/name;
- exact inventory/tar identity;
- all 99 D2Z materials verified;
- D2W offline complete;
- exact D2Y TRAIN/DEVELOPMENT identities;
- no provider network call.

### Real model campaign

- exact `pyqlib==0.9.7` installed only after rehydration;
- exact six D2G model runs;
- pre-label structural profiles frozen;
- DEVELOPMENT labels revealed only after durable D2S preregistration;
- D2E/Holm and D2I only when structurally admissible;
- no FINAL_HOLDOUT, broker, PAPER, capital or LIVE authority.

Knowledge Contract and Core Safety must also succeed on the exact same head before D3A can be called certified.
