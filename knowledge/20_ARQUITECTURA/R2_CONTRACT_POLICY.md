# R2 — MACHINE-READABLE CONTRACT POLICY

Date: 2026-08-10
Track: v0.28R / R2

## Purpose
Capital-sensitive messages and durable state must not change shape silently. R2 therefore keeps a versioned registry at `src/autotrade/contracts/registry.json` and explicit serializers in `contract_payloads.py`.

## Rules
- Contract IDs are `Name@integer-version`.
- Current runtime objects must serialize through an explicit binding and validate against their declared contract.
- Unknown fields fail closed unless a contract explicitly opts into extra fields.
- Decimal values are encoded as finite strings to avoid float ambiguity.
- Timestamps are ISO-8601 and timezone-aware; nullable timestamps are explicit.
- Nested capital-sensitive objects reference another registered contract rather than validating only as a generic object.
- Registry and contract fingerprints are deterministic SHA-256 values.

## Compatibility
Two policies exist:

### `strict`
Any shape change requires a new version and explicit migration/review. Fill, execution, order lifecycle, reservations, safety state, risk telemetry and ledger evidence are strict by default.

### `additive`
A later version may preserve compatibility only when:
- contract name is unchanged;
- version increases;
- every old field is unchanged;
- newly added fields are optional;
- extra-field policy is unchanged.

Removing/changing an existing field or adding a required field is breaking.

## CI evidence
`python scripts/check_contract_registry.py` is part of Core Safety Tests. Pytest additionally validates real domain objects against the registry, nested contracts, malformed values and compatibility failures.

## Version-count policy
Historical counts such as 77 or 207 schemas are evidence that the old system had broad machine-readable coverage, **not a quota**. v0.28R adds contracts when a real boundary exists and never creates dummy schemas to match a historical count.

## Change workflow
1. Change domain/runtime behavior.
2. Update explicit serializer binding.
3. Add a new contract version when shape/semantics change.
4. Add migration/compatibility tests.
5. Run Core Safety Tests.
6. Update ADR/debt register when the change is architectural.

## Capital
Contract validation is a safety boundary, not trading authorization.

**LIVE TRADING: BLOQUEADO.**
