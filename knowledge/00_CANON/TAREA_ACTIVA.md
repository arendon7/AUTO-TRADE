# TAREA ACTIVA — AUTO-TRADE

Fecha: 2026-08-24

## Objetivo inmediato
**W84 — cerrar `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` ligando la evidencia Shadow/Forward R5 existente al exact candidate/artifact/runtime identity certificado por W83, sin selección ex post, sin recalibración con resultados forward y sin conceder PAPER candidate, broker authority, capital authority ni LIVE.**

## Stack actual
- R0–R5: tracks formalmente certificados; R5 sigue siendo el último track formal del machine debt register principal;
- R6 first real PAPER canary broker-truth: cerrado;
- R7B real PAPER close: PR #49, obligación operacional independiente;
- W78 deterministic execution qualification: certificado;
- W79 Strategy Promotion Governance + Strategy Lab read-only: certificado;
- W80 Durable Promotion Assessment: certificado;
- W81 Execution Cost Continuity: certificado;
- W82 Fee-Complete Execution Accounting: certificado; `TD-R7D-002` CLOSED;
- W83 Execution Strategy-Version Binding: **behavioral + canonical closure completed; `EXECUTION_STRATEGY_VERSION_UNBOUND` resolved for the exact bound candidate**;
- W84 Shadow/Forward Promotion Binding: **ACTIVE / NEXT IMPLEMENTATION WAVE**.

## W83 — cierre técnico
W83 no añadió `strategy_version` a `OrderIntent` ni creó otro registry de ejecución. Construyó sidecar evidence para demostrar:

`selected W79 candidate == frozen TrialSpec/StrategySpec identity == loaded deterministic runtime identity == semantic origin of the W82-qualified existing intent`.

### Artifact/config identity
`strategy_execution_binding.py` liga:
- exact W79 policy + selected trial id/fingerprint;
- DEVELOPMENT `TrialSpec`;
- `StrategySpec.canonical_hash` congelado como `spec_hash`;
- exact parameters + dataset hash;
- W82 product/venue/quote-currency provenance;
- deterministic safe-DSL signal;
- MARKET semantic projection;
- full existing `OrderIntent` fingerprint.

### Runtime implementation identity
`promotion_strategy_version_binding.py` liga el `TrialSpec.code_version` a `W83_SAFE_DSL_RUNTIME_CODE_IDENTITY_V2`, cuyo hash agregado cubre:
- `research/dsl.py`;
- `research/strategy.py`;
- `research/market.py`;
- implementación Python;
- versión Python major.minor.patch exacta.

El receipt conserva los source hashes individuales para inspección y exige `selected_trial.code_version == loaded_runtime_code_hash`.

### Behavioral exact-head W83
Head:
`177517a29d677a34dc4a711b56b955bb5cf2cd51`

Dedicated W83 `32688103622`:
- **25/25 W83 PASS**;
- W83 boundary PASS;
- W82/W81/W80/W79/W78/Research boundaries PASS.

Core Safety `32688103642`:
- **2991/2991 PASS**;
- exact coverage `85.04640770024064%`;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- inherited boundaries through W83 PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

Knowledge Contract `32688103696`: PASS.

ADR: `knowledge/30_DECISIONES/ADR-0015-w83-execution-strategy-version-binding.md`.

## Qué resolvió W83
Sólo puede remover:

`EXECUTION_STRATEGY_VERSION_UNBOUND`.

