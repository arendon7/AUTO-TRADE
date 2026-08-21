# TAREA ACTIVA — AUTO-TRADE

Fecha: 2026-08-21

## Objetivo inmediato
**Construir R7 como PAPER Trading Workbench: broker truth -> posiciones -> reduce/close/protection -> Strategy Lab -> Auto-Paper Runner.**

Base exacta: `0cbb782015eeed200b9851b53764ac6389c3d9ff`.
Branch R7: `work/r7-paper-operations-strategy-lab`.

Nota de registry: `R6` sigue siendo el next track formal del machine debt register R0–R5 hasta que su merge/certificación canónica sea cerrada; R7 se desarrolla stacked sin fingir esa promoción.

## Hito que habilita R7
El first canary `first-canary-57d01d35e8b25f4babc57695ac87d962` quedó terminalmente reconciliado:
- filled;
- un solo entry attempt;
- no segundo POST;
- broker truth position `0.000143959 BTC`;
- recovery GET-only;
- lifecycle `ENTRY_FILLED_UNPROTECTED`.

## R7A — ahora
1. leer account/positions/open orders desde Alpaca PAPER;
2. normalizar broker symbol `BTCUSD` a identidad interna `BTC/USD` sin reescribir raw evidence;
3. mostrar exposure/P&L y órdenes abiertas;
4. detectar exposure sin protección;
5. cero write authority en esta capa.

## R7B — inmediatamente después
Implementar operación risk-reducing usable:
- `Close 100%`;
- reducción parcial explícita;
- protección STOP_LIMIT;
- fresh position binding;
- Capital Safety + OMS;
- durable attempt antes de POST;
- exactly-one POST por attempt;
- `UNKNOWN => RECONCILIATION_ONLY`;
- broker-truth final position.

La BTC actual será el primer caso de cierre/protección PAPER, no se creará otra entrada sólo para probar R7B.

## R7C — Strategy Lab
Integrar R1–R5 ya certificados en una UI única:
- datasets/provenance;
- DSL/plantillas;
- backtest realista;
- walk-forward/bootstrap;
- TRAIN/VALIDATION/HOLDOUT;
- tournament + multiple testing;
- regime/portfolio/Health;
- shadow/forward;
- comparador y promotion status.

## R7D — Auto-Paper Runner
Después del Lab, permitir estrategias promovidas operar automáticamente en PAPER bajo permisos deterministas y acotados. La IA puede proponer estrategias, pero no emitir órdenes directamente.

Toda entrada automática cruza:
`Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`.

Gates mínimos: max trade notional, gross/net exposure, concurrent positions, daily loss, drawdown, cooldown, trade count, freshness, Health, kill switch, version binding y permit expiry.

## Negative tests
Conservar y ampliar los **Negative tests** para:
- LIVE host/path;
- POST desde Portfolio Truth;
- credential persistence;
- wrong account;
- stale position;
- close qty > broker qty disponible;
- duplicate exit POST;
- UNKNOWN retry;
- strategy version drift;
- failed Health;
- breached daily loss/drawdown;
- AI/model output intentando saltar Strategy Runtime/Safety/OMS.

## Regla de velocidad
R7 se desarrolla en tramos verticales operables. No esperar a terminar toda la arquitectura para mostrar producto, pero tampoco habilitar una superficie de broker write antes de que su propio lifecycle/reconciliation/negative tests esté certificado.

## No-claims
Un canary exitoso prueba infraestructura, no una estrategia rentable. La promoción se basa en evidencia de research + shadow/forward + PAPER realized behavior.

**LIVE TRADING: BLOQUEADO.**
