# OSS-3D3D — Dual Holdout Acquisition Preregistration

## Estado

D3D es el primer hijo del head D3C certificado:

`2f168513482e188ed381550ba4ab3780011e600d`

No adquiere datos de holdout. No ejecuta D2J, D2M, D2K o D2N. No emite ni consume permisos. Su única función es congelar **antes de cualquier descarga** la geometría real de dos holdouts separados.

## Problema que resuelve

La campaña real D2U termina exactamente en:

`2026-01-01T00:00:00+00:00`

y contiene únicamente WARMUP/TRAIN/DEVELOPMENT. Por contrato:

`final_holdout_descriptors_included = false`

Por tanto, el repositorio todavía no dispone de un FINAL_HOLDOUT real adquirido que pueda convertirse honestamente en un commitment D2J. Tampoco debe elegirse el período económico después de conocer el resultado predictivo.

D3D elimina esa discrecionalidad futura congelando simultáneamente los dos períodos.

## Reserva temporal ex ante

### Predictive FINAL_HOLDOUT

Q1 2026:

- inicio: `2026-01-01T00:00:00+00:00`;
- fin: `2026-04-01T00:00:00+00:00`;
- meses: enero, febrero y marzo de 2026;
- 2.160 barras horarias esperadas por símbolo;
- 2.159 cross-sections predictivas potenciales después del horizonte de una barra;
- 6.477 observaciones potenciales para tres símbolos.

Este período queda reservado exclusivamente para D2J/D2K.

### ECONOMIC_HOLDOUT

Q2 2026:

- inicio: `2026-04-01T00:00:00+00:00`;
- fin: `2026-07-01T00:00:00+00:00`;
- meses: abril, mayo y junio de 2026;
- 2.184 barras horarias esperadas por símbolo.

Este período queda reservado exclusivamente para D2M/D2N.

### Período posterior

D3D no reserva datos desde:

`2026-07-01T00:00:00+00:00`

Esto deja evidencia temporal posterior fuera de ambos holdouts para futuras extensiones, sin incorporarla a esta decisión.

## Universo y proveedor congelados

D3D hereda exactamente la geometría científica de D2U:

- proveedor: Binance Spot Public Data Archive;
- instrumentos: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`;
- quote: USDT;
- intervalo: `1h`;
- granularidad: monthly;
- metadata de instrumento: la misma identidad research-only utilizada por D2U.

Se generan exactamente 18 `BinanceSpotArchiveDescriptor`:

- 9 para Q1: 3 meses × 3 símbolos;
- 9 para Q2: 3 meses × 3 símbolos.

Los URLs y paths del descriptor son únicamente identidad estructural. D3D no ejecuta ningún cliente HTTP ni abre archivos ZIP/CHECKSUM.

## Lineage D3C

El plan queda ligado a:

- D3C certified head: `2f168513482e188ed381550ba4ab3780011e600d`;
- D3C artifact hash: `1d271c31f506be21d2a99c857a1007cc5676cfcb5ab83fb6a524171a38dbaf43`;
- stable scientific outcome: `5b4261b186e30cbdc30ec2c3025305fbb96650247a066cdf470ce53e80f86ea7`;
- certified D3A baseline: `ce703916e83161cc71e73e56e4a9ffab5c0989bf630cfbae260a90a5846ca9cc`;
- exact canonical D2U plan fingerprint resolved from source.

Así, la selección temporal posterior no puede desacoplarse del DEVELOPMENT winner ya certificado.

## Política de separación

D3D congela:

`PREDICTIVE_Q1_AND_ECONOMIC_Q2_DISJOINT_NO_REUSE_V1`

Invariantes:

1. Q1 comienza exactamente al terminar DEVELOPMENT.
2. Q2 comienza exactamente al terminar Q1.
3. No existe solapamiento temporal.
4. Ningún descriptor puede pertenecer a ambas familias.
5. El uso futuro de Q1 es predictivo solamente.
6. El uso futuro de Q2 es económico solamente.
7. El período económico se elige antes de cualquier observación del predictivo.

## Adecuación muestral

D2J exige al menos:

- 30 cross-sections;
- 90 observaciones totales;
- 3 observaciones por cross-section.

La geometría Q1 preregistrada ofrece teóricamente 2.159 cross-sections y 6.477 observaciones con los tres activos, antes de cualquier inspección de valores. D3D sólo congela esta capacidad geométrica; no afirma que los datos adquiridos sean válidos hasta que una etapa posterior verifique integridad, continuidad y soporte real.

D2M exige al menos 60 barras y tres símbolos para el holdout económico; Q2 reserva 2.184 barras por símbolo, ampliamente por encima del mínimo geométrico.

## Registro durable

`SQLiteDualHoldoutAcquisitionPlanRegistry` almacena:

- `plan_id` único;
- fingerprint SHA-256 único;
- JSON canónico exacto;
- timestamp de preregistración.

El registro incluye triggers `BEFORE UPDATE` y `BEFORE DELETE`, por lo que es append-only. La misma identidad exacta es idempotente; un plan distinto no puede reutilizar el mismo `plan_id`.

El workflow genera dos artifacts de evidencia:

1. JSON canónico del plan;
2. SQLite durable con la preregistración.

La futura etapa de adquisición deberá verificar ese SQLite en `mode=ro`/`query_only` antes de efectuar cualquier solicitud de red.

## Lo que D3D prohíbe

D3D no puede:

- descargar archivos Binance;
- leer bytes de mercado;
- abrir ZIP/CHECKSUM;
- construir MarketDataset de holdout;
- materializar FactorMatrixArtifact;
- materializar SupervisedLabelArtifact;
- producir predicciones;
- calcular Rank IC, p-values, PnL, Sharpe o drawdown;
- crear el protected commitment D2J todavía;
- crear el economic commitment D2M todavía;
- iniciar D2K/D2N;
- emitir/consumir HoldoutPermit;
- retunear o reseleccionar modelos;
- autorizar broker/OMS/Safety/OrderIntent;
- otorgar PAPER, capital o LIVE.

Estado obligatorio:

```text
network_acquisition_performed = false
final_holdout_observed         = false
economic_holdout_observed      = false
holdout_permit_issued          = false
holdout_permit_consumed        = false
execution_authorized           = false
paper_execution_authorized     = false
capital_authority              = NONE
live_trading                   = BLOCKED
```

## Siguiente frontera

Después de certificar D3D, el siguiente bloque correcto será un adaptador de **acquisition-only** que:

1. lea el registro D3D en modo read-only;
2. descargue exclusivamente los 18 archivos preregistrados y sus CHECKSUM;
3. verifique cada archivo con D2T;
4. mantenga separadas Q1 y Q2;
5. produzca evidencia raw/provenance durable;
6. todavía no exponga valores a D2J/D2M ni ejecute D2K/D2N.

Sólo después de verificar esa adquisición podrán construirse commitments value-opaque reales para D2J y D2M.

## Certificación

D3D queda certificado únicamente si el mismo head pasa:

- Dedicated D3D;
- Knowledge Contract;
- Core Safety Tests.

El Dedicated debe verificar primero el artifact D3C certificado exacto y después ejecutar únicamente la preregistración offline. Ningún paso del Dedicated descarga datos Q1/Q2.