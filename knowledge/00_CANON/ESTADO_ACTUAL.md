# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-23
Estado canónico: **R0–R5 CERTIFIED; R6 FIRST PAPER CANARY BROKER-TRUTH CLOSED; R7 PAPER OPERATIONS ACTIVE; W78/W79/W80/W81 TECHNICALLY CERTIFIED; W82 FEE-COMPLETE EXECUTION ACCOUNTING NEXT.**

## Tracks formales
El machine debt register principal mantiene R0–R5 como tracks certificados contiguos; **R5 sigue siendo el último track formalmente certificado** bajo ese registro.

Los hitos R6 y W78–W81 tienen certificaciones técnicas específicas descritas abajo y no se reinterpretan como una promoción automática del track registry.

## R6 / R7B
El first canary real PAPER ya alcanzó broker truth y recovery GET-only. PR #49 mantiene separada la obligación operacional de reducir/cerrar la exposición residual BTC/USD con su propio lifecycle one-shot, Safety/OMS y reconciliation.

W78–W81 no alteran ese writer ni convierten Strategy Lab en broker authority.

## W78 — Execution Qualification
W78 certificó ejecución determinista no-network sobre el control plane existente:

`OrderIntent -> Capital Safety -> OMS -> deterministic PAPER broker -> Fill/EventLedger -> Portfolio/Reconciliation`

No predice fills Alpaca futuros ni prueba rentabilidad.

## W79 — Promotion Governance
W79 congeló thresholds antes de DEVELOPMENT y candidato antes de FINAL_HOLDOUT, con campañas separadas, strategy id/version y fingerprints. Conserva:

`EVIDENCE_QUALIFIED != PAPER_CANDIDATE`.

Strategy Lab es GET-only, SQLite `mode=ro/query_only`, sin credenciales, broker POST o capital authority.

## W80 — Durable Promotion Assessment
W80 persiste assessments científicos append-only/hash-chained y permite que Strategy Lab lea gate truth durable con verificación independiente read-only. No concede PAPER candidate.

Final certified W80 head de la rama: `fb6cc382b4cfc36cb68e1612e7c29b040332ba2e`.

## W81 — Execution Cost Continuity — CERTIFIED
PR #53 / `work/w81-execution-cost-continuity`, apilado sobre W80.

Behavioral implementation head certificado:

`a335e301c1252e32c225282b8bcbe8442787c6f2`

### Qué demuestra
W81 prueba matemáticamente que el impacto non-fee usado en W78 no es más favorable que el Research cost model preregistrado.

Para cada scenario calcula side-aware midpoint -> touch -> adverse price y compara:

`effective_non_fee_impact_bps >= research_half_spread_bps + research_slippage_bps`

Una observación favorable no compensada queda BLOCKED.

### Candidate binding
Un PASS científico W81 aislado no resuelve blockers globales. `PromotionCostContinuityResolution` exige:
- exact W80 assessment;
- selected strategy exacta;
- exact intent fingerprint;
- W80 `EXECUTION_SENSITIVITY=PASS`;
- W81 measurement hash presente en el evidence set de ese gate;
- causalidad temporal W80/W81 -> resolution.

Sólo entonces se resuelve `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN` para ese candidato.

### Fees deliberadamente abiertas
W81 conserva siempre:
- `FEE_ACCOUNTING_INCOMPLETE`;
- `fee_accounting_complete=false`;
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

### Evidencia técnica
Dedicated W81 run `32673372166`:
- 27/27 W81 PASS;
- 12/12 W78 qualification regression PASS;
- W81 scientific boundary PASS;
- W81 candidate-resolution boundary PASS;
- W78/W79/W80/Research boundaries PASS.

Core Safety run `32673372143`:
- 2917/2917 PASS;
- exact coverage `85.04500398769511%` >= 85%;
- `execution_cost_continuity.py`: 81%;
- `promotion_cost_continuity.py`: 81%;
- todos los boundaries R5–R7/W78–W81 PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

## Debt R7D
- `TD-R7D-001` **CLOSED** — non-fee Research -> W78 execution-cost continuity;
- `TD-R7D-002` **OPEN P1** — fee-complete execution accounting;
- `TD-R7D-003` **OPEN P2** — safe remaining-quantity reservation after partial fills.

Además siguen abiertos `EXECUTION_STRATEGY_VERSION_UNBOUND` y `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

## Evidencia externa PAPER
No se fabrica un real-price receipt para W81. El first-canary broker truth disponible no contiene, para un candidato W80, la combinación exacta de account/order/strategy identity + authoritative execution price requerida para atribución segura. Un futuro adapter sólo podrá ser read-only e identity-bound.

## Próximo hito — W82
W82 debe cerrar `TD-R7D-002` mediante un contrato Fee-Complete Execution Accounting separado. Debe distinguir assumptions Research, fees simuladas y fees realmente observadas, evitando double-count y cualquier inferencia no autorizada.

Auto-Paper todavía no es el siguiente paso.

## Authority
Research/W78/W79/W80/W81/W82 científico no tiene camino directo al writer.

Cualquier automatización futura continúa obligada a:

`Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

## No-claims
- W81 cost continuity != fee-complete P&L;
- simulation != future Alpaca fill;
- PAPER qualification != LIVE qualification;
- evidence qualification != capital authority.

**LIVE TRADING: BLOQUEADO.**
