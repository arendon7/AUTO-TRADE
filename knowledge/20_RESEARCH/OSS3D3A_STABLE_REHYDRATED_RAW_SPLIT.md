# OSS-3D3A — Stable Rehydrated Raw Split

## Purpose

OSS-3D3A is the first bridge after the durable D2Z descriptor-material manifest. Its job is to take already-acquired public Binance archive bytes, prove that every descriptor still equals the frozen real-data campaign at the scientifically relevant byte/material level, assemble the canonical D2U raw history, and expose only the existing raw D2R TRAIN and pre-label D2S DEVELOPMENT sources.

D3A is intentionally **not** an acquisition runner and intentionally does **not** execute Qlib. It is the offline equality/admission boundary between reacquired public history and the supervised research pipeline.

## Why D2V/D2W cannot simply be replayed as the durable identity

D2U acquisition receipts contain `acquired_at`. D2V seals contain `sealed_at`. As a consequence, a legitimate future reacquisition of exactly the same Binance ZIP and CHECKSUM bytes will create different receipt hashes and therefore may create a different D2U assembly-evidence fingerprint and `HistoricalResearchPartitionMaterial.fingerprint`.

That difference is operational provenance, not a change in the market history.

D2Y remains the immutable audit record of the original real campaign, including its 398-file source inventory, D2V seal and original D2U partition-material fingerprint. D2Z adds the receipt-timestamp-independent material commitment for all 99 descriptors.

D3A therefore does not pretend that a new receipt-bound partition fingerprint must equal the old D2Y partition fingerprint.

## Two identities, deliberately separated

### Audit fingerprint

Each D3A run records:

- the ordered descriptor -> reacquisition-receipt hash lineage root;
- the newly assembled D2U partition-material fingerprint;
- the newly assembled D2U assembly-evidence fingerprint;
- the complete D3A evidence fingerprint.

These may legitimately change when the same bytes are reacquired at a different time.

### Scientific fingerprint

The D3A scientific fingerprint excludes receipt/acquisition lineage and the receipt-bound D2U partition-material fingerprint. It binds only reproducible scientific identity:

- scientific identity policy;
- exact D2Y seal fingerprint;
- exact D2Z stable 99-material root;
- canonical collection id + D2U plan fingerprint;
- TRAIN warmup universe hash;
- exact TRAIN universe hash;
- DEVELOPMENT warmup universe hash;
- exact DEVELOPMENT universe hash;
- stable research-universe semantic identity;
- raw D2R TRAIN source hash;
- raw pre-label D2S DEVELOPMENT source hash.

Two reacquisitions over byte-identical D2Z material must have the same scientific fingerprint even when their receipt timestamps and audit fingerprints differ.

## Canonical ordering

The public entry point is:

`build_canonical_stable_rehydrated_raw_split(materials=...)`

It has no caller-supplied expected TRAIN/DEVELOPMENT hashes. It reconstructs and verifies:

1. canonical D2Y real-campaign evidence seal;
2. canonical D2Z durable descriptor-material manifest;
3. canonical D2U V2 plan;
4. exact D2Z -> D2Y -> D2U binding.

Only then does it process the supplied already-acquired bytes.

## Per-descriptor admission

For every descriptor in exact D2U plan order:

1. require exactly one `RehydratedDescriptorMaterial`;
2. require the exact canonical descriptor semantics;
3. locate the exact D2Z `StableDescriptorMaterialIdentity`;
4. call `verify_rehydrated_descriptor_material(...)`;
5. require exact archive SHA-256;
6. require exact CHECKSUM payload SHA-256;
7. rebuild D2T from those bytes;
8. require exact D2T snapshot artifact hash;
9. require exact normalized dataset hash;
10. preserve the new acquisition receipt hash only as audit lineage.

Any provider-byte or normalized-material drift fails before raw split admission.

## D2U assembly and D2Y universe equality

After all descriptor gates succeed, D3A calls the existing `assemble_historical_collection(...)` primitive. This deliberately preserves D2U continuity, cross-asset support and partition geometry instead of implementing a second stitcher.

The newly assembled receipt-bound partition fingerprint is recorded, not required to reproduce D2Y.

The market-data universes **are** required to reproduce D2Y exactly:

- TRAIN universe == D2Y `TRAINING_UNIVERSE_HASH`;
- DEVELOPMENT universe == D2Y `DEVELOPMENT_UNIVERSE_HASH`.

If either differs, D3A fails closed before downstream source creation.

The 20-bar TRAIN and DEVELOPMENT warmups are reconstructed by the same D2U partition logic and are included in the scientific fingerprint.

## Downstream handoff

Only after all equality gates pass does D3A create:

- `RawTrainingMarketSource.build(...)` for D2R;
- `RawDevelopmentMarketSource.build(...)` for D2S.

TRAIN and DEVELOPMENT must expose the same research-universe semantic identity.

This is a **raw-only** handoff. D3A does not call the D2R label derivator, does not materialize D2S DEVELOPMENT labels, does not execute the six D2G models, and does not compute D2D/D2E metrics.

## Adversarial guarantees

The D3A suite proves:

- valid D2Z-gated material creates exact D2R/D2S raw sources;
- two different receipt lineages over identical bytes change audit lineage and D2U partition fingerprints;
- those same two runs preserve identical scientific fingerprint and raw source hashes;
- archive-byte drift fails at the D2Z gate;
- wrong expected TRAIN universe fails closed;
- wrong expected DEVELOPMENT universe fails closed;
- missing material fails closed;
- duplicate material fails closed;
- reordered D2Z identities fail closed;
- receipt-bound partition-fingerprint reproduction cannot be re-enabled;
- network/Qlib/FINAL_HOLDOUT/promotion/execution/PAPER/capital/LIVE authority cannot be enabled.

Synthetic fixtures prove software/governance semantics only. They are not evidence of model skill or profitability.

## Network boundary

D3A accepts already-acquired bytes. It does not import or invoke:

- D2U network acquisition adapter;
- real archive transport;
- D2V restart-safe acquisition campaign;
- urllib/requests/httpx/socket;
- subprocess/process execution.

A later operational child may use the already-certified D2U GET-only acquisition adapter and pass its outputs into D3A. That later runner must remain separately governed and must not weaken D3A.

## FINAL_HOLDOUT and model boundary

D3A does not load FINAL_HOLDOUT values. It does not issue or consume a holdout permit. It does not fit or predict with Qlib. It does not materialize DEVELOPMENT labels or metrics.

The correct sequence remains:

public historical reacquisition
→ D2Z exact material equality
→ D3A D2U assembly + D2Y TRAIN/DEVELOPMENT equality
→ raw D2R TRAIN / pre-label D2S DEVELOPMENT
→ later supervised/model stages under their existing preregistration rules

FINAL_HOLDOUT remains outside this path.

## Authority

Canonical D3A evidence must remain:

- `network_used_by_handoff = false`
- `train_label_artifact_materialized = false`
- `development_label_artifact_materialized = false`
- `prediction_values_loaded = false`
- `development_metrics_computed = false`
- `qlib_runtime_used = false`
- `final_holdout_values_loaded = false`
- `promotion_authorized = false`
- `execution_authorized = false`
- `paper_execution_authorized = false`
- `capital_authority = NONE`
- `live_trading = BLOCKED`

D3A is evidence transport into research, not trading authority.
