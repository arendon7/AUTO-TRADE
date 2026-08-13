# DEBT REGISTER — v0.28R

Fecha: 2026-08-12
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
| `TD-R6-010` | P1 | OMS-owned external PAPER handoff | **CLOSED** — OMS owns durable VALIDATED→SUBMITTING; handoff event precedes state change; direct R6 OrderStore mutation prohibited |
| `TD-R6-011` | P1 | human final PAPER execution decision | **CLOSED** — exact prepared package → durable human-only one-shot decision → no-network execution bridge → OMS SUBMITTING → human-gated single POST writer |
| `TD-R6-012` | P1 | crash-safe same-attempt resume | **CLOSED** — only PREPARED + zero attempts + same-attempt CONSUMED may resume; UNKNOWN/different/stale remain fail-closed |
| `TD-R6-013` | P1 | operational external PAPER lifecycle harness | **CLOSED** — certified sanitized preflight + same-core preparation/provenance + separate human decision + triple-gated restart-safe single-shot execution + separate evidence capture; no real POST required for structural closure |
| `TD-R6-014` | P1 | native multi-asset ProductCapabilities | explicit asset class/venue profile bound to broker attestation; crypto/equity order/TIF/market-hours/protection semantics cannot cross; permanent cross-product CI |
| `TD-R6-015` | P1 | crypto protective lifecycle | entry reconciliation → confirmed filled qty → protective stop-limit ≤ position; partial fills, trigger/non-fill, disconnect, restart, cancel/replace and PROTECTION_AT_RISK fail-closed |
| `TD-R6-016` | P1 | crypto PAPER writer/idempotency/reconciliation | exact PAPER crypto order payload + durable client_order_id + UNKNOWN-before-I/O + no blind retry + exact ACK/fill reconciliation + zero LIVE authority |
| `TD-R6-017` | P1 | crypto 24/7 qualification evidence | repeated PAPER evidence across liquidity/time/weekend conditions; fills, spread/slippage, protection, reconciliation and residual stop-limit risk captured; no profitability claim from connectivity |
| `TD-R6-018` | P1 | native Multi-Asset Mac Control Center | one localhost Hub exposing Equities + Crypto with ephemeral credentials, machine-readable product status, reliable macOS browser open, no hidden POST path and dual-arch UAT |

R6 P0/P1/P2 OPEN: **11**. R6 cannot certify until all close with evidence.

### Multi-asset boundary
`TD-R6-014`–`018` were added before enabling any crypto broker write. The BTC/USD Crypto PAPER Lab remains rehearsal/read-only while these debts are open. ADR-0009 defines the durable architecture; `skills/crypto-trading/SKILL.md` and `skills/multi-asset-safety/SKILL.md` are mandatory for this workstream.

## Deuda no bloqueante fuera de R6
| ID | Sev | Track | Área |
|---|---|---|---|
| `TD-OPS-001` | P3 | OPS | Graphify semantic/deep real pending supported runtime |

## Capital
**LIVE TRADING: BLOQUEADO.**
R6 may qualify bounded external PAPER only; crypto broker writes remain disabled until their product-specific lifecycle is certified; no R6 artifact may authorize LIVE.
