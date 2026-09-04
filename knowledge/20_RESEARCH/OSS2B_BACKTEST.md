# OSS-2B — CROSS-SECTIONAL FUTURE-BAR BACKTEST

Fecha: 2026-09-04
Rama: `research/oss2-cross-sectional`
Autoridad: RESEARCH ONLY

## Propósito

OSS-2B hace económicamente evaluable el ranking multi-activo creado en OSS-2A.

La ruta queda:

`AlignedMarketUniverse`
`-> ranking al cierre t`
`-> CrossSectionalRankingEvidence prefix-only`
`-> rebalanceo de research en open(t+1)`
`-> spread + slippage + fees + volume cap`
`-> equity / turnover / drawdown / tracking error`
`-> StrategyReturnSeries`
`-> R4 dependence/diversification/robustness existentes`

No existe broker POST ni OrderIntent en esta ruta.

## Anti-look-ahead

Para todo fill:

`execution_bar_index = signal_bar_index + 1`

El ranking usa el prefijo cerrado hasta `signal_bar_index`.

En barras contiguas, `close(t)` y `open(t+1)` comparten el timestamp frontera. Por ello la prueba de aislamiento temporal usa el índice de barra futuro, no un gap artificial de timestamp.

Los tests modifican brutalmente precios futuros y demuestran que rankings y fills producidos antes de esa mutación permanecen idénticos.

## Rebalanceo

Cada rebalance:

1. calcula equity al open de la barra futura;
2. convierte pesos research a cantidades objetivo;
3. redondea por `quantity_step`;
4. procesa ventas primero;
5. procesa compras después con el cash disponible;
6. limita cantidad por `max_volume_participation`;
7. aplica `ExecutionCostModel` existente;
8. no fuerza trades bajo `min_trade_notional`;
9. calcula tracking error contra los pesos deseados.

## Por qué ventas antes que compras

En una cartera long-only sin leverage, una rotación debe liberar capital de posiciones salientes antes de financiar posiciones entrantes.

Esto reduce el riesgo de que un simulador acepte temporalmente cash negativo o asuma financiación inexistente.

## Costos

Se reutiliza el contrato `ExecutionCostModel`:

- fee bps;
- half-spread bps;
- adverse slippage bps.

BUY:

`execution_price > reference_open` cuando existen fricciones.

SELL:

`execution_price < reference_open` cuando existen fricciones.

La compra además se limita por cash después de incorporar fee por unidad.

## Liquidez de ejecución

El ranking ya filtra average dollar volume, pero eso no garantiza capacidad de ejecución.

OSS-2B agrega:

`quantity <= bar.volume * max_volume_participation`

Si el mercado simulado no permite alcanzar el target, la posición queda parcial. El sistema no inventa volumen.

## Tracking error

Después de cada rebalance se calcula:

`sum(abs(actual_asset_weight - desired_asset_weight))`

Se reportan:

- average target tracking error;
- max target tracking error.

Un candidato con Sharpe alto pero tracking error excesivo no debe interpretarse como estrategia implementable.

## Métricas

`CrossSectionalBacktestMetrics` incluye:

- net return;
- annualized volatility;
- Sharpe;
- Sortino;
- max drawdown;
- turnover;
- average/max gross exposure ratio;
- max volume participation;
- total fees;
- average/max target tracking error;
- fills;
- rebalances.

No existe objetivo fijo de retorno diario dentro del optimizador.

## Integración con R4

`CrossSectionalBacktestResult.to_strategy_return_series(...)` transforma retornos del backtest en el contrato existente:

`StrategyReturnSeries`

Esto permite reutilizar sin duplicar:

- `build_dependence_evidence`;
- correlaciones y clusters;
- `DiversificationBudgetPolicy`;
- `validate_allocation_budget`;
- `evaluate_allocation_robustness`;
- leave-one-out;
- perturbation scenarios.

## Tests OSS-2B

Se prueba explícitamente:

- t -> t+1 future-bar execution;
- costos > zero-cost baseline;
- volume participation cap;
- tracking error cuando no hay liquidez suficiente;
- rotación SELL antes de BUY;
- cash nunca negativo;
- invariancia de decisiones tempranas ante future-tail mutation;
- conversión a StrategyReturnSeries;
- determinismo/result hash;
- validación fail-closed de config;
- ausencia de authority fields.

## Autoridad

- Research fills: simulation evidence only.
- Broker: NONE.
- OMS: NONE.
- Capital Safety writer: NONE.
- PAPER execution: FALSE.
- LIVE: BLOCKED.

## Siguiente bloque

OSS-2C debe congelar un pequeño universo de configuraciones cross-sectional DEVELOPMENT y compararlas en Tournament, sin usar FINAL_HOLDOUT para elegir lookback, top-N, rebalance cadence ni caps.
