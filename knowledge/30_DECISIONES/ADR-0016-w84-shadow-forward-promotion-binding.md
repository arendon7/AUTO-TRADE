# ADR-0016 — W84 Shadow/Forward Promotion Binding

Fecha: 2026-08-24
Estado: **ACCEPTED / BEHAVIORAL IMPLEMENTATION CERTIFIED / `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` RESOLVED FOR THE EXACT BOUND CANDIDATE ONLY**

## Contexto
W83 cerró `EXECUTION_STRATEGY_VERSION_UNBOUND` para la identidad exacta del candidate seleccionado, pero dejó correctamente abierto `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

R5 ya disponía de registries Shadow/Forward append-only y hash-protected. Sin embargo, una cadena R5 internamente íntegra no demostraba por sí sola que cada `StrategyShadowObservation` hubiera sido producida por la exact StrategySpec/runtime W83. El campo `source_fingerprint` podía ser un SHA-256 opaco suministrado por el caller y la frozen config ligaba el `strategy_id`, no cada outcome a la versión/artifact/runtime exactos.

Aceptar esa cadena como promotion evidence habría permitido atribuir retornos de otra implementación con el mismo string de estrategia al candidate certificado. W84 debía cerrar esa discontinuidad sin crear un segundo Shadow engine, sin seleccionar o recalibrar después de observar outcomes y sin acuñar PAPER/LIVE/capital authority.

## Decisión
W84 adopta una capa de **measurement provenance determinista** delante de la resolución Shadow/Forward y reutiliza la persistencia R5 como cadena durable.

La continuidad certificada pasa a ser:

`exact W83 candidate/artifact/runtime -> frozen W84 measurement plan/policy -> deterministic prefix-only measurement receipts -> exact R5 Shadow observations -> exact R5 Forward tail -> W84 promotion resolution`.

## 1. Forward measurement runtime identity
`src/autotrade/forward_shadow_measurement.py` define `ForwardMeasurementRuntimeIdentity`.

La identidad de medición no es sólo el runtime safe-DSL W83. También cubre las piezas que determinan la economía del resultado medido:
- W83 safe-DSL aggregate runtime hash;
- `research/backtest.py`;
- `research/costs.py`;
- `domain.py`;
- implementación Python;
- versión Python major.minor.patch exacta.

Cambiar cualquiera de esas superficies invalida `measurement_runtime_hash`.

## 2. Pre-outcome ForwardMeasurementPlan
`ForwardMeasurementPlan` se congela antes de forward activation y liga:
- exact W83 resolution + binding hashes;
- selected trial/strategy identity heredada;
- exact `StrategySpec.canonical_hash`;
- W83 runtime hash;
- W84 measurement runtime hash + source hashes;
- exact `BacktestConfig` hash e initial cash;
- exact history dataset hash;
- source/symbol/venue/quote currency/timeframe;
- número de history bars;
- `planned_at`;
- `forward_activated_at`;
- no-authority flags.

El history dataset debe terminar exactamente en `planned_at`. La activación debe ocurrir estrictamente después del freeze y alineada al timeframe.

La secuencia es:

`closed history -> plan/policy freeze -> deterministic bridge/state bars -> forward activation -> qualification periods`.

Los datos posteriores al freeze pero anteriores a activation pueden establecer estado determinista del algoritmo, pero no cuentan como qualification return.

## 3. Prefix-only measurement receipts
Cada `ForwardShadowMeasurementReceipt` se produce reutilizando `BacktestEngine` sobre un dataset que termina exactamente en el período que se está midiendo.

Para el período k se usa:

`history + post_freeze_dataset[:k]`

y nunca el tail futuro. Por tanto el receipt del período 1 no puede cambiar cuando aparecen períodos 2, 3, etc.

Cada receipt liga:
- plan + policy hashes;
- ordinal;
- candidate id/version/spec;
- W83 runtime + measurement runtime;
- BacktestConfig;
- prefix dataset hash;
- deterministic prefix result hash;
- period bounds;
- equity before/after;
- `return_fraction`;
- `previous_measurement_hash`;
- capture timestamp;
- receipt hash;
- hard-coded no authority.

La cadena de receipts es hash-linked.

## 4. Exact per-observation R5 binding
El `source_fingerprint` de cada `StrategyShadowObservation` debe ser exactamente el `measurement_hash` del receipt determinista correspondiente.

W84 revalida además:
- strategy id;
- period start/end;
- return fraction;
- receipt identity/chain;
- candidate/config identity.

Un hash arbitrario, una estrategia distinta con el mismo string, un retorno fabricado o un receipt de otra policy no pueden pasar.

R5 continúa siendo la authority durable para:
- `FrozenShadowConfig`;
- append-only Shadow records;
- control/head hashes;
- `FrozenForwardPolicy`;
- append-only Forward evidence;
- tail/tamper detection.

W84 no crea un segundo Shadow database/engine.

## 5. Candidate-only Shadow semantics
La frozen Shadow config de promoción debe contener exclusivamente:

`{selected_strategy_id: Decimal("1")}`.

Un portfolio mixto no puede ocultar la debilidad del selected candidate.

`FrozenShadowConfig.source_config_hash` recommite el exact W84 policy hash.

## 6. Forward policy recommitment
`FrozenForwardPolicy` liga:
- exact forward campaign;
- exact activation;
- candidate-only Shadow config fingerprint;
- `frozen_parameters_hash = W84 policy_hash`;
- `source_code_hash = W84 measurement_runtime_hash`.

El source-code commitment es deliberadamente más amplio que el runtime W83 porque la medición depende también del BacktestEngine, costo y domain semantics.

## 7. Fixed horizon / no optional stopping
W84 preregistra `required_forward_periods = N`.

Semántica exacta:
- menos de N: `PENDING / FORWARD_WINDOW_INCOMPLETE`;
- exactamente N: evaluar thresholds congelados;
- más de N ya observados: `FAIL / FORWARD_WINDOW_OVERRUN`.

No se permite esperar hasta que una ventana se vuelva favorable ni decidir ignorando outcomes posteriores ya conocidos.

Además, el Forward registry debe representar el tail eligible Shadow completo y en orden exacto. No se puede omitir selectivamente un período adverso.

## 8. Freshness contra cherry-picking temporal
La policy congela:
- `max_capture_lag_seconds`;
- `max_assessment_delay_seconds`.

Su suma debe ser estrictamente menor que un market period.

La causalidad obligatoria es:

`measurement_data_cutoff_at <= measurement_captured_at <= assessed_at`.

Esto obliga a materializar y decidir la ventana antes de que pueda cerrarse silenciosamente el período siguiente.

## 9. Resolution semantics
`PromotionShadowForwardResolution` puede retirar únicamente:

`SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