Debe conservar:
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TD-R7D-003` OPEN;
- broker-authoritative fee proof FALSE;
- realized profitability unauthorized;
- PAPER candidate FALSE;
- runtime/external execution authority FALSE;
- capital authority NONE;
- LIVE BLOCKED.

## Problema exacto W84
R5 ya dispone de infraestructura robusta de Shadow/Forward:
- `FrozenShadowConfig`;
- `StrategyShadowObservation`;
- `ShadowPeriodRecord`;
- append-only/hash-protected `SQLitePortfolioShadowRegistry`;
- `FrozenForwardPolicy`;
- `ForwardPeriodEvidence`;
- append-only/hash-protected `SQLiteForwardEvidenceRegistry`.

Pero esas cadenas no constituyen por sí solas promotion binding para el exact candidate W83. El blocker permanece porque aún debe probarse que:

`exact W83 candidate/runtime identity == frozen Shadow identity == frozen Forward policy identity == immutable post-activation evidence chain`.

## Regla central W84
La identidad de estrategia certificada por W83 debe quedar congelada **antes** de observar outcomes usados como forward evidence.

W84 no puede:
- seleccionar una nueva strategy/config después de ver forward returns;
- recalibrar parameters/weights/thresholds usando outcomes forward;
- sustituir el candidate W83 por otro con el mismo `strategy_id` o `strategy_version`;
- usar un `source_code_hash` o `frozen_parameters_hash` no ligado a W83;
- mezclar shadow records de otra config/campaign;
- interpretar una cadena append-only íntegra pero de identidad equivocada como promotion evidence válida.

## Contrato mínimo W84
Diseñar un sidecar `ShadowForwardPromotionBinding` / candidate-bound resolution que vincule como mínimo:
1. exact W83 resolution id/hash;
2. exact W83 execution binding evidence hash;
3. exact W79 campaign/policy/candidate identity heredada;
4. selected strategy id/version/trial fingerprint;
5. W83 `strategy_spec_hash`;
6. W83 loaded runtime code hash + source-set identity;
7. exact frozen Shadow config fingerprint;
8. strategy weight/identity relevante para el selected candidate;
9. exact `FrozenForwardPolicy.fingerprint`;
10. `frozen_parameters_hash` y `source_code_hash` equivalentes a la identidad W83 o explícitamente derivados de ella;
11. activation chronology anterior a todo forward observation aceptado;
12. contiguous verified forward evidence head/control hash;
13. threshold/gate policy frozen antes del periodo forward;
14. immutable resolution hash.

## Reutilizar, no duplicar R5
Por defecto W84 debe reutilizar `research/shadow.py` y `research/forward.py` como authority científica de las cadenas persistidas. No crear otro shadow engine ni copiar la lógica append-only salvo que exista una brecha demostrable.

La nueva capa debe preferir lectura/verificación de receipts existentes y candidate binding por hash/provenance.

## Fail-closed W84
Negative tests mínimos:
- W83 resolution hash mismatch;
- selected candidate/trial mismatch;
- same strategy string con W83 artifact/runtime distinto;
- Shadow config creada después del primer forward outcome;
- candidate ausente o con weight ambiguo en frozen config;
- shadow config fingerprint mismatch;
- forward policy de otra campaign/config;
- `source_code_hash` distinto del runtime identity W83;
- `frozen_parameters_hash` no derivable del exact frozen candidate;
- forward evidence anterior a activation;
- gaps/reordering/tail deletion/tamper en Shadow o Forward;
- forward threshold elegido después de observar outcomes;
- evidence chain vacía cuando el contrato exija evidencia observada;
- receipt reuse para otra candidate/campaign;
- removal de blocker sin exact W83+Shadow+Forward binding;
- PAPER candidate true;
- broker/network/writer/Safety/OMS authority;
- LIVE distinto de BLOCKED.

## Gate de cierre W84
No cerrar W84 hasta demostrar en el mismo exact head:
- dedicated W84 PASS;
- permanent Shadow/Forward promotion binding boundary PASS;
- W83/W82/W81/W80/W79/W78 boundaries PASS;
- Research Authority PASS;
- Core Safety completo PASS;
- coverage >=85%;
- Debt Register PASS;
- Canonical Knowledge PASS;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` removido sólo para la exact candidate/runtime/shadow/forward identity.

## Lo que NO ocurre después automáticamente
Cerrar W84 no debe equivaler automáticamente a PAPER candidate. Debe existir una decisión explícita posterior que consuma la cadena completa y evalúe cualquier requisito restante de PAPER probation/readiness. `EVIDENCE_QUALIFIED != PAPER_CANDIDATE` sigue siendo invariante.

## Debt R7D separado
- `TD-R7D-001`: CLOSED;
- `TD-R7D-002`: CLOSED;
- `TD-R7D-003`: **OPEN P2** — safe remaining-quantity reservation after partial fills.

W84 no cierra `TD-R7D-003` por inferencia.

## Authority permanente
La cadena futura continúa siendo:

`Research -> Promotion Evidence -> Durable Assessment -> Economic Qualification -> Strategy Version Binding -> Shadow/Forward Binding -> PAPER Candidate Decision -> Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

IA/model output no puede saltarse esa cadena ni convertirse directamente en order authority.

**EVIDENCE_QUALIFIED != PAPER_CANDIDATE.**
**PAPER CANDIDATE: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
