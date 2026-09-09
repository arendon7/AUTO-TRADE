# OSS-3D2X — Post-Outage Continuous Historical Research Window

## Status

D2X re-preregisters the D2U historical root after the first controlled real-provider execution revealed a genuine Binance Spot outage inside the original March 2023 monthly archives.

This is a **data-integrity correction**, not model tuning, metric tuning, candidate reselection, holdout observation or economic optimization.

## Evidence that triggered D2X

The original D2U V1 canonical family contained 108 monthly descriptors:

- BTCUSDT, ETHUSDT, SOLUSDT;
- Binance Spot;
- 1h klines;
- 2023-01 through 2025-12.

A provider scan executed every descriptor through the certified D2T immutable-snapshot constructor. Results:

- 108 descriptors scanned;
- 105 passed D2T exactly;
- 3 failed;
- the only failing files were BTCUSDT, ETHUSDT and SOLUSDT for 2023-03;
- all three failed at row 564 on the same market interruption.

Provider archive SHA-256 values observed in the full scan:

- BTCUSDT 2023-03: `7f2afb8e0179a57ac31eab5205660298ba5eb77039ac2e21aef9b715ff3d06ce`
- ETHUSDT 2023-03: `90d268be1e0d39f7411f88ca3c0f21fe110fee7ff3c1dd054555e1fea6e22d8d`
- SOLUSDT 2023-03: `7345fc8807e3e73e11595b5b371bf96d414048f77d1498964fa5c5f8fe3f02e7`

At row 564, all three assets open at `1679659200000` (2023-03-24 12:00 UTC), then close early at approximately 12:39:41 UTC with flat OHLC and zero volume. The next available kline opens at `1679666400000` (14:00 UTC). There is no 13:00 UTC bar.

This is consistent with Binance's public record that Spot trading was suspended on 24 March 2023 because of a matching-engine issue and resumed at 14:00 UTC.

## Scientific decision

D2T remains unchanged and strict.

D2X explicitly rejects these alternatives:

- synthesize a 13:00 bar;
- forward-fill OHLC;
- create a zero-volume synthetic hour;
- interpolate prices;
- relax fixed-kline geometry;
- permit a cross-file gap;
- silently delete only the affected row and stitch across it.

The research contract requires continuous hourly support. Therefore the canonical research window is moved wholly after the outage.

## Canonical D2U V2 family

The new finite preregistered family is:

- provider: Binance public Spot monthly archive;
- symbols: BTCUSDT, ETHUSDT, SOLUSDT;
- interval: 1h;
- first month: 2023-04;
- last month: 2025-12;
- months: 33;
- descriptors: 99;
- collection start: `2023-04-01T00:00:00+00:00`;
- exact warmup: first 20 hourly bars;
- TRAIN start: `2023-04-01T20:00:00+00:00`;
- DEVELOPMENT start: `2025-01-01T00:00:00+00:00`;
- DEVELOPMENT end: `2026-01-01T00:00:00+00:00`.

Canonical collection id:

`oss3d2u-binance-spot-btc-eth-sol-1h-2023apr-2025-v2`

Canonical plan fingerprint:

`f43abdcd532f07d37b54f93abafbd3b29a3c3ee07afeedbae95b7aa15153c59d`

## Versioned contracts

D2X advances the D2U identities to V2:

- `OSS3D2U_HISTORICAL_COLLECTION_PLAN_V2`
- `OSS3D2U_ACQUIRED_SNAPSHOT_BINDING_V2`
- `OSS3D2U_HISTORICAL_COLLECTION_ASSEMBLY_EVIDENCE_V2`
- `OSS3D2U_HISTORICAL_RESEARCH_PARTITION_MATERIAL_V2`

Policies remain fail-closed and explicitly post-outage:

- finite family preregistered before network;
- exact month-to-month no-gap/no-overlap/no-fill;
- warmup → TRAIN → DEVELOPMENT only;
- exact D2T artifact + acquisition receipt per descriptor.

## What did not change

D2X does not change:

- D2T immutable snapshot semantics;
- BTC/ETH/SOL universe;
- 1h timeframe;
- DEVELOPMENT 2025 period;
- feature formulas;
- label formula;
- six-candidate D2F family;
- D2E metric or multiple-testing policy;
- D2I winner semantics;
- FINAL_HOLDOUT protocol;
- economic-holdout protocol;
- broker, OMS or Safety behavior.

No DEVELOPMENT labels were used to choose the new start date. The change is caused solely by verified raw-provider continuity.

## Authority

D2X has no model-execution or trading authority.

- FINAL_HOLDOUT values: not loaded
- DEVELOPMENT metrics: not computed by D2X
- Qlib: not required
- promotion: not authorized
- PAPER execution: not authorized
- capital authority: `NONE`
- LIVE trading: `BLOCKED`

## Acceptance gates

D2X is acceptable only if CI proves all of the following:

1. canonical plan has exactly 99 descriptors;
2. first period is 2023-04 and last is 2025-12;
3. 2023-03 is absent;
4. TRAIN starts 2023-04-01 20:00 UTC after exactly 20 warmup bars;
5. DEVELOPMENT remains 2025-01-01 through 2026-01-01;
6. no fill/interpolation/imputation surface exists;
7. D2T production implementation is byte-identical to the D2W certified base;
8. D2U, D2V and D2W regressions pass;
9. Research Authority remains fail-closed;
10. Qlib remains absent from this boundary.
