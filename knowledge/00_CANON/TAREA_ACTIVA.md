# TAREA ACTIVA — AUTO-TRADE

Fecha: 2026-08-24

## Objetivo inmediato
**W83 — cerrar `EXECUTION_STRATEGY_VERSION_UNBOUND` mediante un binding reproducible y hash-bound entre el candidato seleccionado por Promotion Governance y la definición determinista exacta de estrategia/runtime que produciría futuros execution intents, sin conceder PAPER candidate, broker authority, capital authority ni LIVE.**

## Stack actual
- R0–R5: tracks formalmente certificados; R5 sigue siendo el último track formal del machine debt register principal;
- R6 first real PAPER canary broker-truth: cerrado;
- R7B real PAPER close: PR #49, obligación operacional independiente;
- W78 deterministic execution qualification: certificado;
- W79 Strategy Promotion Governance + Strategy Lab read-only: certificado;
- W80 Durable Promotion Assessment: certificado;
- W81 Execution Cost Continuity: certificado;
- W82 Fee-Complete Execution Accounting: behavioral implementation certificada y `TD-R7D-002` CLOSED; el cierre integral de PR #54 exige exact-head Dedicated W82 + Core Safety + Knowledge Contract verdes sobre el mismo SHA que contiene este canon.

## W82 — resultado consolidado
W82 separa:
1. Research fee assumption;
2. deterministic simulated qualification fee;
3. product fee mechanics;
4. broker-observed fee truth.

El candidate resolution sólo puede eliminar `FEE_ACCOUNTING_INCOMPLETE` cuando la cadena W81/W82 exacta, product economics y fee schedule attestation conservadora coinciden para el mismo candidate/assessment.

Para Alpaca crypto, mientras no exista evidencia certificada de tier/rol más favorable, el floor de qualification es **25 bps**. La fuente documental está versionada, expira en 30 días y no puede ser refrescada por caller input.

`TD-R7D-002` queda **CLOSED** como fee-complete deterministic qualification accounting.

Eso NO significa:
- broker-authoritative fee proven;
- realized fee-complete P&L;
- realized profitability;
- Auto-Paper readiness;
- positive expectancy futura;
- capital authority.

W82 conserva explícitamente:
- `broker_authoritative_fee_proven=false`;
- `realized_profitability_authorized=false`;
- `paper_candidate_authorized=false`;
- `external_execution_authorized=false`;
- `capital_authority=NONE`;
- `live_trading=BLOCKED`.

## Evidencia W82 behavioral exact-head
Behavioral head: `78f3a1a7d454b0c096b0c6f1085942bb1c131452`.

Dedicated W82 run `32682423352`:
- **47/47 W82 PASS**;
- fee-accounting boundary PASS;
- promotion fee-resolution boundary PASS;
- W81/W80/W79/W78/Research boundaries PASS.

Core Safety run `32682423322`:
- **2964/2964 PASS**;
- coverage exacta `85.13062266745237%`;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- inherited boundaries PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

La certificación integral de PR #54 sólo es válida cuando el exact head que contiene este canon vuelve a demostrar Dedicated W82 + Core Safety + Knowledge Contract; no se reutiliza una corrida de un SHA anterior como certificación del SHA final.

## Problema exacto W83
Promotion Governance hoy conoce y congela `strategy_id` / `strategy_version`, pero el sistema todavía conserva el blocker:

`EXECUTION_STRATEGY_VERSION_UNBOUND`.

No basta con que una futura estrategia runtime use el mismo string de versión. Debe demostrarse que el artefacto determinista evaluado/seleccionado y la definición que generaría futuros execution intents son exactamente el mismo objeto lógico, sin recompilación, reinterpretación o sustitución silenciosa.

W83 debe impedir al menos:
- mismo strategy id/version con distinto DSL/config/artifact;
- artifact equivalente semánticamente pero con hash distinto aceptado sin revisión;
- runtime recompilado después de candidate freeze;
- parameters/defaults distintos entre Research y execution runtime;
- market/product universe drift;
- strategy code/config cambiado conservando version string;
- intent creado desde una strategy distinta y presentado como perteneciente al candidate;
- selección ex post de la definición ejecutable después de ver holdout/forward evidence.

