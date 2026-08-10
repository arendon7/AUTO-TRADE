# ADR-0007 — R2 Order Lifecycle, Fill-Level Accounting and Safe Replace

Status: Accepted
Date: 2026-08-10
Track: v0.28R / R2

## Context
R0/R1 exposed two control-plane debts that must be fixed before any external broker work:

1. Portfolio accounting marks an entire `order_id` as applied after the first order result. A later partial fill for that same order would therefore be ignored.
2. Reconciliation only fetches authoritative execution for `SUBMITTING/UNKNOWN`; a local `PARTIALLY_FILLED` order cannot progress to further fills/final state.
3. Broker/OMS interfaces do not yet expose cancel/replace lifecycle.

These are P1 R2 debts because partial execution is normal broker behavior and incorrect handling can misstate exposure.

## Decision

### 1. Fill identity is the accounting idempotency boundary
Portfolio state is updated from immutable `Fill` events, keyed by `fill_id`, not from a one-time `order_id` marker.

Requirements:
- duplicate `fill_id` with identical immutable content is idempotent;
- duplicate `fill_id` with conflicting content is an integrity conflict;
- every fill is tied to one order/symbol/side;
- cumulative filled quantity may never exceed intended quantity;
- portfolio applies each fill exactly once even after restart/reconciliation.

### 2. Durable Fill Store
Introduce a Fill Store with in-memory and SQLite implementations.

The OMS records broker-observed fills before treating the execution snapshot as reconciled. The portfolio independently keeps its own applied-fill set. This intentionally creates two idempotency layers:
- OMS/broker evidence: which fills are known;
- portfolio projection: which known fills have been applied.

A crash between them is recoverable by replaying known/cumulative fills through the portfolio store.

### 3. BrokerExecution is an authoritative cumulative snapshot
For a given order, `BrokerExecution.fills` represents all fills known by that broker snapshot, not only the latest delta.

OMS merges by `fill_id` and derives cumulative quantity/average from durable fills. Repeated broker snapshots are safe.

### 4. Lifecycle states
R2 lifecycle uses:
- `VALIDATED`
- `SUBMITTING`
- `SUBMITTED`
- `PARTIALLY_FILLED`
- `CANCEL_PENDING`
- `REPLACE_PENDING`
- terminal: `FILLED`, `CANCELLED`, `REJECTED`, `EXPIRED`
- ambiguous: `UNKNOWN`

No transition out of a terminal state except idempotent replay of the same authoritative terminal result.

### 5. Reconciliation covers every non-terminal broker-side order
Reconciliation must query authoritative execution for:
`SUBMITTING`, `UNKNOWN`, `SUBMITTED`, `PARTIALLY_FILLED`, `CANCEL_PENDING`, `REPLACE_PENDING`.

An absent/ambiguous broker result remains potential risk and blocks new risk according to reconciliation policy.

### 6. Cancel is explicit and ambiguity-safe
A cancel request first persists `CANCEL_PENDING`, then calls broker I/O.

- authoritative cancel => `CANCELLED` (possibly with partial fills already present);
- already filled => reconcile `FILLED`;
- I/O/error ambiguity => `UNKNOWN`, never assume cancelled and never blind retry.

### 7. Replace cannot bypass Safety Kernel
A replacement is modeled conservatively as **cancel-old then separately safety-approved new intent**.

Sequence:
1. new intent receives an independent RiskDecision against current effective portfolio/reservations;
2. old order enters cancel lifecycle;
3. if old cancellation is not authoritative, replacement is not submitted;
4. after authoritative old terminal state, new order uses a new idempotency key/order identity and normal guarded submission.

This avoids mutating an already-approved immutable intent and prevents replace from increasing risk without a fresh safety decision.

`REPLACE_PENDING` may represent the orchestration boundary, but broker-native in-place replacement is not required for R2 equivalence. A future broker adapter may optimize implementation only if semantics remain identical.

### 8. Reservation semantics
Reservations remain conservative:
- unfilled/open remainder continues to consume risk capacity;
- terminal `FILLED/CANCELLED/REJECTED/EXPIRED` releases the original reservation after all known fills are applied;
- ambiguous lifecycle => reservation `UNKNOWN` and reconciliation fail-closed.

R2 must later refine reservation notional to remaining quantity where safe; until certified, retaining the original reserved notional is conservative and acceptable.

## Failure invariants
- duplicate fill cannot double exposure;
- conflicting duplicate fill fails closed;
- partial fill survives restart;
- later fills of same order are applied;
- cancel ambiguity never becomes `CANCELLED` locally without broker evidence;
- replace never submits new risk before old cancel is authoritative;
- overfill or inconsistent broker status is rejected as broker-state corruption;
- terminal status cannot silently regress to open.

## Scope
This ADR defines local/durable PAPER control-plane semantics. It does not add an external broker, network endpoint or LIVE authority.

## Capital
**LIVE TRADING: BLOQUEADO.**
