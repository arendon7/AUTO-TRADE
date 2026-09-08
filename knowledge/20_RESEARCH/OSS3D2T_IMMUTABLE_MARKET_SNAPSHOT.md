# OSS-3D2T — Immutable historical market-data snapshot

Status: **research-only / stacked DRAFT boundary**.

OSS-3D2T closes the next provenance gap after D2R/D2S.  D2R and D2S can deterministically derive TRAIN and DEVELOPMENT research artifacts from raw OHLCV, but those boundaries intentionally begin with an in-memory `AlignedMarketUniverse`.  D2T moves one level closer to the external source: it proves how an already-downloaded Binance Spot public-data archive becomes the exact `MarketDataset` material later consumed by AUTO-TRADE.

D2T is deliberately **offline**.  It receives the exact provider ZIP bytes plus the provider `.CHECKSUM` text as inputs.  It does not download either file, does not use API keys, does not call the Binance REST API, does not run Qlib, and cannot create labels, metrics, orders or execution authority.

## Why D2T exists

R3 already provides a bounded GET-only Binance `/api/v3/klines` ingestion path and an `ExternalDatasetArtifact`.  That remains useful for read-only API intake and reproducibility checks.

D2T addresses a different scientific requirement: a durable historical snapshot whose lineage begins with the provider's downloadable archive itself.

The provider's public-data repository documents that:

- Spot data is published as daily or monthly ZIP archives;
- kline files use the `/data/spot/{daily|monthly}/klines/...` hierarchy;
- each ZIP has a sibling `.CHECKSUM` file containing the SHA-256 used for integrity verification;
- Spot archive timestamps from **2025-01-01 onward are in microseconds**;
- older Spot archive timestamps are in milliseconds;
- archived files may later be replaced to correct discovered issues.

That last property is important: a URL is not an immutable scientific identity.  D2T therefore binds the **exact bytes observed at acquisition time**, not merely the provider URL.

Reference upstream documentation:

```text
https://github.com/binance/binance-public-data
https://data.binance.vision
```

## Boundary placement

```text
provider archive acquisition                  D2T normalizer
(outside D2T; future D2U)                     (offline)

ZIP bytes + .CHECKSUM text
        |
        v
provider SHA-256 validation
        |
        v
strict one-member ZIP validation
        |
        v
exact CSV byte hash
        |
        v
provider kline schema/timestamp validation
        |
        v
canonical UTC OHLCV rows
        |
        v
MarketDataset
        |
        v
HistoricalMarketSnapshotArtifact
```

D2T intentionally does **not** perform:

```text
network acquisition
cross-file stitching
TRAIN/DEVELOPMENT split assignment
feature derivation
label derivation
model fitting
prediction
metrics
FINAL_HOLDOUT evaluation
economic simulation
broker/PAPER/LIVE execution
```

Those belong to different boundaries.

## Existing R3 compatibility

D2T does not replace `src/autotrade/research/external_data.py`.

It reuses only the canonical fixed-interval registry:

```text
FIXED_INTERVAL_MS
```

No R3 transport/provider class is imported.  In particular D2T cannot import or instantiate:

```text
BinanceSpotHistoricalProvider
UrllibReadOnlyTransport
ReadOnlyHttpTransport
```

Both R3 and D2T converge on the same domain type:

```text
MarketDataset
```

This keeps downstream research independent of whether the raw material arrived through a bounded API campaign or an immutable archive snapshot.

## Canonical provider descriptor

`BinanceSpotArchiveDescriptor` freezes the provider-facing identity before parsing any bytes:

- provider: `BINANCE_SPOT_PUBLIC_DATA_ARCHIVE`;
- market: `SPOT`;
- data kind: `klines`;
- symbol;
- interval;
- daily/monthly granularity;
- exact calendar period;
- expected archive filename;
- expected CSV filename;
- expected `.CHECKSUM` filename;
- exact relative archive path;
- exact UTC period start/end;
- timestamp-unit policy;
- expected row count.

The descriptor also carries `InstrumentMetadata`, but under the explicit policy:

```text
RESEARCH_SERIALIZATION_ONLY_NOT_TRADING_FILTER_AUTHORITY_V1
```

`price_tick` and `quantity_step` therefore provide stable research serialization/universe identity only.  D2T does not claim that they are current Binance exchange filters or safe execution precision.

## Supported intervals in v1

The upstream public archive supports more intervals than D2T v1 accepts.

D2T v1 intentionally restricts itself to:

