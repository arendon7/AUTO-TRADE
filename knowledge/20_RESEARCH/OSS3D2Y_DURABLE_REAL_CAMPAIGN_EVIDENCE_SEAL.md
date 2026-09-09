# OSS-3D2Y — Durable Real Campaign Evidence Seal

## 1. Purpose

OSS-3D2Y converts the successful D2X/D2V real Binance historical acquisition campaign into a **durable cryptographic research evidence root**.

D2Y exists because GitHub Actions artifacts are operational conveniences with finite retention. The scientific identity of the campaign must not disappear when an artifact expires.

D2Y therefore freezes the exact certified code identity, D2U V2 plan identity, real provider campaign seal, TRAIN/DEVELOPMENT material identities, double-pass execution evidence and cryptographic commitments to the downloaded evidence inventory.

D2Y does **not** store the historical market payloads in Git, does not create new market observations, does not impute missing bars, does not train or select a model, does not observe FINAL_HOLDOUT values and has no trading authority.

---

## 2. Upstream certified scientific root

D2Y is rooted in the exact certified OSS-3D2X head:

```text
D2X certified commit
= a2abb8fa73ef3b40c4d8523fc326df1e71a06355
```

That D2X head had all three certification gates green:

- OSS-3D2X Dedicated — SUCCESS;
- Knowledge Contract — SUCCESS;
- Core Safety — SUCCESS.

D2X itself freezes the post-outage D2U V2 plan:

```text
collection id
= oss3d2u-binance-spot-btc-eth-sol-1h-2023apr-2025-v2

plan fingerprint
= f43abdcd532f07d37b54f93abafbd3b29a3c3ee07afeedbae95b7aa15153c59d

descriptors
= 99

period
= 2023-04 through 2025-12

symbols
= BTCUSDT / ETHUSDT / SOLUSDT

interval
= 1h
```

The March 2023 Binance outage remains excluded by preregistration. D2T remains strict and unchanged. There is no gap fill, interpolation or synthetic market bar.

---

## 3. Controlled real campaign

The operational wrapper was committed separately from the certified scientific head:

```text
operational wrapper commit
= 1aa46b5664e03f16a7855fa8be896b5f8436674b
```

The wrapper itself did not become the research runtime. Its first action was to checkout the exact D2X certified commit `a2abb8fa73ef3b40c4d8523fc326df1e71a06355` and prove `git rev-parse HEAD` matched it before any provider GET.

GitHub Actions run:

```text
workflow run id = 34309982898
```

The workflow used `set -euo pipefail` around the campaign command piped through `tee`. Therefore a Python failure could not be masked by a successful `tee` process.

Before network acquisition the workflow re-ran the D2X boundary and proved Qlib absent.

---

## 4. First pass — real provider acquisition

The first pass ran with explicit public historical GET authority only.

Observed result:

```text
descriptor_count                         = 99
acquired_from_network                    = 99
reused_after_seal_reverification         = 0
reconciled_unsealed_final_material       = 0
missing_without_network_authority        = 0
complete                                 = true
network_enabled                          = true
```

Thus the canonical family was acquired **99/99** from the public provider surface.

No provider credentials were required. No trading endpoint was used.

The exact first-result file SHA-256 is:

```text
1f099595c716db489b5e39eb3a976765f524c7252f276042b438eccdc711bbc0
```

---

## 5. Second pass — zero-network full reverification

The exact same evidence root was immediately processed again without `--execute-public-get`.

Observed result:

```text
descriptor_count                         = 99
acquired_from_network                    = 0
reused_after_seal_reverification         = 99
reconciled_unsealed_final_material       = 0
missing_without_network_authority        = 0
complete                                 = true
network_enabled                          = false
```

This is the required **0 GET / 99 reverified** proof.

The second pass reproduced exactly:

- campaign seal;
- D2U partition material fingerprint;
- TRAIN universe hash;
- DEVELOPMENT universe hash.

The exact offline-result file SHA-256 is:

```text
1b64445ac17cd27dcf9093bc14c4c95ab1fae4f698b5754290ec81416e47bc5b
```

---

## 6. Frozen real-data identities

### Campaign seal

```text
4f49d8899e4b11685165cbea948bd09232a3747aef90344606e6a5ac27857f7d
```

### D2U partition material

```text
7cac5172e70ecf6b36c9d51e433ce57184586b08ff525ea94af63ef331a3f873
```

### TRAIN universe

```text
0e998c27a9636f1005b429f052ec902f0b9fb91d66e235dc0c3b2f090fa3cd69
```

### DEVELOPMENT universe

```text
5fb5866a084f97cebff3b75f11be9f9ecf667880fc11584ad52b53cfdd820c9d
```

These are the canonical identities downstream real-data research must reproduce before model execution can be considered bound to this campaign.

A downstream process that produces a different TRAIN or DEVELOPMENT universe is not the same D2Y evidence root even if it uses the same symbols and dates.

