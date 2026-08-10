# ADR-0003 — Durable State, Atomic Risk Reservations and Reconciliation

Status: Accepted
Date: 2026-08-10

## Context
Foundation v0.2 protegía el camino de ejecución dentro de un proceso, pero OMS, ledger y portfolio eran in-memory. Un restart, crash o dos procesos podían perder idempotencia global, olvidar el kill switch o aprobar órdenes contra la misma capacidad de riesgo.

## Decision
Adoptar para Foundation v0.3 un backend SQLite local con WAL y transacciones como referencia durable de paper/control-plane.

### Estado durable
- Event Ledger persistente, append-only lógico y encadenado por hash.
- OMS/OrderStore durable con idempotency key única.
- `SUBMITTING` se persiste antes de broker I/O para representar ambigüedad de crash.
- Portfolio State versionado con optimistic concurrency.
- Kill switch persistente y versionado.
- Risk reservations persistentes con generación global.

### Reserva cross-process
Una aprobación de riesgo se calcula sobre portfolio persistido + todas las reservas activas. La reserva solo se confirma si no cambió ni la versión del portfolio ni la generación de reservas. Si cambian, se reevaluará; no se consume capacidad stale.

### Safety-state race
Cada `RiskDecision` incluye `safety_state_version`. El OMS rechaza una aprobación si el estado de seguridad cambió antes del submit. Esto reduce la ventana de una aprobación anterior a un kill switch.

### Recovery/Reconciliation
Cada arranque marca temporalmente reconciliation/broker state como no confiables y ejecuta reconciliación antes de nuevo riesgo. Órdenes `SUBMITTING/UNKNOWN` se consultan contra evidencia broker-side. Posiciones, órdenes abiertas y reservas deben concordar.

Una reserva activa sin orden OMS es `ORPHAN_RISK_RESERVATION` y mantiene fail-closed.

## Scope
`DurablePaperBroker` sirve como simulador de seguridad de ejecución e idempotencia, no como fill model realista ni como broker productivo.

SQLite es suficiente para Foundation/paper local; no implica que sea el datastore final de una topología live distribuida.

## Consequences
+ Restart y retry conservan idempotencia y exposición.
+ Dos procesos no pueden comprometer la misma capacidad de riesgo stale.
+ Kill switch sobrevive restart.
+ Crash después de broker commit puede recuperarse mediante reconciliación.
+ Estado inconsistente bloquea nuevo riesgo.
- Aumenta complejidad de persistencia y recuperación.
- Un broker real deberá implementar inspección/reconciliación equivalentes.
- Quedan ventanas e incertidumbres de I/O que requieren broker-side client IDs, cancelación y gates adicionales antes de live.

## Evidence
CI v0.3: 70 tests PASS; cobertura total 86.38%; Knowledge Contract PASS antes del cierre documental final.

## Capital status
**LIVE TRADING permanece bloqueado.**
