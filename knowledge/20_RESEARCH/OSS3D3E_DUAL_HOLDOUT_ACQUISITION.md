# OSS-3D3E — Dual Holdout Raw Acquisition

## Estado

D3E es hijo directo del head D3D certificado:

`7911c962733811bb7a6713c8e06587c09df1bcdb`

D3D congeló, antes de cualquier descarga, dos familias mutuamente excluyentes:

- **PREDICTIVE_FINAL_HOLDOUT:** Q1 2026;
- **ECONOMIC_HOLDOUT:** Q2 2026.

Su plan canónico tiene fingerprint:

`d6dc987cc2053a6617796fdb89df9467c0895a090c5745c91b157edc5d7a4af2`

D3E no elige períodos, símbolos, frecuencia ni proveedor. Su única función es adquirir y sellar la evidencia raw correspondiente a esos 18 descriptores ya preregistrados.

## Geometría exacta

Proveedor y universo:

- Binance Spot public archives (`data.binance.vision`);
- símbolos: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`;
- intervalo: `1h`;
- granularidad: monthly;
- credenciales: ninguna;
- endpoints de trading: prohibidos.

Familia predictiva:

- enero, febrero y marzo de 2026;
- 9 ZIP + 9 CHECKSUM;
- reservada exclusivamente para el futuro D2J/D2K.

Familia económica:

- abril, mayo y junio de 2026;
- 9 ZIP + 9 CHECKSUM;
- reservada exclusivamente para el futuro D2M/D2N.

Los conjuntos de descriptor fingerprints deben permanecer disjuntos.

## Gate previo a red

Antes de cada intento de adquisición D3E abre el SQLite D3D con:

`mode=ro` + `PRAGMA query_only = ON`

y exige simultáneamente:

1. tabla canónica D3D presente;
2. `plan_id` canónico;
3. fingerprint exacto `d6dc987...`;
4. serialización JSON canónica exacta;
5. descriptor incluido exactamente en una de las dos familias.

Si cualquiera falla, no puede realizarse ningún GET.

## Contrato de red

Cada descriptor nuevo tiene exactamente un intento compuesto por tres requests:

1. `CHECKSUM A`;
2. `ZIP`;
3. `CHECKSUM B`.

Reglas:

- método exclusivamente `GET`;
- HTTPS;
- host exacto `data.binance.vision`;
- paths limitados a los 18 descriptores D3D;
- timeout máximo 30 s;
- redirects prohibidos;
- HTTP 200 obligatorio;
- `CHECKSUM A == CHECKSUM B` byte a byte;
- cero retries dentro del intento;
- una campaña completamente fresca tiene presupuesto exacto de **54 GETs**.

## D2T como boundary de integridad

El ZIP y CHECKSUM adquiridos se entregan al normalizador offline D2T existente.

D2T comprueba, entre otros:

- checksum SHA-256 del proveedor antes de abrir ZIP;
- un único CSV esperado;
- timestamp-unit policy;
- cobertura mensual completa sin gaps/duplicates;
- geometría horaria exacta;
- identidad canónica del dataset normalizado.

D3E persiste:

- ZIP original;
- CHECKSUM original;
- `d2t-snapshot.json`;
- `d3e-acquisition-receipt.json`.

## Raw acquired no significa holdout evaluado

D3E hace explícita esta separación:

- `raw_market_bytes_acquired = true` después de adquisición;
- `d2t_integrity_normalization_performed = true`;
- `scientific_holdout_evaluation_observed = false`;
- `feature_values_materialized = false`;
- `label_values_materialized = false`;
- `prediction_values_materialized = false`;
- `metrics_computed = false`.

D2T necesariamente lee precios para verificar integridad estructural. Esa lectura mecánica no autoriza análisis científico del holdout ni acceso de D2J/D2K/D2M/D2N a esos valores.

## Restart safety

La evidencia real debe vivir fuera del repositorio Git.

Jerarquía:

`<root>/<PURPOSE>/<SYMBOL>/<YYYY-MM>/`

Una adquisición nueva se escribe primero bajo:

`<root>/.oss3d3e-staging/...`

Sólo después de verificar completamente ZIP, CHECKSUM, receipt y snapshot D2T la carpeta se renombra atómicamente al destino final.

Un staging residual provoca fail-closed y requiere resolución del operador. Nunca se reutiliza ni elimina silenciosamente.

Un descriptor ya sellado sólo se reutiliza después de releer y reverificar todos sus archivos raw + receipt + D2T contra el descriptor preregistrado.

Si existe una carpeta final completa pero falta el sello SQLite —por ejemplo, crash después del rename y antes del INSERT— D3E puede reconciliarla únicamente después de la misma reverificación completa.

## Ledger append-only

`oss3d3e-dual-holdout-acquisition.sqlite3` contiene:

- descriptor seals;
- complete campaign seal.

Ambas tablas tienen triggers `NO UPDATE` y `NO DELETE`.

El campaign seal sólo puede existir cuando:

- existen 18 descriptor seals únicos;
- 9 son predictivos;
- 9 son económicos;
- las familias son disjuntas;
- todo el material fue completamente reverificado.

D3E **no concatena los meses en universos de evaluación** y no produce una matriz feature-ready.

## Authority boundary

D3E no crea:

- features;
- labels;
- predicciones;
- métricas;
- Qlib runtime;
- D2J protocol/commitment;
- D2L strategy binding;
- D2M economic protocol/commitment;
- D2K start/evaluation;
- D2N start/evaluation;
- holdout permit;
- OrderIntent;
- broker/OMS execution.

Siempre:

- `scientific_holdout_evaluation_observed = false`;
- `d2j_or_d2m_commitment_created = false`;
- `holdout_permit_issued = false`;
- `holdout_permit_consumed = false`;
- `execution_authorized = false`;
- `paper_execution_authorized = false`;
- `capital_authority = NONE`;
- `live_trading = BLOCKED`.

## Salida esperada del Dedicated real

Con el artifact D3D certificado como admission input, una campaña nueva debe demostrar:

- descriptor count = 18;
- acquired from network = 18;
- sealed predictive = 9;
- sealed economic = 9;
- missing = 0;
- complete = true;
- un campaign seal durable;
- presupuesto fresco = 54 GETs;
- Qlib ausente antes y después;
- research authority PASS.

El artifact D3E debe preservar el evidence root completo junto con el SQLite para que un hijo posterior pueda derivar commitments estructurales sin volver a descargar ni escoger períodos.
