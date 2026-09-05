# OSS-3D2A — DEVELOPMENT inference contract

Estado: **RESEARCH ONLY / NO EXECUTION AUTHORITY**.

OSS-3D2A define la frontera canónica entre el dataset supervisado TRAIN ya congelado por OSS-3D1 y una matriz de features DEVELOPMENT de OSS-3B. Su función es preparar y verificar una solicitud de inferencia reproducible para un laboratorio Qlib externo, sin introducir Qlib, MLflow, Redis ni otra superficie ML en el core de AUTO-TRADE.

## 1. Problema que resuelve

OSS-3A ya define cómo debe regresar una predicción Qlib verificable. OSS-3B define features point-in-time y OSS-3C labels con horizonte futuro explícito. OSS-3D1 une únicamente features + labels TRAIN y asigna un `training_dataset_hash` canónico.

Faltaba una frontera que respondiera, antes de ejecutar cualquier runtime externo:

1. ¿qué bundle TRAIN exacto se debe usar?;
2. ¿qué feature artifact DEVELOPMENT exacto se puede inferir?;
3. ¿qué modelo/configuración/runtime se espera?;
4. ¿qué filas exactas deben regresar como predicción?;
5. ¿cómo se demuestra que DEVELOPMENT labels no participaron en la inferencia?;
6. ¿cómo se liga posteriormente un OSS-3A real sin convertirlo en señal operativa?

OSS-3D2A resuelve exclusivamente esas preguntas.

## 2. No existe un `ModelTrainingReceipt` previo a la predicción

`ModelTrainingReceipt` de OSS-3D1 se produce al ligar un `QlibPredictionArtifact`; por tanto usarlo como requisito antes de inferir generaría una dependencia circular.

OSS-3D2A se liga directamente a:

- `TrainingBundleArtifact` TRAIN;
- `FactorMatrixArtifact` DEVELOPMENT;
- identidad preregistrada de modelo/configuración;
- versión Qlib requerida;
- hash del código del runner externo esperado.

El recibo de DEVELOPMENT sólo aparece después de que exista un OSS-3A real que supere todas las verificaciones.

## 3. Regla anti-leakage

La API de producción OSS-3D2A no acepta un artifact de labels como argumento y no importa `oss3_supervised_label_artifact`.

Política canónica:

```text
FORBID_DEVELOPMENT_LABELS_V1
```

Esto separa deliberadamente dos etapas futuras:

```text
TRAIN features + TRAIN labels
        |
        v
OSS-3D1 training bundle
        |
        +--------------------+
        |                    |
        v                    v
model training        DEVELOPMENT features
        |                    |
        +---------> OSS-3D2A inference request
                              |
                              v
                      isolated Qlib runner
                              |
                              v
                       OSS-3A predictions
                              |
                              v
                    OSS-3D2A binding receipt

DEVELOPMENT labels --------------X inference path
```

Los labels DEVELOPMENT podrán utilizarse después, en una frontera de evaluación separada. No forman parte de D2A.

## 4. Compatibilidad TRAIN → DEVELOPMENT

`DevelopmentInferenceRequest.build()` exige:

- bundle con partition `TRAIN`;
- feature artifact con partition `DEVELOPMENT`;
- mismo `campaign_id`;
- mismo frozen `research_split_hash`;
- mismo `source_universe_hash`;
- mismo `feature_schema_hash`;
- `train_end <= inference_start`;
- features point-in-time bajo `AVAILABLE_AT_LE_AS_OF`;
- al menos una fila de inferencia.

No existe fallback, coerción ni adaptación automática ante mismatch.

## 5. Identidad completa del request

El manifest liga criptográficamente:

### TRAIN

- training bundle hash;
- training bundle manifest hash;
- TRAIN feature artifact hash;
- TRAIN label artifact hash;
- label definition hash;
- train start/end.

### DEVELOPMENT

- DEVELOPMENT feature artifact hash;
- source dataset hash;
- row payload hash;
- partition = DEVELOPMENT;
- point-in-time policy;
- inference start/end;
- row count;
- exact prediction keyset hash.

