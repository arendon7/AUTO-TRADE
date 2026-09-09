# OSS-3D3B — Certified Artifact Real Rehydration

## Purpose

OSS-3D3B converts the original real-data evidence already certified by D2Y into a live in-memory D3A raw split without reacquiring Binance.

The source is the exact GitHub Actions artifact whose ZIP hash, byte size, source inventory, tarball and campaign identities were frozen by D2Y. The artifact remains a transport container only; D3B never treats artifact availability as the scientific root. D2Y and D2Z remain the durable cryptographic contracts.

This stage exists so the current real TRAIN/DEVELOPMENT history can move into downstream research immediately while the certified original artifact is still available, rather than spending another 297 provider GETs merely to reproduce data that is already preserved and verified.

## Canonical source

D3B expects the exact D2Y Actions artifact ZIP:

- 5 exact top-level members;
- exact ZIP byte count;
- exact D2Y artifact ZIP SHA-256;
- exact first-pass result SHA-256;
- exact offline-reverification result SHA-256;
- exact source inventory SHA-256;
- exact evidence tar SHA-256;
- exact tar checksum-file SHA-256.

The artifact ZIP is supplied as a local file. The D3B rehydrator itself has no network or artifact-download authority.

## Container verification

Before any descriptor is presented to D3A, D3B requires the artifact ZIP member family and order to be exactly:

1. `d2x-v2-real-result.json`
2. `d2x-v2-offline-reverify-result.json`
3. `d2x-v2-evidence-inventory.json`
4. `oss3d2x-v2-real-evidence-20260908.tar.gz`
5. `oss3d2x-v2-real-evidence-20260908.tar.gz.sha256`

No extra ZIP member, directory, symlink or duplicate member is accepted.

The source inventory must be canonical UTF-8 JSON and remains the D2Y 398-file family:

- 99 descriptors × 4 files = 396 descriptor evidence files;
- `oss3d2u-plan.sqlite3`;
- `oss3d2v-campaign.sqlite3`.

D3B validates every inventory path, byte count and SHA-256.

## Safe tar reading

The evidence tar is read in memory. D3B does not call `extract()` or `extractall()`.

Every member must:

- be below the single certified root `oss3d2x-v2-evidence`;
- use a safe relative POSIX path;
- be a regular file or directory only;
- correspond exactly to one source-inventory entry when it is a file;
- reproduce the inventory byte count and SHA-256.

The regular-file set must equal the inventory set exactly. Extra, missing, duplicate, symlink, hardlink or unsafe-path members fail closed.

## Original 99 descriptor evidence

For every canonical D2U descriptor, D3B reads exactly four original files from the verified source family:

- provider archive ZIP;
- provider CHECKSUM text;
- original D2T snapshot JSON;
- original D2U acquisition receipt JSON.

The original D2T snapshot document is rebound to the D2Z stable descriptor identity:

- descriptor fingerprint;
- archive SHA-256;
- CHECKSUM payload SHA-256;
- D2T artifact hash;
- normalized dataset hash.

The original receipt is also checked against the D2Y/D2Z material identity and must still declare the original research-only acquisition semantics: three HTTP 200 responses, stable CHECKSUM, exact final URLs, zero retry, public network use, no credentials, no trading endpoint, no FINAL_HOLDOUT and no execution/PAPER/capital/LIVE authority.

The receipt fingerprint is then recomputed from its canonical JSON payload and passed to D3A only as audit lineage.

## D2Z and D3A gates

D3B does not implement another market normalizer or splitter.

The original archive/CHECKSUM bytes are passed to `build_canonical_stable_rehydrated_raw_split(...)`, which:

1. re-verifies canonical D2Y;
2. re-verifies canonical D2Z;
3. rebuilds D2T from each original provider archive;
4. requires exact D2Z stable material identity for all 99 descriptors;
5. calls the existing D2U assembler;
6. requires exact D2Y TRAIN universe;
7. requires exact D2Y DEVELOPMENT universe;
8. creates only the existing D2R raw TRAIN and D2S pre-label DEVELOPMENT sources.

## Stronger original-receipt equality

D3A is deliberately tolerant of a future reacquisition receiving new timestamp-bound receipt hashes. Its scientific fingerprint therefore does not require a future D2U partition-material fingerprint to equal the original D2Y one.

D3B is different: it consumes the **original receipt family from the original D2Y artifact**.

Therefore D3B requires:

`rehydrated D2U partition material fingerprint == original D2Y D2U partition material fingerprint`

Canonical value:

`7cac5172e70ecf6b36c9d51e433ce57184586b08ff525ea94af63ef331a3f873`

Failure of this equality means the original evidence did not reproduce its own certified assembly and must fail closed.

## Result surface

`scripts/run_oss3d3b_certified_artifact_rehydration.py` writes a compact canonical JSON result. It contains hashes and authority state only, not OHLCV values.

The result records:

- D3B material/evidence fingerprints;
- D3A scientific/material/evidence fingerprints;
- artifact/inventory/tar hashes;
- D2Z stable root;
- original + rehydrated partition fingerprint;
- raw TRAIN source hash;
- raw DEVELOPMENT source hash;
- TRAIN universe hash;
- DEVELOPMENT universe hash;
- descriptor/source-file counts;
- authority-denial state.

The result can be durably frozen in a later evidence seal without treating the temporary artifact itself as a permanent source of truth.

## CI versus real execution

Ordinary D3B PR CI performs **no artifact download** and no network request. It certifies the parser and governance using deterministic synthetic artifact ZIP/tar/inventory fixtures and re-proves D3A/D2Z/D2Y upstream boundaries.

After D3B code is certified, a separate operational child branch may download artifact ID `10088057589`, verify the exact D2Y ZIP SHA-256 before invocation, and run the certified D3B SHA. That operational workflow must not change D3B code or obtain model/execution authority.

## Why Binance reacquisition is deferred

A new Binance reacquisition remains useful as a recoverability/provider-drift test, especially after the Actions artifact expires. It is not necessary to unblock the current real-data research campaign because the original bytes and receipts are presently available and already certified.

If/when provider reacquisition is performed, D3A—not D3B's original-partition equality rule—is the correct scientific gate because new receipt timestamps are expected.

## FINAL_HOLDOUT and Qlib boundary

D3B does not materialize TRAIN labels, DEVELOPMENT labels, predictions or metrics. It does not install or run Qlib. It does not load FINAL_HOLDOUT or issue/consume any holdout permit.

The next research stage may consume the D3B-produced raw D2R/D2S sources under the already-certified supervised ordering rules, but D3B itself cannot cross that boundary.

## Authority

Canonical D3B evidence remains:

- `network_used_by_rehydrator = false`
- `provider_network_used_by_rehydrator = false`
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

D3B is a real-data provenance bridge, not a trading or profitability claim.
