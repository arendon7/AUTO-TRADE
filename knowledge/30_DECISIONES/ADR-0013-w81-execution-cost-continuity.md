# ADR-0013 — W81 Execution Cost Continuity

Fecha: 2026-08-23
Estado: **ACCEPTED / behavioral implementation CERTIFIED; cierre canónico en exact-head CI**

## Contexto

W78 ya vinculaba el `ExecutionCostModel` de Research a una matriz determinista de escenarios PAPER y exigía que `scenario.slippage_bps >= research.slippage_bps`. Esa condición era necesaria, pero no suficiente para demostrar continuidad económica total del componente no-fee.

Research modela desde un precio de referencia:

`research_non_fee_impact_bps = half_spread_bps + slippage_bps`

W78, en cambio, parte del touch observado —ask para BUY, bid para SELL— y desde allí aplica slippage adverso. Un spread observado muy estrecho podía producir un impacto efectivo menor que el preregistrado aunque la porción de slippage individual cumpliera el threshold.

W81 cierra exactamente ese hueco sin alterar W78 ni crear autoridad de ejecución.

## Decisión 1 — medir desde midpoint

Para cada escenario W78 se reconstruye de forma side-aware:

- `midpoint = (bid + ask) / 2`;
- BUY touch = ask;
- SELL touch = bid;
- adverse price = touch ajustado por `scenario.slippage_bps` en dirección adversa;
- `effective_non_fee_impact_bps` se mide desde midpoint hasta adverse price.

La evidencia sólo es conservadora si:

`effective_non_fee_impact_bps >= research_half_spread_bps + research_slippage_bps`

Si la observación de mercado es más favorable y la fricción W78 no compensa esa diferencia, el escenario queda `BLOCKED` con `EFFECTIVE_NON_FEE_IMPACT_BELOW_RESEARCH`.

No existe elección ex post del método de coste.

## Decisión 2 — cross-term exacto, BUY y SELL

La comparación usa la aritmética multiplicativa real de W78; no aproxima simplemente `observed_half_spread + slippage`.

Esto importa especialmente en SELL, donde el cross-term reduce ligeramente el impacto. Por ejemplo, con half-spread observado de 5 bps y slippage de 2 bps, SELL produce 6.999 bps y no puede reinterpretarse como 7 bps. W81 lo bloquea si Research preregistró 7 bps.

## Decisión 3 — evidence object hash-bound

`ExecutionCostContinuityEvidence` vincula como mínimo:

- exact `ExecutionCostModel` hash;
- W78 qualification contract hash;
- scenario matrix hash;
- W78 sensitivity measurement hash;
- `OrderIntent` fingerprint existente, sólo como identidad de evidencia;
- market fingerprint;
- scenario id/hash/outcome hash;
- symbol + side;
- midpoint, touch y adverse price;
- observed half-spread;
- scenario slippage;
- effective non-fee impact;
- Research non-fee target;
- continuity margin;
- status/reason code;
- execution-evaluated timestamp separado del later assessment timestamp;
- canonical SHA-256 del observation receipt y del evidence receipt.

W81 no construye `OrderIntent`; sólo consume uno ya existente para binding científico.

## Decisión 4 — tiempo de ejecución != tiempo de auditoría

La validez de market freshness se evalúa contra el timestamp autoritativo en que W78 produjo el sensitivity report, no contra el momento posterior en que W81 construye su receipt.

Así una ejecución W78 válida no se vuelve stale sólo porque se audite segundos o minutos después. El receipt W81 conserva ambos tiempos y exige causalidad temporal válida.

## Decisión 5 — resolver blocker sólo para el candidato exacto

Un `ExecutionCostContinuityEvidence` PASS aislado **no** puede eliminar `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN` de cualquier estrategia.

`PromotionCostContinuityResolution` requiere simultáneamente:

1. exact W80 `StrategyPromotionAssessmentReceipt`;
2. exact selected strategy del assessment;
3. exact execution intent fingerprint;
4. gate W80 `EXECUTION_SENSITIVITY` en PASS;
5. `sensitivity_measurement_hash` W81 incluido literalmente en `execution_gate.evidence_hashes`;
6. W81 continuity PASS;
7. `resolved_at >= promotion_assessed_at`;
8. `resolved_at >= continuity_assessed_at`.

