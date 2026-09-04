# OSS-2 IMPLEMENTATION — ALIGNED UNIVERSE + CROSS-SECTIONAL RANKING

Fecha: 2026-09-04
Rama: `research/oss2-cross-sectional`
Base: OSS-1 exact head `c86af5e85095966b48e0a73b6089a951e659212c`
Autoridad: RESEARCH ONLY

## Objetivo

OSS-2 abre la investigación multi-activo sin abrir ninguna vía nueva de ejecución.

La primera implementación se limita deliberadamente a dos contratos:

1. universo multi-activo estrictamente alineado;
2. ranking momentum cross-sectional long-only con filtro de liquidez y pesos objetivo de research.

No se incorporan todavía broker orders, rebalanceo real, shorting cross-sectional, derivados ni leverage.

## AlignedMarketUniverse

`universe.py` crea un contrato fuerte para comparar activos en el mismo instante económico.

Requisitos actuales:

- mínimo dos activos;
- símbolos únicos y orden canónico;
- misma moneda cotizada;
- mismo timeframe;
- mismo número de barras;
- timestamps exactamente iguales por barra;
- cero gaps permitidos en OSS-2 inicial.

La política es intencionalmente estricta. En vez de rellenar silenciosamente datos faltantes o mezclar snapshots desfasados, el universo falla cerrado.

El `universe_hash` vincula:

- nombre del universo;
- quote currency;
- timeframe;
- símbolo exacto;
- `dataset_hash` exacto de cada activo.

## Evidencia prefix-only

Un punto crítico de OSS-2 es que la identidad de una observación cross-sectional tampoco puede depender de datos futuros.

Por ello `rank_cross_sectional_momentum(...)` construye internamente:

`historical_universe = universe.prefix(as_of_bar_index + 1)`

El ranking y su `universe_hash` quedan ligados únicamente a barras conocidas hasta `t`.

Una modificación arbitraria de la cola futura después de `t` no puede cambiar:

- el hash del prefijo usado por la evidencia;
- rankings en `t`;
- pesos en `t`;
- fingerprint final de la evidencia en `t`.

## Cross-sectional momentum

Configuración actual:

- `lookback_bars`;
- `top_n`;
- `min_average_dollar_volume`;
- `max_weight_per_asset`;
- `require_positive_momentum`.

Momentum por activo:

`close[t] / close[t-lookback] - 1`

Liquidez:

promedio de `close * volume` sobre el trailing lookback cerrado hasta `t`.

Orden de ranking:

1. momentum descendente;
2. símbolo como desempate determinista.

Un activo puede estar bien rankeado y aun así quedar INELIGIBLE por liquidez o momentum no positivo.

## Asignación inicial

OSS-2 usa long-only equal-weight entre máximo `top_n` activos elegibles.

El peso individual se limita por `max_weight_per_asset`.

Si el cap impide invertir 100%, el remanente permanece explícitamente como `cash_weight`; el sistema no redistribuye por encima del cap para forzar exposición.

Ejemplo:

- 2 activos seleccionados;
- equal-weight teórico = 50% cada uno;
- cap = 30%;
- pesos = 30% + 30%;
- cash = 40%.

Esto es una decisión de research conservadora y auditable.

## Lo que OSS-2 todavía NO hace

- no ejecuta órdenes;
- no convierte pesos en `OrderIntent`;
- no llama OMS;
- no llama Capital Safety;
- no usa broker/network;
- no rebalancea cuentas PAPER;
- no autoriza LIVE;
- no selecciona parámetros con HOLDOUT;
- no usa Qlib todavía;
- no implementa short portfolio ni market-neutral.

## Relación con infraestructura existente

AUTO-TRADE ya contiene:

- `portfolio_dependence.py` para correlación/dependencia de series de estrategias;
- `allocation_robustness.py` para escenarios leave-one-out y perturbaciones;
- políticas de presupuesto/diversificación.

OSS-2 no duplica esas funciones. La ruta posterior será:

`cross-sectional strategy backtest`
`-> return series`
`-> existing dependence evidence`
`-> existing diversification budget`
`-> existing allocation robustness`

## Tests

`test_research_oss2_cross_sectional.py` cubre:

- canonicalización del universo;
- hash de identidad;
- rechazo de clocks desalineados;
- rechazo de monedas mixtas;
- rechazo de longitudes distintas;
- mínimo dos activos;
- prefijos válidos/inválidos;
- ranking correcto;
- filtro de liquidez;
- filtro de momentum positivo;
- top-N;
- weight cap + cash explícito;
- invariancia ante cambios en la cola futura;
- cambio legítimo al avanzar `as_of`;
- validaciones adversariales;
- ausencia de campos de autoridad.

## Siguiente sub-wave OSS-2B

Para medir rentabilidad neta y comparar el cross-sectional contra OSS-1 falta un simulador multi-activo future-bar-only que:

1. calcule ranking al cierre `t`;
2. reprograme la asignación para `t+1`;
3. aplique spread/slippage/fees al open de `t+1`;
4. contabilice turnover, cash, exposure, drawdown y P&L;
5. emita una return series compatible con los módulos de dependencia/robustez existentes;
6. permanezca estrictamente research-only.

Ese será el siguiente bloque antes de cualquier Tournament cross-sectional.

## Autoridad

- Broker authority: NONE.
- OMS authority: NONE.
- Capital Safety writer authority: NONE.
- PAPER execution authority: FALSE.
- LIVE: BLOCKED.
