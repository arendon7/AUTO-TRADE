# OSS-3D2K — One-shot predictive FINAL_HOLDOUT evaluator

## Estado

`OSS-3D2K` es la primera frontera OSS-3 autorizada a exponer los outcomes del `FINAL_HOLDOUT` protegido.

Su autoridad termina en una decisión predictiva terminal `PASS`/`FAIL`. No autoriza promoción, PAPER, capital, LIVE ni permite afirmar rentabilidad.

## Dependencia científica

D2K consume un `OSS3FinalHoldoutProtocolReceipt` de OSS-3D2J. Ese protocolo ya congela:

- el winner DEVELOPMENT exacto de D2I;
- toda la lineage D2H/D2E/D2D/D2G/D2A;
- el `model_config_hash` exacto;
- el TRAIN bundle original;
- el protected FINAL_HOLDOUT exacto por commitment;
- los umbrales predictivos finales;
- `max_evaluations = 1`;
- prohibición de retuning, reselection, fallback y segundo intento.

D2K no redefine ninguna de esas decisiones.

## Secuencia irreversible

La secuencia de `SQLiteOSS3FinalHoldoutEvaluationRegistry.evaluate(...)` es:

1. verificar D2J y la identidad del protected holdout sin leer outcomes;
2. re-ligar el source D2A request y el TRAIN bundle original;
3. rechazar cualquier credencial de broker/exchange presente en el proceso;
4. construir el `HoldoutPermit` exacto derivado por D2J;
5. ejecutar `BEGIN IMMEDIATE`;
6. insertar el permit consumido y el `OSS3D2K_FINAL_HOLDOUT_START_V1` en la misma transacción;
7. `COMMIT`;
8. sólo entonces ejecutar `_checkout(...)` del protected holdout;
9. reproducir el winner con Qlib bajo `deny_network()`;
10. calcular Rank IC y exact sign test;
11. emitir un terminal receipt `PASS` o `FAIL` append-only.

La propiedad crítica es:

```text
credential reject
    -> durable permit burn + start receipt
        -> protected holdout checkout
            -> model/evaluation
                -> terminal PASS/FAIL
```

No existe una ruta en la que se observen labels y después se decida si consumir el permit.

## Sin refit en DEVELOPMENT

D2K evalúa el modelo seleccionado, no un modelo nuevo posterior a la selección.

Por eso exige:

```text
source_request.request_hash == D2I winner request_hash
training_bundle.artifact_hash == D2A original training_bundle_hash
train_features.partition == TRAIN
train_labels.partition == TRAIN
model_config_hash == frozen winner model_config_hash
runner_code_hash == frozen D2G runner_code_hash
QLIB_VERSION == 0.9.7
```

DEVELOPMENT features y DEVELOPMENT labels no son argumentos del evaluator.

El modelo se vuelve a ajustar únicamente al TRAIN bundle original para reproducir el estimator congelado sobre features FINAL_HOLDOUT. No se incorporan DEVELOPMENT labels al fit.

## Protected FINAL_HOLDOUT material

D2K usa un formato separado del artifact OSS-3B/OSS-3C DEVELOPMENT para evitar falsificar semánticamente una partición DEVELOPMENT.

`OSS3FinalHoldoutMaterial` contiene:

- source campaign;
- frozen research split;
- universe;
- feature schema;
- label definition;
- feature and label source dataset hashes;
- partition start/end;
- canonical feature names;
- point-in-time feature rows;
- future-horizon label rows.

Los feature/label keysets deben ser idénticos y estar canónicamente ordenados por `(timestamp, symbol)`.

El material deriva exactamente el `OSS3ProtectedFinalHoldoutCommitment` que D2J congeló antes de la evaluación. Cualquier diferencia en features, labels, keyset, cross-section support o provenance cambia el commitment y falla antes de consumir el permit.

## Sample adequacy

El commitment D2J exige estructuralmente:

```text
holdout_cross_sections >= 30
total_observations >= 90
minimum observations per cross-section >= 3
```

D2K endurece esto después del checkout: deben sobrevivir al menos 30 cross-sections **no degeneradas** para Rank IC. Una cross-section con scores constantes o labels constantes no cuenta como evidencia válida.

Si quedan menos de 30 cross-sections válidas, la evaluación termina como structural `FAIL`; el permit ya fue consumido y no existe retry.

## Métrica primaria

D2K conserva exactamente la métrica primaria de D2E/D2J:

```text
mean_cross_sectional_rank_ic
```

