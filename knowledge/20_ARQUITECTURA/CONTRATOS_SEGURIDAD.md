# CONTRATOS DE SEGURIDAD DE CAPITAL

## Principio
Safety no es una recomendación del modelo. Es un conjunto de invariantes ejecutables, redundantes y fail-closed.

## Invariantes mínimas
1. `OrderIntent` no puede llegar al broker directamente.
2. Toda orden requiere `RiskDecision=APPROVED` vigente y validación del Capital Safety Kernel.
3. Cantidad, precio y notional deben estar dentro de límites por instrumento y portfolio.
4. Idempotency key única por intención lógica; reintentos no duplican exposición.
5. Datos stale/ausentes/NaN/inf => REJECT/HALT.
6. Precio fuera de sanity band configurable => REJECT.
7. Exceso de daily loss o drawdown => HALT_NEW_RISK.
8. Reconciliation mismatch relevante => HALT_NEW_RISK.
9. Broker/account ambiguity => HALT_NEW_RISK.
10. Kill switch activo => ninguna orden que aumente riesgo.
11. Configuración inválida o faltante => no usar defaults peligrosos; FAIL CLOSED.
12. Todos los rechazos, halts, órdenes y fills producen `LedgerEvent` auditable.

## Límites que existirán
- max_order_notional
- max_position_notional
- max_strategy_gross_exposure
- max_portfolio_gross_exposure
- max_net_exposure
- max_leverage
- max_daily_loss
- max_drawdown
- max_open_orders
- max_order_rate
- stale_market_data_ms
- price_deviation_bps
- allowed_symbols
- allowed_order_types

Los valores concretos no se inventan en esta fase; se fijarán después mediante configuración versionada, con unidades explícitas y tests de frontera.

## Defensa en profundidad
Strategy -> Risk -> Safety Kernel -> OMS -> Broker Adapter. Cada capa valida lo que le corresponde; ninguna confía ciegamente en la anterior.

## Reglas de recuperación
Tras restart/crash:
- reconstruir estado desde ledger + broker;
- reconciliar antes de aceptar riesgo nuevo;
- nunca asumir que una orden enviada no fue aceptada si la respuesta quedó ambigua;
- tratar estado desconocido como riesgo existente hasta resolverlo.
