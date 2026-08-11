# ADR-0008 — R3 Read-Only Market Data and Preregistered Research Governance

Status: Accepted
Date: 2026-08-10
Track: v0.28R / R3

## Context
R0–R2 already certify durable capital controls and a synthetic/reproducible research foundation. R3 must add real public market data and research governance without introducing a path from internet input or research output to broker execution.

The main risks are not only network bugs. They include ambiguous/incomplete datasets, silent gaps, non-reproducible provenance, trial cherry-picking, omitted failures, parameter mining and HOLDOUT contamination.

## Decision
R3 separates four authority planes:

1. **Public Data Plane** — outbound read-only market-data requests only.
2. **Canonical Dataset Plane** — validated immutable datasets/manifests/checksums.
3. **Research Governance Plane** — preregistered trials, complete trial accounting and statistical evidence.
4. **Execution Plane** — remains isolated; R3 has no broker/network execution capability.

### Public-data boundary
- Disabled by default.
- HTTPS only.
- Exact host allowlist; redirects/final URL must remain on the allowed public-data host.
- GET only.
- Endpoint allowlist; trading/account/private endpoints are not representable by the provider contract.
- No API key/secret/private headers.
- Explicit timeout and bounded maximum rows/pages.
- Transport is injected so ordinary CI uses deterministic fakes and never depends on the internet.

Initial provider target is Binance Spot public historical klines using the official public market-data host and the documented `GET /api/v3/klines` endpoint. Exact endpoint/limit assumptions must be verified against current official Binance documentation before implementation changes.

### Canonical intake
R3 defines a fixed-interval request contract using a half-open UTC range `[start, end)`. The adapter maps that to provider semantics and requires:
- aligned boundaries;
- expected exact bar count for the requested fixed interval;
- strictly ordered unique open timestamps;
- no silent imputation;
- OHLCV validity via the R1 `MarketDataset` contract;
- no gaps inside the requested range;
- canonical response checksum;
- dataset checksum and provenance manifest.

An empty, partial, malformed, duplicated, conflicting or out-of-range response is not a certified dataset.

### Trial preregistration
Every evaluated research trial must have an immutable preregistration created **before** result recording. A campaign freezes its expected trial IDs. Promotion evidence cannot be produced until all expected trials have terminal accounting (`COMPLETED` or `FAILED`). Failed trials count; omission is a hard governance error.

### Multiple testing
R3 may compute deterministic multiplicity adjustments only over a complete frozen trial universe. PBO/Deflated Sharpe are not emitted merely because a caller asks for them: explicit statistical preconditions must be satisfied and evidenced. Missing preconditions produce `NOT_COMPUTED`/fail-closed evidence, never a fabricated number.

### HOLDOUT
TRAIN and DEVELOPMENT may be used in iterative research. FINAL_HOLDOUT remains a one-shot final-validation capability using the existing protected holdout permit mechanism. Trial governance must record that authorization. Tuning code cannot consume HOLDOUT.

### Research Control Center
Reporting is read-only over registries/manifests/evidence. It cannot import/use OMS or broker adapters to create execution authority. ChatGPT/research output remains advisory only.

## Provider scope
R3 initially supports fixed-duration Binance Spot intervals needed by the reconstructed research campaign. Irregular calendar intervals are not accepted until their completeness semantics are separately specified and tested.

## Failure semantics
Network timeout, non-200 response, malformed JSON, unexpected content shape, host drift, redirect, coverage mismatch, checksum mismatch, duplicate/conflicting rows or governance incompleteness all fail closed.

## Consequences
+ Real data becomes reproducible evidence rather than an opaque fetch.
+ Trial cherry-picking becomes structurally detectable.
+ HOLDOUT and execution authority remain isolated.
- Real-data campaigns are intentionally bounded and more conservative than ad-hoc notebook research.
- R3 does not yet certify streaming, external PAPER or broker-side protection.

## Capital status
**LIVE TRADING: BLOQUEADO.**
