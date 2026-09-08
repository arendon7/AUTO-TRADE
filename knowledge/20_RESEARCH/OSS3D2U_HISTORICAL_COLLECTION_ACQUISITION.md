# OSS-3D2U — Frozen historical collection acquisition and partition assembly

Status: **research-only / stacked DRAFT boundary**.

OSS-3D2U connects the certified D2T immutable single-archive primitive to the certified D2R/D2S raw-market research chain.  D2T proves one provider archive.  D2U freezes a finite family of those archives **before network acquisition**, acquires each member through a tightly bounded public GET sequence, verifies every member through D2T, and stitches the resulting snapshots into explicit WARMUP, TRAIN and DEVELOPMENT material.

D2U does not open FINAL_HOLDOUT, does not derive labels, does not run Qlib, and does not evaluate profitability.  It creates auditable raw historical material only.

## Scientific purpose

Before D2U, AUTO-TRADE can prove:

```text
provider archive bytes -> D2T immutable MarketDataset
raw TRAIN universe      -> D2R TRAIN bundle
raw DEVELOPMENT universe-> D2S selection-safe DEVELOPMENT path
```

The missing link is collection-level provenance:

```text
Which provider files were selected?
Was that family frozen before download/results?
Were all symbols/months acquired?
Did any file change while it was being downloaded?
Were consecutive archives stitched without gaps/overlaps/fills?
Which exact raw bars became WARMUP, TRAIN and DEVELOPMENT?
```

D2U answers those questions without weakening D2T, D2R or D2S.

## Canonical v1 real-data family

D2U freezes the first substantial real historical research family as:

```text
provider   = Binance Spot public archive
symbols    = BTCUSDT, ETHUSDT, SOLUSDT
interval   = 1h
granularity= monthly
first month= 2023-01
last month = 2025-12
months     = 36
symbols    = 3
files      = 108
```

The canonical collection id is:

```text
oss3d2u-binance-spot-btc-eth-sol-1h-2023-2025-v1
```

This family is a **protocol choice**, not a result-selected family.  D2U does not claim that BTC/ETH/SOL is profitable, optimal or sufficient for production.  Changing the symbol set, interval, month range or partition dates after observing DEVELOPMENT results requires a new protocol/version rather than mutating this collection.

No 2026 archive descriptor is part of D2U v1.

## Partition geometry

The material period is:

```text
collection_start  = 2023-01-01T00:00:00Z
development_end   = 2026-01-01T00:00:00Z
```

At 1h resolution the first twenty bars are a dedicated causal warmup:

```text
WARMUP
2023-01-01 00:00 UTC
through
2023-01-01 19:00 UTC
```

TRAIN begins immediately after that warmup:

```text
TRAIN start = 2023-01-01T20:00:00Z
TRAIN end   = 2025-01-01T00:00:00Z  (exclusive)
```

DEVELOPMENT is the full calendar year 2025:

```text
DEVELOPMENT start = 2025-01-01T00:00:00Z
DEVELOPMENT end   = 2026-01-01T00:00:00Z  (exclusive)
```

The twenty-bar DEVELOPMENT warmup is not a new future partition.  It is the last twenty bars of TRAIN immediately preceding DEVELOPMENT.

D2U therefore materializes:

```text
training_warmup
training
development_warmup
development
```

and nothing else.

## Why FINAL_HOLDOUT is absent

D2U v1 intentionally sets:

```text
final_holdout_descriptors_included = false
final_holdout_values_loaded        = false
```

The collection ends exactly at DEVELOPMENT end.  This is stronger than downloading later data and promising not to use it: the later raw material simply is not part of the D2U plan.

Existing D2J/D2K one-shot holdout governance remains the authority for future holdout work.  A real holdout dataset must be introduced under a separate value-opaque commitment protocol, not smuggled into D2U.

## Finite plan before network

`HistoricalCollectionPlan` contains the complete ordered descriptor grid.

For every month and symbol there must be exactly one canonical D2T descriptor.  D2U rejects:

- a missing symbol-month;
- duplicate descriptor fingerprints;
- non-monthly archives in v1;
- interval drift;
- symbol drift;
- gaps between months;
- noncanonical ordering;
- an archive family extending past DEVELOPMENT end.

The plan fingerprint binds the complete family.

## Durable preregistration

Before any network call, the exact plan must be stored in:

```text
oss3d2u_historical_collection_plans
```

through `SQLiteHistoricalCollectionPlanRegistry`.

The table is append-only.  UPDATE and DELETE are rejected by SQLite triggers.

Repeated preregistration is idempotent only when:

```text
same collection_id
same plan fingerprint
same canonical JSON
```

A valid but different plan cannot reuse the collection id.

This gives D2U an auditable temporal statement:

```text
finite data family frozen durably
BEFORE
first provider GET
```

