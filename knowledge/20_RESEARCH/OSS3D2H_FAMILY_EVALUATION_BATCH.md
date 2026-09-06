# OSS-3D2H — Preregistered family evaluation batch

## Scope

OSS-3D2H is a DEVELOPMENT-only orchestration and evidence layer. It joins the already-certified research stages without adding a new model family or a new optimizer:

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

The central invariant is that the model universe and ranking policy are durable before DEVELOPMENT label values become semantically active.

`prepare_family_evaluation_preregistration()` may inspect only DEVELOPMENT label identity and support required to freeze the experiment: artifact hash, manifest identity, partition bounds, row count and `(label_as_of, symbol)` keys. It must not inspect `row.value` or compute DEVELOPMENT metrics.

`preregister_family_evaluation()` durably creates the complete D2E campaign and all six preregistered trial specs.

`evaluate_preregistered_family()` first revalidates the frozen D2G outputs, exact label artifact and untouched durable preregistration. Only after that check does it invoke OSS-3D2D, where label values are allowed for predictive evaluation.

## Atomicity before ledger writes

All six D2D evaluation artifacts are built in memory before the first terminal trial write. An invalid candidate/label pair therefore fails before any partial family result is recorded.

After all six D2D artifacts exist, D2H records them against the six preregistered D2E trials and invokes the D2E tournament. Re-running the batch against an already-terminalized ledger is rejected.

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

D2H has no broker, OMS, Safety, OrderIntent or network authority. Qlib execution belongs to D2G; D2H only consumes already-frozen D2G evidence, although its dedicated integration test executes the full six-model chain to prove compatibility.

## CI certification intent

The dedicated D2H workflow installs exactly `pyqlib==0.9.7`, executes the six preregistered models once, performs durable preregistration, evaluates all six predictions through D2D, completes the D2E tournament, and then re-runs D2G/D2E/D2D/D2A/D2F, Research Authority, W83 and the protected FINAL_HOLDOUT boundary.

Core Safety must independently remain above the existing 85% branch-aware coverage floor. The floor must never be reduced to certify D2H.

## Interpretation

A successful D2H tournament is DEVELOPMENT research evidence only. A DEVELOPMENT winner is not a trading strategy promotion, does not prove future alpha or profitability, and has no PAPER/LIVE or capital authority. FINAL_HOLDOUT remains a later, separately governed one-shot frontier.
