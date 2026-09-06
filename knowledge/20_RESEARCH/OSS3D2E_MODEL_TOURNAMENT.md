# OSS-3D2E — Preregistered DEVELOPMENT Model Tournament

## Status

Research-only protocol. This stage freezes **how** a finite family of Qlib model candidates will be compared on DEVELOPMENT evidence before a later runner stage produces a concrete multi-model family.

OSS-3D2E does not train models, does not invoke Qlib, does not observe FINAL_HOLDOUT, does not calculate executable PnL and grants no PAPER, capital or LIVE authority.

## Why this stage exists before multi-model execution

If the model family, ranking metric or significance procedure were chosen after inspecting several DEVELOPMENT results, the research process would gain an unrecorded adaptive degree of freedom. D2E therefore preregisters the comparison protocol first.

The sequence is:

```text
TRAIN features + labels
        |
        v
OSS-3D1 frozen training bundle
        |
        v
OSS-3D2A DEVELOPMENT inference request (no DEVELOPMENT labels)
        |
        v
isolated Qlib runner -> immutable OSS-3A predictions
        |
        v
OSS-3D2A prediction receipt
        |
        v
OSS-3D2D prediction + DEVELOPMENT label evaluation
        |
        v
OSS-3D2E frozen family tournament
```

A future child stage may expand the isolated runner and instantiate a concrete finite family, but it must obey the D2E protocol rather than redesign it after seeing results.

## Frozen candidate universe

A D2E plan contains a `CampaignSpec` whose complete `expected_trial_ids` are fixed before any result is recorded. Every candidate is preregistered as a `TrialSpec` in `DEVELOPMENT` phase.

Candidate identity binds:

- trial ID;
- hypothesis ID;
- model family;
- model-config SHA-256;
- exact OSS-3D2A request SHA-256;
- Qlib version;
- expected isolated-runner code SHA-256;
- the candidate-specific OSS-3D2C environment-attestation SHA-256.

The family must contain between 2 and 32 candidates. Trial IDs, substantive `(model_family, model_config_hash)` identities and inference request hashes must all be unique. Adding a new candidate after preregistration requires a new campaign; the existing campaign is not expanded.

## D2C attestation versus common runtime identity

OSS-3D2C V1 is not a model-neutral environment document. Its manifest deliberately binds not only Python/Qlib/distributions, but also:

- `model_family`;
- `model_config_hash`;
- `runner_code_hash`.

Therefore two legitimate model candidates can and normally will have different D2C `artifact_hash` values even when they were executed in the same scientific software environment.

D2E preserves that exact candidate-specific attestation hash for provenance, but does **not** require those artifact hashes to be equal.

For fair comparison, D2E separately freezes a `RuntimeEnvironmentIdentity` containing only the model-neutral D2C fields:

- policy `D2C_MODEL_NEUTRAL_RUNTIME_IDENTITY_V1`;
- Python implementation and version;
- platform system and machine architecture;
- libc name and version;
- Qlib distribution and version;
- installed-distribution count;
- installed-distribution-set SHA-256.

Its canonical fingerprint is the common `runtime_environment_hash` for the tournament family.

This creates two simultaneous guarantees:

```text
candidate-specific D2C artifact hash
    -> proves the exact model/config/runner-associated attestation for that candidate

shared RuntimeEnvironmentIdentity hash
    -> proves candidates are compared under the same model-neutral software environment
```

The shared runner-code hash is enforced independently at candidate level. It is intentionally excluded from `RuntimeEnvironmentIdentity`, because it is not an installed-environment property.

A future execution stage must derive and verify the model-neutral identity from every concrete D2C attestation before it preregisters the real D2E campaign. D2E itself remains core-side and does not import the isolated lab.

## Fair-comparison constraints

All candidates in one D2E family must share:

- one Qlib version;
- one generalized runner code hash;
- one model-neutral `RuntimeEnvironmentIdentity` fingerprint;
- one source campaign;
- one frozen research-split hash;
- one source-universe hash;
- one label-definition hash;
- one DEVELOPMENT label artifact;
- one exact evaluation keyset;
- one canonical UTC DEVELOPMENT evaluation window.

Each candidate retains its own exact D2C attestation hash, model family, model-config hash and inference-request hash.

The evaluation window must use canonical `+00:00` UTC serialization. Naive timestamps, `Z` aliases and non-UTC offsets are rejected.

## Input rebinding

D2E does not trust an evaluation merely because it is an instance of the D2D artifact class. Before recording a candidate result it rebinds the D2D evaluation and D2A receipt to the preregistered candidate.

The checks include:

- source campaign, frozen split, universe and label definition;
- exact DEVELOPMENT label artifact;
- exact D2D evaluation keyset and D2A inference keyset;
- evaluation start/end;
- model family and model config;
- Qlib version;
- runner code hash;
- the candidate-specific D2C attestation hash;
- the common model-neutral runtime-environment hash through the frozen trial parameters;
- exact prediction-receipt fingerprint;
- exact prediction artifact hash and prediction manifest hash carried by that receipt;
- exact D2A request hash;
- exact prediction/evaluation observation count.

