# R6 — Crypto PAPER Execution Contract

Status: **DESIGN + STRUCTURAL IMPLEMENTATION; USER-FACING POST DISABLED**

This document defines the minimum execution contract required before AUTO-TRADE may expose a crypto PAPER order button. It does not authorize LIVE trading and it does not claim strategy profitability.

## 1. Product boundary

Crypto is a first-class AUTO-TRADE product, not an equity symbol with a slash.

Shared deterministic authority:
- Capital Safety Kernel;
- OMS concepts and durable order identity;
- Event/audit ledger principles;
- portfolio/risk truth;
- reconciliation-first recovery;
- explicit human authority for bounded PAPER canaries;
- fail-closed defaults.

Crypto-specific authority:
- canonical `BASE/QUOTE` identity;
- 24/7 market-hours model;
- broker-attested fractional quantity and price increments;
- market/limit/stop-limit semantics;
- GTC/IOC semantics;
- no current R6 opening short or margin authority;
- separate entry/protection lifecycle;
- stop-limit residual risk;
- crypto-specific PAPER qualification evidence.

Equity-specific bracket and session-clock contracts MUST NOT be reused by crypto.

## 2. First crypto canary scope

The first real external crypto canary is intentionally narrow:
- environment: Alpaca PAPER only;
- direction: long-only;
- one canonical broker-attested crypto pair;
- no margin;
- no opening short;
- bounded notional materially below global Safety limits;
- human approval required for the exact immutable prepared package;
- one entry attempt only;
- one protection attempt only;
- no blind retry after ambiguity;
- LIVE structurally denied.

The first canary is a connectivity/execution/protection qualification event, not a profitability test.

## 3. Preferred entry policy: IOC

For the first external crypto canary, prefer an IOC entry (market IOC or tightly bounded limit IOC, selected only after provider/API contract verification and Safety review).

Reason:
- avoids leaving a long-lived BUY remnant while AUTO-TRADE later wants to place an opposing SELL protection;
- simplifies terminality and reconciliation;
- reduces self-trade/wash-trade interaction risk;
- makes the first-canary state machine easier to fail closed.

If a future strategy requires resting GTC entries, that is a separate capability requiring cancel authority, cancel acknowledgement, reconciliation and restart tests before it may be enabled.

## 4. Durable execution identity

Every external attempt binds at minimum:
- lifecycle id;
- account attestation fingerprint;
- asset attestation fingerprint;
- ProductCapabilities fingerprint;
- symbol;
- order role (`ENTRY` or `PROTECTION`);
- canonical broker payload hash;
- deterministic `client_order_id`;
- quantity and prices;
- Safety decision identity/freshness when connected to the final coordinator;
- human approval identity when connected to the final coordinator.

A changed symbol, profile, asset attestation, quantity, price, account, Safety decision or approval requires a new prepared package and new lifecycle.

## 5. UNKNOWN-before-I/O rule

Immediately before any POST, the durable lifecycle must transition:
- `ENTRY_PREPARED -> ENTRY_SUBMISSION_UNKNOWN`, or
- `PROTECTION_PREPARED -> PROTECTION_SUBMISSION_UNKNOWN`.

Only after that durable transition may the writer touch the network.

Consequences:
- timeout = UNKNOWN;
- connection reset = UNKNOWN;
- malformed ACK = UNKNOWN;
- valid but identity-mismatched ACK = UNKNOWN;
- process crash after transition and before response handling = UNKNOWN;
- restart from UNKNOWN = `RECONCILE_ONLY`;
- a second POST is forbidden until reconciliation proves the original attempt did not create an order under the durable `client_order_id`; even an order-not-found response does not by itself create automatic retry authority in the first canary.

## 6. Entry reconciliation

Broker truth is established with exact PAPER GETs:
1. order by durable `client_order_id`;
2. exact crypto position for the canonical pair.

The POST ACK alone is not sufficient to state that an exposure exists or does not exist.

For the first canary:
- cumulative fill may never regress;
- cumulative fill may never exceed intended quantity;
- confirmed net long must equal cumulative entry fills before any unrelated position-changing action is allowed;
- an open partial entry remains `ENTRY_PARTIALLY_FILLED` and is not eligible for opposing SELL protection;
- the entry must become terminal (filled/canceled/expired/rejected) before protection is prepared;
- a terminal partial fill becomes `ENTRY_FILLED_UNPROTECTED` for exactly the confirmed quantity;
- terminal zero fill may reconcile flat.

