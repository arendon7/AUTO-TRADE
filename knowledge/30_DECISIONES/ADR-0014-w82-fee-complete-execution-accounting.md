# ADR-0014 — W82 Fee-Complete Execution Accounting

Fecha: 2026-08-24
Estado: **ACCEPTED / BEHAVIORAL IMPLEMENTATION CERTIFIED / TD-R7D-002 CLOSED**

## Contexto
W81 cerró la continuidad conservadora del componente non-fee entre Research y W78, pero mantuvo correctamente `FEE_ACCOUNTING_INCOMPLETE`. La existencia de `ExecutionCostModel.fee_bps` no probaba por sí sola que la qualification ejecutiva usara una semántica de fee completa, product-aware ni coherente con el venue objetivo.

W82 debía resolver esa deuda sin modificar el `domain.Fill` compartido, sin convertir una Research assumption en broker truth y sin conceder broker, Safety, OMS, capital o LIVE authority.

## Decisión
W82 separa cuatro verdades que no pueden colapsarse:

1. **Research fee assumption** — `ExecutionCostModel.fee_bps`;
2. **deterministic simulated qualification fee** — fee aplicada a los fills W78 bajo un contrato explícito;
3. **product-specific fee mechanics** — moneda, basis, lado, gross/net economics y charge convention;
4. **broker-observed fee activity** — sólo válida si una fuente real auditada la expone con provenance suficiente.

La ausencia de la cuarta capa nunca se interpreta como fee cero.

## Base fee accounting
`src/autotrade/fee_accounting.py` crea contratos/evidencia separados del `Fill` canónico.

Para la qualification simulada:

`fee = filled_quantity * modeled_execution_price * research_fee_bps / 10000`

La evidencia liga, entre otros:
- exact Research cost-model hash;
- W78 qualification/scenario matrix;
- sensitivity measurement/outcomes;
- W81 continuity evidence;
- exact intent + market fingerprints;
- product / asset class / venue / settlement currency;
- fee basis/unidad explícitos;
- gross notional, fee amount y net quote-cash delta;
- timestamps y evidence hashes.

Spread/slippage W81 son non-fee price impact y no se contabilizan otra vez como fee.

## Product-aware fee economics
`src/autotrade/fee_product_economics.py` modela explícitamente:
- `QUOTE_NOTIONAL_PERCENT`;
- `RECEIVED_ASSET_PERCENT`;
- liquidity roles `FIXED`, `MAKER`, `TAKER`, `WORST_CASE`;
- base/quote currencies;
- gross fill economics;
- fee debit economics;
- net base/quote economics.

Gross-vs-net position, rounding, residual quantity o diferencias de inventario no se reinterpretan como fee sin semántica autoritativa.

## Alpaca crypto fee-schedule attestation
`src/autotrade/fee_schedule_attestation.py` conserva un snapshot documental versionado de la fuente oficial Alpaca verificada el 2026-08-24.

Baseline canónico W82:
- source: `https://docs.alpaca.markets/us/docs/crypto-fees`;
- asset class: `crypto`;
- canonical qualification venue: `alpaca-paper-model`;
- Tier 1 maker: **15 bps**;
- Tier 1 taker: **25 bps**;
- unknown/Tier-1 volume assumption;
- no maker guarantee / `WORST_CASE`;
- conservative qualification floor: **25 bps**;
- fee charge semantics: credited asset or fiat according to side;
- posting semantics: fee activity may be delayed / end-of-day;
- source timestamp fixed in the attestation;
- maximum attestation age: 30 days.

El caller no puede:
- refrescar arbitrariamente `source_checked_at`;
- acuñar esta attestation para otro venue;
- reducir maker/taker/floor;
- presentarla como broker-observed fee activity;
- activar execution/LIVE authority.

## Final candidate-bound resolution
`src/autotrade/promotion_fee_accounting.py` V3 es el único punto W82 capaz de remover `FEE_ACCOUNTING_INCOMPLETE`.

