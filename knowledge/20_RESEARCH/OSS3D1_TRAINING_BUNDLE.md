# OSS-3D1 — Training Bundle & Model Training Receipt

Fecha: 2026-09-05  
Rama: `research/oss3d1-training-bundle-contract`  
Base: OSS-3C certificado (`4eb285dc14df3fbb21f0bef592dfe7a9fc3d814e`)  
Estado: RESEARCH ONLY / DRAFT

## 1. Objetivo

Unir de forma determinista y auditable un artifact OSS-3B de features TRAIN con un artifact OSS-3C de labels TRAIN, sin introducir Qlib ni otras dependencias ML dentro del core de AUTO-TRADE.

El resultado es un `TrainingBundleArtifact` cuyo `artifact_hash` es la identidad canónica que un productor OSS-3A deberá declarar como `training_dataset_hash`.

## 2. Compatibilidad V1

El pairing sólo es válido si features y labels coinciden exactamente en:

- `campaign_id`;
- `research_split_hash`;
- partition = `TRAIN`;
- `partition_start`;
- `partition_end`;
- `source_universe_hash`;
- conjunto y orden canónico de claves `(timestamp, symbol)`.

La política es:

```text
EXACT_TIMESTAMP_SYMBOL_KEYSET_V1
```

No existen joins posicionales, `inner join`, `dropna` silencioso ni tolerancia a samples faltantes.

## 3. Dataset provenance

Los hashes de dataset de features y labels **no tienen que ser iguales**. Pueden provenir de pipelines o transformaciones diferentes, pero ambos quedan retenidos:

- `feature_source_dataset_hash`;
- `label_source_dataset_hash`.

Además el bundle liga:

- feature artifact hash;
- label artifact hash;
- feature schema hash;
- label definition hash;
- campaign/split/universe;
- partition/window;
- sample count;
- pairing policy.

## 4. Training dataset identity

```text
OSS-3B features + OSS-3C labels
          |
          | exact compatibility + key pairing
          v
OSS-3D1 TrainingBundleArtifact
          |
          | artifact_hash == training_dataset_hash
          v
isolated Qlib producer
          |
          v
OSS-3A prediction artifact
```

La identidad no depende de nombres de archivo o rutas locales.

## 5. Prediction binding

`bind_prediction()` acepta únicamente un `QlibPredictionArtifact` OSS-3A que satisfaga:

- `training_dataset_hash == bundle.artifact_hash`;
- `feature_schema_hash == bundle.feature_schema_hash`;
- `train_start == bundle.partition_start`;
- `train_end == bundle.partition_end`.

Si pasa, produce un `ModelTrainingReceipt` que liga:

- feature artifact;
- label artifact;
- training bundle;
- prediction artifact;
- campaign + frozen split;
- feature schema + label definition;
- Qlib version;
- model family/config;
- producer code;
- train/inference windows.

Así evitamos modificar retroactivamente el contrato OSS-3A certificado sólo para añadir metadata de labels/campaign.

## 6. Canonicalización

- canonical UTF-8 JSON;
- duplicate keys denied;
- exact schemas;
- canonical UTC `+00:00` timestamps;
- bounded artifact size;
- SHA-256 identities recalculated;
- direct constructors also validate hashes/windows.

## 7. Isolation boundary

OSS-3D1 importa únicamente contratos locales OSS-3A/3B/3C. No importa el runtime externo Qlib.

Prohibido:

- `qlib`, MLflow, Redis;
- network/HTTP/socket;
- subprocess/process execution;
- pickle/dill/cloudpickle;
- broker/OMS/Safety/engine;
- dynamic code execution;
- order construction/submission.

## 8. Authority

`ModelTrainingReceipt` fija:

```text
execution_authorized = FALSE
paper_execution_authorized = FALSE
capital_authority = NONE
live_trading = BLOCKED
```

El bundle y el receipt son evidencia de lineage, no autoridad operativa.

## 9. Qué no demuestra

OSS-3D1 no demuestra alpha, generalización, significancia económica ni rentabilidad. Tampoco habilita PAPER o LIVE.

El modelo producido seguirá sujeto a DEVELOPMENT, multiple-testing, robustness, costs, FINAL_HOLDOUT, execution sensitivity y Shadow/Forward.

## 10. Siguiente frontera

OSS-3D2 deberá ser el runner ML/Qlib aislado. Deberá leer artifacts verificados, entrenar fuera del core, producir OSS-3A y un receipt verificable, sin broker credentials ni acceso a OMS/Safety.