Sólo entonces el resolution receipt remueve:

`TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN`

para ese assessment/candidato exacto.

## Decisión 6 — fees permanecen separadas

W81 es deliberadamente continuidad **non-fee**.

Siempre mantiene:

- `FEE_ACCOUNTING_INCOMPLETE`;
- `fee_accounting_complete=false`;
- `fee_accounting_state=INCOMPLETE_NOT_ASSESSED_BY_W81`;
- `TD-R7D-002` OPEN.

W81 no inventa fees dentro de `Fill`, no declara realized net profitability y no convierte una fee assumption Research en una fee realizada.

## Decisión 7 — otros blockers no cambian

W81 tampoco cierra:

- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TD-R7D-003` partial-fill remaining-quantity reservation.

El resolution receipt fuerza `strategy_version_execution_bound=false`.

## Evidencia PAPER externa

W81 permite conceptualmente adjuntar evidencia externa PAPER cuando exista una fuente compatible, pero **no fabrica esa fuente**.

En el estado actual no existe una evidencia autoritativa de execution price + account/order/strategy identity suficientemente completa para vincularla a un candidato W80 sin introducir semántica inferida. Por tanto, W81 cierra la continuidad del modelo Research -> W78 de forma determinista y deja cualquier futuro adapter PAPER como read-only, identity-bound y fail-closed.

No se reutiliza la posición neta del first canary como sustituto de un candidate execution receipt.

## Authority boundary

Los módulos W81 no pueden:

- importar broker clients;
- usar red o credenciales;
- mutar SQLite;
- importar Safety, OMS, TradingPipeline o writers;
- construir `OrderIntent`;
- autorizar PAPER candidate;
- autorizar ejecución externa;
- conceder capital authority;
- habilitar LIVE.

Valores obligatorios:

- `paper_candidate_authorized=false`;
- `external_execution_authorized=false`;
- `capital_authority=NONE`;
- `live_trading=BLOCKED`.

## Negative tests

W81 certifica, entre otros:

- favorable spread no compensado => BLOCKED;
- SELL cross-term inferior al threshold => BLOCKED;
- BUY equivalente conservador => PASS;
- un escenario favorable no se lava con otro stress PASS;
- wrong cost-model/matrix/report/intent/market hash => reject;
- missing W78 measurement => BLOCKED;
- crossed/stale/future market => fail closed;
- strategy drift => reject;
- W81 measurement ausente del W80 execution gate => BLOCKED;
- W80 execution gate no PASS => BLOCKED;
- resolution temporalmente anterior a sus fuentes => reject;
- tampered receipt/hash/authority flags => reject.

## Certificación behavioral implementation

Head: `a335e301c1252e32c225282b8bcbe8442787c6f2`.

Dedicated W81 run `32673372166`:
- 27/27 W81 PASS;
- 12/12 W78 qualification regression PASS;
- W81 scientific boundary PASS;
- W81 candidate-resolution boundary PASS;
- W78/W79/W80/Research boundaries PASS.

Core Safety run `32673372143`:
- 2917/2917 PASS;
- exact coverage `85.04500398769511%` >= 85%;
- `execution_cost_continuity.py` 81%;
- `promotion_cost_continuity.py` 81%;
- R5/R6/R7/W78/W79/W80/W81 boundaries PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

## Consecuencia de deuda

`TD-R7D-001` puede pasar a CLOSED: la continuidad non-fee Research -> W78 ya es matemáticamente demostrable, hash-bound, candidate-bound y fail-closed.

Esto **no** significa fee-complete P&L ni Auto-Paper readiness.

## Siguiente hito

W82 debe atacar `TD-R7D-002` — Fee-Complete Execution Accounting — mediante un contrato económico separado, sin deformar `Fill` ni inferir fees no observadas.

**LIVE TRADING: BLOQUEADO.**
