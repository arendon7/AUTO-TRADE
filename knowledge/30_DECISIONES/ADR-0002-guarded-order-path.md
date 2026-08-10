# ADR-0002 — Guarded Order Path y reducción estricta de riesgo

Status: Accepted
Date: 2026-08-10

## Context
Foundation v0.2 introduce el primer camino ejecutable desde una intención de estrategia hasta un broker de papel. El riesgo principal es que una decisión antigua, un retry, una condición de pérdida o una respuesta ambigua termine aumentando exposición accidentalmente.

## Decision
Adoptar un único camino lógico:

`OrderIntent -> CapitalSafetyKernel -> RiskDecision -> OMS -> Broker -> EventLedger`

No existe interfaz soportada Strategy -> Broker.

### RiskDecision
Cada aprobación queda ligada a:
- `intent_id`;
- fingerprint inmutable del `OrderIntent`;
- fingerprint del `MarketSnapshot` evaluado;
- versión de límites;
- TTL corto.

El OMS rechaza una aprobación si cualquiera de estos contratos deja de coincidir.

### Idempotencia
El OMS reserva la idempotency key antes de I/O con el broker. Un retry idéntico devuelve el resultado conocido sin reenviar. La misma key con otro intent es conflicto bloqueante.

### Ambigüedad
Una excepción o respuesta inválida del broker después del intento de envío se registra como `UNKNOWN`. No se interpreta como `REJECTED` ni como ausencia de exposición.

### Risk reduction
Cuando kill switch, daily-loss o drawdown están activos, puede permitirse una orden únicamente si reduce estrictamente la exposición absoluta tanto de la posición agregada del símbolo como de la posición de la estrategia, sin cruzar de signo. Reconciliation mismatch o broker state desconocido bloquean incluso esa ruta hasta conocer el estado real.

## Safety impact
+ Impide replay de aprobaciones sobre órdenes o mercado distintos.
+ Reduce riesgo de doble envío por retries dentro del proceso.
+ Evita convertir un timeout de broker en falso estado seguro.
+ Permite cerrar exposición durante un halt sin abrir una posición opuesta accidental.

## Limitación aceptada temporalmente
El lock/reservation actual es intra-proceso y el ledger/portfolio no son durables. Por ello esta ADR **no autoriza live trading**. Foundation v0.3 debe implementar persistencia, reconciliación y reservas atómicas cross-process antes de avanzar hacia brokers reales.

## Evidence
- Suite Core Safety: 52 tests PASS.
- Cobertura total CI: 93.28%.
- Knowledge Contract: PASS.
