# OSS-3D2I — DEVELOPMENT winner selection seal

## Scope

OSS-3D2I freezes the exact winner already produced by the preregistered OSS-3D2E DEVELOPMENT tournament. It is a research-lineage seal only.

```text
D2H completed family evidence
        ↓
D2E preregistered ranking winner
        ↓
D2I immutable winner seal
        ↓
future D2J protocol preregistration only
```

D2I does not access, authorize or consume FINAL_HOLDOUT.

## Meaning of winner

`winner` means only the candidate ranked first by the D2E primary metric:

```text
mean_cross_sectional_rank_ic / MAXIMIZE
```

D2I deliberately does not reinterpret this as:

- proof of statistical significance;
- proof of alpha;
- proof of profitability;
- authorization to retune or reselect;
- authorization to consume FINAL_HOLDOUT;
- promotion to PAPER or LIVE.

The raw exact-sign-test p-value and Holm-adjusted p-value are copied into the seal as evidence. They are not converted into a new post-hoc significance threshold.

## Exact immutable lineage

The seal rebinds the winner across the full D2H chain:

- D2H preregistration fingerprint;
- D2H batch-evidence fingerprint;
- D2E plan fingerprint;
- D2E tournament-evidence fingerprint;
- winner trial and hypothesis ids;
- model family and model-config hash;
- D2A request hash;
- OSS-3A prediction artifact hash;
- D2A prediction receipt hash;
- D2G environment attestation hash;
- D2G run-evidence hash;
- D2D evaluation artifact hash;
- shared D2G semantic runner hash;
- model-neutral runtime environment hash;
- D2E primary metric and winner value;
- raw and Holm-adjusted p-values.

Any mismatch fails closed.

## Freeze semantics

The canonical seal permanently records:

```text
selection_scope = DEVELOPMENT_RANKING_WINNER_ONLY
next_frontier = OSS3D2J_PROTOCOL_PREREGISTRATION_ONLY
statistical_significance_claim_authorized = false
alpha_claim_authorized = false
profitability_claim_authorized = false
reselection_allowed = false
retuning_allowed = false
final_holdout_observed = false
final_holdout_authorized = false
holdout_permit_consumed = false
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

This makes the selected DEVELOPMENT candidate immutable before a later protocol can even discuss FINAL_HOLDOUT.

## Relationship with existing one-shot FINAL_HOLDOUT governance

AUTO-TRADE already has durable one-use `final_validation` permits and OSS-2H consumes them before protected holdout checkout. D2I intentionally imports none of those APIs.

D2I cannot create a permit, consume a permit, inspect a protected holdout universe or call the OSS-2H evaluator. The next stage, D2J, should preregister an OSS-3-specific final-validation protocol around this exact sealed candidate before any holdout authorization can exist.

That later protocol should remain separate from the evaluator itself so policy/gates are frozen before a one-use permit is consumed.

## Statistical interpretation

D2E applies the preregistered exact one-sided sign test and Holm family correction over the frozen family. D2I preserves those numbers verbatim.

D2I does not add a post-hoc `p < 0.05` rule. If a future protocol requires a statistical gate, that gate must be preregistered in the next protocol before FINAL_HOLDOUT consumption rather than inferred retroactively from D2E results.

## Authority boundary

D2I contains no:

- Qlib runtime;
- pandas/numpy/scipy/sklearn runtime;
- broker or exchange client;
- OMS or Safety authority;
- `OrderIntent`;
- holdout permit API;
- protected holdout checkout;
- PAPER/LIVE promotion path.

A D2I seal is therefore evidence of exact DEVELOPMENT selection only.

## Next stage

OSS-3D2J should bind exactly one D2I seal and preregister the complete final-validation protocol without viewing FINAL_HOLDOUT. It should define the protected artifact identity, immutable evaluation gates, one-shot authorization identity and terminal no-reselection/no-retuning semantics.

Only a later evaluator should be allowed to consume that authorization and expose FINAL_HOLDOUT once.
