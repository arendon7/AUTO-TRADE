# DEBT REGISTER — v0.28R

Fecha: 2026-08-11
Estado: **R0–R5 CERTIFIED; R6 ACTIVE**

## Authority
La autoridad machine-readable es `knowledge/00_CANON/debt_register.json`. Si esta vista humana discrepa, manda el JSON + CI.

## Regla
Ningún track puede certificarse con deuda conocida P0/P1/P2 asignada a ese track. Deuda nueva se registra antes de implementar y no se rebaja para satisfacer una release.

## Certified tracks
- **R0 — PASS**
- **R1 — PASS**
- **R2 — PASS**
- **R3 — PASS**
- **R4 — PASS**
- **R5 — PASS e integrado/post-merge recertificado en `main` `75dcbef65b061f742745ba7be0665521967e0587`**

Post-merge R5: Core Safety `31466198629` PASS, Knowledge Contract `31466198624` PASS, **606 tests / 86.49% coverage**.

## R6 — deuda registrada antes de implementación
| ID | Sev | Área | Condición de cierre |
|---|---|---|---|
| `TD-R6-001` | P1 | External Alpaca PAPER gateway | disabled default + exact PAPER host/credentials/account attestation + Safety/OMS mandatory; LIVE/arbitrary host reject before I/O |
| `TD-R6-002` | P1 | submit ambiguity/idempotency/reconciliation | durable client_order_id binding; timeout/ambiguity => UNKNOWN + reconcile; never blind retry |
| `TD-R6-003` | P1 | bounded PAPER canary | explicit opt-in + certified prerequisites + synchronized state + tighter notional cap; never auto-upsize |
| `TD-R6-004` | P1 | PAPER qualification evidence | terminality/fills/slippage/reconciliation evidence durable and tamper-evident; incomplete evidence unqualified |
| `TD-R6-005` | P1 | equity bracket protection | parent + exactly TP/SL legs, coherent prices/qty/TIF, authoritative increments and nested response verification |
| `TD-R6-006` | P1 | PAPER trade_updates evidence | PAPER-only authenticated bounded stream + order correlation + ordering/terminality/disconnect fail-closed |
| `TD-R6-007` | P1 | unsupported products | **CLOSED** — self-validating us_equity-only bracket surface + permanent product-boundary CI; unsupported classes/modes fail closed before I/O |
| `TD-R6-008` | P1 | permanent PAPER-only authority boundary | **CLOSED** — dual permanent CI gates enforce exact PAPER-only network/write authority, LIVE-deny, Safety/OMS and AI/research separation |
| `TD-R6-009` | P1 | final write Safety/OMS recheck | **CLOSED** — dual authoritative PRE_CONSUME/PRE_IO recheck, cryptographic phase chain, version-race rejection, zero-I/O fail-closed |

R6 P0/P1/P2 OPEN: **6**. R6 cannot certify until all close with evidence.

## Deuda no bloqueante fuera de R6
| ID | Sev | Track | Área |
|---|---|---|---|
| `TD-OPS-001` | P3 | OPS | Graphify semantic/deep real pending supported runtime |

## Capital
**LIVE TRADING: BLOQUEADO.**
R6 may qualify bounded external PAPER only; no R6 artifact may authorize LIVE.