```text
1m
3m
5m
15m
30m
1h
2h
4h
6h
8h
12h
1d
```

Each accepted interval:

- is fixed-duration;
- is whole-second compatible;
- divides a UTC day exactly;
- has unambiguous daily/monthly archive coverage.

D2T v1 deliberately excludes:

```text
1s
3d
1w
1mo
```

This is not a claim that the provider lacks those files.  It is a governance choice to avoid introducing archive-boundary or variable-calendar semantics before they receive their own explicit contract.

## Timestamp transition

The provider archive semantics changed at the exact UTC boundary:

```text
2025-01-01T00:00:00+00:00
```

D2T freezes:

```text
before 2025-01-01 -> MILLISECONDS_PRE_2025_01_01
from   2025-01-01 -> MICROSECONDS_FROM_2025_01_01
```

The parser does not infer units from magnitude.  The expected unit is determined solely from the descriptor period, and a payload encoded in the opposite unit fails exact open-time geometry.

This matters because magnitude-based auto-detection could silently reinterpret malformed or mixed material.

## Integrity ordering

The most important ordering rule is:

```text
SHA256(exact ZIP bytes)
    MUST equal
provider .CHECKSUM digest
    BEFORE
ZIP parsing begins
```

If the checksum does not match, D2T fails without attempting to parse the ZIP.

After that gate, D2T requires:

1. a valid ZIP;
2. no duplicate member names;
3. exactly one non-directory member;
4. that member's filename equals the descriptor's exact CSV filename;
5. no encrypted member;
6. only allowed compression modes;
7. bounded compressed/archive/CSV sizes;
8. bounded row count.

The raw identities preserved are:

```text
archive_sha256
provider_checksum_sha256
checksum_payload_sha256
csv_payload_sha256
```

They are separate on purpose.  For example, two `.CHECKSUM` texts could name the same archive digest but differ byte-for-byte; D2T preserves both the semantic provider digest and the exact checksum-file payload identity.

## Provider kline row contract

Each CSV row must contain the exact Binance kline column count and parse into:

```text
open time
open
high
low
close
volume
close time
quote asset volume
number of trades
taker buy base asset volume
taker buy quote asset volume
ignore
```

D2T validates at minimum:

- integer open/close timestamps;
- exact expected open timestamp for row index;
- exact close timestamp:

```text
close_time = open_time + interval - 1 provider time unit
```

- positive finite OHLC prices;
- non-negative finite volume fields;
- OHLC geometry;
- non-negative trade count;
- full exact requested period;
- no gaps;
- no duplicates;
- no trailing or missing rows.

Only the canonical OHLCV subset enters `MarketDataset`; the other provider fields remain validation inputs, not features.

## Normalized material identity

Provider timestamps are converted to timezone-aware UTC bar starts.  D2T then constructs ordinary AUTO-TRADE `Bar` and `MarketDataset` objects.

The snapshot preserves both:

```text
normalized_row_payload_sha256
normalized_dataset_hash
```

The first binds D2T's canonical serialized rows.  The second binds the existing `MarketDataset` identity and therefore integrates with the material-lineage rules already used by D2R/D2S.

The producer semantic hash includes:

```text
oss3_market_snapshot.py
external_data.py
market.py
universe.py
```

A semantic change to interval definitions or the canonical market/universe types therefore changes the D2T producer identity.

## HistoricalMarketSnapshotArtifact

The durable artifact stores:

- artifact version;
- immutable manifest;
- manifest fingerprint;
- instrument metadata;
- canonical normalized rows.

On read, D2T reconstructs and verifies the artifact rather than trusting stored hashes.

A row mutation, manifest mutation, payload-hash mutation or dataset-hash mutation must fail closed.

`verify_source(...)` provides a stronger replay: the caller supplies the descriptor plus exact ZIP/CHECKSUM material again and D2T reconstructs the snapshot.  The rebuilt artifact must equal the durable one exactly.

## Daily and monthly geometry

D2T certifies both provider granularities.

Examples covered by tests:

```text
2024-12-31 daily 1h -> 24 rows, millisecond timestamps
2025-01-01 daily 1h -> 24 rows, microsecond timestamps
2024-02 monthly 1d -> 29 rows, leap-year millisecond timestamps
2025-02 monthly 1d -> 28 rows, microsecond timestamps
```

Monthly periods are derived by calendar boundaries, not by a fixed number of days.

A monthly archive missing even one expected row fails exact coverage.

