# OSS-3D3F — Protected dual-holdout materialization

## Purpose

D3F is the first stage allowed to deterministically derive the **protected material identity** of the two D3D-reserved 2026 holdouts from certified raw market evidence. It does not evaluate either holdout.

Ordering: D3D freezes Q1/Q2 geometry → D3E acquires/seals the exact 18 descriptors → D3F fully re-verifies D3E offline and derives only value-opaque commitments → later D2J/D2L/D2M may preregister → only the existing one-shot evaluators may later consume protected material after durable authorization.

## Certified source

D3F is anchored to D3E branch head `129e546cc09257b11dfe6e96915d370b91126a3f`, complete campaign seal `8ce57f1e6a6ce6999d8599af7df38b5b06662834739aa175cadd672c21eed105`, and D3D plan `d6dc987cc2053a6617796fdb89df9467c0895a090c5745c91b157edc5d7a4af2`.

The machine-readable source facts live in `OSS3D3E_CERTIFIED_BASELINE_129e546c.json`.

## Frozen real identities

A complete real-data discovery replay on head `56ca1f9450e2a5f005d9c8fd49e4f8d49d5f13c1` produced the following deterministic roots. They are now frozen in `OSS3D3F_FROZEN_IDENTITIES.json` and every certification replay must reproduce them exactly:

- D3F public evidence: `a9681ff31f4d74176d072c2076d129516d8db19cfb8aea093432ec6f7ac9d5b6`;
- Q1 predictive D2J commitment: `a6fef49419ef22b75be45385a805f8f3242653a3fcb74cb708be6548ac865bde`;
- Q2 economic D2M commitment: `349e277981ff08a4918eea68dd8fd86c745c2669f188b55727ac1b0ebfd70369`.

These hashes bind the protected values without disclosing them. Freezing a commitment is not observing a holdout and does not issue or consume any holdout permit.

## Predictive Q1 material

Q1 is the D2J/D2K predictive `FINAL_HOLDOUT`:

- BTCUSDT, ETHUSDT, SOLUSDT;
- Binance Spot, 1h;
- `[2026-01-01T00:00:00Z, 2026-04-01T00:00:00Z)`;
- 2,160 raw bars per symbol;
- 2,159 predictive cross-sections;
- 6,477 feature/label observations.

The frozen model family requires twenty causal lookback bars. D3F does not discard the first twenty hours of Q1. It recovers the exact final twenty certified December 2025 bars from D2Y/D2Z and uses them solely as pre-Q1 warmup.

Feature semantics remain `momentum_20` and `volatility_20`; the target remains the frozen one-bar forward simple close return. Feature and label rows are generated in process memory and are never written to Git, Actions artifacts, stdout or the public D3F evidence file. D3F emits only exact D2J-compatible commitment hashes and structural counts.

`label_values_exposed=false` and `final_holdout_observed=false` remain mandatory.

## Economic Q2 material

Q2 is reserved for D2M/D2N economic qualification:

- BTCUSDT, ETHUSDT, SOLUSDT;
- 1h;
- `[2026-04-01T00:00:00Z, 2026-07-01T00:00:00Z)`;
- 2,184 raw bars per symbol.

D3F constructs only the existing `EconomicHoldoutCommitment`: universe hash, dataset-set hash, symbols, quote currency, timeframe and exact half-open partition geometry. It does not create economic predictions, target weights, fills, PnL or economic metrics.

## Temporal separation

D3D froze Q1 and Q2 as adjacent **half-open** windows. The canonical boundary is valid when `Q1.partition_end == Q2.partition_start`; overlap exists only when `Q2.partition_start < Q1.partition_end`. D3F enforces that interpretation and requires exact contiguity for the canonical pair.

### Known downstream compatibility item

The existing D2N helper `_verify_temporal_separation` currently rejects `economic_start <= predictive_end`. That predates the D3D half-open reservation and would reject the exact D3D boundary despite no shared bar. D3F does not weaken or bypass D2N. D2N must receive a dedicated, test-covered compatibility correction from `<=` to `<` before any D2N authorization or economic checkout is attempted.

## Authority boundary

D3F grants no D2J/D2M registration, holdout permit, Qlib execution, predictive/economic metrics, profitability claim, promotion, PAPER execution, capital authority or LIVE trading. Permanent state remains `capital_authority = NONE` and `live_trading = BLOCKED`.
