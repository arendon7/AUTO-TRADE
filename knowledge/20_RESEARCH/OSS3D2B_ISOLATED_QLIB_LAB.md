# OSS-3D2B — Isolated Qlib runtime canary

Estado objetivo: **RESEARCH ONLY / EXTERNAL LAB / NO EXECUTION AUTHORITY**.

OSS-3D2B introduce por primera vez un runtime Qlib real en AUTO-TRADE, pero lo hace fuera de `src/autotrade`, fuera del `pyproject.toml` del core y detrás de los contratos ya certificados OSS-3A/B/C/D1/D2A.

## 1. Objetivo

Demostrar que un modelo Qlib real puede:

1. consumir features + labels TRAIN canónicos;
2. entrenarse sobre el bundle OSS-3D1 exacto;
3. inferir únicamente sobre features DEVELOPMENT preregistradas por OSS-3D2A;
4. devolver un `QlibPredictionArtifact` OSS-3A real;
5. producir un `DevelopmentPredictionReceipt` D2A;
6. hacerlo sin red, providers Qlib, DEVELOPMENT labels, broker, OMS, Safety o autoridad de capital.

Este milestone prueba integración técnica y lineage. **No prueba alpha ni rentabilidad.**

## 2. Aislamiento físico

El lab reside en:

```text
labs/oss3_qlib/
```

El core mantiene:

```text
src/autotrade/...
pyproject.toml
```

sin dependencia Qlib.

La dependencia externa está pinneada únicamente en:

```text
labs/oss3_qlib/requirements.txt
pyqlib==0.9.7
```

El job Dedicated instala primero AUTO-TRADE sin Qlib, prueba el boundary y sólo después instala el runtime externo dentro de ese job.

## 3. Por qué Qlib 0.9.7

OSS-3D2B fija exactamente `pyqlib==0.9.7` para evitar floating versions. La release dispone de soporte/wheels para Python 3.12 y expone el API `LinearModel.fit(dataset)` / `predict(dataset)` utilizado por este canario.

El uso de una versión pinneada no implica que todas sus dependencias transitivas sean parte del core. Son dependencias del entorno de laboratorio únicamente.

## 4. Primer modelo: ridge determinista

Modelo canónico:

```text
family: qlib_linear_ridge_v1
implementation: qlib.contrib.model.linear.LinearModel
estimator: ridge
alpha: 1.0
fit_intercept: true
include_valid: false
prediction_segment: test
```

Decisiones deliberadas:

- un único modelo;
- una única configuración;
- sin random search;
- sin grid search;
- sin Bayesian optimization;
- sin tuning posterior a resultados;
- sin selección de modelo basada en DEVELOPMENT;
- sin valid segment adaptativo.

El propósito inicial es validar el pipeline real Qlib con mínima superficie científica antes de incorporar modelos más expresivos.

## 5. Model config hash

La configuración anterior se serializa como JSON canónico y se liga por SHA-256.

Un request D2A cuyo `model_config_hash` no coincida exactamente falla antes de importar Qlib.

## 6. Semantic runner code hash

D2B calcula SHA-256 sobre los bytes y nombres de:

```text
model_contract.py
dataset_adapter.py
network_guard.py
runner.py
requirements.txt
```

Ese hash debe coincidir con `expected_runner_code_hash` del request D2A.

Consecuencia: modificar lógica, adapter, network guard o dependencia invalida automáticamente una preregistración anterior.

## 7. Inputs del runner

El CLI acepta exactamente:

```text
--request
--training-bundle
--train-features
--train-labels
--development-features
--prediction-output
--receipt-output (opcional)
```

No existe argumento `--development-labels`.

Antes de Qlib el runner:

1. lee todos los artifacts mediante sus readers canónicos;
2. reconstruye OSS-3D1 desde TRAIN features + TRAIN labels;
3. exige que el hash reconstruido sea el bundle recibido;
4. verifica hashes exactos de TRAIN feature/label;
5. ejecuta `request.verify_inputs(bundle, development_features)`;
6. verifica modelo/config/Qlib version/semantic runner hash;
7. construye el adapter in-memory.

## 8. Dataset adapter mínimo

`QlibArtifactDatasetAdapter` implementa sólo:

```text
prepare("train", ["feature", "label"], data_key="learn")
prepare("test", "feature", data_key="infer")
```

Cualquier otro segment, `col_set` o `data_key` falla cerrado.

TRAIN se materializa con:

```text
columns level 0 = feature | label
index = datetime | instrument
```

DEVELOPMENT contiene exclusivamente features.

No hay:

- Qlib data provider;
- calendario externo;
- `.bin` market-data store;
- descarga de datos;
- `qlib.init()`;
- `DatasetH` con loaders externos;
- qrun.