Para PASS exige simultáneamente:
1. exact W81 candidate-bound resolution;
2. exact W82 base fee receipt;
3. exact product-economics receipt;
4. exact Research/W78/W81 identity chain;
5. product/asset/venue/symbol/side/market-time consistency;
6. fresh versioned Alpaca fee-schedule attestation;
7. canonical venue `alpaca-paper-model`;
8. `FeeChargeConvention.RECEIVED_ASSET_PERCENT`;
9. `FeeLiquidityRole.WORST_CASE`;
10. Research fee y product policy >= documented 25 bps floor;
11. finite/bounded fee economics y directionally possible gross/net economics;
12. immutable hashes + temporal causality.

La defensa de venue ocurre además en la propia attestation factory/dataclass. Una graph internamente re-hasheada pero con otra charge convention o liquidity role falla en el resolver final.

## PAPER broker fee truth
`src/autotrade/paper_fee_activity_evidence.py` permanece deliberadamente fail-closed:
- `PENDING_PUBLICATION` cuando no existe fee activity certificada;
- `fee_amount=None`;
- `zero_fee_inferred=false`;
- `broker_authoritative_fee_proven=false`;
- credentials no persistidas;
- no broker network desde accounting core.

El first-canary gross-fill vs net-position delta no se acepta como fee receipt.

## Candidate blocker semantics
Un W82 PASS puede remover únicamente:

`FEE_ACCOUNTING_INCOMPLETE`.

Debe conservar:
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TD-R7D-003` OPEN;
- `broker_authoritative_fee_proven=false`;
- `realized_profitability_authorized=false`;
- `paper_candidate_authorized=false`;
- `external_execution_authorized=false`;
- `capital_authority=NONE`;
- `live_trading=BLOCKED`.

## Adversarial closure
La suite W82 prueba, entre otros:
- missing/partial fee evidence no puede presentarse COMPLETE;
- Research fee assumption no puede presentarse como realized fee;
- fee de wrong account/order/symbol/strategy/product/venue no puede reutilizarse;
- currency/unit/basis mismatch;
- negative/NaN/Infinity fee;
- spread/slippage double count;
- fee schedule drift/tampering/staleness;
- caller floor < documented broker floor;
- Alpaca attestation mint para non-Alpaca venue;
- validly rehashed charge-convention drift;
- validly rehashed liquidity-role drift;
- gross/net inconsistency;
- synthetic fee presented as broker-observed;
- W81 candidate mismatch;
- resolution chronology/hash tampering;
- fee blocker removed without complete receipt;
- strategy-version o Shadow/Forward blockers removed como side effect;
- broker/network/writer/Safety/OMS authority;
- PAPER candidate true;
- LIVE distinto de BLOCKED.

## Exact behavioral certification
Behavioral exact head:

`66dbc63941cb2d6552ff1dfadc292dc020e1ecb2`

Dedicated W82 run `32684230790`:
- **49/49 W82 PASS**;
- fee-accounting boundary PASS;
- promotion fee-resolution boundary PASS;
- canonical Alpaca venue + `RECEIVED_ASSET_PERCENT` + `WORST_CASE` boundary PASS;
- W81/W80/W79/W78/Research boundaries PASS.

Core Safety run `32684230698`:
- **2966/2966 PASS**;
- exact coverage `85.12870855148343%` >= 85%;
- `fee_product_economics.py`: 97%;
- `fee_schedule_attestation.py`: 90%;
- `promotion_fee_accounting.py`: 79%;
- Contract Registry: 10 contracts PASS;
- registry SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- Research/R5/R6/R7/W78/W79/W80/W81/W82 boundaries PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

## Cierre de deuda
`TD-R7D-002` queda **CLOSED** para **fee-complete deterministic qualification accounting**.

Esto NO significa:
- broker-observed realized fee proof;
- realized fee-complete P&L;
- realized profitability;
- positive expectancy futura;
- Auto-Paper readiness;
- PAPER candidate promotion;
- capital authority;
- LIVE readiness.

## Consecuencia
El siguiente blocker científico es `EXECUTION_STRATEGY_VERSION_UNBOUND` y corresponde a W83. W83 debe reutilizar la identidad exacta de candidate/assessment/economic qualification y probar que la estrategia seleccionada es exactamente el artefacto/runtime determinista que originaría futuros intent identities; no puede resolver el blocker por igualdad de strings solamente.

**PAPER CANDIDATE: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
