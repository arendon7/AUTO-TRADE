# OSS-3D2V — Restart-safe real historical acquisition campaign

Status: DEVELOPMENT research infrastructure only.

D2V executes the already-certified OSS-3D2U historical collection plan. It does not select assets, dates, partitions, labels, models, metrics or trading actions.

## 1. Frozen upstream identity

D2V consumes `canonical_oss3d2u_collection_plan()` unchanged.

The current D2U v1 family is:

- provider: Binance Spot public archive;
- symbols: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`;
- interval: `1h`;
- archive granularity: monthly;
- first month: `2023-01`;
- last month: `2025-12`;
- descriptors: `36 × 3 = 108`;
- WARMUP: first 20 hourly bars;
- TRAIN: `2023-01-01T20:00:00Z` through `2025-01-01T00:00:00Z`;
- DEVELOPMENT: calendar year 2025;
- FINAL_HOLDOUT descriptors: absent.

D2V contains no alternate descriptor constructor. Changing this family requires a new upstream protocol/version, not a D2V runtime flag.

## 2. Why D2V exists

D2T proved that one already-downloaded public archive can be normalized into an immutable hash-bound `MarketDataset`.

D2U proved that a finite descriptor family can be preregistered before network use, acquired by the triple-checksum protocol, and assembled into exact WARMUP/TRAIN/DEVELOPMENT material.

D2V adds the operational durability needed to execute the entire real family safely:

1. persistent evidence outside the git repository;
2. append-only descriptor seals;
3. restart/reconciliation semantics;
4. full raw evidence reverification before reuse;
5. atomic directory publication;
6. a final complete-collection seal only after the D2U assembler succeeds.

## 3. Evidence root

Real evidence must resolve outside the AUTO-TRADE repository.

The runtime rejects:

- the repository root itself;
- any descendant of the repository root;
- a symlink used as the evidence root.

`.gitignore` additionally excludes common D2V evidence directory names as defense in depth. This ignore rule is not the primary control: the runtime path boundary is.

The evidence root contains local research evidence and SQLite ledgers. Raw provider archives are not committed to GitHub.

## 4. Durable ledgers

### 4.1 D2U plan ledger

Before a real transport can be built, D2V creates/reuses:

`oss3d2u-plan.sqlite3`

and executes:

1. `preregister(plan)`;
2. `require_exact(plan)`.

This preserves the D2U rule that the full finite family exists durably before the first GET.

### 4.2 D2V campaign ledger

D2V creates:

`oss3d2v-campaign.sqlite3`

with two append-only tables:

- `oss3d2v_descriptor_seals`;
- `oss3d2v_campaign_seals`.

UPDATE and DELETE are blocked by SQLite triggers raising `OSS3D2V_APPEND_ONLY`.

There is intentionally no mutable `RUNNING -> DONE` row. Current campaign state is derived from immutable seals plus reverified filesystem evidence.

## 5. Descriptor evidence layout

Each descriptor has one final directory:

`<evidence-root>/<SYMBOL>/<YYYY-MM>/`

The exact file set is:

1. provider ZIP archive;
2. provider `.CHECKSUM`;
3. `d2t-snapshot.json`;
4. `d2u-acquisition-receipt.json`.

Extra files or nested directories cause reverification failure.

## 6. Acquisition authority

D2V has no independent HTTP implementation.

Missing descriptors may be acquired only through D2U:

`acquire_preregistered_archive(...)`

which already enforces:

`CHECKSUM A -> ZIP -> CHECKSUM B`

with:

- three GET requests exactly;
- zero implicit retries;
- exact preregistered paths;
- exact final URL/no redirects;
- byte-identical CHECKSUM A/B;
- provider SHA-256 verification before ZIP parsing;
- no credentials;
- no private/trading endpoint.

The real transport is built only after durable D2U plan verification.

## 7. Network default

The operator CLI defaults to offline verification/status mode.

A user must explicitly supply:

`--execute-public-get`

to allow acquisition of descriptors that do not already verify locally.

Without that flag:

- no transport is accepted;
- no real transport is constructed;
- existing sealed evidence is reverified;
- unsealed final evidence may be reconciled after full reverification;
- missing descriptors are counted and reported;
- no campaign seal is produced until the family is complete.

For the canonical family, a clean first execution requires at most 324 planned HTTP calls: three GETs per 108 descriptors. There is no hidden retry loop.

## 8. Atomic persistence

A newly acquired descriptor is never written directly into its final directory.

D2V writes the four-file evidence set into an isolated staging namespace, rereads it, reconstructs `AcquiredArchiveMaterial`, and verifies its fingerprint against the in-memory acquired material.

Only after that verification does D2V perform an atomic directory rename into the final symbol/month path.

An existing final descriptor directory is never overwritten.

A preexisting staging directory for the same descriptor is treated as ambiguous local state and requires explicit review; it is never used as trusted evidence.

## 9. Restart semantics

### 9.1 Descriptor already sealed

D2V does not trust the SQLite seal alone.

It rereads:

- ZIP bytes;
- CHECKSUM text;
- canonical D2T snapshot;
- canonical D2U receipt.

Construction of `AcquiredArchiveMaterial` re-executes the D2T source verification chain. D2V then reconstructs the expected descriptor seal using the original `sealed_at` and requires the fingerprint to equal the durable seal.

Only then is the descriptor considered reusable.

### 9.2 Final evidence exists but descriptor seal is missing

This represents a legitimate crash window: directory publication may have completed immediately before the SQLite insert.

D2V fully reverifies the final evidence. If and only if it reproduces valid D2U/D2T material bound to the exact plan, D2V creates the missing append-only descriptor seal without performing another network acquisition.

### 9.3 Seal exists but evidence is missing or altered

Fail closed.

D2V does not redownload over or around sealed evidence. Missing/tampered raw material makes the seal non-reproducible and requires operator investigation.

### 9.4 Partial or unexpected final directory

Fail closed.

The expected four-file set is exact. D2V does not guess which file is authoritative.

## 10. Descriptor seal

`OSS3D2V_DESCRIPTOR_EVIDENCE_SEAL_V1` binds:

- D2U collection id;
- D2U plan fingerprint;
- descriptor fingerprint;
- symbol/month;
- archive SHA-256;
- checksum payload SHA-256;
- D2T snapshot artifact hash;
- normalized dataset hash;
- D2U acquisition receipt hash;
- complete `AcquiredArchiveMaterial` fingerprint;
- canonical relative evidence directory;
- seal timestamp;
- restart/staging policies.

Authority fields are fixed:

- FINAL_HOLDOUT values requested: false;
- execution authorized: false;
- PAPER execution authorized: false;
- capital authority: `NONE`;
- LIVE: `BLOCKED`.

## 11. Complete collection seal

D2V cannot create `OSS3D2V_COMPLETE_COLLECTION_SEAL_V1` from descriptor counts alone.

After all 108 descriptors have reverified, D2V calls the certified D2U assembler with:

- all 108 D2T snapshots in exact plan order;
- one D2U acquisition receipt fingerprint per descriptor.

Only successful assembly permits the final seal.

The complete seal binds:

- D2U plan fingerprint;
- exact ordered descriptor-seal fingerprint family;
- D2U assembly evidence fingerprint;
- D2U partition material fingerprint;
- TRAIN warmup universe hash;
- TRAIN universe hash;
- DEVELOPMENT warmup universe hash;
- DEVELOPMENT universe hash;
- campaign/restart/storage policies.

Therefore a collection with 108 individually valid files but a cross-file gap, cross-asset support mismatch, wrong partition geometry or other D2U assembly failure cannot be called complete.

## 12. Rerun behavior

A complete healthy rerun in default offline mode should report:

- network acquisitions: 0;
- sealed descriptors reverified/reused: 108;
- unsealed finals reconciled: 0;
- missing: 0;
- complete: true;
- same durable campaign seal fingerprint.

This is the normal reproducibility check before downstream research consumes the real collection.

## 13. Corruption behavior

If one byte of a sealed provider ZIP changes, D2V fails while reloading that descriptor. It does not reach acquisition for that descriptor and does not silently replace the evidence with the provider's current version.

This matters because provider archives may legitimately be corrected or replaced over time. D2V preserves the exact bytes actually acquired for this research campaign.

## 14. Operator CLI

Offline verification/status:

```bash
python scripts/run_oss3d2v_real_acquisition.py \
  --evidence-root /absolute/path/outside/AUTO-TRADE
