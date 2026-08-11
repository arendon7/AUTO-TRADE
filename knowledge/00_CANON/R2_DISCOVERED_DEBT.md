# R2 — DISCOVERED DEBT (ACTIVE WORKING LOG)

Date: 2026-08-10
Status: OPEN — must be reconciled into `DEBT_REGISTER.md` before R2 close.

This file exists because newly discovered P1/P2 issues must be recorded immediately even while the canonical debt table is being edited on the same active branch.

| ID | Sev | Area | Finding | Required close condition |
|---|---|---|---|---|
| TD-R2-006 | P1 | Recovery | crash after OMS persists terminal order/fills but before portfolio fill projection/reservation release can strand a terminal order with unapplied exposure | startup reconciliation replays durable fills for terminal orders, releases/repairs reservation from authoritative broker snapshot, exact-once after repeated restart |
| TD-R2-007 | P1 | Safety input integrity | `PortfolioSnapshot` aggregate gross/net values are not yet proven exactly consistent with its position maps before risk calculations | Safety Kernel or authoritative snapshot boundary validates finite positions and exact gross/net + per-strategy gross consistency; adversarial tests reject contradictory snapshots |
| TD-R2-008 | P2 | Projection integrity | applied-fill projection table tracks `fill_id` but not an independent immutable fill fingerprint | either store/verify fill fingerprint at projection boundary or document/prove that Fill Store + broker/ledger integrity makes conflicting projection impossible across all recovery paths |
| TD-R2-009 | P2 | Lifecycle evidence | `REPLACE_PENDING` exists in lifecycle but cancel-first orchestration does not yet persist a distinct replace-request transition/evidence | persist replace-request boundary or explicitly remove state and contract claim; tests prove no bypass/ambiguity |

## Rule
R2 cannot leave DRAFT while any P1 in this working log remains open. Before R2 merge this file must be reconciled with the canonical Debt Register and either removed or converted to a closed historical record.

## Capital
**LIVE TRADING: BLOQUEADO.**