Para cada timestamp:

1. agrupa las observaciones por símbolo;
2. exige al menos 3 observaciones;
3. excluye cross-sections degeneradas;
4. calcula Spearman Rank IC como Pearson sobre average ranks.

Los zeros de Rank IC no se cuentan en el exact sign test, igual que en D2E.

## Gates finales

D2K no inventa thresholds. Materializa exactamente los gates preregistrados en D2J:

```text
FINAL_NONZERO_RANK_IC_CROSS_SECTIONS_MIN >= 20
FINAL_MEAN_CROSS_SECTIONAL_RANK_IC_MIN >= 0.02
FINAL_ONE_SIDED_EXACT_SIGN_TEST_P_MAX <= 0.05
```

`PASS` requiere los tres gates simultáneamente.

No hay Holm en FINAL_HOLDOUT porque D2J congela un único winner y una única evaluación. El control de multiplicidad de la familia ya ocurrió en DEVELOPMENT D2E.

## Terminalidad

Dos clases de terminal receipt:

### Metric PASS/FAIL

Incluye:

- prediction payload hash;
- result hash;
- predictive metrics;
- las tres gates;
- `failed_gate_ids`;
- decisión mecánica.

### Structural FAIL

Si, después de consumir el permit, ocurre una excepción de integridad, runtime o sample adequacy, D2K emite:

```text
decision = FAIL
failure_code = EVALUATION_ERROR:<ExceptionType>
metrics = null
gates = []
predictive_validation_passed = false
```

No se fabrican métricas parciales.

## No second chance

Las tablas SQLite tienen uniqueness por:

- `evaluation_id`;
- `protocol_id`;
- `protocol_receipt_hash`;
- winner binding;
- holdout commitment;
- holdout authorization id;
- start hash;
- terminal receipt hash.

UPDATE y DELETE están bloqueados por triggers append-only.

Un start consumido sin terminal receipt es tratado como autorización gastada y bloquea retry.

Un FAIL métrico también es terminal. No se puede:

- reentrenar;
- cambiar alpha;
- elegir el segundo candidato;
- modificar el holdout;
- repetir con otro `evaluation_id` en el mismo durable registry.

## Runtime isolation

El único import de Qlib vive dentro de `_run_frozen_final_model(...)` y ocurre bajo:

```python
with deny_network():
```

El evaluator exige `pyqlib==0.9.7` y reutiliza la configuración exacta del winner D2F/D2G.

Antes de cualquier permit burn se rechazan variables de credenciales con prefijos Alpaca/APCA, IBKR, Binance, Coinbase, Kraken, Bybit, OKX, Bitget, KuCoin y `BROKER_`.

## Authority boundary

Incluso un PASS mantiene:

```text
retuning_allowed = false
reselection_allowed = false
fallback_candidate_allowed = false
second_attempt_allowed = false
profitability_claim_authorized = false
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

D2K responde únicamente:

> ¿El winner DEVELOPMENT congelado conserva evidencia predictiva preregistrada en el único FINAL_HOLDOUT protegido?

No responde:

> ¿La estrategia es rentable después de costes reales?

ni:

> ¿Debe operar capital?

Esas son fronteras posteriores y separadas.

## Pruebas D2K

La suite dedicada debe cubrir al menos:

- real Qlib aligned FINAL_HOLDOUT -> predictive PASS;
- reversed FINAL_HOLDOUT -> metric FAIL;
- structural failure after checkout -> terminal FAIL;
- degenerate cross-sections -> terminal structural FAIL;
- second attempt after PASS -> rejected;
- second attempt after FAIL -> rejected;
- holdout commitment drift before permit -> rejected without consumption;
- broker credentials before permit -> rejected without consumption;
- TRAIN artifact drift -> rejected without consumption;
- protected wrapper second checkout -> rejected;
- append-only UPDATE/DELETE rejection;
- read-only start/terminal/permit reconstruction;
- stable evaluator semantic hash;
- static absence of broker/OMS/Safety/PAPER/LIVE authority.

## Siguiente frontera

Un PASS D2K es evidencia OOS predictiva de una sola evaluación. La siguiente frontera no debe saltar directamente a LIVE.

La transición correcta es construir una **evidence qualification layer** que combine el receipt D2K con las pruebas económicas y operacionales ya existentes del sistema (costes, slippage, drawdown, W79-W87, Safety, PAPER readiness), sin reutilizar FINAL_HOLDOUT para tuning ni volver a seleccionar modelos.
