# TAREA ACTIVA

## Objetivo
Construir Research v0.4: un entorno de investigación/backtesting reproducible y suficientemente realista para empezar a buscar edges sin contaminar el holdout ni comprometer el control plane.

## Secuencia
1. Definir contrato canónico de market data: instrument metadata, OHLCV/trades, timestamps, timezone, calidad y gaps.
2. Implementar backtester event-driven separado del plano de ejecución live.
3. Modelar costos explícitos: fees, spread, slippage y latencia configurable.
4. Definir Strategy interface determinista para research; output = señales/intenciones, nunca órdenes broker-side.
5. Implementar portfolio/accounting de backtest con PnL realizado/no realizado, turnover, exposición y drawdown.
6. Implementar dataset splits temporales: train/development/protected holdout.
7. Implementar walk-forward y robustness checks contra parameter mining/look-ahead.
8. Crear Experiment Registry reproducible: dataset hash, strategy version, parameters, assumptions, metrics y artifacts.
9. Incorporar tests negativos de leakage, datos desordenados/duplicados, gaps, costos cero accidentales y fills imposibles.
10. Diseñar primera biblioteca pequeña de hipótesis/edges para evaluar después de certificar el motor.

## Definition of Done
- Un backtest se reproduce desde dataset + config + strategy version.
- Fees/spread/slippage/latency nunca desaparecen silenciosamente.
- Protected holdout no puede usarse desde el loop normal de tuning.
- No existe look-ahead en eventos, features ni fills.
- Métricas incluyen retorno neto, volatilidad, Sharpe/Sortino cuando aplique, max drawdown, turnover, hit rate, profit factor, exposure y capacidad aproximada.
- Tests de seguridad/chaos y tests negativos pasan sin relajar gates.
- La investigación no introduce bypass al Safety Kernel/OMS.
- ADR, estado y handoff se actualizan al cerrar.
- Graphify se regenera cuando el runtime local esté disponible.
- **LIVE TRADING permanece bloqueado.**
