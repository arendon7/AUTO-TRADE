# OSS-3D2H — Preregistered family evaluation batch

## Scope

OSS-3D2H is a DEVELOPMENT-only orchestration and evidence layer. It joins the already-certified research stages without adding a new model family, optimizer or trading authority:

```text
OSS-3D2F frozen six-model family
        ↓
OSS-3D2G six frozen prediction/receipt/attestation outputs
        ↓
D2H prepare phase: identity-only label binding + D2E plan construction
        ↓
durable D2E preregistration in SQLiteTrialLedger
        ↓
OSS-3D2D DEVELOPMENT evaluation for all six frozen predictions
        ↓
OSS-3D2E preregistered tournament + exact sign-test p-values + Holm
```

FINAL_HOLDOUT is not an input to D2H.

## Scientific sequencing

The central invariant is that the complete model universe, ranking policy and exact prediction evidence are durable before DEVELOPMENT label values become semantically active.

`prepare_family_evaluation_preregistration()` may inspect only DEVELOPMENT label identity and support required to freeze the experiment: artifact hash, manifest identity, partition bounds, row count and `(label_as_of, symbol)` keys. It must not inspect `row.value` or compute DEVELOPMENT metrics.

`preregister_family_evaluation()` durably creates the complete D2E campaign and all six preregistered trial specs.

`evaluate_preregistered_family()` first revalidates the frozen D2G outputs, exact label artifact and untouched durable preregistration. Only after those checks does it invoke OSS-3D2D, where label values are allowed for predictive evaluation.

## Immutable D2G preregistration bindings

D2H does not store mutable dictionaries as the canonical candidate binding. Each frozen D2G output is projected into `FrozenCandidateOutputBinding`, a frozen typed contract that binds:

- candidate id;
- D2A request hash;
- OSS-3A prediction artifact hash;
- D2A receipt fingerprint;
- candidate environment-attestation hash;
- model-neutral runtime-environment hash;
- D2G run-evidence fingerprint;
- model-config hash;
- shared D2G semantic runner hash.

The preregistration contains exactly six immutable bindings in canonical candidate order. Replacing, omitting or changing any output after preregistration fails closed.

## Prevalidation before ledger writes

All six D2D evaluation artifacts are built in memory before the first terminal trial write. An invalid candidate/label pair therefore fails before any family result is terminalized.

After all six D2D artifacts exist, D2H records them against the six preregistered D2E trials and invokes the D2E tournament. Re-running a completed batch against an already-terminalized campaign is rejected; D2H does not silently overwrite or reinterpret an existing experiment.

## Exact family and provenance

D2H accepts only the complete canonical D2F family. For every candidate it rebinds:

- D2F candidate id and model-config hash;
- D2A request hash;
- OSS-3A prediction artifact;
- D2A prediction receipt;
- D2G candidate environment attestation;
- D2E model-neutral runtime environment identity;
- D2G run evidence;
- shared D2G semantic runner hash;
- exact DEVELOPMENT keyset.

No missing candidate, substitute request, runtime drift, label artifact substitution or post-preregistration universe change is accepted.

## Evaluation policy

D2H delegates predictive metrics to certified OSS-3D2D and selection/multiplicity control to certified OSS-3D2E. It does not introduce a second ranking rule.

D2E remains frozen to:

```text
primary_metric = mean_cross_sectional_rank_ic
mode = MAXIMIZE
multiple_testing = one-sided exact sign test + Holm
```

PnL, Sharpe, Sortino, CAGR, portfolio returns and trading profitability are outside D2H.

## Execution boundary

D2H is research only:

```text
final_holdout_observed = false
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

D2H has no Qlib import, broker, OMS, Safety, OrderIntent or network authority. Qlib execution belongs exclusively to D2G; D2H consumes already-frozen D2G evidence.

## CI certification intent

The dedicated D2H workflow intentionally proves two separate boundaries:

1. **Before Qlib is installed**, D2H deterministic/adversarial tests prove exact-six-family preregistration, immutable bindings, durable-before-metrics sequencing, label-artifact immutability, full-family prevalidation and authority denial.
2. **Afterwards**, CI installs exactly `pyqlib==0.9.7` and re-runs the D2G six-model real-runtime suite to prove that the upstream outputs consumed by D2H remain executable under the certified isolated runtime.

The workflow then re-proves D2G, D2E, D2D, D2A, D2F, artifact lineage, Research Authority, W83 and the protected one-shot FINAL_HOLDOUT boundary.

Core Safety must independently remain above the existing branch-aware coverage floor. The floor must never be reduced to certify D2H.

## Interpretation

A successful D2H tournament is DEVELOPMENT research evidence only. A DEVELOPMENT winner is not a trading-strategy promotion, does not prove future alpha or profitability, and has no PAPER/LIVE or capital authority. FINAL_HOLDOUT remains a later, separately governed one-shot frontier.
