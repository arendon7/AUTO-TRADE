# ADR-0016 — W84 Shadow/Forward Promotion Binding

Fecha: 2026-08-24
Estado: **ACCEPTED / BEHAVIORAL IMPLEMENTATION CERTIFIED / FINAL W84 TRUST BOUNDARY SOURCE-AUTHORITATIVE + PROCESS-CLOCK VERIFIED**

## Contexto
W83 resolvió `EXECUTION_STRATEGY_VERSION_UNBOUND` para la identidad exacta del candidate seleccionado y dejó correctamente abierto `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

R5 ya proveía registries Shadow/Forward append-only y hash-protected. Sin embargo, una cadena R5 íntegra no probaba por sí sola que cada `StrategyShadowObservation` proviniera de la exact StrategySpec/runtime W83: `source_fingerprint` podía ser un SHA-256 opaco suministrado por el caller. W84 debía ligar los outcomes a una medición reproducible, impedir optional stopping y conservar la separación estricta entre evidencia científica y autoridad de trading.

La revisión de W84 encontró además un segundo problema de trust boundary: un `ShadowForwardPromotionEvidence` coherentemente reconstruido podía volver a calcular su propio hash y mentir sobre métricas, ventanas o freshness. Por esa razón un hash válido prueba autoconsistencia del objeto, no verdad durable de sus campos.

Finalmente, R5 Shadow/Forward no conserva un timestamp independiente de append que pueda actuar como fuente temporal autoritativa de la decisión. Por tanto `assessed_at`, aunque hash-bound dentro del receipt, tampoco puede ser tratado como prueba independiente de que la decisión ocurrió dentro del budget congelado.

## Decisión
W84 se cierra en cuatro capas separadas:

`exact W83 candidate/artifact/runtime`
`-> frozen W84 measurement plan/policy`
`-> deterministic prefix-only measurement receipts`
`-> exact R5 Shadow + exact R5 Forward`
`-> PromotionShadowForwardResolution (intermedio)`
`-> PromotionShadowForwardSourceVerification (intermedio)`
`-> PromotionShadowForwardFinalVerification (canónico)`.

**Sólo `PromotionShadowForwardFinalVerification` es la salida W84 admisible para W85.**

## 1. Forward measurement provenance
`src/autotrade/forward_shadow_measurement.py` define una identidad de medición que liga:
- exact W83 safe-DSL runtime;
- `research/backtest.py`;
- `research/costs.py`;
- `domain.py`;
- implementación y patch exactos de Python;
- exact StrategySpec, BacktestConfig e history dataset.

`ForwardMeasurementPlan` se congela antes de forward activation. El history dataset debe terminar exactamente en `planned_at`; activation debe ser posterior y alineada al timeframe.

Cada qualification period ejecuta el `BacktestEngine` sobre un prefijo que termina en ese período. El receipt k no puede depender de períodos k+1..N. Los receipts son hash-linked y su `measurement_hash` es el único `source_fingerprint` válido para la observación Shadow correspondiente.

## 2. Candidate-only Shadow y Forward recommitment
La Shadow config de promoción debe contener exactamente:

`{selected_strategy_id: Decimal("1")}`.

Además:
- `FrozenShadowConfig.source_config_hash = W84 policy_hash`;
- `FrozenForwardPolicy.frozen_parameters_hash = W84 policy_hash`;
- `FrozenForwardPolicy.source_code_hash = W84 measurement_runtime_hash`;
- Forward liga el exact candidate-only Shadow config fingerprint.

R5 continúa siendo la única autoridad durable de persistencia para Shadow/Forward. W84 puede leer/verificar esas cadenas pero no registrar configs, policies ni appendear records.

## 3. Fixed horizon / no optional stopping
Para `required_forward_periods = N` preregistrado:
- `< N` -> `PENDING / FORWARD_WINDOW_INCOMPLETE`;
- `N` -> evaluación de thresholds congelados;
- `> N` ya observado -> `FAIL / FORWARD_WINDOW_OVERRUN`.

El tail Shadow eligible completo debe corresponder uno-a-uno y en orden al tail Forward. Un período adverso no puede omitirse.

## 4. `PromotionShadowForwardResolution` V2 es intermedio
`resolve_promotion_shadow_forward_binding(...)` conserva valor como receipt de identidad y semántica de blockers. Puede retirar únicamente:

`SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

Pero **no es el trust boundary final de W84**. Un objeto evidence rehash-valid puede ser autoconsistente sin probar que sus métricas siguen correspondiendo a la verdad durable R5. Ninguna wave posterior debe consumir este V2 directamente.

## 5. Source-authoritative verification obligatoria
`src/autotrade/promotion_shadow_forward_source_verification.py` introduce:

`PromotionShadowForwardSourceVerification`

Esta capa vuelve a leer y verificar:
- R5 Shadow config, records y control head;
- R5 Forward policy, records y control head;
- exact measurement receipts;
- exact preregistered horizon;
- Shadow↔Forward record identity;
- measurement↔Shadow binding;
- measurement receipts hash/head;
- qualification start/end/duration;
- cumulative return;
- peak-to-trough drawdown;
- capture lag;
- policy thresholds.

Las métricas finales se recalculan desde los records Forward verificados, no desde los números declarados por el evidence receipt.

