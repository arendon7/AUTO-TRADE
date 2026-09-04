# OSS-2 STACK STATUS

Fecha: 2026-09-04

## Stack

- Parent: `research/oss-strategy-foundation` (OSS-1).
- Child: `research/oss2-cross-sectional` (OSS-2).
- PR OSS-1: #61.
- PR OSS-2: #62.

## Implementado en OSS-2

### OSS-2A
- `AlignedMarketUniverse` estricto;
- cross-sectional momentum prefix-only;
- filtro de liquidez;
- top-N long-only;
- max-weight cap;
- cash residual explícito;
- evidencia hash-bound sin authority fields.

### OSS-2B
- backtest multi-activo future-bar-only;
- ranking en `t`, ejecución simulada de research en `t+1`;
- fees, half-spread y slippage explícitos;
- volume participation cap;
- SELL-before-BUY;
- cash no negativo;
- turnover, drawdown, exposure y tracking error;
- `StrategyReturnSeries` para dependencia/robustez R4.

## Parent correction

OSS-1 detectó un único fallo de test por orden de validación fail-closed. El contrato de DSL era correcto; se corrigió únicamente la expectativa del test en el parent. Coverage observada antes del fix: 85.02%, por encima del floor 85%.

La PR OSS-2 debe evaluarse siempre contra el parent OSS-1 vigente y no contra un SHA histórico del parent.

## Autoridad

- Research only.
- Broker: NONE.
- OMS: NONE.
- Capital authority: NONE.
- PAPER execution: FALSE.
- LIVE: BLOCKED.