## Acquisition is a separate lab authority

The core module:

```text
src/autotrade/research/oss3_market_collection.py
```

has no network imports.

The only D2U network surface is:

```text
labs/oss3_market_data/archive_acquisition.py
```

It reuses the existing R3 public-data transport abstractions:

```text
ReadOnlyRequest
ReadOnlyHttpTransport
PublicDataPolicy
UrllibReadOnlyTransport
```

This reuse is intentional.  D2U does not need another generic HTTP client.

## Exact network allowlist

The acquisition transport is allowlisted to:

```text
https://data.binance.vision
```

and only the exact archive/checksum paths derived from the frozen D2U descriptors.

No API key is used.  No authenticated Binance endpoint is used.  No trading endpoint is present.

Even if a redirect target is another allowlisted D2U path, D2U rejects it.  Each response must satisfy:

```text
response.final_url == request.url
```

so the identity of one planned file cannot silently become another planned file.

## Triple-checksum acquisition protocol

A single descriptor acquisition attempt performs exactly:

```text
1. GET CHECKSUM A
2. GET ZIP
3. GET CHECKSUM B
```

Then:

```text
CHECKSUM_A_BYTES == CHECKSUM_B_BYTES
```

is required before D2T normalization.

The reason is provider mutability.  Binance's public-data documentation notes that archived files can be replaced when issues are corrected.  If a replacement happens during acquisition, a single checksum read could bind the wrong generation of the ZIP.

The second checksum creates a bounded consistency window:

```text
stable provider checksum
surrounding
one exact ZIP read
```

It is not a cryptographic proof that the provider can never change data; it is a fail-closed acquisition consistency test for the observed attempt.

## Zero implicit retries

One `acquire_preregistered_archive(...)` attempt contains exactly three transport sends and no retry loop.

The receipt records:

```text
request_count     = 3
retries_performed = 0
```

A caller may start a new explicit attempt after a transport failure.  D2U does not silently repeat a request after ambiguous I/O and then pretend the result came from one indivisible observation.

Because the operation is GET-only, this is primarily a provenance/reproducibility rule rather than a capital-safety rule.

## D2T is still the byte normalizer

After CHECKSUM A and B are proven identical, D2U does not reimplement ZIP/CSV logic.

It calls:

```text
build_binance_spot_archive_snapshot(...)
```

from certified D2T.

D2T then proves:

- provider SHA before ZIP parse;
- exact ZIP member;
- timestamp unit;
- exact monthly coverage;
- kline geometry;
- raw/canonical hashes;
- normalized `MarketDataset` identity.

D2U acquisition receipts bind the resulting D2T artifact hash and normalized dataset hash.

## Acquisition receipt

`ArchiveAcquisitionReceipt` binds:

- collection id;
- plan fingerprint;
- descriptor fingerprint;
- exact archive/checksum URLs;
- three HTTP statuses;
- checksum-A payload SHA;
- ZIP payload SHA;
- checksum-B payload SHA;
- D2T snapshot artifact hash;
- D2T normalized dataset hash;
- acquisition timestamp;
- exact acquisition-order policy;
- exact request policy;
- request count;
- retry count.

It explicitly states:

```text
network_used                  = true
provider_credentials_used     = false
trading_endpoints_used        = false
final_holdout_values_requested= false
execution_authorized          = false
paper_execution_authorized    = false
capital_authority             = NONE
live_trading                  = BLOCKED
```

Network use is acknowledged rather than hidden; network access is not capital authority.

## Raw-byte persistence

`AcquiredArchiveMaterial` retains:

```text
exact ZIP bytes
exact CHECKSUM text
D2T normalized snapshot
D2U acquisition receipt
```

`write(root)` atomically persists four files under a symbol/period directory:

```text
{symbol}/{month}/{provider ZIP}
{symbol}/{month}/{provider .CHECKSUM}
{symbol}/{month}/d2t-snapshot.json
{symbol}/{month}/d2u-acquisition-receipt.json
```

Temporary files are replaced atomically and should not remain after success.

These large raw archives are evidence material and are not intended to be committed to the source repository.

## Collection assembly

`assemble_historical_collection(...)` is offline.

It requires:

```text
exact complete D2T artifact family
exact descriptor fingerprints from plan
one acquisition receipt hash per descriptor
```

For each symbol, monthly snapshots are sorted according to the preregistered plan and stitched only when:

```text
next_dataset.started_at == previous_dataset.ended_at
```

No forward fill, interpolation, dropping or overlap resolution exists in D2U.

Each stitched `MarketDataset` must itself have no gaps.

## Cross-asset support

The stitched symbol datasets are converted into the existing `AlignedMarketUniverse`.

That certified primitive requires:

- canonical symbol order;
- one quote currency;
- one timeframe;
- identical bar count;
- identical timestamps;
- no gaps.