Any mismatch fails closed.

## Primary metric

The sole ranking metric is preregistered as:

```text
mean_cross_sectional_rank_ic / MAXIMIZE
```

D2E recomputes this value directly from the ordered D2D `CrossSectionalIC.spearman_ic` evidence before recording the trial. The declared D2D aggregate must equal that recomputation exactly.

This prevents an internally constructed or tampered D2D object from supplying an arbitrary aggregate ranking value while retaining otherwise self-consistent hashes.

Other D2D metrics — Pearson IC, global rank IC, MAE, RMSE, sign accuracy and mean cross-sectional Pearson IC — are recorded only as diagnostics. They cannot change tournament ranking.

## Exact common support

D2D can omit a timestamp when its cross-section is degenerate and correlation is undefined. Therefore two apparently comparable model evaluations could otherwise be ranked over different timestamp sets.

D2E hashes the canonical ordered set of cross-sectional timestamps for every completed candidate and requires the support hash and support count to be identical across all completed candidates before a tournament can be accepted.

No row trimming, intersection-after-results, nearest-time matching or opportunistic common-window selection is allowed.

## Sign evidence

For each candidate, D2E computes a one-sided exact sign test over the per-timestamp rank IC values:

```text
H0: P(rank_IC > 0) = 0.5
H1: P(rank_IC > 0) > 0.5
```

Zero rank-IC observations are excluded from the binomial count. If all observations are zero, the raw p-value is 1.0.

For `n` non-zero observations and `k` positives:

```text
p = P[X >= k],  X ~ Binomial(n, 0.5)
```

This test is deliberately simple, exact and independent of return/PnL assumptions. It does not establish economic profitability and does not model serial dependence; later robustness work can add block-based IC evidence without changing this preregistered ranking metric.

## Multiple-testing control

Every completed candidate records its raw sign-test p-value in the canonical trial ledger. Explicitly failed candidates remain part of the frozen family and receive p=1.0 through the existing campaign Holm evidence.

D2E then applies the existing Holm adjustment over the **entire preregistered family**.

Holm evidence is diagnostic. It does not replace or modify the primary tournament metric and cannot retrospectively change the candidate universe.

Deflated Sharpe and return-based PBO are intentionally not reused here because D2E ranks predictive rank IC, not a return series. Those tools remain appropriate in their own return/economic-validation layers.

## Deterministic tournament

D2E reuses the existing `TournamentSpec` and `evaluate_strategy_tournament` implementation. The frozen tournament:

- requires the full campaign to be terminal;
- rejects any campaign containing FINAL_HOLDOUT trials;
- requires its candidate IDs to equal the complete DEVELOPMENT universe;
- ranks `mean_cross_sectional_rank_ic` in MAXIMIZE direction;
- breaks exact metric ties only by immutable strategy/model and trial identity.

A failed candidate is ineligible but is not erased from family accounting.

## Evidence artifact

`OSS3D2ETournamentEvidence` binds:

- D2E plan fingerprint;
- common model-neutral `runtime_environment_hash`;
- canonical tournament evidence/fingerprint;
- Holm family evidence;
- family size;
- winner trial ID;
- winner primary metric;
- winner raw sign-test p-value;
- winner Holm-adjusted p-value;
- exact common cross-section support hash.

The artifact also encodes permanent negative authority:

```text
research_only = true
final_holdout_observed = false
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

A D2E winner is only a DEVELOPMENT research winner. It is not a promoted strategy, a PAPER candidate, an order source or evidence of future profitability.

## Failure semantics

D2E fails closed when, among other cases:

- the family is not preregistered;
- campaign accounting is incomplete;
- a candidate is outside the frozen universe;
- the same substantive model/request is duplicated under another ID;
- candidate Qlib or runner identities differ within a family;
- candidate Qlib identity differs from the frozen model-neutral runtime identity;
- completed trials do not retain the frozen common runtime-environment fingerprint;
- a candidate-specific D2C attestation hash does not match its D2D evaluation;
- D2A/D2D provenance differs from preregistration;
- the primary metric does not recompute from D2D cross-sections;
- completed candidates have different cross-sectional support;
- the tournament has no eligible completed winner;
- any evidence attempts to observe FINAL_HOLDOUT, promote, execute or grant capital/LIVE authority.

## What D2E does not claim

D2E does **not** prove alpha, future returns, capacity, implementation shortfall, robustness under costs, or live tradability. A model can have positive DEVELOPMENT rank IC and still fail every later economic or risk gate.

The appropriate next research stage after D2E certification is to extend the isolated Qlib runner with a small, predeclared finite model family and instantiate a D2E campaign **before** those DEVELOPMENT results are inspected. That stage must derive the common `RuntimeEnvironmentIdentity` from every concrete candidate D2C attestation and prove equality before comparison. FINAL_HOLDOUT remains unavailable until the winning candidate has been frozen through the appropriate one-shot holdout protocol.
