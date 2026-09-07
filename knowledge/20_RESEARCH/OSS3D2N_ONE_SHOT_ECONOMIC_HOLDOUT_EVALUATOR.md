# OSS-3D2N — One-shot cost-aware economic holdout evaluator

## Estado y propósito

`OSS-3D2N` es la frontera económica one-shot posterior a D2M. Su función es convertir una estrategia predictiva ya congelada en D2L y una política económica ya preregistrada en D2M en una única evaluación OOS cost-aware sobre un economic holdout posterior al predictive FINAL_HOLDOUT.

La secuencia científica canónica es:

```text
D2I DEVELOPMENT winner
  -> D2J protected predictive FINAL_HOLDOUT protocol
    -> D2L predictor-to-strategy preregistration
      -> D2M preregistered economic protocol
        -> D2K one-shot predictive FINAL_HOLDOUT
          -> D2N one-shot economic holdout
```

D2N sólo puede comenzar si existe un D2K terminal `PASS` durable para el mismo protocolo predictivo. Un D2K ausente, `FAIL`, conflictivo o no durable bloquea D2N antes de exponer el economic holdout.

D2N produce evidencia económica research. Incluso un `PASS` no autoriza promoción, PAPER, capital ni LIVE.

## Separación temporal obligatoria

El economic holdout debe comenzar estrictamente después del final del predictive FINAL_HOLDOUT D2J:

```text
predictive_holdout.partition_end < economic_holdout.partition_start
```

La política se congela como:

```text
ECONOMIC_HOLDOUT_STRICTLY_AFTER_PREDICTIVE_HOLDOUT_V1
```

Con esto, el economic holdout no es una segunda lectura de la misma ventana que ya influyó en la validación predictiva.

## Inputs permitidos

`SQLiteOSS3EconomicHoldoutEvaluationRegistry.evaluate(...)` recibe únicamente:

- `evaluation_id`;
- D2M `economic_protocol` durable;
- D2L preregistration receipt;
- D2J protocol receipt;
- `ProtectedEconomicHoldout`;
- timestamp `now`.

No expone como parámetros configurables:

- initial cash;
- costes;
- thresholds;
- policy variants;
- retuning;
- fallback candidate;
- broker;
- `OrderIntent`.

Eso evita introducir hiperparámetros post-hoc al evaluar el holdout.

## Economic holdout material

`EconomicHoldoutMaterial` contiene:

- `AlignedMarketUniverse` exacto;
- `QlibPredictionArtifact` del modelo congelado;
- `commitment_id`;
- versión canónica de material.

Su `commitment` debe reconstruir exactamente el `EconomicHoldoutCommitment` preregistrado en D2M:

- mismo universe hash;
- mismo dataset-set hash;
- mismos símbolos;
- misma quote currency;
- mismo timeframe;
- misma ventana;
- mismo bar count;
- mismo symbol count.

Los predictions deben cubrir todas las barras no terminales y el universo completo de forma determinista.

## Checkout protegido

`ProtectedEconomicHoldout` no libera el material sólo porque exista un objeto start con campos correctos.

Antes de `_checkout()`:

1. D2N valida lineage D2M/D2L/D2J;
2. valida separación temporal;
3. rechaza credenciales de broker;
4. exige D2K terminal PASS durable;
5. registra durablemente el D2N start;
6. `_checkout()` reabre el SQLite y demuestra que ese start exacto existe.

Sólo entonces el wrapper expone market values.

Un fallo posterior al start es terminal y no devuelve un segundo intento.

## NAV fijo

D2N v1 usa:

```text
INITIAL_CASH = 100000 quote units
INITIAL_CASH_POLICY = FIXED_100000_QUOTE_UNITS_V1
```

El NAV no es parámetro de `evaluate()`. Esto evita que quantity-step, min-notional o cash constraints se conviertan en un grado de libertad posterior al holdout.

## Portfolio construction

D2N no define una estrategia nueva. Usa exclusivamente la proyección determinista D2L:

```text
QlibPredictionArtifact
  -> project_prediction_artifact(D2L binding)
    -> PredictiveTargetAllocation
```

