# R2 — FAILURE PATH REVIEW

Date: 2026-08-10
Track: v0.28R / R2 Capital Safety + OMS
Status: ACTIVE REVIEW — becomes certified evidence only after final branch CI passes.

## Principle
A happy-path fill is insufficient evidence for capital safety. R2 reviews each state boundary where uncertainty, restart, concurrency or malformed broker evidence could otherwise create duplicate or unbounded exposure.

## Failure-path matrix

| Boundary | Failure / adversary | Required behavior | Evidence |
|---|---|---|---|
| Risk approval | market stale/future/invalid/crossed | reject before reservation/broker | `test_r2_risk_matrix.py` |
| Risk approval | order/position/strategy/portfolio/net/leverage exact boundary + epsilon | exact configured boundary may pass; epsilon over rejects | `test_r2_risk_matrix.py` |
| Risk approval | internally contradictory portfolio gross/net/strategy maps | `INVALID_PORTFOLIO_SNAPSHOT`, fail closed | `test_r2_portfolio_snapshot_integrity.py`, risk matrix |
| Risk approval | open reservations already consume exposure | pending risk counts against strategy/portfolio/leverage | `test_r2_reservation_risk_matrix.py`, existing durable reservation tests |
| Risk approval | kill/circuit becomes active after approval but before submit | safety-state version mismatch rejects stale decision | `test_durable_state.py`, `test_r2_risk_telemetry.py` |
| Daily risk | max daily loss reached | durable circuit activates atomically with ledger evidence | `test_r2_risk_telemetry.py` |
| Drawdown | max drawdown reached | durable circuit activates; rollover never auto-clears it | `test_r2_risk_telemetry.py` |
| Circuit recovery | next session / good telemetry | no automatic risk restoration; explicit human acknowledgement required | `test_r2_risk_telemetry.py` |
| Order submit | crash after broker commit before local resolution | restart queries broker; no duplicate submit | `test_durable_state.py` |
| Order submit | OMS terminal order/fill committed but crash before portfolio projection/reservation release | startup replays durable fill exactly once and repairs reservation | `test_r2_crash_projection_recovery.py` |
| Fill ingestion | same `fill_id`, identical payload | idempotent replay | `test_r2_fill_lifecycle.py`, `test_r2_projection_integrity.py` |
| Fill ingestion | same `fill_id`, conflicting payload | hard integrity conflict; never silently skip | `test_r2_fill_lifecycle.py`, `test_r2_projection_integrity.py` |
| Fill progression | partial -> partial/full cumulative broker snapshot | only new fills alter exposure; cumulative VWAP/quantity exact | `test_r2_fill_lifecycle.py` |
| Broker snapshot | snapshot drops previously observed fill | conflict / fail closed | `test_r2_fill_lifecycle.py` |
| Broker snapshot | total filled quantity exceeds intent | reject overfill | `test_r2_fill_lifecycle.py` |
| Cancel | authoritative cancel of open order | terminal CANCELLED; reservation released | `test_r2_fill_lifecycle.py` |
| Cancel | partial fill then cancel | filled exposure retained; only unfilled capacity released | `test_r2_partial_cancel.py` |
| Cancel | cancel acknowledgement lost | local order becomes UNKNOWN; reservation UNKNOWN; reconciliation degraded | `test_r2_fill_lifecycle.py`, `test_r2_replace.py` |
| Replace | normal replace | durable replace request -> authoritative cancel -> fresh risk evaluation -> new idempotency key | `test_r2_replace.py`, `test_r2_replace_evidence.py` |
| Replace | cancel ambiguous | no replacement submit | `test_r2_replace.py` |
| Replace | crash after original cancel before replacement submit | retry uses durable replace marker and resumes exactly once | `test_r2_replace_evidence.py` |
| Replace | different replacement attempts reuse same original request identity | ledger identity conflict / fail closed | `test_r2_replace_evidence.py` |
| Event replay | same semantic event retried at later wall-clock time | first durable timestamp wins; same payload is idempotent | `test_r2_idempotent_events.py` |
| Reconciliation | broker state unavailable | degraded, fail closed | reconciliation tests |
| Reconciliation | local/broker position mismatch | `POSITION_MISMATCH`; new risk blocked | durable-state tests |
| Reconciliation | broker open order untracked locally | fail closed | reconciliation tests |
| Reconciliation | local open order missing at broker | fail closed | reconciliation tests |
| Reservation | orphan / state mismatch | fail closed until reconciliation resolves | reservation/reconciliation tests |
| Contract boundary | runtime object changes without registered shape | registry validation/CI fails | `test_contract_registry.py`, `test_contract_payloads.py`, `check_contract_registry.py` |
| Ledger | persisted event payload modified | hash-chain verification fails | durable ledger tests |

## Replace semantics
R2 deliberately does **not** use an in-place broker replace that could mutate exposure outside a fresh risk decision. Replacement is:

`ORDER_REPLACE_REQUESTED -> REPLACE_PENDING -> authoritative cancel -> CANCELLED -> new OrderIntent -> fresh Safety Kernel -> fresh reservation -> submit`

If the original resolves as FILLED/EXPIRED/REJECTED instead of CANCELLED, automatic replacement aborts. If cancellation is ambiguous, replacement aborts and the original becomes UNKNOWN until reconciliation.

## Fill accounting authority
- Broker execution snapshots are cumulative evidence.
- `fill_id` + immutable fill fingerprint is the accounting idempotency boundary.
- Fill Store and portfolio projection each independently detect conflicting reuse.
- Portfolio exposure changes from **new fills**, not from repeated cumulative order summaries.

## Circuit authority
Daily loss/drawdown telemetry is durable. A threshold breach updates circuit state and appends its ledger evidence in the same SQLite transaction. Circuit activation increments `safety_state_version`, invalidating a decision approved milliseconds earlier.

Automatic processes may activate/reduce/block risk. They do not automatically acknowledge/clear a circuit.

## Known scope boundary
R2 proves local deterministic control-plane semantics using durable paper/inspectable broker interfaces. It does not certify network transport, an external broker, broker-native bracket protection or PAPER canaries; those belong to R6 after R2–R5 are green.

## Capital status
**LIVE TRADING: BLOQUEADO.**
