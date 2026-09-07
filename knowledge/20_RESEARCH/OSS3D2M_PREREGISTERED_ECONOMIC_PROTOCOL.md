# OSS-3D2M — Preregistered cost-aware economic qualification protocol

## Estado y propósito

`OSS-3D2M` congela **antes de cualquier checkout FINAL_HOLDOUT** cómo debe interpretarse económicamente la estrategia predictiva creada en D2L.

D2M no ejecuta Qlib, no lee precios, no recibe barras, no lee labels, no calcula PnL y no autoriza órdenes. Su trabajo es más temprano: dejar registradas de forma durable las reglas que un evaluador económico one-shot posterior deberá obedecer.

La secuencia científica para una campaña nueva es:

```text
D2I DEVELOPMENT winner
  -> D2J protected predictive FINAL_HOLDOUT protocol
    -> D2L predictor-to-strategy preregistration
      -> D2M economic protocol preregistration
        -> D2K one-shot predictive FINAL_HOLDOUT
          -> D2N one-shot economic holdout evaluation
```

La rama de desarrollo está técnicamente apilada después de D2K/D2L, pero la gobernanza durable de D2M rechaza una nueva preregistración si el SQLite autoritativo ya contiene un D2K start o el permit correspondiente.

## Por qué D2M es necesario

Un Rank IC predictivo positivo no es suficiente para afirmar que existe una estrategia rentable. Entre `score` y retorno realizado existen al menos:

- portfolio construction;
- turnover;
- spread;
- slippage;
- fees;
- restricciones de liquidez;
- rounding por quantity step;
- min notional;
- delay entre señal y ejecución;
- drawdown;
- frecuencia suficiente de trades/rebalances.

D2L congeló el portfolio construction. D2M congela la interpretación económica y los gates antes de observar resultados OOS.

## Cost policy v1

La política canónica D2M v1 usa un modelo research explícitamente no-cero:

```text
fee_bps        = 10
half_spread_bps = 5
slippage_bps    = 5
--------------------
total            20 bps
```

Además:

```text
max_volume_participation = 10%
min_trade_notional       = 10 quote units
shorting                 = false
leverage                 = false
margin                   = false
zero-cost mode            = forbidden
```

La ejecución económica posterior debe usar:

```text
signal at closed bar t
  -> target allocation already frozen by D2L
    -> execution no earlier than next bar open
      -> spread/slippage applied adversely
        -> fee charged
          -> volume cap + quantity-step floor + min-notional
```

Estos 20 bps son una **hipótesis research conservadora**, no evidencia de comisiones reales de un broker.

Por eso D2M conserva explícitamente:

```text
requires_w81_w82_continuity = true
broker_authoritative_costs_claimed = false
```

W81/W82 continúan siendo obligatorios para demostrar que los costes/product economics reales no son peores que lo modelado.

## Economic decision policy v1

Los gates económicos quedan congelados ex ante:

```text
net_return       > 0
Sharpe           >= 1.5
profit_factor    >= 1.3
max_drawdown     <= 0.15
fills            >= 10
rebalances       >= 10
```

Todos deben pasar simultáneamente.

El objetivo no es prometer rentabilidad; es evitar aceptar una estrategia sólo porque una métrica aislada se vea bien.

El protocolo también congela:

```text
max_evaluations = 1
retuning_allowed = false
reselection_allowed = false
fallback_policy_allowed = false
second_attempt_allowed = false
failure_is_terminal = true
```

## Annualization

D2M no congela un `252` o `365` arbitrario independiente del timeframe. Congela la política:

```text
SECONDS_PER_365_DAY_YEAR_DIV_TIMEFRAME_V1
```

El evaluador posterior deberá derivar los períodos por año desde el timeframe exacto del economic holdout.

## Economic holdout commitment

D2M no recibe barras ni outcomes. Recibe únicamente `EconomicHoldoutCommitment`, que congela:

- `universe_hash`;
- `source_dataset_set_hash`;
- nombre de universo;
- símbolos en orden canónico;
- quote currency;
- timeframe;
- inicio/fin de partición;
- número de barras;
- número de símbolos.

Y exige:

```text
market_values_exposed = false
economic_outcomes_observed = false
bar_count >= 60
symbol_count >= 3
```

Esto permite comprometer la identidad del dataset sin usar sus precios para elegir costes o thresholds.

## Durable ordering

D2M debe usar el mismo SQLite autoritativo en el que se registró D2L.

En `BEGIN IMMEDIATE`:

```text
verify exact durable D2L receipt
  -> verify no D2K evaluation start
    -> verify no consumed predictive holdout permit
      -> build D2M receipt
        -> append-only INSERT
          -> COMMIT
```

Si D2L no existe en ese SQLite, D2M falla.

Si D2K ya comenzó, D2M falla.

Si el permit ya fue consumido, D2M falla.

Esto impide escoger costes o gates después de observar el predictive holdout.

## Append-only identity

La tabla `oss3_predictive_economic_protocols` liga de forma única:

- `economic_protocol_id`;
- D2L receipt/binding;
- strategy semantic hash;
- D2J protocol/receipt;
- expected holdout authorization;
- cost policy;
- decision policy;
- economic holdout commitment;
- D2M semantic code hash;
- receipt hash.

UPDATE y DELETE están bloqueados por triggers.

## Authority boundary

Incluso después de registrar D2M:

```text
predictive_final_holdout_observed = false
economic_holdout_observed = false
economic_holdout_consumed = false
profitability_claim_authorized = false
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

D2M es preregistration, no trading authority.

## Relación con W81/W82

D2M no reemplaza W81/W82.

D2M responde:

> ¿Qué costes, restricciones y gates económicos quedaron congelados antes del OOS?

W81/W82 responden posteriormente:

> ¿Los costes y fee semantics observados/calificados para el producto real son compatibles y conservadores frente al modelo research?

Si la respuesta de W81/W82 es negativa, la estrategia sigue bloqueada aunque una evaluación económica research haya pasado.

## Tests D2M

La suite dedicada cubre:

- canonical nonzero 20 bps cost policy;
- long-only/no-leverage/no-margin;
- Sharpe/PF/DD/net-return gates exactos;
- value-opaque economic holdout;
- sample floors;
- exact durable D2L state;
- read-only round trip;
- idempotencia con receipt idéntico;
- missing-D2L fail-closed;
- D2K-start-before-D2M rejection;
- permit-before-D2M rejection;
- conflicting cost policy rejection;
- append-only UPDATE/DELETE rejection;
- stable semantic code hash.

El boundary estático además prohíbe Qlib, pandas/numpy/sklearn, broker, OMS, Safety, `OrderIntent`, network, subprocess y cualquier input que contenga barras, labels, predictions o economic outcomes.

## Siguiente frontera

La siguiente frontera correcta es **OSS-3D2N**, un economic holdout evaluator one-shot que materialice el protocolo D2M sobre un `AlignedMarketUniverse` exacto, convierta allocations D2L en fills simulados next-bar, aplique costes/volume/rounding y emita PASS/FAIL terminal.

D2N tampoco deberá autorizar PAPER/LIVE; únicamente producirá evidencia económica OOS que luego pueda componerse con W81-W87 y Safety.