# OSS-3D2W — Sealed raw split handoff

Status: DEVELOPMENT research infrastructure only.

D2W is the provenance bridge between a **completed, sealed and reproducible D2V historical collection** and the already-existing D2R/D2S raw research contracts.

It does not acquire data, build features, materialize labels, run Qlib, compute metrics or authorize execution.

## 1. Upstream prerequisite

D2W requires a pre-existing `OSS3D2V_COMPLETE_COLLECTION_SEAL_V1` for the exact supplied D2U plan.

The canonical upstream plan remains:

- Binance Spot public historical archives;
- `BTCUSDT`, `ETHUSDT`, `SOLUSDT`;
- `1h`;
- monthly archives;
- `2023-04` through `2025-12`;
- 99 exact descriptors;
- first 20 bars = WARMUP;
- TRAIN begins `2023-04-01T20:00:00+00:00`;
- DEVELOPMENT begins `2025-01-01T00:00:00+00:00`;
- DEVELOPMENT ends `2026-01-01T00:00:00+00:00`;
- no FINAL_HOLDOUT descriptors.

D2W does not contain a descriptor constructor and does not redefine this family.

## 2. Why a separate handoff exists

D2V proves that the local evidence root contains a complete reproducible raw campaign and binds it to the D2U assembled partition material.

D2R and D2S, however, consume `AlignedMarketUniverse` objects through their own explicit raw-source contracts:

- `RawTrainingMarketSource`;
- `RawDevelopmentMarketSource`.

D2W makes that transition explicit and hash-bound so downstream research cannot silently use a different dataset, a different partition cut or a different D2V acquisition.

## 3. Mandatory pre-existing D2V seal

D2W first opens the D2V append-only campaign ledger and looks up the complete seal for the D2U collection id.

If the seal does not already exist, D2W fails.

This is stronger than checking whether files happen to exist. D2W is not allowed to turn an arbitrary directory of archives into a research split.

The pre-existing seal must match:

- collection id;
- D2U plan fingerprint;
- descriptor count;
- FINAL_HOLDOUT absent.

## 4. Full offline reverification

After the pre-existing seal is accepted, D2W calls the certified D2V campaign runner with:

`allow_network=False`

This causes D2V to:

1. reread every sealed descriptor directory;
2. reread every provider ZIP;
3. reread every provider CHECKSUM;
4. reread every canonical D2T snapshot;
5. reread every canonical D2U acquisition receipt;
6. reconstruct every `AcquiredArchiveMaterial`;
7. reverify every descriptor seal;
8. reassemble the exact D2U WARMUP/TRAIN/DEVELOPMENT material;
9. reproduce the same complete D2V campaign seal.

D2W rejects the handoff if the offline run is not complete, if any network acquisition is reported, or if the resulting campaign seal fingerprint differs from the pre-existing seal.

## 5. Exact D2U material binding

The reconstructed `HistoricalResearchPartitionMaterial` must match the D2V complete seal on all of these identities:

- D2U partition material fingerprint;
- D2U assembly evidence fingerprint;
- TRAIN warmup universe hash;
- TRAIN universe hash;
- DEVELOPMENT warmup universe hash;
- DEVELOPMENT universe hash.

Therefore the downstream split is not inferred from dates or filenames. It is the exact object family already committed by D2V.

## 6. D2R TRAIN source

D2W constructs:

```text
RawTrainingMarketSource.build(
    warmup_universe = D2U.training_warmup,
    training_universe = D2U.training,
)
```

The existing D2R constructor itself enforces:

- exact 20-bar warmup;
- same symbols and research-universe semantics;
- exact warmup/TRAIN continuity;
- TRAIN raw values only;
- no DEVELOPMENT values;
- no FINAL_HOLDOUT values;
- no prediction values;
- no execution authority.

D2W does **not** call `derive_raw_training_bundle()` in production code. TRAIN labels/features remain a subsequent D2R derivation step.

## 7. D2S DEVELOPMENT source

D2W constructs:

```text
RawDevelopmentMarketSource.build(
    warmup_universe = D2U.development_warmup,
    development_universe = D2U.development,
)
```

The existing D2S constructor enforces:

- exact 20-bar pre-DEVELOPMENT warmup;
- same symbols and research-universe semantics;
- exact warmup/DEVELOPMENT continuity;
- full raw DEVELOPMENT path acknowledged;
- no supervised label artifact materialized;
- no predictions;
- no DEVELOPMENT metrics;
- no FINAL_HOLDOUT/economic-holdout values;
- no execution authority.

## 8. One research-universe identity

D2W requires the TRAIN and DEVELOPMENT material to produce the same stable research-universe identity.

This identity contains instrument semantics rather than bar bytes:

- symbols;
- venue;
- quote currency;
- timeframe;
- price tick;
- quantity step.

Material hashes remain separate and continue to bind the exact bytes/bars for each partition.

## 9. Research split hash

D2W creates one deterministic `research_split_hash` from:

- D2W split policy;
- collection id;
- D2U plan fingerprint;
- D2V complete campaign seal fingerprint;
- D2U partition material fingerprint;
- research-universe identity hash;
- TRAIN warmup universe hash;
- TRAIN universe hash;
- DEVELOPMENT warmup universe hash;
- DEVELOPMENT universe hash;
- TRAIN start;
- DEVELOPMENT start;
- DEVELOPMENT end;
- warmup bar count.

This is the split identity that downstream D2R and D2S artifacts must share.