```

Execute missing public GET acquisitions:

```bash
python scripts/run_oss3d2v_real_acquisition.py \
  --evidence-root /absolute/path/outside/AUTO-TRADE \
  --execute-public-get
```

The CLI prints machine-readable JSON with descriptor counts, acquisition/reuse/reconciliation counts, completion state and campaign seal fingerprint when complete.

## 15. CI policy

Ordinary PR CI does not download the real 108-file campaign.

It uses deterministic local ZIP/CHECKSUM fixtures and an injected read-only transport to prove:

- empty offline status makes zero GETs;
- complete acquisition makes exactly three GETs per descriptor;
- full campaign seals and assembles;
- complete offline rerun makes zero GETs;
- sealed raw corruption fails closed;
- crash-window reconciliation uses no network;
- ledger UPDATE/DELETE fail;
- repository-local evidence root fails;
- no-network mode rejects an injected transport;
- authority cannot be escalated.

The CI also re-proves D2U/D2T/R3 and Research Authority boundaries and verifies Qlib is absent.

## 16. Explicit non-authority

D2V does not authorize or perform:

- FINAL_HOLDOUT access;
- label construction/reveal;
- prediction generation;
- Qlib/model fitting;
- development metrics or tournament evaluation;
- strategy promotion;
- broker access;
- OMS writes;
- Safety writes;
- OrderIntent creation;
- PAPER execution;
- capital movement;
- LIVE trading.

D2V's only bounded side effect is public historical-data acquisition plus local evidence persistence outside Git.

## 17. Next boundary after D2V

Only after a real canonical D2V collection seal exists should the project construct real D2R TRAIN material and real D2S DEVELOPMENT preregistration from the sealed hashes.

The order remains:

1. freeze data family;
2. acquire and seal raw evidence;
3. derive TRAIN material;
4. preregister DEVELOPMENT predictions/statistical plan before labels;
5. evaluate DEVELOPMENT;
6. keep FINAL_HOLDOUT inaccessible until its separate governance gate.

No D2V result, including a complete collection seal, is a statement that any strategy is profitable or safe for capital.