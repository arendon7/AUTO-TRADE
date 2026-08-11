# R4 — Instrument Master precertification

Status: **NOT YET DEBT-CLOSED**

This evidence note records the candidate boundary for `TD-R4-001`. It does not certify R4, does not create PAPER/LIVE authority and does not authorize broker execution.

Candidate invariants:
- authoritative execution-sensitive metadata is structurally separate from research `InstrumentMetadata`;
- append-only contiguous per-instrument versions;
- canonical identities reject surrounding-whitespace aliases;
- tick/step and min/max quantity/notional semantics fail closed;
- min/max quantity must align to authoritative `quantity_step`;
- explicit TRADING/HALTED/DISABLED/UNKNOWN status;
- source/version/as-of/source-payload SHA-256 provenance;
- deterministic payload fingerprint;
- every durable read cross-checks embedded payload fingerprint against independently persisted fingerprint column;
- payload/column/JSON corruption blocks reads and subsequent version publication;
- stale/future/expired metadata fails closed;
- `research.InstrumentMetadata` cannot be published into the authoritative store;
- machine-readable `AuthoritativeInstrumentRules@1` contract is required by CI.

`TD-R4-001` remains OPEN until full CI passes on this hardened tree and the evidence is then referenced from the machine-readable Debt Register.

**LIVE TRADING: BLOCKED.**
