# OSS-2E — Preregistered FINAL_HOLDOUT eligibility policy

Status: DEVELOPMENT-only preregistration.

Base evidence authority: certified OSS-2D only.

This policy is frozen before any FINAL_HOLDOUT observation. It decides only whether the already-selected OSS-2 candidate may consume one future FINAL_HOLDOUT evaluation under a separate boundary. It does not authorize PAPER, LIVE, broker writes, OMS handoff, capital allocation, OrderIntent construction, or network access.

## Hard gates

Every gate must pass. There is no weighted score and no discretionary override.

1. PBO <= 0.35.
2. Deflated Sharpe probability >= 0.80.
3. Moving-block bootstrap probability positive >= 0.60.
4. Moving-block bootstrap median compounded return >= 0.00.
5. Moving-block bootstrap lower compounded return >= -0.10.
6. 1.5x cost-stress common-window net return >= 0.00.
7. 1.5x cost-stress common-window Sharpe >= 0.00.
8. 1.5x cost-stress max drawdown <= 0.35.
9. 2.0x cost-stress common-window net return >= 0.00.
10. 2.0x cost-stress common-window Sharpe >= 0.00.
11. 2.0x cost-stress max drawdown <= 0.35.
12. Local-neighbor median Sharpe >= 0.00.
13. Fraction of local neighbors not exceeding the selected candidate >= 0.50.
14. Selected Sharpe minus local-neighbor median >= -0.25.

## Required OSS-2D provenance

OSS-2E fails closed unless the input package preserves the certified OSS-2D contract:

- canonical OSS-2D policy fingerprint;
- 8 balanced PBO partitions and 70 CSCV orientations;
- Deflated Sharpe over exactly 12 frozen candidates using `common_window_sharpe`;
- 2,000 moving-block bootstrap iterations, block size 4, seed 20260904;
- exact sample-size binding between bootstrap and DSR;
- exact 1.5x and 2.0x cost-stress evidence;
- complete local-sensitivity evidence with at least two preregistered neighbors.

## Decision semantics

- `HOLDOUT_ELIGIBLE`: all 14 gates pass. This freezes the exact candidate plus OSS-2D evidence plus OSS-2E policy fingerprint for one future holdout boundary.
- `REJECT`: one or more gates fail. FINAL_HOLDOUT must remain unopened for this candidate.

Neither decision contains or accepts FINAL_HOLDOUT observations. `HOLDOUT_ELIGIBLE` is not a profitability claim and is not a capital or execution authorization.