## Multi-asset snapshot set

`build_aligned_snapshot_universe(...)` converts multiple individual snapshots into one `AlignedMarketUniverse` only if they have exact common semantics and support.

The set requires equality of:

- provider identity;
- interval;
- period start;
- period end;
- timeframe;
- quote currency;
- ordered bar timestamp support.

Symbols are normalized into deterministic order before the universe is produced.

`AlignedSnapshotSetEvidence` binds the individual snapshot fingerprints and the resulting material universe hash.

This is the first D2T bridge toward the raw multi-asset universe expected by D2R/D2S, but D2T does **not** concatenate adjacent daily/monthly files.

## Why D2T does not stitch files

Constructing a real TRAIN or DEVELOPMENT partition will normally require multiple provider archives.

Cross-file assembly introduces additional questions:

- does the end of file N meet the start of file N+1 exactly?
- are all symbols available over identical periods?
- what happens if the provider replaced only one historical archive?
- how is a collection manifest frozen?
- can one period accidentally be reused in two scientific partitions?
- how are warmup/TRAIN/DEVELOPMENT boundaries committed before labels or metrics?

Those are collection-level concerns and are intentionally deferred to D2U rather than hidden inside a single-file snapshot primitive.

## Adversarial requirements

D2T tests must prove at least:

1. correct pre-2025 millisecond semantics;
2. correct post-2025 microsecond semantics;
3. checksum failure occurs before ZIP parse;
4. exact checksum filename and one-line contract;
5. extra ZIP member rejection;
6. duplicate ZIP member rejection;
7. opposite timestamp unit rejection;
8. exact close-time geometry;
9. gap/shift rejection;
10. truncated-period rejection;
11. daily artifact roundtrip;
12. monthly artifact roundtrip;
13. leap-year monthly geometry;
14. artifact tamper detection;
15. source replay mismatch rejection;
16. deterministic multi-asset alignment;
17. period/support mismatch rejection;
18. execution/trading-filter authority mutation rejection.

## Static boundary

`scripts/check_oss3d2t_immutable_market_snapshot_boundary.py` additionally proves by AST/source inspection that D2T:

- imports only `FIXED_INTERVAL_MS` from R3 external-data code;
- has no socket/urllib/requests/httpx/aiohttp/subprocess authority;
- has no Qlib runtime;
- has no supervised labels;
- has no DEVELOPMENT evaluator/tournament;
- has no FINAL_HOLDOUT/economic evaluator;
- has no broker/OMS/Safety/OrderIntent authority;
- verifies provider checksum before calling the ZIP reader;
- requires an exact one-member ZIP;
- freezes timestamp transition and accepted intervals;
- denies trading-filter certification.

## Authority

Every D2T manifest and snapshot-set evidence remains:

```text
network_used_by_normalizer = false
provider_credentials_used = false
trading_filters_certified = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

D2T is historical-data provenance.  A valid snapshot proves data identity and normalization, not profitability, strategy quality, tradability or permission to send an order.

## Relationship to D2R and D2S

Once D2T snapshots are assembled into a future collection boundary, their resulting `AlignedMarketUniverse` can feed the already-certified raw research chain:

```text
D2T immutable snapshots
  -> future D2U collection/partition assembly
    -> D2R raw TRAIN provenance
    -> D2F/D2G six-model family

D2T immutable snapshots
  -> future D2U collection/partition assembly
    -> D2S raw DEVELOPMENT provenance
    -> D2H/D2D/D2E evaluation
```

D2R/D2S do not need to know how the provider files were acquired; they receive only the canonical material universe and can bind its exact dataset hashes.

## Next scientific boundary: D2U

After D2T certification, the next step should be **immutable historical collection assembly and acquisition evidence**.

D2U should:

- freeze a finite list of provider archive descriptors before download;
- acquire ZIP and `.CHECKSUM` material through a bounded GET-only process;
- persist the exact raw bytes outside the scientific normalizer;
- bind acquisition timestamps/status/content sizes without treating them as market truth;
- normalize each file through D2T;
- assemble consecutive snapshots with exact no-gap/no-overlap rules;
- create explicit WARMUP/TRAIN/DEVELOPMENT material partitions;
- prevent the same bar from silently crossing partition roles;
- remain independent from labels, metrics and execution.

Only after that collection is certified should AUTO-TRADE run a substantial real historical DEVELOPMENT campaign.  D2T by itself does not open FINAL_HOLDOUT, PAPER, capital or LIVE authority.
