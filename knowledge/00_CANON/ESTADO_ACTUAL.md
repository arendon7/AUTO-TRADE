# ESTADO ACTUAL

Fecha: 2026-08-10
Fase: Foundation v0.3

## Hecho
- Architecture Baseline 1.0 y memoria Graphify + Obsidian incorporadas en `main`.
- Contratos de dominio, Capital Safety Kernel, OMS y Paper Broker base implementados desde Foundation v0.2.
- Backend SQLite/WAL implementado para estado durable local de Foundation/paper.
- Event Ledger durable con unicidad y cadena hash verificable.
- OMS durable con idempotencia cross-process y estado `SUBMITTING` persistido antes de broker I/O.
- Portfolio State Store versionado con optimistic concurrency y aplicación idempotente de fills.
- Reservas atómicas de riesgo: versión de portfolio + generación de reservas evitan aprobar simultáneamente contra capacidad stale.
- Kill switch persistente y versionado; sobrevive restart.
- `RiskDecision` ligado a `safety_state_version`; una aprobación vieja se invalida si cambia el estado de seguridad antes del submit.
- `DurablePaperBroker` idempotente y recuperable implementado como simulador de seguridad de ejecución.
- Reconciliation Engine ejecutable: órdenes ambiguas, posiciones, órdenes abiertas y reservas se cotejan con estado broker-side.
- Arranque fail-closed: reconciliation/broker state se degradan antes de reconciliar y solo vuelven a estado confiable si existe concordancia.
- Crash después de broker commit puede recuperarse sin duplicar orden ni exposición.
- Reservas huérfanas se detectan como inconsistencia y bloquean nuevo riesgo.
- 70 pruebas PASS con 86.38% de cobertura combinada reportada por CI.
- Knowledge Contract PASS.

## Limitaciones conocidas
- SQLite es el backend durable de referencia para Foundation/paper local; no está promovido como datastore final de una topología live distribuida.
- `DurablePaperBroker` no es un modelo realista de fills para backtesting.
- Un broker real deberá aportar client IDs idempotentes, account/order inspection, cancelación, timeouts y reconciliación equivalentes.
- Sigue existiendo riesgo operativo inherente a una orden ya en vuelo durante una activación de kill switch; antes de live se requieren execution fences/cancelación broker-side y certificación adicional.
- Falta lifecycle completo de partial fills/cancel/replace y recuperación de esos estados.
- No existe ingesta real de market data ni broker real.
- No existen todavía backtester, research pipeline ni estrategias certificadas.
- Los límites monetarios productivos todavía no están definidos; solo existen fixtures TEST-USD de pruebas.
- Graphify está integrado mediante runbook/scripts, pero `graphify-out/` aún debe generarse en un runtime con Graphify disponible.

## Estado de capital
**LIVE TRADING: BLOQUEADO.**
No existe autorización técnica ni humana para operar dinero real.

## Próximo hito
Research v0.4: market-data contract + backtester event-driven + fees/spread/slippage/latency + strategy interface + experiment registry + holdout protegido + walk-forward/robustness gates. En paralelo se mantendrá una lista explícita de hardening pendiente antes de cualquier broker real.