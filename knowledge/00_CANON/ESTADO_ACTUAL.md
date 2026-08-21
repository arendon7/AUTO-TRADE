# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-21
Estado canónico: **R0–R5 CERTIFIED; R6 FIRST PAPER CANARY BROKER-TRUTH CLOSED; R7 ACTIVE**.

## R6 — hito real alcanzado
El primer canary crypto PAPER compatible con el mínimo real de Alpaca terminó reconciliado por verdad del broker.

Evidencia de operador:
- `attempt_id=first-canary-57d01d35e8b25f4babc57695ac87d962`;
- `broker_order_id=2fa68a01-7408-48c5-b3c2-0b387987d2b6`;
- `client_order_id=atr6c-entry-72c2b075ad9c96a71108664a5919dca3be326109`;
- broker status `filled`;
- broker fill `0.00014432 BTC`;
- net broker position `0.000143959 BTC`;
- reconciliation `ORDER_PLUS_POSITION`;
- `entry_attempt_count=1`;
- `phase=RECOVERED_GET_ONLY`;
- `retry_post=false`;
- `credentials_persisted=false`;
- `LIVE=BLOCKED`;
- lifecycle `ENTRY_FILLED_UNPROTECTED`.

No hubo segundo POST durante recovery. La rotación de PAPER key quedó validada sólo después de same-account GET proof y el lookup de posición usa la semántica real `BTCUSD` de Alpaca.

Exact-head R6: `0cbb782015eeed200b9851b53764ac6389c3d9ff`.
Certificación: 16/16 workflows green; Core Safety 2,469 tests; coverage 85.08%.

## Qué significa y qué no
Ya no necesitamos tratar “¿podemos tocar Alpaca PAPER con seguridad?” como la pregunta principal. El plumbing de entrada/recovery real está demostrado.

Esto **no** demuestra edge, Sharpe sostenible, capacidad LIVE ni rentabilidad. R1–R5 contienen motores de research/backtest/walk-forward/holdout/tournament/shadow/forward/Health que ahora deben convertirse en producto operativo.

## Exposición actual
Existe una posición PAPER BTC neta observada `0.000143959`, marcada por lifecycle como `ENTRY_FILLED_UNPROTECTED`.

Por eso la primera prioridad R7 no es otra entrada: es broker-truth portfolio visibility y capacidad risk-reducing de proteger/reducir/cerrar.

## R7 activo
Branch: `work/r7-paper-operations-strategy-lab`.
Documento: `docs/R7_PAPER_OPERATIONS_STRATEGY_LAB.md`.

R7 se divide en:
- R7A Portfolio Truth / Positions;
- R7B Reduce/Close/Protection PAPER;
- R7C Strategy Lab;
- R7D Auto-Paper Runner;
- R7E Paper probation.

La primera pieza R7A es un gateway GET-only para account + posiciones + órdenes abiertas. No incorpora writer, cancel, replace ni LIVE authority.

## Modelo de automatización
La IA puede generar hipótesis, variantes, experimentos y análisis. No puede tener un camino directo a POST.

Una estrategia que opere automáticamente debe estar:
- versionada y fingerprinted;
- reproducible;
- promovida por gates research/forward;
- acotada por `PaperStrategyPermit`;
- evaluada por Portfolio + Capital Safety;
- stageada por OMS;
- enviada por writer PAPER one-shot;
- reconciliada contra broker truth;
- vigilada por Health/kill switch/loss limits.

## Negative tests permanentes
Se mantienen los Negative tests de acciones fuera de orden, stale/tampered evidence, wrong account, credential drift, unsupported asset, over-notional, risk limits, kill switch, broker ambiguity, UNKNOWN restart, reconciliation mismatch, direct writer access y cualquier intento LIVE.

## Capital
R6 ejecutó un canary PAPER real y actualmente existe exposición PAPER BTC pequeña. Esto es sandbox broker capital, no capital LIVE.

**LIVE TRADING: BLOQUEADO.**