---

## 7. Evidence inventory commitment

The campaign evidence root contained exactly:

```text
398 total files
= 396 descriptor evidence files
+ 2 SQLite registry files
```

For each of the 99 descriptors the evidence root contains four files:

1. exact provider ZIP;
2. exact provider CHECKSUM file;
3. D2T immutable snapshot JSON;
4. D2U acquisition receipt JSON.

The two additional files are the D2U plan registry and D2V campaign registry.

The downloaded inventory was independently checked after the workflow completed. All expected `99 × 4 = 396` descriptor evidence paths were present and there were no unexpected files beyond the two registries.

Exact inventory SHA-256:

```text
94d47d9bfef334b590a0946fa0094b6534c542f1e5ef41d6a481b893f5a6ce57
```

Inventory metadata:

```text
source_file_count        = 398
source_total_bytes       = 12,733,744
descriptor_evidence      = 396
```

Registry file SHA-256 values:

```text
oss3d2u-plan.sqlite3
= dcf7746539618e81cfb5952a9dbdc6bd9133b6cf929f672612e6de8dcdd8ca64

oss3d2v-campaign.sqlite3
= 5dd3598ae1f8306a62933adebeee1d23f70f86423674312ccb937b61aa978857
```

---

## 8. Archive commitments

The exact evidence tar generated inside the workflow has SHA-256:

```text
c5d0f694a68f8bf7b67c35774900efaaaad7b425e95735f60660824e07446277
```

The SHA-256 of the accompanying `.sha256` file is:

```text
95000f9bde4deaa56aefb6fdffeaf513433f86896b27030e775f248ce032e813
```

GitHub artifact metadata:

```text
artifact id          = 10088057589
artifact name        = oss3d2x-v2-real-campaign-20260908
artifact size        = 6,188,378 bytes
artifact ZIP SHA-256 = 23598e3bd2f42b645095291115451d115dae60455b813c5653f28ebd5a6b7dc7
expires at           = 2026-10-09T04:14:03Z
```

The artifact ZIP was downloaded after the run and independently hashed. Its local SHA-256 exactly matched GitHub's artifact digest.

**Artifact expiry does not invalidate the D2Y scientific evidence seal.** Expiration only removes a convenience copy. The durable seal remains in Git and downstream rehydration must reproduce the frozen plan/material/universe identities.

---

## 9. D2Y seal fingerprint

The typed `DurableRealCampaignEvidenceSeal` includes all frozen identities, execution counters, artifact commitments and authority denials.

Canonical D2Y seal fingerprint:

```text
919009bf61a1a3512e99c3b8edb21017b8d0de4f97722dcc3849f5ba1b5f9beb
```

`verify_oss3d2y_real_campaign_evidence_seal()` additionally rebinds the seal to the current canonical D2U V2 collection id, plan fingerprint and descriptor count. A structurally valid seal from another campaign is rejected.

---

## 10. Rehydration rule

D2Y does not authorize downstream code to simply trust matching filenames or date ranges.

A future real-data rehydration stage must fail closed unless it can reproduce at minimum:

```text
collection id
plan fingerprint
99-descriptor completeness
D2U partition material fingerprint
TRAIN universe hash
DEVELOPMENT universe hash
```

If the public provider changes historical bytes, if a descriptor is missing, if D2T semantics change, or if stitching changes, at least one frozen identity must diverge and the candidate must be rejected as not being the D2Y campaign.

No silent fallback to cached, interpolated or synthetic data is permitted.

---

## 11. Scientific interpretation

D2Y proves something narrow but important:

> the project now has a complete, immutable and independently reverified real historical market-data root for the preregistered BTC/ETH/SOL 1h research family.

It does **not** prove:

- model skill;
- statistical significance;
- economic profitability;
- expected future return;
- robustness under execution costs;
- readiness for PAPER or LIVE trading.

Those are separate downstream questions with separate protocols.

The 2025 DEVELOPMENT segment remains research data, not FINAL_HOLDOUT. D2Y does not rerun a model tournament or select a winner from these market-data hashes.

---

## 12. Authority boundary

D2Y permanently records:

```text
FINAL_HOLDOUT values loaded = false
promotion authorized        = false
execution authorized        = false
PAPER execution authorized  = false
capital authority           = NONE
LIVE trading                = BLOCKED
```

D2Y contains no Qlib runtime, broker, OMS, Safety, order writer, holdout permit or execution surface.

---

## 13. Next frontier

The appropriate next stage is a **real-data rehydration and research-bundle binding gate**. It should reconstruct the canonical TRAIN/DEVELOPMENT material from the D2Y root, verify the frozen hashes, and only then expose that material to the already-certified Qlib research stack.

That future stage must preserve the existing model/tournament governance. It must not use the new real-data campaign as an excuse to retune after seeing DEVELOPMENT results or to observe FINAL_HOLDOUT.
