# OSS-3A — Qlib Prediction Artifact Contract

Fecha: 2026-09-05  
Rama: `research/oss3a-qlib-prediction-artifact`  
Base: OSS-2H certificado (`82d55d4ab72009ff5ecdacaa284b1dd8f53778fb`)  
Estado: RESEARCH ONLY / DRAFT

## 1. Objetivo

Incorporar Qlib como fuente de investigación cuantitativa sin convertirlo en dependencia del runtime operativo de AUTO-TRADE y sin permitir que un framework ML externo adquiera autoridad sobre broker, OMS, Safety, PAPER, capital o LIVE.

Qlib queda del lado **producer/lab**. AUTO-TRADE queda del lado **consumer/verifier**.

```text
isolated Qlib lab
    |
    | canonical prediction JSON
    v
OSS-3A strict local parser
    |
    | hash/provenance/schema/time validation
    v
QlibPredictionEvidence
    |
    +--> RESEARCH / model comparison / future tournament integration

NO --> OrderIntent / OMS / broker / Safety writer / capital / LIVE
```

## 2. Decisión de dependencia

OSS-3A **no añade `pyqlib` al `pyproject.toml`**.

Razones:

1. el core no necesita ejecutar entrenamiento para verificar predicciones;
2. el entorno Qlib puede usar un stack ML mucho más amplio que AUTO-TRADE;
3. aislar el producer reduce superficie de supply-chain y dependencia transitiva;
4. evita mezclar notebooks/experiments/model stores con la frontera operacional;
5. permite congelar el output por hashes antes de que entre al pipeline de investigación.

El artefacto no es pickle, no contiene callables y no puede cargar código.

## 3. Identidad mínima obligatoria

Cada manifest liga:

- producer exacto: `microsoft/qlib`;
- licencia declarada: `MIT`;
- versión Qlib;
- familia de modelo;
- SHA-256 de configuración de modelo;
- SHA-256 de dataset de entrenamiento;
- SHA-256 de esquema/features;
- SHA-256 del código productor;
- ventana temporal de entrenamiento;
- ventana temporal de inferencia;
- número de predicciones;
- SHA-256 del payload de predicciones.

El artifact completo tiene además su propio SHA-256 canónico.

## 4. Contrato temporal

El contrato exige:

```text
train_start < train_end <= inference_start < inference_end
```

Cada fila debe cumplir:

```text
inference_start <= prediction.timestamp < inference_end
```

Esto no demuestra por sí solo ausencia total de leakage dentro del laboratorio Qlib; sí impide que AUTO-TRADE acepte un artefacto cuyo calendario declarado ya revele solapamiento entre entrenamiento e inferencia.

Las pruebas posteriores deberán ligar también el dataset/feature matrix al calendario para reforzar la evidencia anti-lookahead end-to-end.

## 5. Canonicalización

- JSON UTF-8 solamente;
- top-level schema exacto, sin campos adicionales;
- manifest schema exacto;
- row schema exacto `{timestamp, symbol, score}`;
- timestamps UTC canónicos con `+00:00`;
- score finito; `NaN`, `+Inf`, `-Inf` rechazados;
- filas ordenadas por `(timestamp, symbol)`;
- una sola fila por `(timestamp, symbol)`;
- máximo 2,000,000 predicciones;
- tamaño de archivo limitado a 25 MB;
- hashes recalculados durante read.

## 6. Authority boundary

La evidencia OSS-3A fija permanentemente:

- execution authorized: `FALSE`;
- PAPER execution authorized: `FALSE`;
- capital authority: `NONE`;
- LIVE: `BLOCKED`.

El boundary estático rechaza imports directos de:

- `qlib`, `mlflow`, `redis`;
- networking HTTP/socket;
- subprocess/process execution;
- pickle/dill/cloudpickle;
- brokers/OMS/Safety/engine.

También rechaza `eval`, `exec`, `compile`, dynamic import y métodos equivalentes de orden/execution.

## 7. Qué prueba OSS-3A

OSS-3A prueba que AUTO-TRADE puede recibir un conjunto de predicciones externas con identidad y procedencia deterministas y detectar modificaciones no autorizadas.

No prueba:

- que el modelo tenga alpha;
- que Qlib haya sido configurado correctamente;
- que el dataset externo sea económicamente útil;
- que no haya leakage oculto dentro de las features upstream;
- que el modelo sea rentable fuera de muestra;
- que pueda promocionarse a PAPER/LIVE.

Todo eso sigue sujeto a los gates científicos existentes.

## 8. Siguiente wave propuesta

### OSS-3B — Factor Matrix Export Contract

Congelar una matriz de features/factors con:

- dataset provenance hash;
- feature definitions hash;
- `(timestamp, symbol)` coverage;
- missing-value policy;
- point-in-time availability / observation timestamps;
- train/development/holdout split identity.

### OSS-3C — Isolated Qlib Lab Runner

Script/tool de laboratorio opcional y separado que:

1. instala/usa Qlib fuera del core;
2. consume únicamente una factor matrix OSS-3B;
3. entrena un modelo preregistrado;
4. produce el artifact OSS-3A;
5. nunca conoce credenciales de broker ni importa superficies de ejecución AUTO-TRADE.

### OSS-3D — ML Ranking Tournament Adapter

Convertir `QlibPredictionEvidence` en input de un backtest/tournament research-only con costos, multiple-testing, DEVELOPMENT, HOLDOUT y Shadow/Forward. La integración no debe convertir una predicción externa directamente en una orden.

## 9. Estado operacional

```text
Qlib runtime inside AUTO-TRADE core: FALSE
Network access in OSS-3A: NONE
Broker access: NONE
OMS access: NONE
Safety writer: NONE
OrderIntent: NONE
PAPER execution: FALSE
Capital authority: NONE
LIVE: BLOCKED
```
