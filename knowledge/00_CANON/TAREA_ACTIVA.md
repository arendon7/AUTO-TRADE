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
- W82 Fee-Complete Execution Accounting: behavioral exact-head certificado; `TD-R7D-002` CLOSED;
- W83 Execution Strategy-Version Binding: **ACTIVE / NEXT IMPLEMENTATION WAVE**.

## W82 — cierre técnico consolidado
W82 separa cuatro verdades:
1. Research fee assumption;
2. deterministic simulated qualification fee;
3. product-specific fee mechanics;
4. broker-observed fee truth.

El candidate resolution sólo elimina `FEE_ACCOUNTING_INCOMPLETE` cuando la cadena W81/W82 exacta, product economics y fee schedule attestation conservadora coinciden para el mismo candidate/assessment.

Para Alpaca crypto, mientras no exista evidencia certificada de tier/rol más favorable:
- venue canónico: `alpaca-paper-model`;
- Tier 1 maker: 15 bps;
- Tier 1 taker: 25 bps;
- qualification floor: **25 bps**;
- charge convention requerida para este path: `RECEIVED_ASSET_PERCENT`;
- liquidity role requerido: `WORST_CASE`;
- snapshot documental expira a 30 días.

Una attestation Alpaca no puede acuñarse para otro venue. Una product receipt re-hasheada con otra charge convention o liquidity role no puede retirar el blocker.

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
Behavioral head:

`66dbc63941cb2d6552ff1dfadc292dc020e1ecb2`

Dedicated W82 run `32684230790`:
- **49/49 W82 PASS**;
- fee-accounting boundary PASS;
- promotion fee-resolution boundary PASS;
- canonical Alpaca venue + charge semantics + WORST_CASE boundary PASS;
- W81/W80/W79/W78/Research boundaries PASS.

Core Safety run `32684230698`:
- **2966/2966 PASS**;
- coverage exacta `85.12870855148343%`;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- inherited R5/R6/R7/W78–W82 boundaries PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

El cierre documental posterior es un descendiente documentation-only de ese behavioral head y debe recertificarse exact-head antes de considerar PR #54 integralmente cerrado; esa evidencia final se registra en el PR sin volver a modificar el canon.

## Problema exacto W83
Promotion Governance conoce y congela `strategy_id` / `strategy_version`, pero el sistema mantiene:

`EXECUTION_STRATEGY_VERSION_UNBOUND`.

Un string de versión no basta. Debe demostrarse que el artefacto determinista evaluado/seleccionado y la definición que generaría futuros execution intents son exactamente el mismo objeto lógico, sin recompilación, reinterpretación o sustitución silenciosa.

W83 debe impedir al menos:
- mismo strategy id/version con distinto DSL/config/artifact;
- artifact semánticamente parecido pero con hash distinto aceptado sin revisión;
- runtime recompilado o cambiado después de candidate freeze;
- parameters/defaults distintos entre Research y execution runtime;
- market/product/universe drift;
- strategy code/config cambiado conservando version string;
- intent derivado de una strategy distinta y presentado como perteneciente al candidate;
- selección ex post de la definición ejecutable después de observar holdout/forward evidence.

## Discovery ya confirmado para W83
No modificar `OrderIntent` por reflejo.

El sistema ya tiene identidad fuerte reutilizable:
- `TrialSpec` conserva `strategy_id`, `strategy_version`, `parameters`, `dataset_hash`, `split_name`, `phase` y `code_version`;
- `TrialSpec.fingerprint` hash-bindea su payload canónico;
- W79 `StrategyPromotionPolicy` conserva `selected_trial_id`, `selected_trial_fingerprint`, `selected_strategy_id`, `selected_strategy_version` y `tournament_fingerprint`;
- W80/W81/W82 conservan la identidad candidate/assessment downstream;
- `OrderIntent` conserva `strategy_id` e `intent_fingerprint`, pero no `strategy_version`.

La hipótesis preferida es un **sidecar/provenance receipt** que conecte el frozen trial/artifact con el runtime derivation e intent fingerprint, evitando expandir el contrato central de `OrderIntent` salvo que la inspección de runtime demuestre que es imprescindible.

## Contrato mínimo W83
Diseñar un `ExecutionStrategyVersionBinding` / receipt separado que vincule como mínimo:
1. exact W79 campaign/candidate identity;
2. exact W80 promotion assessment;
3. exact W81 cost-continuity resolution;
4. exact W82 fee-accounting resolution;
5. selected `strategy_id`;
6. selected `strategy_version`;
7. selected trial id/fingerprint;
8. canonical deterministic strategy artifact hash;
9. canonical DSL/config/parameter/default hash o equivalente existente;
10. product/symbol/universe identity requerida por la estrategia;
11. deterministic runtime/compiler/interpreter version si afecta semantics;
12. exact runtime definition/fingerprint;
13. deterministic derivation proof from frozen strategy identity to intent fingerprint/template identity without broker write;
14. chronology proving the artifact/runtime identity was frozen at the required time;
15. immutable receipt hash.

## Regla de identidad W83
Un string `strategy_version` por sí solo **no es authority**.

El binding sólo puede PASS si puede demostrarse:

`selected candidate strategy identity == frozen deterministic artifact identity == runtime strategy identity used for intent derivation`.

Si cualquier material falta, cambia o no puede recomputarse determinísticamente, el blocker permanece.

## Integración con W82
W83 consume W82; no reabre ni reescribe fee economics.

Un eventual resolution sólo puede remover:

`EXECUTION_STRATEGY_VERSION_UNBOUND`.

Debe conservar:
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TD-R7D-003` OPEN;
- PAPER candidate FALSE;
- broker-authoritative fee proof FALSE salvo evidencia futura separada;
- realized profitability unauthorized;
- external execution FALSE;
- capital authority NONE;
- LIVE BLOCKED.

## Fuera de alcance W83
W83 NO debe:
- crear Auto-Paper runner;
- generar broker POST;
- usar credenciales;
- cambiar writers;
- mutar broker lifecycle;
- llamar Safety/OMS para obtener authority;
- cerrar `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- cerrar `TD-R7D-003`;
- conceder PAPER candidate;
- habilitar LIVE.

## Negative/adversarial tests mínimos
- same id/version + different artifact hash;
- same artifact + different parameters/defaults;
- strategy version string spoof;
- artifact/runtime frozen after candidate freeze cuando chronology exige freeze anterior;
- W80/W81/W82 candidate mismatch;
- selected trial fingerprint mismatch;
- product/symbol/universe drift;
- interpreter/runtime version drift;
- intent fingerprint derivado de una estrategia distinta;
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
