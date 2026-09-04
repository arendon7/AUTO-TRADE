# OSS STRATEGY FOUNDATION — AUTO-TRADE

Fecha: 2026-09-04
Rama: `research/oss-strategy-foundation`
Estado: RESEARCH ONLY — sin autoridad PAPER/LIVE

## Objetivo

Construir una base de investigación cuantitativa propia aprovechando patrones, ideas y componentes conceptuales de proyectos públicos maduros, sin reemplazar el OMS, Capital Safety Kernel, reconciliación, gates científicos ni la cadena de autoridad ya certificada en AUTO-TRADE.

La integración prioriza tres cosas:
1. diversidad real de familias de estrategia;
2. trazabilidad de procedencia/licencia;
3. validación científica y económica antes de cualquier promoción.

## Regla de integración

No se hace `copy/paste` indiscriminado de motores completos. Cada proyecto externo se clasifica como:
- **ENGINE/REFERENCE**: patrones arquitectónicos y semánticos;
- **RESEARCH ADAPTER**: librería que puede ejecutarse en un entorno aislado de research;
- **IDEA SOURCE**: hipótesis/estrategias reimplementadas de forma nativa en la Safe Strategy DSL;
- **REFERENCE ONLY**: fuente útil cuya licencia o arquitectura desaconseja incorporar código al core.

Toda estrategia que llegue a AUTO-TRADE debe terminar expresada en contratos propios, con datos, parámetros, costes, código/runtime y hashes controlados por AUTO-TRADE.

## Proyectos priorizados

### QuantConnect / LEAN — prioridad A
Rol: ENGINE/REFERENCE + IDEA SOURCE.
Licencia observada: Apache-2.0.
Usar como referencia para:
- event-driven backtesting;
- modelos de slippage/fees/fills;
- indicadores y portfolio/risk patterns;
- walk-forward y optimización;
- brokerage abstractions.

No sustituye el OMS ni el Capital Safety Kernel de AUTO-TRADE.

### Microsoft Qlib — prioridad A
Rol: RESEARCH ADAPTER + IDEA SOURCE.
Licencia observada: MIT.
Usar para:
- factor research;
- cross-sectional ranking;
- supervised ML;
- experiment tracking concepts;
- model comparison.

Los outputs ML nunca constituyen autoridad de ejecución. Deben convertirse en evidencia/research signal y atravesar la cadena de preregistration -> DEVELOPMENT -> selection -> HOLDOUT -> Shadow/Forward.

### Hummingbot — prioridad A para microestructura
Rol: IDEA SOURCE + ENGINE/REFERENCE.
Licencia observada: Apache-2.0.
Usar para:
- market making;
- inventory-aware quoting;
- order-book/microprice research;
- connector semantics.

No conectar Hummingbot directamente al capital de AUTO-TRADE; cualquier futura ejecución debe seguir usando la autoridad OMS/Safety/writer ya existente.

### NautilusTrader — prioridad A como referencia de ejecución/eventos
Rol: ENGINE/REFERENCE.
Licencia observada: LGPL-3.0; tratar inicialmente como REFERENCE ONLY para evitar mezclar el core antes de una revisión jurídica/técnica específica.
Usar como referencia para:
- deterministic event-driven architecture;
- clocks/events;
- order state machines;
- high-performance market-data concepts.

### Freqtrade — prioridad B
Rol: IDEA SOURCE / REFERENCE ONLY.
Licencia observada: GPL-3.0.
No copiar código al core propietario. Reimplementar únicamente ideas/hipótesis de forma independiente.
Usar para:
- crypto strategy patterns;
- hyperparameter research ideas;
- protections/cooldowns;
- operational bot UX references.

### vectorbt — prioridad B
Rol: RESEARCH ADAPTER condicionado a licencia.
Licencia observada: Apache-2.0 + Commons Clause.
Puede ser útil para exploración vectorizada masiva, pero su Commons Clause exige mantener separación y revisión antes de incorporarlo a un producto comercial. Por defecto: referencia/laboratorio, no dependencia del core.

### Goldman Sachs gs-quant — prioridad B
Rol: ANALYTICS/IDEA SOURCE.
Útil para estadística, riesgo y derivados. No es motor primario de ejecución.

