# OSS-2 IMPLEMENTATION — MULTI-ASSET RESEARCH + COMMON-WINDOW TOURNAMENT

Fecha: 2026-09-04
Rama: `research/oss2-cross-sectional`
Base certificada OSS-1: `4bcf5735d18a9ffa10dd4de0ab3a741069273f30`
Autoridad: RESEARCH ONLY

## Objetivo

OSS-2 abre investigación multi-activo económicamente evaluable sin abrir ninguna vía nueva de ejecución. La implementación actual contiene tres sub-waves acumulativas:

1. **OSS-2A** — universo alineado + ranking momentum cross-sectional;
2. **OSS-2B** — backtester multi-activo future-bar-only, cost-aware y volume-bounded;
3. **OSS-2C** — campaña DEVELOPMENT finita, preregistrada y comparada sobre una ventana temporal común.

No existe en esta capa broker authority, OMS authority, Capital Safety writer, `OrderIntent`, PAPER execution authority ni LIVE authority.

---

## OSS-2A — AlignedMarketUniverse

`universe.py` crea un contrato estricto para comparar activos en el mismo instante económico.

Requisitos:

- mínimo dos activos;
- símbolos únicos y orden canónico;
- misma moneda cotizada;
- mismo timeframe;
- mismo número de barras;
- timestamps exactamente iguales por barra;
- cero gaps permitidos en esta etapa.

El `universe_hash` vincula nombre del universo, quote currency, timeframe, símbolos y `dataset_hash` exacto de cada activo.

### Evidencia prefix-only

`rank_cross_sectional_momentum(...)` trabaja contra:

`historical_universe = universe.prefix(as_of_bar_index + 1)`

Por tanto una modificación arbitraria de la cola futura después de `t` no puede cambiar retroactivamente:

- hash del prefijo;
- ranking en `t`;
- pesos en `t`;
- fingerprint de la evidencia en `t`.

### Ranking momentum

Configuración:

- `lookback_bars`;
- `top_n`;
- `min_average_dollar_volume`;
- `max_weight_per_asset`;
- `require_positive_momentum`.

Momentum:

`close[t] / close[t-lookback] - 1`

Liquidez:

promedio de `close * volume` sobre el lookback cerrado hasta `t`.

Orden determinista:

1. momentum descendente;
2. símbolo como tie-break.

La asignación inicial es long-only equal-weight, limitada por `max_weight_per_asset`. El exceso permanece como cash explícito en vez de redistribuirse por encima del cap.

---

## OSS-2B — Future-bar multi-asset backtest

`cross_sectional_backtest.py` convierte los pesos de Research en una simulación económica, nunca en autoridad de ejecución.

### Regla temporal

- ranking: cierre de `t`;
- decisión objetivo: después del cierre de `t`;
- primer instante ejecutable: open de `t+1`.

No existe fill same-bar.

### Ejecución simulada

El motor aplica:

- long-only;
- sin leverage;
- ventas antes que compras;
- quantity-step rounding;
- cash affordability real;
- límite de participación de volumen;
- `min_trade_notional`;
- spread;
- slippage adverso;
- fees explícitos.

### Métricas

El resultado incluye:

- equity curve;
- net return;
- annualized volatility;
- Sharpe;
- Sortino;
- max drawdown;
- turnover;
- gross exposure;
- total fees;
- average/max volume participation;
- average/max target tracking error;
- fills;
- rebalances.

El tracking error evita que una estrategia aparente alcanzar pesos teóricos que el simulador no pudo ejecutar por cash, quantity step o liquidez.

### Integración R4

`CrossSectionalBacktestResult.to_strategy_return_series(...)` entrega una `StrategyReturnSeries` compatible con la infraestructura existente:

`cross-sectional result`
`-> StrategyReturnSeries`
`-> portfolio_dependence.py`
`-> diversification budget`
`-> allocation_robustness.py`

No se duplica el motor de dependencia o robustez existente.

---

## OSS-2C — Frozen DEVELOPMENT campaign

`oss2_campaign.py` resuelve el problema de comparar configuraciones con warmups diferentes sin dar ventaja temporal a los lookbacks cortos.

### Universo congelado

La campaña contiene exactamente **12 candidatos**:

- lookbacks: 12, 24, 48 y 96 barras;
- rebalanceo: cada 1, 4 o 12 barras.

Producto cartesiano: `4 x 3 = 12` trials.

