# OSS-3B — Point-in-Time Factor Matrix Artifact

Fecha: 2026-09-05  
Rama: `research/oss3b-factor-matrix-artifact`  
Base: OSS-3A certificado (`81651c41b8f6d578494d86fc7d8c2275c4c8e73c`)  
Estado: RESEARCH ONLY / DRAFT

## 1. Objetivo

Crear el contrato canónico del dataset de features que podrá salir de AUTO-TRADE hacia un laboratorio ML/Qlib aislado.

OSS-3A protege el **output de predicciones**. OSS-3B protege el **input de factores**.

```text
market / research sources
    |
    | point-in-time factor construction
    v
OSS-3B canonical factor matrix
    |
    | strict local artifact boundary
    v
isolated ML / Qlib laboratory
    |
    | OSS-3A canonical predictions
    v
AUTO-TRADE research verification
```

## 2. Problema científico que resuelve

Una matriz de features puede parecer temporalmente correcta y aun contener leakage si un dato sólo estuvo disponible después del instante en que el modelo supuestamente lo utilizó.

Por eso cada fila OSS-3B tiene dos relojes explícitos:

- `as_of`: instante lógico de la decisión/snapshot de investigación;
- `available_at`: instante máximo en que los inputs necesarios para ese vector estuvieron efectivamente disponibles según el productor.

La condición obligatoria es:

```text
available_at <= as_of
```

Además:

```text
partition_start <= as_of < partition_end
```

El verificador no infiere disponibilidad a partir del timestamp de una barra. La disponibilidad es un dato explícito y hash-bound que deberá ser producido por el adapter upstream correspondiente.

## 3. Protección del FINAL_HOLDOUT

OSS-3B V1 sólo admite:

- `TRAIN`
- `DEVELOPMENT`

`FINAL_HOLDOUT` no existe como valor válido de `FactorMatrixPartition` y se rechaza durante lectura/construcción.

Esto evita que el laboratorio Qlib genérico reciba accidentalmente el holdout protegido mediante el canal normal de exportación de factores.

Si en el futuro un modelo congelado necesita ser evaluado sobre FINAL_HOLDOUT, deberá existir una frontera distinta, one-shot y permit-aware, compatible con OSS-2G/OSS-2H. No se reutilizará OSS-3B como bypass.

## 4. Separación features vs labels

OSS-3B V1 es **feature-only**.

El schema de una feature exige `role=FEATURE` y el schema de una fila sólo contiene:

```text
as_of
available_at
symbol
values[]
```

No existe campo `label`, `target`, `future_return`, `outcome` ni equivalente en el artifact.

Esto es deliberado: los labels supervisados tienen otra semántica temporal —incluido un horizonte futuro y una fecha de disponibilidad propia— y merecen un contrato separado para evitar que se mezclen inadvertidamente con features point-in-time.

## 5. Feature schema

Cada feature queda ligada a:

- `name`;
- `dtype=float64`;
- `role=FEATURE`;
- `formula_hash`;
- `source_id`;
- `source_hash`;
- `lookback_bars`.

El orden completo del arreglo de features forma parte de `feature_schema_hash`. Los vectores `values[]` usan exactamente ese orden y deben tener la misma anchura.

## 6. Dataset provenance

El manifest liga:

- producer exacto OSS-3B;
- `producer_code_hash`;
- `source_dataset_hash`;
- `source_universe_hash`;
- partition y ventana temporal;
- feature count;
- row count;
- missing-value policy;
- point-in-time policy;
- feature schema hash;
- row payload hash.

El artifact completo tiene su propio SHA-256 canónico. Para OSS-3C, ese `artifact_hash` será el `training_dataset_hash` que deba aparecer posteriormente en el artifact OSS-3A.

## 7. Missing values

V1 usa una política deliberadamente restrictiva:

```text
missing_value_policy = FORBID
```

Sólo se aceptan números finitos. Se rechazan:

- `null`/`None`;
- NaN;
- +Inf;
- -Inf;
- strings numéricos;
- booleanos.

Si un modelo futuro necesita imputación, la imputación deberá convertirse en una transformación explícita y hash-bound en una versión posterior; no se permitirá semántica implícita de missing values.

## 8. Canonicalización

- JSON UTF-8 solamente;
- claves duplicadas rechazadas;
- schema top-level/manifest/feature/row exacto;
- una única representación byte-canónica;
- timestamps UTC canónicos `+00:00`;
- filas ordenadas por `(as_of, symbol)`;
- identidad `(as_of, symbol)` única;
- máximo 512 features;
- máximo 2,000,000 filas;
- máximo 50 MB por artifact V1;
- hashes recalculados durante ingestion.

## 9. Qué no prueba `available_at`

El contrato detecta contradicciones internas y hace auditable la afirmación temporal del productor, pero **no puede por sí solo demostrar que el productor calculó correctamente `available_at`**.

Por tanto, el siguiente adapter desde datos de mercado debe:

1. usar sólo datos cerrados/observables;
2. derivar disponibilidad desde el contrato real de cada fuente;
3. conservar provenance de source dataset/universe;
4. ser probado contra lookahead explícito;
5. no reinterpretar `started_at` de una barra como disponibilidad salvo que el timeframe/fuente demuestre esa equivalencia.

## 10. Authority boundary

OSS-3B fija:

```text
Qlib runtime: NONE
network: NONE
process execution: NONE
broker: NONE
OMS: NONE
Safety writer: NONE
OrderIntent: NONE
PAPER execution: FALSE
capital authority: NONE
LIVE: BLOCKED
FINAL_HOLDOUT export: FALSE
labels included: FALSE
```

El boundary estático prohíbe imports de Qlib/MLflow/Redis, networking, subprocess, serialización ejecutable y superficies operativas AUTO-TRADE.

## 11. Siguientes waves

### OSS-3C — Supervised Label Artifact

Contrato separado para labels con:

- `label_as_of`;
- `horizon_end`;
- `available_at`;
- formula hash;
- source provenance;
- partition identity;
- prohibición explícita de usar un label antes de `available_at`.

### OSS-3D — Isolated Qlib Lab Runner

El runner externo deberá aceptar sólo artifacts OSS-3B/3C verificados y producir OSS-3A. No tendrá acceso a broker, OMS, Safety ni credenciales.

### OSS-3E — ML Ranking Tournament Adapter

Las predicciones OSS-3A entrarán a un backtest research-only y deberán pasar nuevamente costos, multiple-testing, DEVELOPMENT/HOLDOUT, execution sensitivity y Shadow/Forward antes de cualquier ruta de promoción.

## 12. Estado operacional

OSS-3B no es una estrategia ni una autorización de trading. Es una frontera de integridad de datos para investigación reproducible.