### Numerai — prioridad B
Rol: ML/feature/model inspiration.
Usar como referencia para neutralización, ensembles, ranking y disciplina de validación; no como execution engine.

### Zipline Reloaded — prioridad C
Rol: independent backtest reference.
Puede usarse como segundo motor de validación para detectar diferencias de modelado, pero no se propone como runtime operativo.

### StockSharp — prioridad C / HOLD
No incorporar hasta resolver de forma inequívoca la licencia aplicable a la versión exacta evaluada.

## Familias iniciales del Strategy Tournament

AUTO-TRADE incorpora un catálogo inicial de hipótesis, no estrategias aprobadas:

1. `trend_ema_atr_v1` — trend following con confirmación EMA + normalización ATR.
2. `ts_momentum_multi_horizon_v1` — momentum temporal multi-horizonte.
3. `cross_sectional_momentum_v1` — ranking relativo multi-activo.
4. `mean_reversion_zscore_v1` — reversión condicionada por volatilidad/liquidez.
5. `donchian_breakout_atr_v1` — breakout de canal con filtro de volatilidad.
6. `volatility_regime_switch_v1` — meta-capa de cambio de régimen.
7. `pairs_residual_reversion_v1` — stat-arb/pairs con pruebas de estabilidad.
8. `market_making_inventory_aware_v1` — microestructura, research only.
9. `carry_basis_v1` — funding/basis, research only hasta existir contratos de derivados.
10. `ml_cross_sectional_rank_v1` — ranking ML aislado y leakage-safe.

## Metodología de selección

Ninguna estrategia se optimiza para una rentabilidad diaria fija. El objetivo de research es maximizar expectativa neta ajustada por riesgo y robustez.

Cada familia deberá pasar, como mínimo:
- dataset/provenance hash;
- preregistro de hipótesis y parámetros;
- costes explícitos: spread, fees, slippage y latencia;
- DEVELOPMENT con contabilidad de todos los trials;
- múltiples-testing correction;
- selección congelada;
- FINAL_HOLDOUT intocable durante tuning;
- estabilidad de parámetros;
- walk-forward;
- bootstrap/Monte Carlo;
- sensibilidad a fees/slippage;
- análisis por régimen;
- turnover/capacity;
- drawdown y tail risk;
- Shadow/Forward antes de PAPER candidate.

## Arquitectura objetivo

`External ideas/libraries`
`-> isolated Research adapters`
`-> native StrategySpec / feature contracts`
`-> BacktestEngine + realistic costs`
`-> preregistered tournament`
`-> multiple-testing + HOLDOUT`
`-> execution sensitivity`
`-> Shadow/Forward`
`-> PAPER candidate admission`
`-> runtime readiness`
`-> explicit human/authority gates`
`-> OMS + Capital Safety + broker writer`

La flecha de research hacia ejecución nunca es directa.

## Próximas implementaciones recomendadas

### Wave OSS-1
Implementar de forma nativa y determinista:
- trend EMA/ATR;
- time-series momentum multi-horizon;
- mean reversion z-score;
- Donchian breakout;
- volatility regime classifier.

### Wave OSS-2
Añadir infraestructura de universo:
- cross-sectional ranking;
- liquidity filters;
- portfolio allocation;
- correlation/diversification controls.

### Wave OSS-3
Adaptador aislado Qlib:
- factor matrix export;
- model training fuera del core;
- signed/hash-bound prediction artifact;
- deterministic ingestion as research evidence only.

### Wave OSS-4
Microstructure lab inspirado en Hummingbot/Nautilus:
- L2 order-book snapshots;
- microprice;
- queue/spread/adverse-selection models;
- inventory-aware market-making simulation.

### Wave OSS-5
Independent model validation:
- ejecutar estrategias elegidas en un segundo backtester compatible;
- comparar fills, retornos, drawdown y costes;
- cualquier divergencia material bloquea promoción hasta explicación.

## Autoridad

Este documento y `strategy_catalog.py` crean solamente un inventario de research.

- PAPER execution authority: FALSE.
- Broker write authority: NONE.
- Capital authority: NONE.
- LIVE: BLOCKED.
