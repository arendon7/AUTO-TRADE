# TAREA ACTIVA

## Objetivo
Convertir el Architecture Baseline 1.0 en Foundation v0.2 ejecutable sin introducir todavía estrategias de dinero real.

## Secuencia
1. Definir contratos de dominio: Instrument, Quote/Bar, Signal, OrderIntent, RiskDecision, Order, Fill, Position, PortfolioSnapshot, LedgerEvent.
2. Implementar Event Ledger append-only.
3. Implementar Capital Safety Kernel mínimo con límites fail-closed.
4. Implementar OMS con idempotencia y estados válidos.
5. Implementar simulador/paper broker determinista.
6. Construir pruebas negativas: tamaño absurdo, precio stale, duplicado, pérdida diaria, drawdown, reconciliación y kill switch.
7. Después: backtester y research pipeline.

## Definition of Done
- Ningún OrderIntent llega a broker sin RiskDecision explícito y validación del Safety Kernel.
- Rechazos quedan auditados en Event Ledger.
- Tests de seguridad pasan.
- No hay credenciales/live broker.
- ADR/handoff/Graphify actualizados.
