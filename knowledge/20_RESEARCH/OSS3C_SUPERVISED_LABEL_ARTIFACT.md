# OSS-3C — Supervised Label Artifact

Fecha: 2026-09-05  
Rama: `research/oss3c-supervised-label-artifact`  
Base: OSS-3B certificado (`8761a8f44b0ee0abd1a6265a4375f5efa753d8cc`)  
Estado: RESEARCH ONLY / DRAFT

## 1. Objetivo

Crear un contrato canónico **separado** para targets supervisados que puedan acompañar a OSS-3B dentro de un laboratorio ML/Qlib aislado.

```text
OSS-3B point-in-time features
       +
OSS-3C future labels
       |
       v
isolated ML / Qlib lab
       |
       v
OSS-3A canonical predictions
```

La separación features/labels es una frontera científica, no sólo una decisión de formato.

## 2. Causalidad temporal

Cada fila OSS-3C contiene:

```text
label_as_of
horizon_end
available_at
symbol
value
```

V1 exige:

```text
partition_start <= label_as_of < horizon_end <= available_at < partition_end
```

Esto evita dos clases de leakage:

1. usar un target antes de que cierre su horizonte futuro;
2. permitir que un label TRAIN dependa de información que ya pertenece temporalmente a DEVELOPMENT o a una frontera posterior.

`available_at` puede ser igual o posterior a `horizon_end`, porque una fuente puede publicar o consolidar el dato con retraso. Nunca puede ser anterior.

## 3. TRAIN / DEVELOPMENT solamente

`LabelPartition` sólo admite:

- TRAIN;
- DEVELOPMENT.

FINAL_HOLDOUT no forma parte del enum V1 y se rechaza estructuralmente.

La evaluación futura de un modelo congelado sobre FINAL_HOLDOUT deberá usar una frontera independiente one-shot/permit-aware compatible con OSS-2G/OSS-2H; OSS-3C genérico no podrá convertirse en bypass del holdout.

## 4. Campaign + frozen split binding

Cada artifact liga:

- `campaign_id`;
- `research_split_hash`;
- partition y ventana;
- source dataset/universe;
- producer code;
- label definition;
- row payload.

Cambiar campaña o split cambia el artifact hash. El split debe haber sido preregistrado/congelado upstream; OSS-3C verifica identidad, no autoriza un split nuevo.

## 5. Definición del label

V1 transporta exactamente una definición de label por artifact:

- `name`;
- `dtype=float64`;
- `role=LABEL`;
- `formula_hash`;
- `source_id`;
- `source_hash`.

El contrato **no ejecuta fórmulas dinámicas** y no fija todavía una fórmula universal de forward return. La semántica matemática se versiona en el productor y queda ligada por hash/provenance.

Esto permite comparar posteriormente, por ejemplo, distintas definiciones preregistradas de retorno o ranking sin introducir `eval`, callables ni módulos arbitrarios.

## 6. Canonicalización e integridad

- JSON UTF-8;
- exact top/manifest/label/row schemas;
- duplicate object keys denied;
- una única representación byte-canónica;
- timestamps UTC canónicos;
- row identity `(label_as_of, symbol)` única;
- canonical row ordering;
- finite float64-compatible numeric values;
- null/NaN/Inf/string/bool denied;
- máximo 2,000,000 rows y 50 MB en V1;
- row payload hash;
- label definition hash;
- manifest fingerprint;
- artifact SHA-256 completo.

## 7. Pairing futuro con OSS-3B

OSS-3D deberá aceptar features y labels sólo si comparten, como mínimo:

- `campaign_id`;
- `research_split_hash`;
- partition;
- universe identity compatible;
- observaciones emparejables por `(as_of, symbol)` según una política explícita.

No se realizará un join silencioso por orden de filas.

La identidad del OSS-3B artifact y la del OSS-3C artifact deberán conservarse en el lineage del modelo entrenado.

## 8. Authority boundary

OSS-3C fija permanentemente:

```text
Qlib runtime: NONE
MLflow/Redis: NONE
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
```

Una etiqueta es evidencia de investigación; nunca autoridad de trading.

## 9. Relación con timestamps existentes

AUTO-TRADE ya distingue inicio y fin de una barra (`started_at`, `ended_at`) y otros subsistemas modelan `occurred_at` y `available_at` separadamente. OSS-3C conserva esa filosofía causal sin modificar el runtime canónico de estrategia.

El productor futuro deberá derivar `horizon_end` y `available_at` desde contratos de datos reales, no asumir que un timestamp de apertura es equivalente a disponibilidad.

## 10. Qué OSS-3C no demuestra

OSS-3C demuestra integridad temporal/estructural del target declarado. No demuestra:

- que la fórmula produzca alpha;
- que el modelo generalice;
- que el target sea económicamente útil;
- ausencia de selection bias fuera del contrato;
- rentabilidad;
- aptitud para PAPER/LIVE.

Todo modelo posterior sigue sujeto a DEVELOPMENT, multiple-testing, walk-forward, robustness, costs, FINAL_HOLDOUT, execution sensitivity y Shadow/Forward.

## 11. Siguiente wave

### OSS-3D — Isolated Qlib Lab Runner

Deberá:

1. ejecutarse fuera del runtime operativo;
2. verificar artifacts OSS-3B/OSS-3C antes de entrenar;
3. exigir campaign/split/partition compatibility;
4. registrar configuración/model/data/code hashes;
5. producir únicamente OSS-3A canonical predictions;
6. no recibir credenciales ni acceso a broker/OMS/Safety;
7. no otorgar ninguna autoridad operativa.

## 12. Estado

OSS-3C permanecerá DRAFT hasta pasar Dedicated + Core Safety sobre el merge ref efectivo. Cualquier certificación es **estructural y research-only**, nunca evidencia de alpha o rentabilidad.