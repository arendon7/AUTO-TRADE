# OSS-3D2Z — Durable Descriptor Material Manifest

## Status and purpose

OSS-3D2Z is a research-only evidence stage downstream of the certified D2Y real-campaign seal. It extracts the **reacquisition-stable identity** of each of the 99 real Binance Spot monthly archives before the convenience GitHub Actions artifact expires.

D2Z does not download new market data, choose a model, calculate labels, evaluate metrics or authorize execution.

## Certified upstream root

D2Y certified head:

`608a4efc6202eed698f6becd7d9b769fbf46fa6f`

D2Y evidence seal:

`919009bf61a1a3512e99c3b8edb21017b8d0de4f97722dcc3849f5ba1b5f9beb`

Source D2Y inventory commitment:

`94d47d9bfef334b590a0946fa0094b6534c542f1e5ef41d6a481b893f5a6ce57`

Source evidence tar commitment:

`c5d0f694a68f8bf7b67c35774900efaaaad7b425e95735f60660824e07446277`

Source GitHub artifact ZIP commitment:

`23598e3bd2f42b645095291115451d115dae60455b813c5653f28ebd5a6b7dc7`

D2Y proved that the original campaign contained exactly **398 files**: four evidence files for each of 99 descriptors plus the two append-only registries.

## Canonical family

D2Z remains bound to the exact D2U V2 plan:

`oss3d2u-binance-spot-btc-eth-sol-1h-2023apr-2025-v2`

Plan fingerprint:

`f43abdcd532f07d37b54f93abafbd3b29a3c3ee07afeedbae95b7aa15153c59d`

Family:

```text
BTCUSDT / ETHUSDT / SOLUSDT
Binance Spot public monthly klines
1h
2023-04 through 2025-12
33 months × 3 symbols = 99 descriptors
```

The descriptor order is the canonical D2U order: month first, then BTCUSDT / ETHUSDT / SOLUSDT.

## Durable compact manifest

The durable descriptor-level evidence file is:

`knowledge/20_RESEARCH/evidence/OSS3D2Z_REAL_DESCRIPTOR_MATERIAL_MANIFEST.json`

Its exact SHA-256 is:

`0edaf50aadb7a56d106b38c398ba2f7e6a4b09a0b21cb0fdd38c9e18573f696b`

Each ordered entry binds:

```text
descriptor_fingerprint
archive_sha256
checksum_payload_sha256
snapshot_artifact_hash
normalized_dataset_hash
material_identity_hash
```

The stable identity policy is:

`EXACT_PROVIDER_ARCHIVE_CHECKSUM_AND_D2T_OUTPUT_RECEIPT_TIMESTAMP_INDEPENDENT_V1`

The ordered 99-entry material root is:

`8aef56ea8e96c2c880765440da86e31041d846324f7b9a3ebd79d0a1a470cdaf`

The D2T snapshot artifact hash already cryptographically binds the normalized rows, manifest, instrument metadata, producer semantic hash, CSV payload hash, normalized-row payload hash and D2T governance flags. D2Z therefore does not redundantly duplicate every D2T manifest field.

## Why receipts are not part of the stable identity

The original D2U acquisition receipt contains `acquired_at`, and D2V descriptor/campaign seals contain `sealed_at`.

Those timestamps are correct audit evidence for the original campaign, but they are **not properties of the market data**. If the same public Binance bytes are acquired later, a new receipt timestamp is expected.

Therefore D2Z deliberately excludes receipt timestamp / receipt-file identity from `material_identity_hash`.

This does not erase the original audit trail: D2Y already binds the complete 398-file source inventory by SHA-256. D2Z simply separates two different questions:

1. **Original-campaign audit:** was the 2026-09-08/09 evidence set exactly the one D2Y certified?
2. **Future material equality:** did a later acquisition return the exact same provider archive/CHECKSUM bytes and reproduce the exact same D2T output?

D2Y answers the first. D2Z answers the second.

## Offline rehydration gate

D2Z introduces:

`verify_rehydrated_descriptor_material(...)`

The function accepts already-acquired raw archive and CHECKSUM bytes. It performs no network I/O.

It requires:

1. the exact canonical descriptor fingerprint;
2. the exact frozen archive SHA-256;
3. the exact frozen CHECKSUM payload SHA-256;
4. a fresh D2T reconstruction from those bytes;
5. the exact frozen D2T snapshot artifact hash;
6. the exact frozen normalized dataset hash.

Any mismatch fails closed.

## Relationship with D2W

D2Z does not replace the existing D2W sealed raw split handoff.

The intended lineage is:

```text
D2Y durable real-campaign seal
  -> D2Z stable descriptor material identities
    -> future bounded D2U public-provider reacquisition
      -> D2Z offline equality gate for all 99 descriptors
        -> D2U exact TRAIN/DEVELOPMENT assembly
          -> D2W offline sealed raw split handoff
            -> D2R raw TRAIN
            -> D2S raw DEVELOPMENT pre-label path
```

No raw market material should be admitted downstream after a fresh reacquisition unless all 99 D2Z identities match.

## Scientific claims not made

D2Z proves data identity only. It does not prove:

- model skill;
- statistical significance;
- expected return;
- profitability;
- robustness of a trading strategy;
- PAPER readiness;
- LIVE readiness.

## Authority boundary

D2Z certification is offline.

```text
network authority during D2Z certification = NONE
Qlib runtime = ABSENT
FINAL_HOLDOUT values = NOT LOADED
labels = NOT MATERIALIZED
predictions = NOT EXECUTED
metrics = NOT COMPUTED
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

There is no broker, OMS, Safety writer or OrderIntent surface.

## Next frontier

Only after D2Z is independently certified should a child stage perform fresh rehydration:

1. pin the exact D2Z-certified SHA;
2. use only the already-certified D2U GET-only provider adapter;
3. reacquire the exact 99 descriptors;
4. pass every descriptor through the D2Z offline equality gate;
5. assemble D2U only if all 99 pass;
6. require the assembled TRAIN and DEVELOPMENT universe hashes to equal the D2Y-frozen hashes;
7. hand off through D2W offline;
8. keep FINAL_HOLDOUT, PAPER, capital and LIVE blocked.

Qlib/model execution should remain disconnected until that real-data rehydration stage is certified.