D2U therefore cannot silently retain one asset with a missing candle while the others continue.

## Partition material

After one exact full aligned universe exists, D2U slices by preregistered timestamps into:

```text
training_warmup       [collection_start, train_start)
training              [train_start, development_start)
development_warmup    final 20 TRAIN bars
development           [development_start, development_end)
```

`HistoricalResearchPartitionMaterial` rechecks:

- same symbols in all four universes;
- same timeframe;
- exact twenty-bar warmups;
- warmup immediately adjacent to each research partition;
- TRAIN immediately adjacent to DEVELOPMENT;
- all universe hashes equal the assembly evidence.

## Bridge to D2R and D2S

D2U outputs the exact domain types already expected downstream:

```text
D2U training_warmup + training
  -> RawTrainingMarketSource.build(...)
  -> D2R

D2U development_warmup + development
  -> RawDevelopmentMarketSource.build(...)
  -> D2S
```

No conversion to another dataframe/storage abstraction is needed between the provenance boundaries.

D2R remains responsible for causal TRAIN features and explicit TRAIN labels.  D2S remains responsible for causal DEVELOPMENT features and preventing the DEVELOPMENT label artifact from existing before candidate/statistical preregistration.

## Synthetic certification fixture

CI does not depend on Binance availability.

The D2U assembly suite builds a small deterministic monthly family:

```text
BTCUSDT / ETHUSDT / SOLUSDT
1d
2025-01 through 2025-03
```

It demonstrates:

```text
20 TRAIN warmup bars
39 TRAIN bars
20 DEVELOPMENT warmup bars
31 DEVELOPMENT bars
```

with exact month-to-month and cross-asset support.

The acquisition suite uses a scripted fake transport to prove network call ordering without internet access.

## Adversarial requirements

D2U tests/boundary must prove at least:

1. canonical v1 plan contains exactly 108 descriptors;
2. canonical v1 contains no 2026 archive descriptor;
3. symbol-month grid must be complete;
4. months must be contiguous;
5. plan registry is append-only;
6. valid conflicting plan cannot reuse collection id;
7. acquisition before durable plan makes zero requests;
8. unplanned descriptor makes zero requests;
9. successful attempt calls CHECKSUM/ZIP/CHECKSUM exactly once each;
10. checksum A/B change fails;
11. redirect fails even to another allowlisted path;
12. stable checksums with corrupted ZIP fail D2T SHA verification;
13. raw and normalized evidence persists atomically;
14. missing snapshot fails collection assembly;
15. missing receipt fails collection assembly;
16. foreign descriptor artifact fails assembly;
17. cross-file gap/overlap fails;
18. aligned universe requires exact cross-asset support;
19. FINAL_HOLDOUT/labels/predictions remain absent;
20. execution/PAPER/capital/LIVE mutations remain impossible.

## Static authority boundary

`scripts/check_oss3d2u_historical_collection_acquisition_boundary.py` proves separately that:

### Core

- has no HTTP/socket/subprocess imports;
- has no R3 external-data transport import;
- has no Qlib/model/evaluator import;
- has no supervised-label import;
- has no holdout evaluator import;
- has no broker/OMS/Safety/OrderIntent import;
- requires complete contiguous monthly grid;
- uses append-only SQLite plan gate;
- stitches no-gap/no-overlap;
- materializes DEVELOPMENT warmup only from prior TRAIN bars.

### Acquisition lab

- reuses only the allowlisted public GET transport types;
- has no alternate HTTP client;
- calls durable `require_exact(plan)` first;
- checks descriptor membership before network;
- performs exactly three sends;
- orders CHECKSUM A -> ZIP -> CHECKSUM B -> D2T;
- contains no retry loop;
- rejects final-URL changes;
- has no authenticated/trading endpoint;
- has no Qlib/labels/broker/OMS/Safety authority.

## Authority

D2U is allowed to acquire **public historical market data** under the frozen plan.  That is its only new authority.

It is denied:

```text
API-key access
private account data
trading endpoints
FINAL_HOLDOUT material
label materialization
prediction generation
model fitting
metric computation
strategy promotion
PAPER execution
capital authority
LIVE trading
```

## Next step after D2U certification

After the D2U code/contract is certified, the next operation should be a controlled **real acquisition campaign** for the frozen 108-descriptor family.

That campaign should:

1. preregister the canonical D2U plan durably;
2. acquire every descriptor through the D2U triple-checksum adapter;
3. persist raw ZIP/CHECKSUM files plus receipts outside git;
4. assemble and verify the full collection;
5. bind D2U material into D2R and D2S;
6. run the already-frozen six-model DEVELOPMENT tournament on the real historical material.

Only after that real DEVELOPMENT result should we decide whether the existing FINAL_HOLDOUT protocol receives a separately committed real holdout.  D2U itself does not provide or authorize it.
