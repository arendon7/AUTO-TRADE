# TAREA ACTIVA — AUTO-TRADE

Fecha: 2026-08-23

## Objetivo inmediato
**Cerrar W78 como capa determinista y sin red de calificación de ejecución para R7 Strategy Lab, preservando intacta la autoridad R7 PAPER real.**

Stack actual:
- R7 close base: `work/r7-paper-close-mac-staging` / PR #49;
- base exacta W78: `1a81a5e7c856064cc170a66e24e87928711eda21`;
- W78: `work/w78-realistic-paper-execution` / PR #50;
- W78 permanece DRAFT hasta certificación exact-head completa.

`R6/R7` mantiene sus writers y reconciliación externos como única autoridad PAPER real. W78 no tiene credenciales, red, writer, broker POST ni LIVE.

## Estado R7B — broker truth / close PAPER real

PR #49 sigue siendo un gate independiente. La exposición BTC/USD residual del first canary no se usa como excusa para emitir nuevas entradas ni para relajar el lifecycle de cierre.

El close real conserva:
- PAPER únicamente;
- strict risk reduction;
- Capital Safety + OMS frescos inmediatamente antes del writer;
- durable UNKNOWN antes del único POST;
- exactamente un POST por attempt;
- ambigüedad => GET-only reconciliation;
- residual exposure => stop, no segundo SELL automático;
- LIVE bloqueado.

**W78 no modifica ni amplía esa autoridad.**

## W78 / R7C — execution qualification

W78 reutiliza el control plane existente:

`OrderIntent -> Capital Safety -> OMS -> DeterministicPaperExecutionBroker -> Fill/EventLedger -> Portfolio/Reconciliation`

Implementado:
1. adverse deterministic slippage;
2. partial fills explícitos;
3. LIMIT marketability después de slippage;
4. stale/future/crossed/spread deterministic rejection;
5. deterministic rejection = `REJECTED`, no falso `UNKNOWN`;
6. broker idempotency y cancel preservando fills;
7. inspectable simulated broker truth;
8. canonical durable portfolio + `ReconciliationEngine`;
9. hash-bound execution scenarios/matrix;
10. Research `ExecutionCostModel` -> W78 qualification contract;
11. scientific `measurement_hash` separado de runtime `evidence_hash`;
12. Execution Sensitivity Lab sobre múltiples escenarios independientes;
13. static no-network/no-writer boundary en workflow W78 y permanentemente en Core Safety.

Un run dedicado W78 posterior a las correcciones funcionales ya demostró compile + authority boundary + suite W78 PASS. La promoción canónica sólo se declara después de repetir esa evidencia en el head final junto con Core Safety y coverage >=85%.

## Deuda descubierta — bloquea R7D, no se oculta dentro de W78

Registrada machine-readable en `knowledge/00_CANON/debt_register_r7d_auto_paper.json`:

- `TD-R7D-001` P1 — continuidad total de costos Research -> PAPER: probar spread observado + slippage contra half-spread + slippage preregistrados;
- `TD-R7D-002` P1 — contabilidad fee-complete antes de profitability/Auto-Paper claims;
- `TD-R7D-003` P2 — reducir reservas después de partial fill sólo cuando quede probado el remaining broker-open quantity.

La reserva actual de partial fill es conservadora: puede sobre-reservar capital, pero no liberar capacidad de forma prematura.

## R7C — siguiente integración de producto

Después de cerrar W78:
- conectar la matriz de ejecución al Strategy Lab;
- consumir candidatos ya gobernados por datasets/provenance y TRAIN/VALIDATION/HOLDOUT;
- mostrar sensibilidad de ejecución junto a walk-forward/bootstrap, multiple testing, regimes, Health y shadow/forward;
- no convertir un buen fill simulado en promotion status por sí solo;
- mantener IA/model output sin autoridad directa de orden.

## R7D — Auto-Paper Runner

No iniciar autoridad automática sólo porque W78 pase CI.

Antes de promoción a Auto-Paper deben cerrar, como mínimo:
- los P1 R7D de costos/fees;
- estrategia promovida por evidencia R1–R5/R7C;
- shadow/forward suficiente;
- permisos deterministas y version-bound;
- max trade notional, gross/net exposure y concurrent positions;
- daily loss/drawdown/cooldown/trade count;
- data freshness + Health + kill switch;
- one-shot external writer + reconciliation;
- realized external PAPER behavior.

Toda futura entrada automática deberá cruzar:

`Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

## Negative tests permanentes

Conservar y ampliar:
- LIVE host/path;
- credential persistence;
- POST desde una capa de research/qualification;
- W78 importando red/writer/Alpaca authority;
- wrong account / stale position;
- duplicate POST / UNKNOWN retry;
- strategy version drift;
- Health / daily loss / drawdown breach;
- partial-fill reservation race;
- research execution assumptions debilitadas en PAPER;
- IA/model output intentando saltar Strategy Runtime/Safety/OMS.

## No-claims

- un canary exitoso prueba infraestructura, no rentabilidad;
- un W78 simulated fill prueba comportamiento determinista bajo supuestos, no ejecución Alpaca futura;
- W78 no es fee-complete profitability evidence;
- Strategy Lab no concede capital authority;
- Auto-Paper y LIVE son promociones separadas con evidencia propia.

**LIVE TRADING: BLOQUEADO.**