## 9. Network denial

Antes del primer `import qlib`, el runner entra en `deny_network()`.

Durante ese scope se bloquean:

```text
socket.socket.connect
socket.socket.connect_ex
socket.create_connection
socket.getaddrinfo
```

Una tentativa de conexión/DNS falla con `QlibLabNetworkDenied`.

La protección se restaura al salir del contexto para no contaminar el proceso llamador.

## 10. Credenciales

D2B se niega a ejecutar si el entorno contiene valores no vacíos bajo prefijos de brokers/exchanges configurados:

```text
APCA_
ALPACA_
IBKR_
BINANCE_
BROKER_
```

El laboratorio no necesita secretos de trading. Su presencia es tratada como una violación de aislamiento.

## 11. Ejecución Qlib

Dentro del network-denied scope:

```text
import qlib
from qlib.contrib.model.linear import LinearModel
```

Se verifica:

```text
qlib.__version__ == 0.9.7
```

Luego:

```text
model = LinearModel(ridge, alpha=1.0, fit_intercept=True, include_valid=False)
model.fit(dataset)
scores = model.predict(dataset, segment="test")
```

No se llama `qlib.init()` y no se usa workflow/recorder/qrun.

## 12. Prediction integrity

El índice devuelto por Qlib debe ser exactamente el índice DEVELOPMENT original:

```text
(datetime, instrument)
```

Se rechazan:

- scores no finitos;
- índice sin timezone;
- filas faltantes;
- filas extra;
- timestamps desplazados;
- símbolos sustituidos;
- orden distinto del keyset canónico.

Sólo después se construye OSS-3A.

## 13. Provenance OSS-3A real

El artifact emitido contiene:

- `producer_id = microsoft/qlib` a través del contrato OSS-3A;
- versión Qlib real comprobada;
- modelo/config exactos;
- bundle TRAIN como `training_dataset_hash`;
- feature schema;
- semantic runner code hash;
- train/inference windows;
- scores reales del modelo Qlib.

A diferencia del dry-run D2A, aquí sí existe un runtime Qlib real y por tanto sí corresponde producir OSS-3A.

## 14. Rebinding final

Después de construir OSS-3A, el runner ejecuta nuevamente:

```text
request.bind_prediction(...)
```

Por tanto la predicción debe superar dos fronteras:

1. integridad interna OSS-3A;
2. correspondencia exacta OSS-3D2A.

Sólo entonces se escribe prediction JSON y, opcionalmente, el receipt.

## 15. Authority boundary

D2B no puede:

- importar broker adapters;
- construir `OrderIntent`;
- acceder a OMS;
- acceder a Safety;
- hacer sizing;
- ejecutar PAPER;
- modificar capital;
- habilitar LIVE.

D2B produce investigación, no órdenes.

## 16. CI

Dedicated D2B debe probar:

1. root core sin Qlib;
2. static boundary antes de instalar runtime externo;
3. instalación exacta `pyqlib==0.9.7`;
4. versión runtime exacta;
5. integración real Ridge fit/predict sobre artifacts sintéticos canónicos;
6. output OSS-3A válido;
7. receipt D2A válido;
8. determinismo para inputs idénticos;
9. network denial;
10. credential denial;
11. no DEVELOPMENT-label CLI;
12. D2A/D1/C/B/A regressions;
13. Research Authority;
14. W83;
15. OSS-2H FINAL_HOLDOUT.

Core Safety debe continuar corriendo sin instalar Qlib, porque `pytest` del core descubre sólo `tests/`; las pruebas externas del lab viven bajo `labs/oss3_qlib/tests/`.

## 17. Limitación de reproducibilidad del entorno

V1 fija la versión primaria `pyqlib==0.9.7`, el código del runner y la configuración del modelo. Las dependencias transitivas resueltas por pip todavía no están convertidas en un lock multiplataforma completo.

Esto es aceptable para el **integration canary**, pero antes de considerar resultados ML comparables longitudinalmente deberá existir una etapa posterior de environment attestation/lock que capture versiones transitivas efectivas y plataforma.

## 18. Próximo paso después de D2B

Si D2B queda certificado, la siguiente frontera recomendada es **OSS-3D2C DEVELOPMENT evaluation**:

- toma OSS-3D2A receipt + OSS-3A predictions;
- abre DEVELOPMENT labels sólo en esa etapa posterior;
- calcula IC/rank-IC, error, turnover proxy y estabilidad por régimen;
- mantiene FINAL_HOLDOUT completamente separado;
- registra una campaña finita de modelos/configs antes de comparar resultados;
- no concede PAPER/LIVE.

**D2B successful ≠ profitable model. D2B successful = real Qlib runtime correctly isolated and reproducibly bound to research artifacts.**