## 7. Protection contract

For a confirmed long crypto position, the first-canary protective order is a separate SELL stop-limit GTC order.

It MUST:
- use the same symbol;
- use the same asset attestation and ProductCapabilities fingerprints;
- be built only after entry reconciliation;
- use broker-attested quantity and price increments;
- cover exactly the confirmed net long quantity for the first canary;
- never exceed confirmed entry fills;
- never exceed confirmed net position;
- satisfy long protection geometry `limit_price <= stop_price`;
- have its own deterministic `client_order_id`;
- pass its own UNKNOWN-before-I/O transition.

This is not an equity bracket and does not inherit equity parent/leg assumptions.

## 8. Stop-limit residual risk

A stop-limit is not treated as guaranteed liquidation.

Explicit risk states include:
- protection accepted/open;
- protection partially filled;
- protection canceled/expired/rejected with remaining position;
- stop triggered while remaining position is not filled;
- broker/order/position disagreement;
- stream/read outage while exposure exists.

Any such condition with remaining exposure becomes `PROTECTION_AT_RISK` or `HALTED_RECONCILIATION_REQUIRED` and must be surfaced as an urgent operator/risk condition. The system must not display a green `protected` state merely because a stop-limit was once accepted.

## 9. Reconciliation and restart

Restart behavior is state-derived:
- submission UNKNOWN -> reconciliation only;
- unprotected confirmed position -> protect or reduce risk, never initiate new exposure;
- protected open/partial -> monitor and reconcile;
- protection at risk -> reduce risk or re-establish protection under a separately certified action;
- flat + zero relevant open orders -> idle.

Persistent event chain and control hashes are checked before state reuse.

## 10. Writer authority

Only `alpaca_paper_crypto_writer.py` may own direct crypto order POST network authority.

It is:
- disabled by default;
- exact PAPER host only;
- exact `/v2/orders` only;
- bounded response size and timeout;
- canonical auth/content headers;
- one transport call after durable UNKNOWN;
- no LIVE hostname literal;
- no equity `order_class`/bracket semantics.

User-facing Mac surfaces remain disconnected from this writer until the final crypto coordinator and authority gates are certified.

## 11. Read/reconciliation authority

`alpaca_paper_crypto_reconciliation.py` is GET-only and uses the existing certified PAPER read transport.

It reads:
- `/v2/orders:by_client_order_id?client_order_id=...`;
- `/v2/positions/<BASE%2FQUOTE>`.

It checks exact order identity, product class, side/type/TIF, quantity, prices, status and position truth before advancing the lifecycle.

## 12. Human authority still required

Before the writer can be reachable from the Mac UI, a final coordinator must require a durable human decision for the exact prepared package. UI clicks alone are not sufficient capital authority.

The coordinator must prove, at minimum:
- PAPER account identity fresh;
- account not blocked;
- exact asset/profile fresh;
- market data fresh;
- portfolio/reconciliation known;
- Safety APPROVED and fresh;
- notional within crypto first-canary cap;
- no conflicting positions/orders;
- immutable prepared package hash;
- explicit human approval bound to that hash;
- writer enabled only inside the one-shot child process/attempt;
- LIVE denied.

## 13. Qualification before strategy use

After structural execution certification, AUTO-TRADE still needs repeated PAPER evidence across crypto conditions:
- different UTC windows;
- liquid and less-liquid periods;
- weekday/weekend observations;
- spread/slippage/fees;
- partial fills;
- protection acceptance and outcomes;
- stop-limit residual-risk scenarios;
- restart/reconciliation drills;
- zero duplicate submission evidence.

Only after execution qualification may strategy forward-testing treat crypto as an eligible execution venue. Strategy research must still pass its own OOS/walk-forward/multiple-testing/robustness gates.

## 14. Current authority

At the time of this document:
- native Equities + Crypto architecture exists;
- generic crypto pair discovery/attestation/data exists;
- no-network crypto order and durable lifecycle contracts exist;
- dedicated PAPER writer and GET reconciliation are under certification;
- user-facing crypto POST remains **DISABLED**;
- LIVE remains **BLOCKED**.
