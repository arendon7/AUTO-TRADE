# TAREA ACTIVA — AUTO-TRADE

Fecha: 2026-08-23

## Objetivo inmediato
**W81 — cerrar `TD-R7D-001` / `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN` demostrando continuidad conservadora del impacto de ejecución Research -> W78 -> evidencia PAPER, sin conceder PAPER candidate, broker authority ni LIVE y sin interpretar fees como cerradas.**

## Stack certificado / activo
- R0–R5: tracks certificados del machine debt register;
- R6 first real PAPER canary broker-truth: cerrado;
- R7B close real: PR #49, obligación operacional independiente;
- W78 execution qualification: PR #50, implementation head certificado `2924456e33c2cc9e6579301b176267513a90861f`;
- W79 Strategy Promotion Governance: PR #51, técnicamente certificado;
- W80 Durable Promotion Assessment: PR #52, behavioral implementation head certificado `492ca4a621b263324b2cb5322490d74beda66a9c`; cierre documental exact-head en curso en la misma rama.

## W80 cerrado técnicamente
W80 ya implementa y certifica:
- journal append-only de Promotion Assessments;
- receipt hash-bound;
- ordinal + predecessor hash por policy;
- `BEGIN IMMEDIATE` para serializar assessment writes;
- evaluación W79 interna, no arbitrary view ingestion;
- evidencia previa que no puede desaparecer silenciosamente;
- gate no-MISSING que no puede volver a MISSING;
- reader independiente `mode=ro/query_only` que no importa el writer;
- revalidación receipt/side-columns/hash-chain;
- binding independiente a la frozen W79 policy y threshold hash;
- Strategy Lab con dos provenance domains separados: W79 governance y W80 durable assessments;
- `NO_DURABLE_W80_ASSESSMENT` explícito cuando no hay receipt;
- `EVIDENCE_QUALIFIED != PAPER_CANDIDATE` preservado.

Evidencia sobre implementation head `492ca4a621b263324b2cb5322490d74beda66a9c`:
- W80 dedicated `32671751555`: 46/46 PASS;
- Core Safety `32671751544`: 2890/2890 PASS;
- coverage exacta: `85.1061367161277%` >= 85%;
- W80 writer boundary PASS;
- W80 independent reader boundary PASS;
- W80 Strategy Lab durable projection boundary PASS;
- Mac Control Center, W79, W78 y Research boundaries PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

## W81 — problema que debe resolver
W78 ya vincula el `ExecutionCostModel` de Research a escenarios deterministas, pero todavía no prueba completamente que el coste efectivo de precio usado durante qualification sea al menos tan conservador como el supuesto preregistrado.

El hueco concreto es:
- Research modela explícitamente `half_spread_bps + slippage_bps`;
- W78 parte del bid/ask observado y aplica slippage configurado;
- un snapshot con spread estrecho puede producir menor price impact total que el preregistrado aun cuando `scenario.slippage_bps >= research.slippage_bps`;
- la evidencia externa PAPER añade realized spread/slippage que debe poder compararse sin cambiar retrospectivamente el modelo Research.

W81 debe impedir que una estrategia parezca robusta sólo porque una capa posterior usó fricción de mercado más favorable que la que se preregistró.

## Contrato mínimo W81
Diseñar una evidencia hash-bound de continuidad que, como mínimo, vincule:
1. exact Research `ExecutionCostModel` / hash;
2. exact W78 qualification contract / scenario matrix hash;
3. quote bid/ask observado y spread efectivo;
4. side/order direction;
5. research half-spread assumption;
6. configured adverse slippage;
7. effective modeled price-impact bps;
8. comparison contra el preregistered Research non-fee impact;
9. clasificación conservadora: `CONSERVATIVE`, `FAVORABLE_OBSERVATION`, `BLOCKED` o equivalente explícito;
10. evidence/provenance hashes;
11. cuando exista evidencia PAPER externa compatible, realized execution-price slippage/spread evidence sin fabricarla;
12. authority flags siempre false/NONE/BLOCKED.

## Regla económica
W81 sólo podrá cerrar `TD-R7D-001` si el pipeline puede demostrar matemáticamente que el escenario usado para qualification no reduce silenciosamente el componente de **price impact** preregistrado.

Si el spread observado es más favorable, el sistema debe:
- compensar mediante un escenario suficientemente adverso; o
- etiquetar la observación como favorable/no-conservadora y bloquear su uso como evidencia conservadora de promoción.

No se permite escoger ex post el método que haga pasar al candidato.

## Fees siguen separadas
`TD-R7D-002` / `FEE_ACCOUNTING_INCOMPLETE` permanece P1 incluso si W81 queda verde.

W81 NO debe:
- inventar fees dentro de `Fill`;
- declarar realized net profitability fee-complete;
- sumar fees nominales sin binding a producto/venue/evidencia;
- quitar `FEE_ACCOUNTING_INCOMPLETE` de los blockers.

La extensión fee-complete será un bloque posterior separado.

## Shadow/Forward y strategy runtime siguen separados
W81 tampoco cierra por sí mismo:
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `TD-R7D-003` partial-fill remaining-quantity reservation.

## Authority boundary W81
La capa de continuidad económica debe permanecer:
- no-network salvo un futuro adapter explícito de evidencia PAPER que sea read-only;
- sin broker credentials en el core científico;
- sin writer;
- sin Safety/OMS execution authority;
- sin `OrderIntent` de Auto-Paper;
- sin PAPER candidate authority;
- sin LIVE.

La IA puede proponer hipótesis/escenarios, pero no elegir retrospectivamente supuestos de coste para hacer pasar una estrategia.

## Negative tests obligatorios
- observed spread menor que Research half-spread sin compensación;
- configured slippage menor que Research slippage;
- side incorrecto al calcular price impact;
- crossed/invalid/future/stale quote;
- cost-model hash drift;
- scenario-matrix hash drift;
- strategy/policy version mismatch;
- favorable observation presentada como conservative;
- missing market evidence interpretada como PASS;
- NaN/Infinity/negative impossible cost components;
- external PAPER evidence de otra cuenta/símbolo/order/strategy vinculada por error;
- tampering de evidence hashes;
- fee blocker eliminado por W81;
- broker/writer/Safety/OMS import desde la capa científica;
- PAPER candidate true;
- LIVE habilitado.

## Gate de cierre W81
No cerrar W81 hasta demostrar sobre un mismo exact head:
- dedicated W81 PASS;
- permanent execution-cost-continuity boundary PASS;
- W80/W79/W78 boundaries PASS;
- Research Authority PASS;
- Core Safety completo PASS;
- coverage >=85%;
- Debt Register PASS;
- Canonical Knowledge PASS;
- documentación actualizada sin reutilizar CI de un head anterior.

## No-claims
- W80 durable assessment != estrategia rentable;
- W81 conservative cost continuity != fee-complete P&L;
- simulation != Alpaca future fill;
- PAPER != LIVE;
- evidence qualification != capital authority.

**LIVE TRADING: BLOQUEADO.**