### Modelo / runtime externo esperado

- model family;
- model config hash;
- required Qlib version;
- expected external runner code hash.

### Gobierno

- campaign;
- frozen split;
- universe;
- feature schema;
- prediction-key policy;
- label-access policy.

## 6. Prediction keyset

Política:

```text
EXACT_TIMESTAMP_SYMBOL_KEYSET_V1
```

El request calcula SHA-256 sobre la secuencia canónica:

```text
(timestamp, symbol)
```

de cada fila DEVELOPMENT.

Un OSS-3A posterior debe regresar:

- el mismo número de predicciones;
- exactamente los mismos timestamps;
- exactamente los mismos símbolos;
- en la identidad canónica esperada.

Una salida parcial, con filas adicionales, símbolos sustituidos o timestamps desplazados falla cerrada.

## 7. Rebinding de artifacts concretos

Un JSON canónico sólo puede declarar hashes; no demuestra por sí mismo que el proceso externo abrió los archivos correctos.

Por eso `verify_inputs()` revalida, contra los objetos ya verificados:

- bundle hash + bundle manifest;
- TRAIN feature/label hashes;
- campaign/split/universe/schema;
- TRAIN window;
- DEVELOPMENT artifact hash;
- DEVELOPMENT dataset/payload hash;
- DEVELOPMENT partition/policy/window/count;
- DEVELOPMENT exact keyset.

Tanto `dry_run()` como `bind_prediction()` ejecutan este rebinding antes de producir evidencia.

## 8. Dry-run significa contract validation, no fake prediction

`dry_run()` no importa ni ejecuta Qlib. Tampoco construye un `QlibPredictionArtifact` sintético, porque OSS-3A declara como productor exacto `microsoft/qlib` y fabricarlo dentro del core falsearía provenance.

La evidencia dry-run deja explícitamente:

```text
development_labels_loaded = false
final_holdout_loaded = false
external_runtime_invoked = false
qlib_imported = false
prediction_artifact_created = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

## 9. Binding de un OSS-3A real

`bind_prediction()` recibe un `QlibPredictionArtifact` ya verificado estructuralmente por OSS-3A y exige igualdad exacta de:

- `training_dataset_hash` = OSS-3D1 bundle hash;
- feature schema;
- model family;
- model config hash;
- Qlib version;
- producer/runner code hash;
- train start/end;
- inference start/end;
- prediction count;
- prediction keyset hash.

Después produce `DevelopmentPredictionReceipt`, que conserva lineage completo pero sigue sin autoridad operativa.

## 10. Qué no hace OSS-3D2A

No hace:

- entrenamiento real;
- inferencia real;
- instalación/import de Qlib;
- MLflow/Redis;
- selección adaptativa de hiperparámetros;
- evaluación con DEVELOPMENT labels;
- FINAL_HOLDOUT;
- construcción de StrategySpec;
- generación de OrderIntent;
- sizing;
- OMS;
- Safety;
- llamadas a broker;
- PAPER execution;
- LIVE.

## 11. Siguiente frontera

La siguiente etapa debe ser **OSS-3D2B**, un runner/lab Qlib realmente aislado y reproducible que:

1. consuma artifacts por hash;
2. valide primero el request D2A;
3. instale dependencias ML únicamente en su entorno aislado;
4. entrene/infiere bajo configuración congelada;
5. produzca exclusivamente OSS-3A canonical JSON;
6. no tenga credenciales ni conectividad a broker/OMS/Safety;
7. pueda ejecutarse de forma determinista en CI/lab antes de cualquier evaluación de alpha.

Después, una frontera distinta podrá evaluar las predicciones contra labels DEVELOPMENT sin contaminar la etapa de inferencia.

## 12. Criterio de éxito

OSS-3D2A es exitoso cuando demuestra que un modelo externo puede recibir una especificación DEVELOPMENT totalmente auditable, sin acceso a labels de evaluación y sin adquirir ninguna autoridad de trading.

**Pasar OSS-3D2A no demuestra alpha, rentabilidad ni preparación para PAPER/LIVE.**
