# TAREA ACTIVA

## Track
**R4 — Portfolio / Regime / Health Governance**

R4 development starts only from R3-integrated, post-merge-green `main`. Until PR #10 is merged and its merge SHA recertified, R4 is the next track but not yet allowed to mutate the certified base.

## Objetivo
Reconstruct the historical R4 portfolio/health capabilities and strengthen them without creating PAPER/LIVE authority. Existing R0/R2 portfolio persistence/reconciliation is infrastructure to reuse, not evidence that R4 is already complete.

## Workstreams R4

### A. Authoritative Instrument Master — `TD-R4-001` P1
1. Define a versioned instrument contract independent from research-only serialization metadata.
2. Persist authoritative symbol/venue/quote currency, price tick, quantity step, min/max quantity, min/max notional and trading status.
3. Every record must carry source/version/as-of provenance and deterministic fingerprint.
4. Unknown/stale/conflicting metadata fails closed.
5. Research `1E-8` serialization precision from the R3 campaign must never be accepted as exchange execution rules.

### B. Portfolio State invariants + correlation-aware research
1. Audit existing versioned Portfolio State and reconciliation invariants rather than rewrite them.
2. Add deterministic portfolio research inputs for strategy return series/exposure history with canonical hashes.
3. Compute correlation/dependence evidence only on authorized TRAIN/DEVELOPMENT data.
4. Define diversification/concentration constraints and cross-strategy exposure budgets.
5. Missing/insufficient/unstable dependence evidence must not silently increase allocation.

### C. Allocation robustness
1. Implement deterministic allocation perturbation tests.
2. Implement leave-one-strategy-out / leave-one-component-out analysis.
3. Measure concentration sensitivity and portfolio degradation under plausible weight perturbations.
4. Record immutable robustness evidence tied to strategy/dataset/config hashes.
5. A fragile optimum cannot be represented as robust merely because its point estimate is best.

### D. TRAIN-calibrated regimes
1. Define market/regime features using only data available at each timestamp.
2. Calibrate thresholds exclusively on TRAIN or explicitly authorized development folds.
3. HOLDOUT may evaluate a frozen regime model but may not alter its thresholds.
4. Regime labels/thresholds/configuration must be hash-bound and immutable for evaluation.
5. Missing/stale regime input => conservative/unknown state, never optimistic inference.

### E. Strategy + Portfolio Health & Drift
1. Define immutable baseline evidence for each strategy and portfolio.
2. Measure distribution/performance/execution-assumption drift using deterministic policies.
3. Separate informative warnings from defensive thresholds.
4. Define health states and transition evidence; repeated runs over identical evidence are idempotent.
5. Retirement/quarantine criteria must be explicit, auditable and reversible only by policy.

### F. Defensive Health Bridge
1. Automated health actions may **reduce, block or quarantine** new risk only.
2. Health logic cannot increase allocation, reactivate a retired strategy or bypass Safety Kernel/OMS.
3. Defensive state survives restart and concurrency.
4. Recovery requires explicit acknowledgement/policy and fresh qualifying evidence.
5. Ambiguous health state chooses the stricter defensive state.

### G. Deterministic Portfolio Manager / sizing
1. Convert approved strategy research evidence into bounded candidate weights/sizes, not executable broker authority.
2. Enforce per-strategy, correlated-cluster and portfolio budgets.
3. Respect current portfolio/reservation/risk state and authoritative instrument constraints.
4. No optimization result may bypass Capital Safety Kernel or OMS.
5. Portfolio Manager output remains advisory/control-plane input until later R5/R6 promotion gates exist.

## Negative tests obligatorios R4
- unknown/stale/conflicting instrument metadata => reject/fail closed;
- invalid tick/step/min/max/notional rules => reject;
- research precision cannot masquerade as authoritative venue filters;
- exact concentration/correlation budget boundary and +epsilon;
- missing or insufficient correlation history cannot increase risk;
- perturbation and leave-one-out evidence is deterministic/reproducible;
- HOLDOUT-derived regime recalibration is impossible;
- look-ahead regime feature/threshold attempt fails;
- baseline hash mismatch or drift-evidence conflict fails closed;
- identical health evidence cannot create duplicate transitions;
- DEGRADED/QUARANTINED/RETIRED cannot auto-recover without required acknowledgement/evidence;
- Defensive Health Bridge cannot increase size/exposure;
- portfolio sizing cannot exceed strategy/cluster/portfolio budgets;
- concurrent defensive updates preserve the stricter state;
- R4 introduces no external PAPER/LIVE order submission.

## Definition of Done R4
- Every historical R4 row in `RECONSTRUCTION_V028R_MATRIX.md` is PASS.
- `TD-R4-001` and every newly discovered R4 P0/P1/P2 is CLOSED with concrete evidence.
- Portfolio/regime/health contracts are deterministic, versioned and fail-closed.
- Allocation robustness and no-HOLDOUT-leakage evidence exists.
- Defensive bridge is reduce/block-only with explicit recovery semantics.
- Tests + branch coverage >=85% and all CI governance gates PASS.
- Canon/matrix/debt/handoff synchronized.
- R4 merge SHA recertified on `main` before R5 begins.
- **LIVE TRADING remains blocked.**
