# AUTO-TRADE Skill — Native Multi-Asset Safety

## Purpose
Use this skill for any change that crosses asset classes or introduces a new product into the shared AUTO-TRADE control plane.

The design principle is **shared safety, separate product semantics**. Capital Safety, OMS, durable identity, Event Ledger, reconciliation, health/circuit state and authority separation are shared. Market hours, precision, order capabilities, broker protection and qualification evidence are product-specific.

## Architectural invariant
No strategy, UI, AI agent or broker adapter may infer that a generic order is executable merely because Safety approves its notional. Execution additionally requires an explicit, fresh, broker-observed `ProductCapabilities` profile for the exact asset class/venue and a product-specific execution contract.

## Required layers
Every supported asset class must have these layers, in this order:

1. **Instrument identity** — canonical symbol/pair and explicit asset class.
2. **Broker asset attestation** — exact venue, status, tradability and precision/capability evidence.
3. **ProductCapabilities** — normalized fail-closed capability envelope bound to the attestation fingerprint.
4. **Market-data contract** — product-specific endpoint, freshness and integrity rules.
5. **Strategy intent** — no broker authority.
6. **Capital Safety** — global and strategy capital limits.
7. **OMS** — durable identity/idempotency/lifecycle.
8. **Product execution adapter** — only order forms supported by the profile.
9. **Protection lifecycle** — product-specific broker-side or defensive protection.
10. **Reconciliation/evidence** — broker truth projected back into durable state.

Skipping or merging these layers in a way that weakens independent checks is prohibited.

## Product boundary rules
- Asset class must be explicit and provenance-bound.
- Unknown classes fail closed.
- A product profile cannot widen broker metadata.
- Capabilities are allowlists, never denylists.
- No generic fallback order type or time-in-force.
- No automatic conversion from unsupported order type to another order type.
- No silent rounding. Price/quantity normalization must be deterministic, direction-aware and tested at boundaries.
- A product adapter cannot call another product's protection implementation.
- Product changes must include cross-product negative tests proving the wrong route cannot execute.

## Shared capital rules
All products share one portfolio-level risk truth. A crypto position and an equity position both consume portfolio gross/net exposure and daily loss/drawdown budgets. Per-product limits may be stricter, but cannot bypass the shared maximums.

The system must be able to apply:
- portfolio gross exposure cap;
- portfolio net exposure cap;
- strategy cap;
- per-position cap;
- per-order cap;
- leverage/margin policy;
- daily loss circuit;
- drawdown circuit;
- open-order capacity;
- stale/unknown broker-state block;
- health/kill-switch restrictions.

Product-specific limits are additional constraints, never replacements.

## Authority matrix
Research/AI may:
- discover strategies;
- evaluate data;
- propose parameters;
- create non-executable intents;
- explain diagnostics.

Research/AI may not:
- mint execution authority;
- select a less restrictive product profile;
- disable a protection model;
- clear a circuit;
- resolve ambiguous broker state by assumption;
- promote PAPER to LIVE.

Human/operator authority may approve bounded PAPER experiments only where the current certified lifecycle explicitly supports it.

## Multi-asset UI rules
The Control Center must make product boundaries visible:
- Equities and Crypto are first-class modes, not hidden flags.
- Each mode shows its market-hours model, supported protection model and current execution status.
- Shared credentials may be entered ephemerally, but asset-class state and evidence remain separate.
- UI labels such as `AVAILABLE`, `READY`, `PROTECTED`, `EXECUTABLE` or `LIVE` must come from machine-readable backend state, not optimistic frontend assumptions.
- A mode that is rehearsal-only must say so clearly and expose no hidden execution endpoint.

## Qualification gates for a new product
A product is not considered natively tradable until all of these are certified:
1. exact broker asset capability attestation;
2. exact market-data adapter;
3. ProductCapabilities binding;
4. sizing/precision normalization;
5. product writer with durable idempotency and ambiguity semantics;
6. product protection lifecycle;
7. restart/crash tests;
8. reconciliation and streaming/polling evidence path;
9. bounded PAPER canary;
10. qualification evidence across normal and adverse scenarios;
11. Mac/UI UAT if exposed to operator;
12. permanent CI authority/product-boundary gate.

## Cross-product adversarial tests
For every new product, include tests that prove:
- equity profile + crypto adapter => reject before I/O;
- crypto profile + equity bracket => reject before I/O;
- crypto order with equity-only TIF => reject;
- equity order with crypto-only market-hours assumption => reject;
- wrong venue/profile fingerprint => reject;
- stale profile => reject at final authority boundary;
- position/open-order mismatch across product ledgers => reconcile/HALT;
- duplicate client-order identity across product namespaces => conflict, never reuse accidentally;
- one product's degraded health cannot be hidden by another product's healthy state.

## Change procedure
1. Read canonical state/debt and the product-specific skill.
2. Verify current primary broker documentation.
3. Register new P1/P2 debt before implementation if the change widens capital-sensitive capability.
4. Implement the smallest reversible contract first.
5. Add positive, negative and cross-product tests.
6. Keep broker write disabled until structural contracts are green.
7. Capture exact-head CI evidence.
8. Only then consider a bounded PAPER canary.

## Completion definition
A native multi-asset implementation is complete only when the operator can select an asset class from one Control Center while the backend still routes through explicit independent product contracts and the same deterministic capital authority model. Visual unification without product isolation is not completion; shared code without shared portfolio risk is also not completion.
