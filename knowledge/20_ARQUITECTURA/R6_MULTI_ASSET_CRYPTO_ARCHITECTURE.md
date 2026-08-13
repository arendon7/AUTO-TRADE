# R6 Native Multi-Asset Architecture — Equities + Crypto

## 1. Objective
Evolve AUTO-TRADE from a US-equity-first PAPER implementation plus a separate BTC/USD lab into one native multi-asset system that can ultimately trade both US equities and crypto without weakening the certified capital-safety model.

This document defines architecture and gates. It does **not** authorize crypto broker writes.

## 2. System shape

```text
Research / Strategy
       |
       v
Non-executable Intent
       |
       +----------------------------+
       |                            |
       v                            v
Instrument / Asset Attestation   Market Data Attestation
       |                            |
       +-------------+--------------+
                     v
             ProductCapabilities
                     |
                     v
            Capital Safety Kernel
                     |
                     v
                    OMS
                     |
          +----------+----------+
          |                     |
          v                     v
 US Equity Adapter       Crypto Adapter
 bracket lifecycle       entry/protection lifecycle
          |                     |
          +----------+----------+
                     v
                Alpaca PAPER
                     |
                     v
       Reconciliation / Event Evidence
                     |
                     v
              Shared Portfolio Truth
```

## 3. Shared control plane
The following components are asset-agnostic only in the sense that they consume explicit, validated inputs:

### Capital Safety
Global authority over:
- max order notional;
- max position notional;
- strategy gross exposure;
- portfolio gross/net exposure;
- leverage policy;
- daily loss;
- drawdown;
- open-order count;
- market-data freshness;
- price-deviation sanity;
- Safety state version/circuit state.

Crypto may have tighter caps, but cannot bypass the global portfolio rules.

### OMS
Owns:
- intent identity;
- idempotency;
- durable order lifecycle;
- external handoff authorization;
- ambiguity semantics;
- replace/cancel state where supported;
- binding to the exact Safety decision and market snapshot.

Product adapters cannot mutate order state directly to fabricate an executable lifecycle.

### Event Ledger
Records tamper-evident control events and broker lifecycle evidence. A product adapter may append only events permitted by its lifecycle.

### Reconciliation
Broker truth dominates local optimism. Unknown, conflicting or missing external state blocks risk-increasing action.

## 4. Product boundary

### 4.1 ProductCapabilities
Every broker-facing asset receives a normalized capability envelope containing:
- explicit asset class;
- venue;
- market-hours model;
- allowed broker order types;
- allowed TIFs;
- fractional capability;
- margin capability;
- short capability;
- protection model;
- source and source fingerprint;
- observation timestamp;
- deterministic profile fingerprint.

The profile is evidence, not authorization.

### 4.2 Binding requirement
Before any future broker write, these identities must agree:

```text
asset_attestation.fingerprint
    -> product_profile.source_fingerprint
    -> product_profile.fingerprint
    -> candidate.product_profile_fingerprint
    -> prepared_package.product_profile_fingerprint
    -> final_write_guard.product_profile_fingerprint
```

Any mismatch or stale profile produces zero broker I/O.

## 5. US-equity route
The existing R6 route remains intact:
- US-equity asset preflight;
- session market clock;
- equity market data;
- whole-share first canary constraints where currently certified;
- Capital Safety;
- OMS;
- explicit human PAPER decision;
- equity bracket payload;
- final freshness/Safety guards;
- single POST;
- trade_updates/reconciliation/evidence.

The multi-asset work must not widen this route accidentally.

## 6. Crypto route

### 6.1 Current certified-safe surface
Current crypto work supports:
- PAPER account read;
- BTC/USD asset metadata read;
- positions/open-orders read;
- BTC/USD orderbook/latest trade read;
- Capital Safety evaluation;
- local OMS VALIDATED;
- no persistent crypto candidate;
- no broker POST.

### 6.2 Target native route

```text
SELECT CRYPTO
  -> exact pair asset attestation
  -> ProductCapabilities
  -> account/position/open-order reconciliation
  -> crypto market snapshot
  -> normalized entry intent
  -> Capital Safety
  -> OMS VALIDATED
  -> human-approved bounded PAPER package
  -> entry writer
  -> UNKNOWN before network I/O
  -> ACK/reconcile entry
  -> confirmed filled quantity
  -> protection intent for <= confirmed filled quantity
  -> protection Safety/defensive guard
  -> protective stop-limit writer
  -> ACK/reconcile protection
  -> monitor partial/terminal states
  -> qualification evidence
```

## 7. Crypto execution state model
Proposed product lifecycle states are deliberately separate from generic OMS status:

```text
READ_ONLY
ENTRY_PREPARED
ENTRY_SUBMISSION_UNKNOWN
ENTRY_ACKNOWLEDGED
ENTRY_PARTIALLY_FILLED
ENTRY_FILLED_UNPROTECTED
PROTECTION_PREPARED
PROTECTION_SUBMISSION_UNKNOWN
PROTECTION_ACKNOWLEDGED
PROTECTED_OPEN
PROTECTION_PARTIALLY_FILLED
PROTECTION_AT_RISK
CLOSING
FLAT_RECONCILED
HALTED_RECONCILIATION_REQUIRED
```

Rules:
- `ENTRY_FILLED_UNPROTECTED` is a high-priority risk state, never a success state.
- No new risk-increasing crypto entry while any crypto position is unprotected or ambiguous during the first-canary phase.
- Protection quantity can never exceed confirmed net long quantity.
- UNKNOWN after POST never blind-retries.
- restart begins with reconciliation before any new order.

