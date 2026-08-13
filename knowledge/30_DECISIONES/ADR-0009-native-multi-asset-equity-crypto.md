# ADR-0009 — Native Multi-Asset Control Plane: US Equities + Crypto

- Status: **ACCEPTED FOR R6 IMPLEMENTATION**
- Date: 2026-08-12
- Scope: AUTO-TRADE R6 PAPER architecture and Mac operator experience
- Capital authority: **no widening by this ADR**
- LIVE: **BLOCKED**

## Context
R6 already has a deeply certified US-equity PAPER lifecycle and a separate BTC/USD Crypto PAPER rehearsal. The separate lab proved that crypto can reuse important deterministic safety components, but leaving crypto as an isolated side application would create duplicated UX, duplicated operator concepts and a long-term risk of two inconsistent control planes.

The opposite shortcut — treating crypto as merely another symbol in the equity canary — is unsafe. Market hours, fractional precision, order capabilities, broker-side protection and failure modes differ by product. In particular, the certified equity canary depends on an equity bracket contract, while the current broker crypto surface exposes a different order capability set.

## Decision
AUTO-TRADE becomes **natively multi-asset** with one operator Control Center and one portfolio-level safety authority, while preserving explicit product-specific execution contracts.

### Shared deterministic core
The following remain shared across Equities and Crypto:
- Capital Safety Kernel;
- portfolio gross/net exposure limits;
- per-order/per-position/per-strategy limits;
- daily loss/drawdown circuits;
- Health/kill-switch restrictions;
- OMS durable identity and idempotency semantics;
- Event Ledger;
- durable broker ambiguity state;
- reconciliation principles;
- human/operator authority separation;
- PAPER/LIVE environment boundaries.

### Product-specific layer
Each asset class must supply and bind:
- explicit asset class and venue identity;
- exact broker asset attestation;
- `ProductCapabilities` profile;
- market-hours model;
- market-data adapter;
- price/quantity precision rules;
- allowed broker order types/TIFs;
- execution adapter;
- protection lifecycle;
- qualification evidence.

The product profile is an allowlist and never grants capital authority by itself.

## Initial product matrix

| Capability | US Equities | Crypto |
|---|---|---|
| Asset class | `US_EQUITY` | `CRYPTO` |
| Market model | session-clocked | continuous 24/7 |
| Fractional | broker-attested | broker-attested / required by current profile |
| Margin/short | broker-attested + policy constrained | denied by current R6 crypto policy |
| R6 protection | certified equity bracket | separate crypto stop-limit lifecycle, not yet execution-certified |
| Current PAPER execution | structurally implemented/certified lifecycle | **not yet enabled** |
| Current rehearsal | yes | BTC/USD yes |
| LIVE | blocked | blocked |

## Crypto protection decision
The equity bracket implementation SHALL NOT be reused for crypto.

The first crypto PAPER execution design will use a distinct lifecycle:

`ENTRY_INTENT -> SAFETY/OMS -> ENTRY_SUBMITTED/UNKNOWN -> ENTRY_RECONCILED -> CONFIRMED_FILLED_QTY -> PROTECTIVE_STOP_LIMIT_INTENT -> SAFETY/OMS/DEFENSIVE_GUARD -> PROTECTION_SUBMITTED/UNKNOWN -> PROTECTION_RECONCILED`

Important: `stop_limit` is not treated as guaranteed liquidation. A triggered stop-limit can remain unfilled if price trades through its limit. Therefore the system must surface `PROTECTION_AT_RISK` / reconciliation-required states rather than claim that a position is fully safe merely because a stop-limit order exists.

No take-profit + stop-loss pair is introduced until the system can prove safe interaction with broker wash-trade/self-trade protections, partial fills, cancel/replace, and exact position-bound reduction semantics.

## ProductCapabilities contract
`src/autotrade/product_profile.py` is the first shared product-boundary primitive.

It must:
- reject unknown/untrusted provenance;
- make asset class explicit;
- bind the profile fingerprint to broker evidence;
- allow only product-supported order/TIF combinations;
- reject crypto margin/short capability overclaims;
- reject equity bracket as a crypto protection model;
- remain independent from strategy profitability and execution authority.

Future broker writes must cryptographically/deterministically bind the product profile fingerprint into the prepared/execution package so a profile cannot change between validation and POST.

## Native Mac Control Center
The user-facing Mac entry point becomes a multi-asset hub:
- **Equities** routes to the existing guided R6 US-equity Control Center.
- **Crypto** routes to the crypto PAPER rehearsal and later the certified crypto execution flow.
- shared PAPER credentials remain ephemeral;
- product state/evidence remains separate;
- UI never labels a route executable unless the backend machine-readable status says so.

The legacy `ABRIR_CRYPTO_PAPER.command` may remain as a convenience alias during migration, but it is no longer the conceptual product boundary.

## Browser launch policy
macOS launcher behavior must use `/usr/bin/open <localhost-url>` first, without shell invocation, and fall back to Python `webbrowser` only if necessary. Failure to open the browser must never stop the localhost server; the terminal must print the URL as a deterministic fallback.

## Research implications
Native crypto support does not imply strategy qualification. Crypto research requires its own evidence campaign with:
- 24/7/weekend chronology;
- realistic fees, spread, slippage and latency;
- fractional/minimum sizing constraints;
- volatility/liquidity/jump regimes;
- walk-forward/OOS and protected holdout;
- multiple-testing controls;
- forward/PAPER observation before promotion.

Equity and crypto strategies may share portfolio allocation later, but allocation must consume one shared portfolio risk budget and respect per-product caps.

## Rejected alternatives
### 1. Replace the equity canary with BTC
Rejected. It would destroy product-specific invariants and falsely reuse bracket/market-clock assumptions.

### 2. Keep two independent apps/control planes permanently
Rejected. It duplicates operator state and increases drift risk between safety semantics.

### 3. Generic broker writer with runtime conditionals only
Rejected. A generic writer makes product leakage too easy. Product-specific adapters behind a shared authority interface are easier to audit and test adversarially.

### 4. Enable crypto POST immediately after read rehearsal
Rejected. Connectivity and Safety validation do not prove protection, ambiguity recovery, reconciliation or qualification.

## Consequences
Positive:
- one coherent operator experience;
- shared portfolio safety;
- explicit product isolation;
- extensible path to future asset classes without symbol heuristics;
- crypto can be tested 24/7 without weakening the equity contract.

Costs:
- more explicit contracts and evidence;
- crypto needs its own writer/protection/reconciliation certification;
- portfolio accounting must eventually distinguish product/venue semantics while remaining globally consistent.

## Required closure evidence before crypto PAPER POST
1. ProductCapabilities bound into candidate/prepared package.
2. Generic crypto pair asset adapter (not BTC hard-coded at execution boundary).
3. crypto sizing/precision normalizer.
4. crypto writer with durable client-order identity and UNKNOWN-before-I/O semantics.
5. entry fill reconciliation including partial fills.
6. protective stop-limit lifecycle for exact confirmed position quantity.
7. restart/crash/cancel-replace adversarial tests.
8. broker wash-trade interaction tests.
9. trade update/poll reconciliation evidence.
10. bounded human-approved PAPER canary.
11. qualification artifact including protection outcome and residual risk.
12. permanent CI cross-product authority gate.

Until these close: **Crypto execution = DISABLED; Crypto rehearsal/read = allowed under R6 PAPER constraints.**