## Contrato mínimo W83
Diseñar un `ExecutionStrategyVersionBinding` / receipt separado que vincule como mínimo:
1. exact W79 campaign/candidate identity;
2. exact W80 promotion assessment;
3. W81 cost-continuity resolution;
4. W82 fee-accounting resolution;
5. selected `strategy_id`;
6. selected `strategy_version`;
7. canonical deterministic strategy artifact hash;
8. canonical DSL/config/parameter hash o equivalente ya existente;
9. product/symbol/universe identity requerida por la estrategia;
10. deterministic runtime/compiler/interpreter version si aplica;
11. exact rules/defaults that affect signal generation;
12. deterministic derivation/proof from strategy artifact to an intent fingerprint or intent template identity without broker write;
13. temporal provenance proving artifact frozen no later than candidate freeze/promotion evidence where required;
14. immutable receipt hash.

## Regla de identidad W83
Un string `strategy_version` por sí solo **no es authority**.

El binding sólo puede PASS si el sistema puede reproducir la misma estrategia desde canonical material y comprobar que:

`selected candidate strategy identity == frozen deterministic artifact identity == runtime strategy identity used for intent derivation`.

Si cualquier componente falta, cambia o no puede recomputarse, el blocker permanece.

## Integración con W82
W83 consume W82; no reabre ni modifica fee economics.

Un eventual resolution sólo puede remover:

`EXECUTION_STRATEGY_VERSION_UNBOUND`.

Debe conservar:
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TD-R7D-003` OPEN;
- PAPER candidate FALSE;
- broker-authoritative fee proof FALSE salvo evidencia separada futura;
- realized profitability unauthorized;
- external execution FALSE;
- capital authority NONE;
- LIVE BLOCKED.

## Fuera de alcance W83
W83 NO debe:
- crear un Auto-Paper runner;
- generar broker POST;
- usar credenciales;
- cambiar writers;
- mutar broker lifecycle;
- llamar Safety/OMS para obtener authority;
- cerrar `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- cerrar `TD-R7D-003`;
- conceder PAPER candidate;
- habilitar LIVE.

## Negative tests / adversarial tests mínimos
- same id/version + different artifact hash;
- same artifact + different parameters/defaults;
- strategy version string spoof;
- artifact frozen after candidate freeze when chronology requires earlier freeze;
- W80/W81/W82 candidate mismatch;
- product/symbol/universe drift;
- interpreter/runtime version drift;
- intent fingerprint derived from a different strategy;
- tampered artifact/receipt hash;
- missing canonical material;
- nondeterministic artifact reconstruction;
- receipt reused for another campaign/candidate;
- blocker removed without exact binding receipt;
- Shadow/Forward blocker removed as side effect;
- broker/network/writer/Safety/OMS authority introduced;
- PAPER candidate true;
- LIVE distinto de BLOCKED.

## Gate de cierre W83
No cerrar W83 hasta demostrar en un mismo exact head:
- dedicated W83 PASS;
- permanent strategy-version binding boundary PASS;
- W82/W81/W80/W79/W78 boundaries PASS;
- Research Authority PASS;
- Core Safety completo PASS;
- coverage >=85%;
- Debt Register PASS;
- Canonical Knowledge PASS;
- `EXECUTION_STRATEGY_VERSION_UNBOUND` sólo removido para el exact candidate/artifact/runtime identity.

## Orden posterior
Después de W83, el siguiente blocker científico es `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`. Esa etapa debe reutilizar la **misma strategy artifact identity** certificada por W83; no puede elegir una versión nueva usando forward outcomes.

Auto-Paper todavía no es el siguiente paso inmediato.

## Authority permanente
La cadena futura continúa siendo:

`Research -> Promotion Evidence -> Durable Assessment -> Economic Qualification -> Strategy Version Binding -> Shadow/Forward Binding -> PAPER Candidate Decision -> Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

IA/model output no puede saltarse esa cadena ni convertirse directamente en order authority.

**PAPER CANDIDATE: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