La verificación falla si un evidence correctamente rehasheado difiere de las fuentes durables. También relee los control heads al final para detectar TOCTOU durante la verificación.

Esta capa prueba **data/source truth**, pero sigue siendo intermedia porque su `verified_at` puede ser suministrado por caller y R5 no contiene un append timestamp independiente que demuestre por sí mismo la hora de decisión.

## 6. Final process-clock verification
`src/autotrade/promotion_shadow_forward_final_verification.py` introduce:

`PromotionShadowForwardFinalVerification`

y `finalize_promotion_shadow_forward_resolution(...)`.

El finalizador público deliberadamente **no acepta `verified_at`**. Lee el reloj UTC del proceso internamente, invoca de nuevo la verificación source-authoritative y deriva el capture time de la fuente verificada:

`measurement_capture_at = qualification_ended_at + source_verified_capture_lag`.

Luego calcula:

`decision_delay = process_verified_at - measurement_capture_at`.

Falla cerrado si:
- el reloj del proceso precede el capture time;
- `decision_delay > policy.max_assessment_delay_seconds`;
- la identidad source verification / policy / W83 / measurement cambia;
- la relectura durable no puede volver a probarse.

Con esto un caller no puede volver a hashear `assessed_at` para simular una decisión temprana cuando el proceso realmente está fuera del budget congelado.

El receipt final exige simultáneamente:
- `source_truth_verified = True`;
- `process_clock_freshness_verified = True`;
- exact Shadow/Forward blocker resuelto;
- strategy-version binding conservado;
- no PAPER execution authority;
- capital authority NONE;
- LIVE BLOCKED.

## 7. Permanent boundary
`scripts/check_w84_shadow_forward_promotion_boundary.py` está cableado en Dedicated W84 y Core Safety.

El boundary prohíbe en producción W84:
- broker/writer imports o calls;
- network authority;
- OMS/Safety/connectivity/paper-close authority;
- direct SQLite authority;
- credentials/endpoints Alpaca;
- `OrderIntent(` construction;
- R5 mutation calls: `register_config`, `append_period`, `register_policy`, `append_shadow_record`;
- PAPER/external/runtime/capital/LIVE escalation.

Además trata explícitamente como intermedios:
- `resolve_promotion_shadow_forward_binding`;
- `verify_promotion_shadow_forward_resolution_sources`.

Ningún otro módulo de producción puede consumir esas etapas como resultado final sin pasar por el finalizador process-clock.

## 8. Adversarial coverage
La suite W84 cubre, entre otros:
- runtime/spec/config/history drift;
- history gaps/lookback/alignment;
- prefix-only/no-lookahead;
- fabricated source fingerprint/return;
- receipt hash/chain tamper;
- missing Shadow period / omitted Forward tail;
- incomplete/overrun horizon;
- threshold y drawdown failures;
- rehash-valid metric/head/window/proof-flag lies;
- forged-but-rehashed measurement receipt;
- parent/base resolution identity drift;
- R5 TOCTOU;
- caller-supplied temporal lies;
- process clock before capture;
- actual process clock fuera del frozen assessment budget;
- authority escalation.

## 9. Behavioral certification
Behavioral exact head:

`f1ed0f675224c515f74a099ddb0beeefd9c96629`

Dedicated W84 run `32745537577`: **SUCCESS**.
- **76/76 W84 PASS**;
- compile PASS;
- permanent W84 boundary PASS;
- W83/W82/W81/W80/W79/W78 boundaries PASS;
- Research Authority PASS.

Core Safety run `32745537856`: **SUCCESS**.
- **3067/3067 PASS**;
- exact measured coverage `85.27030933795895%` >= 85%;
- `forward_shadow_measurement.py`: 93%;
- `promotion_shadow_forward_binding.py`: 89%;
- `promotion_shadow_forward_source_verification.py`: 89%;
- `promotion_shadow_forward_final_verification.py`: 95%;
- Contract Registry: 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- Research/R5/R6/R7/W78-W84 boundaries PASS;
- Debt Register PASS: 60 items / 9 open / 8 blocking on uncertified tracks;
- Canonical Knowledge PASS.

Knowledge Contract run `32745537825`: **SUCCESS**.

El commit documental que contiene este ADR es un descendiente documentation-only del behavioral head y debe recertificarse exact-head; el SHA y los run IDs de esa recertificación se registran en la verdad del PR para evitar autorreferencia infinita dentro del propio archivo.

## 10. Consecuencias para W85
W85 PAPER Candidate Admission / Probation Gate sólo puede consumir:

`PromotionShadowForwardFinalVerification`

No puede aceptar directamente:
- `PromotionShadowForwardResolution` V2;
- `PromotionShadowForwardSourceVerification`;
- un `ShadowForwardPromotionEvidence` suelto;
- hashes/strings reconstruidos por caller.

W85 seguirá siendo una decisión separada. W84 no convierte evidencia en PAPER candidate.

`TD-R7D-003` permanece **OPEN P2** y es independiente.

No se realizó broker POST en W84.

**EVIDENCE_QUALIFIED != PAPER_CANDIDATE.**
**PAPER_CANDIDATE != PAPER_EXECUTION_AUTHORIZED.**
**PAPER CANDIDATE ACTUAL: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
