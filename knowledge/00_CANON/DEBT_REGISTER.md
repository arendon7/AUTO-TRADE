# DEBT REGISTER — v0.28R

Fecha: 2026-08-24
Estado: **R0–R5 CERTIFIED; R6/R7 capability work active; W78–W82 technically certified in stacked branches**

## Authority
La autoridad machine-readable es la composición validada por CI de `knowledge/00_CANON/debt_register.json` + sus extensiones `knowledge/00_CANON/debt_register_*.json`. Si esta vista humana discrepa, manda el registro machine-readable compuesto + CI.

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

R5 sigue siendo el último track formalmente certificado del registro principal. Los hitos R6/R7 y W78–W82 son certificaciones técnicas/capability específicas y no se reinterpretan como promoción automática del track registry.

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

La extensión machine-readable de R6/R7 contiene el estado exacto más reciente de estos ítems; esta tabla histórica no sustituye sus cierres posteriores.

### Multi-asset boundary
`TD-R6-014`–`018` se registraron antes de habilitar autoridad de producto. Cualquier crypto write sigue obligado a ProductCapabilities, Safety/OMS, one-shot writer, UNKNOWN/reconciliation y protección específica; LIVE continúa fuera de autoridad.

## R7D — Research → deterministic PAPER promotion economics
| ID | Sev | Estado | Área | Resultado actual |
|---|---|---|---|---|
| `TD-R7D-001` | P1 | **CLOSED** | Research-to-PAPER non-fee execution-cost continuity | W81 prueba midpoint→touch→adverse continuity candidate-bound; favorable under-cost scenarios BLOCK |
| `TD-R7D-002` | P1 | **CLOSED** | Fee-complete execution accounting | W82 prueba fee-complete **deterministic qualification** con exact Research/W78/W81 binding, product fee mechanics y documented Alpaca crypto floor; no realized broker-fee claim |
| `TD-R7D-003` | P2 | **OPEN** | Partial-fill remaining-quantity risk reservation | conservar full reservation hasta poder probar authoritative remaining open quantity; premature capital release prohibido |

### W82 fee boundary
El cierre de `TD-R7D-002` no significa que una fee haya sido observada en Alpaca.

W82 separa cuatro verdades:
1. Research fee assumption;
2. deterministic simulated fee accounting;
3. product-specific fee mechanics;
4. broker-observed fee activity.

Para Alpaca crypto, la attestation documental W82 verificada el 2026-08-24 fija, mientras no haya evidencia separadamente certificada de tier/rol más favorable:
- Tier 1 maker: 15 bps;
- Tier 1 taker: 25 bps;
- conservative qualification floor: **25 bps**;
- fee sobre el activo/fiat acreditado según lado;
- fee activity potencialmente publicada con retraso/EOD;
- snapshot expirable a 30 días.

Una policy local no puede abaratar ese floor. Cualquier lower-tier assumption requiere evidencia futura de 30-day volume tier + liquidity role.

Broker activity sigue fail-closed:
- `broker_authoritative_fee_proven=false`;
- `realized_profitability_authorized=false`;
- missing fee activity != zero fee.

## Promotion blockers aún abiertos
El cierre W82 no remueve:
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TD-R7D-003`.

W83 debe atacar primero `EXECUTION_STRATEGY_VERSION_UNBOUND`: la versión/artefacto determinista que pudiera producir futuros intents debe quedar criptográficamente ligada a la exact selected strategy id/version congelada por Promotion Governance. Hasta entonces no hay Auto-Paper candidate.

## Deuda no bloqueante fuera del workstream inmediato
| ID | Sev | Track | Área |
|---|---|---|---|
| `TD-OPS-001` | P3 | OPS | Graphify semantic/deep real pending supported runtime |

## Authority
- Strategy Lab / W78–W82 scientific layers: no broker write;
- PAPER candidate: FALSE;
- external execution authority from W82: FALSE;
- W82 capital authority: NONE;
- realized profitability authorization: FALSE;
- LIVE: BLOCKED.

**LIVE TRADING: BLOQUEADO.**