Debe conservar:
- exact W83 candidate/trial/spec/runtime identity;
- exact W84 plan/policy/evidence identity;
- `strategy_version_execution_bound = True`;
- `shadow_forward_promotion_bound = True`;
- PAPER candidate FALSE;
- external execution FALSE;
- runtime execution FALSE;
- capital authority NONE;
- LIVE BLOCKED.

Resolver el blocker científico **no es** una decisión de admisión PAPER.

## Fail-closed / adversarial coverage
La suite W84 cubre, entre otros:
- same strategy string con source fingerprint arbitrario;
- fabricated return dentro de una R5 chain válida;
- changed StrategySpec/runtime/config/history;
- runtime source drift;
- history gaps/lookback insuficiente;
- misaligned activation;
- dataset/source/timeframe drift;
- prefix/no-lookahead semantics;
- receipt identity/hash/chain tamper;
- missing Shadow period;
- omitted Forward tail;
- non-exclusive Shadow config;
- wrong Forward measurement runtime;
- incomplete horizon;
- post-horizon overrun;
- threshold failure;
- drawdown failure;
- capture lag excesivo;
- assessment delay excesivo;
- policy/evidence/resolution hash tamper;
- invalid causality/time bounds;
- blocker drift;
- authority escalation.

## Permanent boundary
`scripts/check_w84_shadow_forward_promotion_boundary.py` queda conectado a:
- `.github/workflows/w84-shadow-forward-promotion.yml`;
- `.github/workflows/core-tests.yml`.

El boundary prohíbe en la superficie W84:
- broker modules/writers;
- network libraries/calls;
- OMS/Safety/connectivity/paper-close authority;
- direct SQLite authority;
- credentials/endpoints Alpaca;
- `OrderIntent(` construction;
- PAPER/LIVE/capital escalation.

También exige estructuralmente los commitments de runtime, prefix-only measurement, exact per-observation fingerprint, complete tail, fixed horizon, freshness y exact blocker semantics.

## Behavioral exact-head certification
Behavioral exact head:

`abf25f4b699f145a629955efe73a798966f29845`

Dedicated W84 run `32740076750`:
- **44/44 W84 PASS**;
- compile PASS;
- W84 permanent boundary PASS;
- W83/W82/W81/W80/W79/W78 boundaries PASS;
- Research Authority PASS.

Core Safety run `32740076693`:
- **3035/3035 PASS**;
- exact measured coverage `85.20576561520785%` >= 85%;
- `forward_shadow_measurement.py`: 93%;
- `promotion_shadow_forward_binding.py`: 89%;
- Contract Registry 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- inherited R5/R6/R7/W78-W84 boundaries PASS;
- Debt Register PASS: 60 items / 9 open / 8 blocking on uncertified tracks;
- Canonical Knowledge PASS.

Knowledge Contract run `32740076824`: **SUCCESS**.

El cierre canónico será un descendiente documentation-only de este behavioral head y debe recertificarse para evitar declarar exact-head sobre un SHA anterior.

## Consecuencia
`SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` queda resuelto **sólo para la exact W83 candidate/artifact/runtime + W84 plan/policy/measurement + R5 Shadow/Forward identity certificada**.

El resultado no es transferible a otra campaign, trial, StrategySpec, runtime, BacktestConfig, history dataset, Shadow config, Forward campaign o evidence tail.

La siguiente decisión debe ser explícita y separada: determinar si la cadena completa de evidence puede ser admitida como `PAPER_CANDIDATE` bajo una policy de probation/readiness. W84 no realiza esa transición.

`TD-R7D-003` permanece OPEN y es independiente.

No se realizó broker POST en W84.

**EVIDENCE_QUALIFIED != PAPER_CANDIDATE.**
**PAPER CANDIDATE: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
