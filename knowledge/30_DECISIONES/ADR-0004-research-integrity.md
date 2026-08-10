# ADR-0004 — Research Integrity, Anti-Look-Ahead and Protected Holdout

Status: Accepted
Date: 2026-08-10

## Context
AUTO-TRADE necesita buscar estrategias rentables sin confundir performance histórica con edge real. El riesgo principal de esta fase no es solo un bug de ejecución: también son look-ahead, costos omitidos, fills imposibles, parameter mining y contaminación del holdout.

## Decision
Research queda aislado del plano live y adopta contratos que hacen explícitas las principales fuentes de autoengaño.

### Market data canónico
- Instrument metadata y barras OHLCV con timestamps timezone-aware.
- Barras estrictamente ordenadas y únicas.
- Detección explícita de gaps.
- Dataset identificado por hash reproducible que incluye provenance/source.

### Anti-look-ahead estructural
Una estrategia solo recibe `history` hasta la barra actual. Una señal debe declararse exactamente al cierre de esa barra y `execution_delay_bars >= 1`; por tanto, no puede ejecutarse en la misma barra cuya información produjo la señal.

### Costos y capacidad
- Fee, half-spread y slippage son explícitos.
- Un backtest con costo total cero requiere opt-in deliberado.
- Max leverage y max volume participation son límites del simulador.
- Una señal sin volumen futuro ejecutable se rechaza.

### Research strategy contract
Las estrategias de research producen `ResearchSignal`. No producen órdenes broker-side y no tienen acceso al OMS para convertir un resultado histórico en autorización de ejecución.

### Temporal splits y holdout
Se separan train, development y protected holdout en orden temporal. El holdout solo se entrega mediante un `HoldoutPermit` de propósito `final_validation` y su `permit_id` se consume de forma durable en el Experiment Registry.

Este mecanismo previene el uso accidental/repetitivo normal; no se considera una barrera criptográfica contra un desarrollador que deliberadamente eluda el proceso. La promoción sigue requiriendo governance humano.

### Experiment Registry
Cada experimento registra:
- dataset hash;
- split;
- strategy id/version/parameters;
- config hash;
- code version;
- metrics y artifacts;
- result hash.

El mismo spec produciendo otro result hash se trata como conflicto de reproducibilidad.

### Walk-forward robustness
Los thresholds no se hardcodean como una definición universal de estrategia ganadora. Se suministran mediante `RobustnessPolicy` y el gate reporta explícitamente dimensiones como número de folds/fills, fracción de folds positivos, mediana/worst return y worst drawdown.

## Scope / limitaciones
- v0.4 es inicialmente single-symbol y bar-based.
- El fill model usa open de barra futura + costos bps; no modela aún order book, impacto dinámico, partial fills realistas, funding/borrow, corporate actions o futures rolls.
- El volumen se interpreta en unidades del activo.
- Sharpe/Sortino son métricas de research con annualization explícita; no garantizan performance futura.
- Los tests actuales usan datasets sintéticos; todavía falta ingesta histórica real.

## Evidence
Head funcional de v0.4: 122 tests PASS, cobertura total 89.40%, `research/gates.py` 100% cubierto, Knowledge Contract PASS.

## Capital status
**LIVE TRADING permanece bloqueado.**