Por tanto quedan heredados y congelados:

- descending score rank;
- deterministic symbol tie-break;
- top 25%;
- long-only;
- equal-weight;
- max 25% por activo;
- reserve cash mínimo;
- sin sign-flip;
- sin leverage;
- sin shorting;
- señal en close t;
- ejecución exactamente en open t+1.

## Ejecución simulada cost-aware

D2N materializa los targets con la política D2M:

```text
fee_bps         = 10
half_spread_bps = 5
slippage_bps    = 5
total           = 20 bps
```

Y además:

```text
max volume participation = 10%
min trade notional       = 10 quote units
quantity rounding        = instrument quantity-step floor
margin                    = forbidden
shorting                  = forbidden
leverage                  = forbidden
cash                      = may not become negative
```

Las ventas se procesan antes que las compras para liberar cash y reducir riesgo antes de aumentar exposición.

Cada fill conserva:

- allocation fingerprint;
- symbol;
- side;
- quantity;
- reference/open price;
- execution price cost-adjusted;
- fee;
- signal index;
- execution index;
- timestamp;
- volume participation;
- realized PnL.

La integridad exige:

```text
execution_index == signal_index + 1
```

## Contabilidad de PnL y profit factor

El motor multi-activo OSS-2 existente no conserva PnL realizado por fill y por tanto no puede suministrar el gate de profit factor D2M sin cambiar su semántica.

D2N define explícitamente:

```text
PROFIT_FACTOR_POLICY = REALIZED_SELL_PNL_AVERAGE_COST_V1
```

Reglas:

- BUY incrementa posición y average cost;
- BUY no realiza PnL;
- SELL reduce posición existente long-only;
- realized PnL se calcula contra average cost;
- fees forman parte de la economía de ejecución;
- profit factor usa ganancias/pérdidas realizadas en closing sells.

No se fabrica un profit factor a partir de retornos de equity y no se confunde con el PF de un motor single-asset distinto.

## Sin liquidación terminal artificial

D2N congela:

```text
MARK_TO_MARKET_ONLY_NO_FORCED_FINAL_TRADE_V1
```

Al final del economic holdout no se crea una venta ficticia sólo para cerrar posiciones y mejorar/empeorar el PF.

El NAV final sí se marca a mercado con los closes observados, mientras el profit factor permanece basado exclusivamente en ventas realmente generadas por la estrategia.

## Métricas

D2N calcula al menos:

- net return;
- annualization factor derivado del timeframe;
- Sharpe;
- profit factor realizado;
- max drawdown;
- turnover;
- total fees;
- max volume participation;
- average/max gross exposure ratio;
- average/max target tracking error;
- fills;
- rebalances;
- realized closing fills;
- realized wins/losses.

Annualization obedece la política D2M:

```text
SECONDS_PER_365_DAY_YEAR_DIV_TIMEFRAME_V1
```

## Gates D2M aplicados mecánicamente

D2N no decide thresholds. Lee los congelados en D2M y emite los gates canónicos:

```text
ECONOMIC_NET_RETURN_POSITIVE
ECONOMIC_SHARPE_MIN
ECONOMIC_PROFIT_FACTOR_MIN
ECONOMIC_MAX_DRAWDOWN_MAX
ECONOMIC_FILLS_MIN
ECONOMIC_REBALANCES_MIN
```

V1 exige simultáneamente:

```text
net_return       > 0
Sharpe           >= 1.5
profit_factor    >= 1.3
max_drawdown     <= 0.15
fills            >= 10
rebalances       >= 10
```

Todos deben pasar para `PASS`.

## Terminalidad one-shot

D2N mantiene dos tablas append-only:

```text
oss3_economic_holdout_evaluation_starts
oss3_economic_holdout_evaluations
```

Ambas bloquean `UPDATE` y `DELETE` mediante triggers.

La secuencia es:

```text
preflight lineage
  -> temporal separation
    -> broker credential rejection
      -> durable D2K PASS
        -> BEGIN IMMEDIATE
          -> durable D2N start
            -> protected checkout
              -> D2L projection
                -> simulation
                  -> metric/gates
                    -> terminal PASS/FAIL
```

Un metric FAIL es terminal.

Un exception/fallo estructural después del durable start también se materializa como terminal FAIL y no habilita retry.

## Broker and authority boundary

Antes del start se rechazan variables de entorno de credenciales para:

- Alpaca/APCA;
- IBKR;
- Binance;
- Coinbase;
- Kraken;
- Bybit;
- OKX;
- Bitget;
- KuCoin;
- prefijo genérico `BROKER_`.

El módulo no puede importar broker, OMS, Safety ni usar `OrderIntent`.

Después de PASS o FAIL:

```text
retuning_allowed = false
reselection_allowed = false
second_attempt_allowed = false
profitability_claim_authorized = false
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

Un PASS significa únicamente:

> el predictor/portfolio congelado superó una única evaluación económica OOS bajo las hipótesis de costes research preregistradas.

No significa broker-truth, rentabilidad futura ni autorización de capital.

## Relación con W81/W82

D2N usa los 20 bps D2M porque fueron congelados ex ante.

Esto no convierte esos costes en verdad de broker. W81/W82 siguen siendo necesarios para demostrar continuidad/conservadurismo de spread, slippage y fee semantics del producto real.

Por tanto una composición futura de promoción debe exigir, como mínimo:

```text
D2K predictive PASS
AND D2N economic PASS
AND W81 cost continuity compatible
AND W82 fee/product economics compatible
AND remaining W83-W87 operational/safety evidence
```

D2N por sí solo no puede cerrar esa cadena.

## Suite D2N

La fixture dedicada reconstruye D2F→D2M, ejecuta un D2K predictivo real con Qlib 0.9.7 y luego construye un economic holdout sintético de 90 barras diarias y 3 activos.

Se prueban al menos:

- favorable market -> PASS de todos los gates;
- adverse market -> terminal FAIL;
- second attempt after PASS -> rejected;
- second attempt after FAIL -> rejected;
- structural failure after checkout -> terminal/nonretryable;
- broker credentials -> reject before start;
- economic commitment drift -> reject before start;
- missing D2K terminal -> block;
- append-only start/terminal tables;
- stable semantic evaluator hash.

Los datasets sintéticos son pruebas de software/gobernanza, no evidencia de rentabilidad del sistema.

## CI dedicado

El workflow D2N debe demostrar:

1. compile + AST boundary antes de Qlib;
2. D2M/D2L/D2J ordering boundaries;
3. Research Authority;
4. W81/W82 continuity boundaries;
5. instalación exacta `pyqlib==0.9.7`;
6. D2N deterministic/adversarial suite;
7. regresión D2K real;
8. seis modelos D2G reales;
9. D2E/D2D regressions;
10. Research Authority posterior al runtime;
11. existing FINAL_HOLDOUT one-shot governance.

Nota: D2N no ejecuta Qlib directamente, pero su fixture y el reader de D2K importan la capa D2K, que depende de pandas/Qlib runtime. Por precisión, D2N no se describe actualmente como una suite funcional pre-Qlib.

## Limitaciones explícitas

D2N v1 no demuestra:

- que 20 bps sean los costes reales futuros;
- queue position o microestructura de exchange;
- partial-fill behavior exacto de un broker;
- latencia real;
- funding/borrow economics;
- impuestos;
- market impact no lineal fuera del volume cap;
- rentabilidad futura;
- readiness PAPER/LIVE.

Es una frontera de evidencia OOS cost-aware y gobernanza one-shot, no una promesa de retorno.

## Siguiente frontera propuesta

Tras certificar D2N, la siguiente frontera correcta no es ejecutar órdenes. Debe ser una **composición durable de evidencia** que vincule D2K PASS + D2N PASS + D2L strategy identity + W81/W82 cost/fee continuity, manteniendo todavía PAPER/LIVE bloqueados hasta que W83-W87 y Safety puedan demostrar la misma identidad de estrategia de extremo a extremo.
