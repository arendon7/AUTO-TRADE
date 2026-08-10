# TAREA ACTIVA

## Objetivo
Construir Foundation v0.3: hacer durable y recuperable el control plane antes de añadir backtester, Strategy Agent o conexiones reales.

## Secuencia
1. Implementar Event Ledger durable con transacciones y constraints de unicidad.
2. Implementar Portfolio State Store versionado con optimistic concurrency/control de revisiones.
3. Implementar Reconciliation Engine entre estado esperado y estado del broker/paper broker.
4. Implementar reserva atómica de riesgo/idempotencia cross-process para cerrar la carrera `evaluate -> submit`.
5. Definir recovery state machine para órdenes `UNKNOWN`, restart y respuesta ambigua del broker.
6. Implementar halt global persistente y procedimiento seguro de reset.
7. Crear pruebas negativas y chaos tests: procesos concurrentes, crash entre persistencia/envío, duplicados, replay, ledger corrupto, broker timeout, fill parcial y mismatch de reconciliación.
8. Solo después: backtester/research pipeline y market-data adapters.

## Definition of Done
- Dos procesos no pueden consumir simultáneamente el mismo presupuesto de riesgo.
- Reintentos/restarts no duplican órdenes ni exposición.
- Estado `UNKNOWN` fuerza reconciliación antes de riesgo nuevo.
- Portfolio se reconstruye/reconcilia desde evidencia durable.
- Tests de seguridad/chaos pasan sin relajar límites.
- Knowledge Contract, ADR y handoff actualizados.
- Graphify regenerado cuando el runtime local esté disponible.
- **LIVE TRADING permanece bloqueado.**