No es un hyperopt adaptativo. El conjunto queda congelado antes de observar resultados.

Las demás condiciones permanecen constantes dentro de una campaña:

- universo exacto;
- top-N;
- filtro de liquidez;
- max weight per asset;
- positive-momentum policy;
- initial cash;
- annualization factor;
- gross target;
- volume participation;
- min trade notional;
- fee model;
- spread;
- slippage;
- code version.

Cada trial vincula además:

- `ranking_fingerprint`;
- `backtest_config_hash`;
- `common_window_start_bar_index`.

`backtest_config_from_oss2_trial(...)` reconstruye la configuración exacta y falla cerrado si aparece una clave adicional, falta una clave, cambia el fingerprint del ranking o no coincide el config hash.

### Ventana común

El máximo lookback congelado es 96 barras. Como la ejecución sólo puede ocurrir en la barra futura, el primer punto comparable queda fijado en:

`common_window_start_bar_index = 96 + 1 = 97`

Los candidatos de lookback 12/24/48 pueden calentarse y operar antes internamente, pero esos retornos previos **no cuentan para el Tournament**.

`evaluate_oss2_common_window(...)` recorta cada `period_returns` desde la misma barra 97 y vuelve a calcular desde una curva normalizada:

- `common_window_net_return`;
- `common_window_annualized_volatility`;
- `common_window_sharpe`;
- `common_window_sortino`;
- `common_window_max_drawdown`;
- número de observaciones;
- timestamps exactos de inicio/fin.

Así dos candidatos con warmups diferentes son evaluados sobre exactamente las mismas observaciones de mercado.

### Tournament

La métrica primaria congelada es:

`common_window_sharpe / MAXIMIZE`

El Tournament existente mantiene sus invariantes:

- el universo candidato debe equivaler al universo DEVELOPMENT completo;
- todos los trials deben ser terminales antes de rankear;
- FINAL_HOLDOUT no puede formar parte de esta campaña;
- los empates se resuelven por identidad determinista, no observando HOLDOUT;
- ganar el Tournament no equivale a promoción a PAPER.

### Flujo de evidencia

`CampaignSpec`
`-> preregister 12 TrialSpec`
`-> reconstruct exact backtest config`
`-> run future-bar backtest`
`-> evaluate common window`
`-> record_completed(metrics)`
`-> require complete DEVELOPMENT universe`
`-> Strategy Tournament`

---

## Tests dedicados

### OSS-2A

`tests/test_research_oss2_cross_sectional.py`

Cubre alineación, canonicalización, ranking, filtros, caps, prefijos y mutación de cola futura.

### OSS-2B

`tests/test_research_oss2_backtest.py`

Cubre `t -> t+1`, costes, volume participation, tracking error, SELL-before-BUY, cash no negativo, determinismo y conversión a `StrategyReturnSeries`.

### OSS-2C

`tests/test_research_oss2_campaign.py`

Cubre:

- 12 candidatos exactos;
- grilla 4 lookbacks x 3 rebalance frequencies;
- start común en barra 97;
- reconstrucción exacta por fingerprints/config hash;
- igualdad de inicio/fin/número de observaciones entre lookbacks 12 y 96;
- rechazo de resultados pertenecientes a otro config;
- rechazo de HOLDOUT;
- ausencia de campos de autoridad;
- flujo end-to-end de 12 backtests a `SQLiteTrialLedger` y `evaluate_strategy_tournament`.

El workflow `OSS-2 Cross-Sectional Research` compila los tres módulos, ejecuta las tres suites OSS-2 y vuelve a probar OSS-1, R4 dependence/allocation, Research Authority y W83.

---

## Estado de autoridad

- Broker authority: NONE.
- OMS authority: NONE.
- Capital Safety writer authority: NONE.
- `OrderIntent` authority: NONE.
- Network authority: NONE.
- PAPER execution authority: FALSE.
- LIVE: BLOCKED.

## Próximo bloque lógico

Después de certificar OSS-2C, la siguiente expansión debe agregar evidencia de robustez económica alrededor del ganador DEVELOPMENT antes de HOLDOUT: sensibilidad local de parámetros, stress de fees/slippage, subperiodos/regímenes y bootstrap/Monte Carlo. Sólo candidatos que sobrevivan esas pruebas deberían llegar a una autorización separada de FINAL_HOLDOUT.
