# TAREA ACTIVA — AUTO-TRADE

Fecha: 2026-08-23

## Objetivo inmediato
**W82 — cerrar `TD-R7D-002` / `FEE_ACCOUNTING_INCOMPLETE` mediante Fee-Complete Execution Accounting, manteniendo W81 non-fee continuity intacto y sin conceder PAPER candidate, broker authority ni LIVE.**

## Stack actual
- R0–R5: tracks formalmente certificados del machine debt register; R5 sigue siendo el último track formal del registro;
- R6 first real PAPER canary broker-truth: cerrado;
- R7B close real: PR #49, obligación operacional independiente;
- W78 execution qualification: certificado;
- W79 Strategy Promotion Governance + Strategy Lab read-only: certificado;
- W80 Durable Promotion Assessment: certificado;
- W81 Execution Cost Continuity: behavioral implementation certificado en `a335e301c1252e32c225282b8bcbe8442787c6f2`, PR #53 DRAFT stacked.

## W81 cerrado técnicamente
W81 demuestra continuidad conservadora del componente **non-fee**:

`Research half-spread + slippage -> W78 effective midpoint-to-adverse-price impact`

La evidencia está ligada a cost-model, qualification contract, scenario matrix, W78 sensitivity measurement, intent, market y cada scenario/outcome. Una observación favorable que reduzca el coste preregistrado queda BLOCKED.

El blocker sólo se puede remover para el candidato exacto cuando un resolution receipt prueba binding con el W80 `EXECUTION_SENSITIVITY` gate y causalidad temporal.

`TD-R7D-001` queda CLOSED.

## W82 — problema exacto
El core `Fill` no contiene un contrato fee-complete. Research sí tiene `fee_bps`, pero W78/W81 no pueden convertir ese supuesto en una fee realizada por interpretación.

Por tanto hoy todavía no puede afirmarse:
- realized net P&L fee-complete;
- profitability after all execution fees;
- Auto-Paper readiness basada en beneficio neto.

W82 debe cerrar esa discontinuidad económica sin contaminar el domain `Fill` ni inventar broker economics.

## Contrato mínimo W82
Diseñar un `FeeAccountingContract`/receipt separado que distinga como mínimo:
1. Research fee assumption y su hash;
2. producto/asset class/venue aplicables;
3. currency/unidad de la fee;
4. fee basis: notional, quantity, asset debit u otra semántica explícita;
5. simulated fee evidence cuando el escenario sea W78 determinista;
6. authoritative PAPER fee evidence sólo cuando una fuente realmente la exponga;
7. exact account/order/client-order/strategy/symbol binding cuando la evidencia sea externa;
8. gross execution economics;
9. explicit fee amount/rate;
10. net execution economics;
11. provenance + timestamp + evidence hash;
12. classification `COMPLETE`, `INCOMPLETE`, `MISSING`, `BLOCKED` o equivalente fail-closed.

## Reglas económicas W82
- una fee Research no es una fee realizada;
- una diferencia gross-vs-net position no se llama fee salvo evidencia autoritativa que pruebe esa semántica;
- no inferir fee amount desde rounding, residual quantity o posición neta;
- no mezclar fees de otro producto, venue, account u order;
- no hacer double-count entre spread/slippage W81 y fee W82;
- net profitability sólo puede existir cuando todas las capas requeridas están explícitamente completas.

## Integración con W81
W82 debe consumir, no reescribir, el W81 continuity receipt.

Un eventual `FEE_ACCOUNTING_INCOMPLETE` sólo puede resolverse si:
- W81 continuity ya valida para el mismo candidato/assessment;
- exact Research fee assumption está ligada;
- el fee evidence set es completo bajo un contrato preregistrado;
- no existe identity drift;
- el resolution receipt conserva `EXECUTION_STRATEGY_VERSION_UNBOUND` y `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

## Fuera de alcance W82
W82 NO debe:
- cambiar el writer;
- habilitar broker network desde el core científico;
- persistir credenciales;
- crear Auto-Paper `OrderIntent`;
- conceder Safety/OMS authority;
- cerrar `TD-R7D-003`;
- cerrar `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- cerrar `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- promover PAPER candidate;
- habilitar LIVE.

## Negative tests
- missing fee evidence tratado como COMPLETE;
- Research fee assumption reinterpretada como realized fee;
- fee de otro account/order/symbol/strategy aceptada;
- wrong currency/unit/basis;
- negative/NaN/Infinity fee;
- double-count de spread/slippage como fee;
- fee schedule drift después de evidence generation;
- gross/net inconsistente;
- partial evidence presentado como fee-complete;
- synthetic fee presentada como broker-observed;
- candidate/assessment mismatch;
- W81 continuity ausente o BLOCKED;
- tampered fee receipt/hash/timestamp;
- `FEE_ACCOUNTING_INCOMPLETE` removido sin receipt completo;
- `TD-R7D-003` cerrado por side effect;
- broker/writer/Safety/OMS import desde accounting core;
- PAPER candidate true;
- LIVE distinto de BLOCKED.

## Gate de cierre W82
No cerrar W82 hasta demostrar en un mismo exact head:
- dedicated W82 PASS;
- permanent fee-accounting boundary PASS;
- W81/W80/W79/W78 boundaries PASS;
- Research Authority PASS;
- Core Safety completo PASS;
- coverage >=85%;
- Debt Register PASS;
- Canonical Knowledge PASS;
- `TD-R7D-002` sólo CLOSED si la evidencia realmente satisface el contrato.

## No-claims
- W81 non-fee continuity != fee-complete economics;
- W82 simulated fee completeness != realized Alpaca fee proof;
- fee-complete accounting != strategy profitability por sí solo;
- PAPER != LIVE;
- evidence qualification != capital authority.

**LIVE TRADING: BLOQUEADO.**