Changing a single sealed raw archive, D2U plan, partition boundary or instrument semantic changes this identity or makes the handoff fail before it can be produced.

## 10. Source campaign id

D2W derives a stable source campaign id from the D2U collection id:

`<collection-id>:raw-split-v1`

D2R can consume this id when deriving the TRAIN feature/label/bundle artifacts.

The id does not imply model or execution authority.

## 11. D2W evidence

`OSS3D2W_SEALED_RAW_SPLIT_HANDOFF_EVIDENCE_V1` binds:

- source campaign id;
- collection id;
- D2U plan fingerprint;
- D2V complete seal fingerprint;
- D2U partition material fingerprint;
- D2U assembly evidence fingerprint;
- research split hash;
- research-universe identity hash;
- D2R raw TRAIN source hash;
- D2S raw DEVELOPMENT source hash;
- all four material universe hashes;
- all four partition bar counts;
- split/reverification/downstream policies.

It also records explicit negative-authority statements:

- network used during handoff = false;
- TRAIN label artifact materialized = false;
- DEVELOPMENT label artifact materialized = false;
- prediction values loaded = false;
- DEVELOPMENT metrics computed = false;
- FINAL_HOLDOUT values loaded = false;
- Qlib runtime used = false;
- promotion authorized = false;
- execution authorized = false;
- PAPER execution authorized = false;
- capital authority = `NONE`;
- LIVE = `BLOCKED`.

## 12. D2W material

`OSS3D2W_SEALED_RAW_SPLIT_HANDOFF_MATERIAL_V1` contains:

1. the D2R `RawTrainingMarketSource`;
2. the D2S `RawDevelopmentMarketSource`;
3. the D2W evidence object.

Its fingerprint binds both source hashes plus the evidence fingerprint.

It does not duplicate the underlying bars into a new serialization format.

## 13. Determinism

Repeated D2W handoffs from the same healthy D2V evidence root must reproduce exactly:

- research split hash;
- D2W evidence fingerprint;
- D2W material fingerprint;
- D2R raw source hash;
- D2S raw source hash.

The `now` argument is used only by the upstream D2V offline verification machinery where required; it does not enter the D2W split identity or evidence payload.

## 14. Corruption behavior

If any sealed provider byte is modified after D2V completion, D2W fails during the mandatory D2V offline reverification.

D2W does not:

- redownload the descriptor;
- update the campaign seal;
- substitute a current provider version;
- repair the evidence silently.

The operator must resolve the D2V integrity problem first.

## 15. Downstream D2R compatibility proof

The D2W CI suite takes the produced `RawTrainingMarketSource` and explicitly calls the existing D2R `derive_raw_training_bundle()` as a downstream compatibility test.

The derived D2R artifacts must preserve the exact D2W `research_split_hash` and source/universe identities.

This derivation exists only in the test. D2W production code remains a raw-source handoff.

## 16. D2S sequencing remains unchanged

D2W does not bypass D2S's scientific order.

The future DEVELOPMENT path remains:

1. D2W sealed DEVELOPMENT raw source;
2. D2S causal DEVELOPMENT features;
3. six frozen D2G predictions;
4. durable D2S statistical preregistration;
5. only then DEVELOPMENT labels materialize;
6. D2H/D2E evaluation.

No DEVELOPMENT label is exposed by D2W.

## 17. FINAL_HOLDOUT remains absent

D2W has no FINAL_HOLDOUT parameter, source or loader.

The D2U plan contains no FINAL_HOLDOUT descriptors and the D2V seal must state that FINAL_HOLDOUT values were not loaded.

A D2W evidence object cannot be mutated to claim FINAL_HOLDOUT access without raising a governance error.

## 18. No Qlib runtime

D2W imports D2R/D2S source contracts but does not import or execute Qlib.

Dedicated CI verifies that the `qlib` package is absent before and after D2W tests.

Model fitting remains in the isolated D2G runner at a later stage.

## 19. CI coverage

D2W CI uses the already-certified deterministic D2V mini-campaign fixture and proves:

- no D2V complete seal => D2W refuses handoff;
- a complete D2V campaign produces exact D2R/D2S raw sources;
- D2W makes no new GETs;
- all D2U/D2V identities remain bound;
- repeat offline handoff is identity-stable;
- tampered sealed raw data fails closed;
- D2W TRAIN source works with existing D2R derivation;
- authority/label/metric/Qlib flags cannot be escalated;
- a different collection id without its own D2V seal is rejected.

The workflow additionally re-proves D2V, D2U, D2R, D2S boundaries and Research Authority.

## 20. Explicit non-authority

D2W cannot authorize or perform:

- public or private network access;
- historical acquisition;
- TRAIN feature/label derivation in production code;
- DEVELOPMENT label materialization;
- predictions;
- model fitting;
- Qlib runtime;
- DEVELOPMENT metrics;
- tournament selection;
- strategy promotion;
- FINAL_HOLDOUT access;
- broker access;
- OMS writes;
- Safety writes;
- OrderIntent creation;
- PAPER execution;
- capital movement;
- LIVE trading.

## 21. Next scientific stage

After a **real** D2V campaign seal exists and D2W reproduces this handoff from it, the next stage can derive the real D2R TRAIN bundle and D2S causal DEVELOPMENT feature material under the shared D2W split identity.

That stage must still stop before DEVELOPMENT labels until the six-model D2S preregistration is durable.

D2W itself makes no profitability claim and does not imply that any strategy is suitable for capital.