## 8. Stop-limit protection model
The first protection implementation is intentionally conservative.

### Entry
Use only an order type permitted by the fresh product profile. The first canary should prefer a price-bounded entry where practical so the experiment has an explicit maximum entry price; exact choice requires final strategy/canary design and current market conditions.

### Protection
After confirmed fill quantity is known:
- create a sell `stop_limit` for at most that confirmed quantity;
- derive stop and limit prices from a deterministic policy and broker price increment;
- require `limit_price <= stop_price` for a long-position protective sell, with a bounded configured protection gap;
- persist the intended protection before network I/O;
- set UNKNOWN before POST;
- reconcile acknowledgement and open quantity;
- monitor for trigger/fill/partial fill/cancel/reject.

### Residual risk
A stop-limit can trigger and still not fill if price moves below the sell limit. Therefore:
- `PROTECTION_ACKNOWLEDGED` means broker order exists, not guaranteed exit;
- a triggered but unfilled protection must become `PROTECTION_AT_RISK`;
- emergency defensive policy must be separately designed and cannot silently submit an unlimited market exit unless explicitly certified.

## 9. Wash-trade/self-trade interaction
Crypto protection must account for broker prevention of potentially interacting opposing orders. The first canary therefore avoids running an independent take-profit sell and stop-limit sell simultaneously until exact broker behavior is tested and a safe OCO-like lifecycle is implemented without assuming unsupported complex-order semantics.

## 10. Precision and sizing
No hard-coded BTC precision is authoritative.

For every pair:
- `min_order_size` from asset attestation;
- `min_trade_increment` from asset attestation;
- `price_increment` from asset attestation;
- deterministic quantity rounding toward lower risk;
- deterministic price normalization appropriate to side/order purpose;
- minimum/maximum notional rechecked after rounding;
- effective notional capped by both product-specific and portfolio Safety limits.

## 11. Market data
Crypto snapshot integrity requires:
- exact canonical pair;
- positive bid/ask/last;
- bid <= ask;
- timezone-aware timestamps;
- bounded component age;
- bounded orderbook/trade skew;
- no future timestamps beyond tolerance;
- response size/status/content validation;
- no fabricated midpoint/last when broker data is missing.

## 12. Strategy research architecture
Execution support and alpha research remain separated.

Crypto strategy research should expose at least:
- return/volatility features by UTC hour and weekday;
- weekend vs weekday behavior;
- spread/liquidity proxies;
- trend/momentum and mean-reversion families;
- volatility-breakout families;
- regime filters;
- turnover/cost sensitivity;
- position-sizing sensitivity;
- tail/gap stress;
- walk-forward/OOS metrics;
- protected holdout metrics;
- multiple-testing adjusted selection;
- forward/PAPER observation.

A strategy cannot reach the execution candidate layer unless its promotion registry says it is qualified for the exact product and pair universe.

## 13. Portfolio risk across products
Future allocation sees one portfolio:

```text
portfolio gross = |equity exposures| + |crypto exposures| + future products
portfolio net   = signed sum of all supported products expressed in a common risk currency
```

Additional product buckets may cap crypto separately, for example:
- max crypto gross as fraction of equity;
- max per-pair notional;
- max aggregate unprotected crypto exposure = 0 for normal operation;
- max concurrent first-canary crypto positions = 1.

These are future policy inputs and must not be invented merely to make a test pass.

## 14. Mac operator architecture
One Mac process should expose:
- `/` — native Multi-Asset Hub;
- `/equities` — existing guided US-equity R6 experience;
- `/crypto` — Crypto PAPER route;
- `/api/meta` — machine-readable product availability and authority status;
- product-specific safe actions behind one localhost/CSRF boundary.

The hub does not expose broker writes while crypto execution remains uncertified.

## 15. Browser-opening fix
Observed Mac UAT showed the server starting correctly at `127.0.0.1:8766` while Safari did not open automatically. The launcher must:
1. start/bind the localhost server first;
2. invoke `/usr/bin/open` directly on macOS;
3. fall back to `webbrowser.open`;
4. preserve the printed localhost URL if both fail;
5. never terminate the server because GUI browser launch failed.

## 16. Certification phases

### Phase MA-0 — architecture and safe UX
- skills/ADR/product profile;
- Mac browser fix;
- one native hub;
- crypto rehearsal integrated without POST;
- CI cross-product boundary.

### Phase MA-1 — generic crypto read plane
- supported pair discovery/normalization;
- generic asset attestation;
- generic crypto market data;
- pair-specific ProductCapabilities.

### Phase MA-2 — crypto writer + ambiguity semantics
- exact PAPER host/path;
- durable client order id;
- UNKNOWN before I/O;
- zero blind retry;
- reconciliation-only ambiguity recovery.

### Phase MA-3 — crypto protection lifecycle
- confirmed-fill bound protection;
- partial fills;
- stop-limit risk state;
- crash/restart;
- cancel/replace;
- self-trade/wash-trade conflict handling.

### Phase MA-4 — bounded PAPER canary
- tiny hard cap;
- human approval;
- full evidence capture;
- zero auto-upsize.

### Phase MA-5 — qualification
- repeated PAPER evidence across time/liquidity conditions;
- reconciliation/protection success evidence;
- strategy qualification remains separate.

## 17. Current authority statement
**US-equity R6 lifecycle: existing certified structural PAPER path.**

**Crypto: native read/rehearsal integration in progress; broker POST remains disabled until MA-1 through MA-3 are certified.**

**LIVE: BLOCKED for all products.**
