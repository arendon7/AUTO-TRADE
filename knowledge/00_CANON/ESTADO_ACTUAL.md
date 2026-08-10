# ESTADO ACTUAL

Fecha: 2026-08-10
Fase: Foundation v0.2

## Hecho
- Architecture Baseline 1.0 y memoria Graphify + Obsidian incorporadas en `main`.
- Contratos de dominio mínimos implementados: market snapshot, order intent, risk decision, order, fill y portfolio snapshot.
- Event Ledger append-only en memoria para Foundation/paper.
- Capital Safety Kernel ejecutable y fail-closed.
- Límites: orden, posición, estrategia, portfolio, net exposure, leverage, pérdida diaria, drawdown y órdenes abiertas.
- Validación de market data stale/futuro/inválido y sanity band de precios.
- Reconciliation mismatch y broker state desconocido bloquean riesgo.
- Kill switch implementado; permite únicamente reducción estricta de riesgo sin flip de posición.
- RiskDecision ligado por fingerprint al intent + market snapshot y con TTL.
- OMS con idempotencia, reserva de key antes de I/O y estado `UNKNOWN` ante respuesta ambigua/inválida del broker.
- Paper Broker determinista implementado.
- Pipeline único `OrderIntent -> Safety Kernel -> OMS -> Paper Broker -> Ledger`.
- 52 pruebas PASS con 93.28% de cobertura total de branches/statements combinada reportada por CI.
- Knowledge Contract PASS.

## Limitaciones conocidas
- La exclusión evaluate+submit es intra-proceso; todavía no existe reserva transaccional cross-process.
- Portfolio/reconciliation todavía no son persistentes ni versionados de forma durable.
- Event Ledger actual es in-memory; falta backend durable.
- Paper Broker no es un modelo realista de fills para backtesting.
- No existe ingesta real de market data ni broker real.
- No existen todavía backtester, research pipeline ni estrategias certificadas.
- Los límites monetarios productivos todavía no están definidos; solo existen fixtures TEST-USD de pruebas.
- Graphify está integrado mediante runbook/scripts, pero `graphify-out/` aún debe generarse en un runtime con Graphify disponible.

## Estado de capital
**LIVE TRADING: BLOQUEADO.**
No existe autorización técnica ni humana para operar dinero real.

## Próximo hito
Foundation v0.3: persistencia durable + portfolio state versionado + reconciliation engine + reservas atómicas de riesgo/idempotencia cross-process + recovery/chaos tests.