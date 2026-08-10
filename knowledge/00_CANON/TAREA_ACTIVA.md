# TAREA ACTIVA

## Objetivo
Completar **R2 — Capital Safety + OMS maturity** sin P0/P1 conocido antes de permitir que R3 introduzca networking de market data real.

## Workstreams R2

### A. Risk-policy completeness
1. Inventariar controles existentes en `safety.py`, state/persistence y engine.
2. Certificar max order notional, price/fat-finger sanity, position, strategy and portfolio exposure, leverage.
3. Verificar interacción con atomic reservations para que capacidad pendiente no pueda consumirse dos veces.
4. Fallar cerrado con price stale/missing/invalid, portfolio state stale o safety-state changed.

### B. Durable loss/drawdown/circuit state
1. Definir ledger/state para realized/unrealized daily-loss inputs según autoridad disponible.
2. Persistir daily-loss/drawdown/circuit state y versionarlo.
3. Probar restart, date/session boundary, duplicated events, rollback/stale writes and concurrent updates.
4. Automatic recovery no puede retirar una restricción defensiva sin la política/acknowledgement requerida.

### C. OMS lifecycle completeness
1. Diseñar state machine explícita para partial fills, cancel requested/confirmed, replace requested/confirmed, rejected, expired, UNKNOWN.
2. Cada transición debe ser idempotente y auditable.
3. Broker I/O ambiguity => UNKNOWN/potential risk, nunca blind retry.
4. Reconciliation debe recuperar partial fills y open/replace/cancel ambiguity sin duplicar exposure.
5. Crash points antes/después de durable write y broker commit deben tener tests.

### D. Machine-readable contracts
1. Crear registry versionado de schemas/contracts para mensajes/estados relevantes.
2. Validar examples/fixtures en CI.
3. Definir compatibility policy para additive vs breaking changes.
4. Evitar quota artificial de 77/207 schemas; cubrir contratos que realmente existen.

### E. Control-plane evidence
1. Elevar cobertura significativa de `persistence.py` y `reconciliation.py` failure branches.
2. Añadir R2 failure-path review.
3. Mantener coverage gate >=85%; no bajar thresholds.
4. Actualizar `DEBT_REGISTER.md` antes de cada cierre.

## Negative tests obligatorios R2
- price missing/zero/negative/NaN/inf/stale/outside sanity band => reject.
- order/position/strategy/portfolio exposure boundary exacta y +epsilon.
- stale portfolio/reservation/safety version => reevaluate/reject.
- duplicate fill does not double-apply cash/position/PnL.
- partial fill preserves remaining quantity and risk capacity correctly.
- cancel ACK lost / broker cancel uncertain => UNKNOWN, no new conflicting risk.
- replace cannot bypass limits or create duplicate exposure.
- crash after broker commit but before local terminal write recovers by reconciliation.
- unknown broker order/position mismatch blocks new risk.
- daily-loss/drawdown circuit survives restart.
- concurrent state updates cannot lose a stricter circuit/kill state.
- invalid contract version/schema fails CI/runtime boundary as applicable.
- PAPER/LIVE remain disabled/fail-closed.

## Definition of Done R2
- All R2 matrix rows PASS.
- `TD-R2-001..004` closed; no R2 P0/P1 open.
- Critical control-plane failure branches covered meaningfully.
- Contract registry/versioning CI PASS.
- R2 threat/failure-path review complete.
- Canon, Debt Register, ADR/handoff synchronized.
- PR CI green and merge SHA recertified on `main`.
- No external broker/live endpoint introduced.
- **LIVE TRADING remains blocked.**
