# OSS-3D2L — Predictive strategy contract

## Estado

`OSS-3D2L` define la semántica de estrategia necesaria para convertir el winner predictivo OSS-3 en **target allocations de investigación** sin inventar una equivalencia falsa con las seis estrategias bar-driven del Safe Strategy DSL.

D2L no ejecuta Qlib, no lee labels, no construye `OrderIntent`, no llama broker/OMS/Safety y no autoriza PAPER, capital ni LIVE.

## Problema que resuelve

D2I/D2J/D2K responden si un modelo Qlib conserva evidencia predictiva cross-sectional. Eso no define por sí mismo una estrategia operable.

Un predictor entrega:

```text
(timestamp, symbol, score)
```

pero una estrategia necesita una regla explícita que diga cómo transformar esos scores en exposición.

Elegir esa regla **después** de observar FINAL_HOLDOUT sería una forma de tuning sobre el holdout. Por eso la secuencia científica correcta de una campaña real es:

```text
D2I winner
  -> D2J protocol + value-opaque FINAL_HOLDOUT commitment
    -> D2L predictor-to-strategy preregistration
      -> D2K one-shot FINAL_HOLDOUT
```

La rama D2L está técnicamente apilada sobre el código D2K certificado para mantener desarrollo incremental; eso no cambia el orden experimental anterior.

## Política v1

La política canónica por defecto es deliberadamente simple y safety-first:

```text
score direction             DESCENDING
selection                   TOP_FRACTION
selection fraction          25%
minimum selected assets     1
maximum selected assets     5
weighting                   EQUAL_WEIGHT
gross target                75%
maximum weight / asset      25%
minimum cash reserve        25%
minimum cross section       3 assets
rebalance                   each prediction timestamp
tie break                   symbol ascending
execution delay             1 bar
shorting                    blocked
leverage                    blocked
score sign flip             blocked
same-bar execution          blocked
adaptive policy search      blocked
hyperparameter optimization blocked
```

Para una cross-section de tres activos, v1 selecciona uno. El cap de 25% limita la exposición a 25% y deja 75% en cash. Con universos mayores puede aproximarse al gross target sin exceder ningún cap.

## Rank-only semantics

D2L usa únicamente el orden de scores:

```python
sorted(rows, key=lambda row: (-float(row.score), row.symbol))
```

No existe un threshold de score, una interpretación de magnitud o una inversión de signo posterior.

Esto es importante porque el torneo D2E seleccionó el modelo con `mean_cross_sectional_rank_ic`: la evidencia científica avala **ranking cross-sectional**, no una calibración económica de la magnitud absoluta del score.

## Identidad de estrategia

D2L liga exactamente:

- D2J protocol receipt;
- D2I seal root;
- winner trial id;
- model family;
- model config hash;
- D2A request hash;
- DEVELOPMENT prediction artifact + payload;
- prediction receipt;
- environment attestation;
- D2G run evidence;
- shared runner code hash;
- runtime environment hash;
- Qlib version;
- original training dataset hash;
- feature schema;
- portfolio policy fingerprint;
- D2L semantic source hash;
- deterministic DEVELOPMENT allocation semantics.

`strategy_semantic_hash` se deriva de ese conjunto y `strategy_version` se deriva de los primeros 24 hex del semantic hash:

```text
oss3d2l-<24 hex>
```

## Eliminación de circularidad

Los allocations DEVELOPMENT tienen dos identidades diferentes:

1. `semantic_fingerprint`: ranking, pesos, soporte, source prediction, policy y delay; **no** incluye `strategy_version` ni `strategy_semantic_hash`;
2. `fingerprint`: artifact final que sí incluye la identidad de estrategia derivada.

D2L congela en el binding únicamente los semantic fingerprints. Después deriva la identidad de estrategia y reconstruye allocations finales para probar que las mismas semánticas sobreviven sin circularidad.

## Preregistration durable antes de D2K

`SQLiteOSS3PredictiveStrategyRegistry` escribe una fila append-only en:

```text
oss3_predictive_strategy_preregistrations
```

Para una nueva fila ejecuta:

```text
BEGIN IMMEDIATE
  -> comprobar que no existe D2K start para protocol/auth id
  -> comprobar que no existe holdout permit consumido
  -> construir receipt
  -> INSERT D2L preregistration
COMMIT
```

La misma base SQLite debe ser la base autoritativa posteriormente utilizada por D2K. De ese modo un D2K start concurrente no puede intercalarse entre el check y el insert de D2L.

Una preregistración ya existente puede releerse idempotentemente aun después; lo prohibido es crear una política nueva una vez iniciado D2K.

### Alcance de esta garantía

La garantía es durable dentro del **SQLite autoritativo compartido**. No pretende ser una garantía criptográfica global contra un operador que copie deliberadamente el estado a otra base independiente y rompa el procedimiento canónico.

La siguiente capa de orquestación deberá exigir explícitamente que el D2K real consuma el mismo datastore cuyo D2L receipt se considera autoritativo.

## Projection API

`project_prediction_artifact(...)` acepta únicamente un artifact compatible con la estrategia congelada:

- mismo model family;
- mismo model config hash;
- mismo Qlib version;
- mismo training dataset;
- mismo feature schema;
- mismo producer/runner code hash;
- inference era no anterior a la congelada.

Además exige que el D2L code hash actual sea el mismo que quedó congelado en el binding.

El resultado continúa siendo `PredictiveTargetAllocation`; no genera órdenes.

## Audibilidad histórica

Un D2L receipt durable puede reconstruirse aunque el source tree posterior cambie. El `binding_code_hash` queda guardado como evidencia histórica.

La igualdad con el código actual se exige sólo cuando se quiere crear una nueva preregistración o proyectar nuevas predictions. Así se evita que una actualización futura vuelva ilegibles los receipts históricos.

## Authority boundary

Toda evidencia D2L mantiene:

```text
development_labels_used = false
policy_frozen_before_final_holdout = true
policy_selected_after_final_holdout = false
final_holdout_observed = false
final_holdout_consumed = false
retuning_allowed = false
reselection_allowed = false
score_sign_flip_allowed = false
profitability_claim_authorized = false
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

Los allocations agregan:

```text
same_bar_execution_allowed = false
order_intents_generated = false
execution_delay_bars = 1
```

## Qué NO demuestra D2L

D2L no demuestra:

- rentabilidad;
- Sharpe;
- drawdown;
- turnover;
- fees/slippage reales;
- capacidad de ejecución;
- elegibilidad PAPER;
- seguridad LIVE.

Su única afirmación es:

> El predictor seleccionado tiene una semántica de portfolio explícita, determinista, reproducible y congelada antes de FINAL_HOLDOUT.

## Siguiente frontera

Después de certificar D2L, la siguiente pieza correcta será un **cost-aware predictive strategy backtest / qualification layer** que utilice la estrategia ya congelada sobre datasets económicos apropiados y la conecte con la evidencia W79–W87 mediante identidad explícita.

Ese trabajo no debe reabrir FINAL_HOLDOUT para escoger parámetros de portfolio ni convertir un PASS predictivo D2K en una afirmación automática de rentabilidad.
