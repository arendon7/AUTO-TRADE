# R4 — Portfolio Dependence precertification

Status: **NOT YET DEBT-CLOSED**

Candidate scope for `TD-R4-002`:
- calibration phase is structurally limited to TRAIN or DEVELOPMENT; FINAL_HOLDOUT cannot be represented;
- all pair correlations in one evidence object use one identical common timestamp intersection;
- insufficient common observations and zero variance fail closed;
- deterministic Decimal Pearson dependence evidence with immutable fingerprints;
- absolute-correlation clusters are deterministic connected components over the complete strategy universe;
- proposed strategy allocations must exactly match the evidence universe;
- explicit per-strategy, correlated-cluster and total allocation budgets accept exact limits and reject +epsilon;
- negative, non-Decimal and non-finite weights fail closed;
- insufficient dependence evidence cannot produce allocation-budget approval;
- this module validates proposed weights only; it does not calculate sizes, promote strategies, submit orders or create broker/OMS authority.

`TD-R4-002` remains OPEN until full CI passes and a final slice certificate is written.

**LIVE TRADING: BLOCKED.**
