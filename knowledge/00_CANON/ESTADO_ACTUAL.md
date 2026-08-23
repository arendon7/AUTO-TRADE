# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-23
Estado canónico: **R0–R5 CERTIFIED; R6 FIRST PAPER CANARY BROKER-TRUTH CLOSED; R7 PAPER OPERATIONS ACTIVE; W78/R7C EXECUTION QUALIFICATION DRAFT.**

## R6 — hito real alcanzado
El primer canary crypto PAPER compatible con el mínimo real de Alpaca terminó reconciliado por verdad del broker.

Evidencia de operador:
- `attempt_id=first-canary-57d01d35e8b25f4babc57695ac87d962`;
- `broker_order_id=2fa68a01-7408-48c5-b3c2-0b387987d2b6`;
- `client_order_id=atr6c-entry-72c2b075ad9c96a71108664a5919dca3be326109`;
- broker status `filled`;
- broker fill `0.00014432 BTC`;
- net broker position observada `0.000143959 BTC`;
- reconciliation `ORDER_PLUS_POSITION`;
- `entry_attempt_count=1`;
- `phase=RECOVERED_GET_ONLY`;
- `retry_post=false`;
- `credentials_persisted=false`;
- `LIVE=BLOCKED`.

No hubo segundo POST durante recovery. La rotación de PAPER key quedó validada sólo después de same-account GET proof y el lookup de posición usa la semántica real `BTCUSD` de Alpaca.

Exact-head R6 first-canary: `0cbb782015eeed200b9851b53764ac6389c3d9ff`.
Ese hito demostró plumbing de entrada/recovery, no edge ni rentabilidad.

## R7 PAPER Operations

### R7A — Portfolio Truth
R7 ya incorporó broker-truth read models para account, posiciones y órdenes abiertas. Esta capa es GET/read-only y no concede broker write authority.

### R7B — risk-reducing close
PR #49 (`work/r7-paper-close-mac-staging`) contiene la superficie estructural de close PAPER risk-reducing sobre la exposición BTC existente.

Invariantes:
- PAPER sólo;
- posición real broker-bound;
- SELL risk-reducing;
- Capital Safety + OMS reconstruidos frescos antes de ejecución;
- human review sin capital authority;
- durable UNKNOWN antes del único POST;
- exactamente un POST por attempt;
- ambigüedad/burned attempt => GET-only reconciliation;
- residual exposure => stop, nunca segundo SELL automático;
- credenciales memory-only;
- LIVE bloqueado.

PR #49 sigue DRAFT hasta que su gate runtime real alcance la verdad terminal exigida. Un CI verde por sí solo no cierra esa obligación.

## R7C / W78 — Strategy Lab execution qualification

Branch: `work/w78-realistic-paper-execution`.
PR #50, stacked exactamente sobre R7 close base `1a81a5e7c856064cc170a66e24e87928711eda21`.

W78 corrige una decisión arquitectónica importante: **no crea un segundo OMS, portfolio manager ni reconciliation engine**. Reutiliza R2/R6/R7:

`OrderIntent -> Capital Safety -> OMS -> deterministic no-network broker -> Fill/EventLedger -> Portfolio/Reconciliation`

Implementado:
- deterministic adverse slippage;
- bounded partial fills;
- LIMIT fill/no-fill después de slippage;
- stale/future/crossed/spread deterministic rejection;
- deterministic rejection = `REJECTED`, no falso `UNKNOWN`;
- local broker idempotency;
- cancel preserving observed fills;
- simulated inspectable broker truth;
- canonical durable portfolio + reconciliation;
- hash-bound execution scenarios y matrices;
- Research cost-model qualification contract;
- scientific `measurement_hash` separado de runtime trace `evidence_hash`;
- Execution Sensitivity Lab multi-scenario;
- W78 static no-network/no-writer boundary;
- boundary W78 incorporado además al Core Safety permanente.

La suite dedicada W78 ya obtuvo un PASS completo en el head de código posterior a la corrección de los vectores LIMIT. La promoción del estado canónico espera la repetición exact-head final junto con Core Safety, coverage >=85%, knowledge/debt contracts y gates heredados.

## Deuda R7D / Auto-Paper ya registrada

Machine-readable: `knowledge/00_CANON/debt_register_r7d_auto_paper.json`.

- `TD-R7D-001` P1 — total execution-cost continuity Research -> PAPER;
- `TD-R7D-002` P1 — fee-complete execution accounting antes de profitability/Auto-Paper claims;
- `TD-R7D-003` P2 — remaining-quantity reservation segura después de partial fills.

La reserva actual tras partial fill es conservadora: puede sobre-reservar, pero no libera capacidad prematuramente.

## Qué significa y qué no

AUTO-TRADE ya tiene infraestructura real PAPER y una capa avanzada de research/qualification, pero eso todavía **no equivale a una estrategia ganadora**.

Para hablar de promoción de estrategia se requiere evidencia acumulativa:
- datasets/provenance;
- backtest con costos;
- walk-forward/bootstrap;
- TRAIN/VALIDATION/HOLDOUT;
- multiple testing/tournament;
- regime/portfolio/Health;
- shadow/forward;
- W78 execution sensitivity;
- fee-complete accounting;
- PAPER realized behavior.

## Modelo de automatización
La IA puede generar hipótesis, variantes, experimentos y análisis. No puede tener un camino directo a POST.

Una estrategia que opere automáticamente deberá estar:
- versionada/fingerprinted;
- reproducible;
- promovida por gates research/forward;
- acotada por permiso determinista;
- evaluada por Portfolio + Capital Safety;
- stageada por OMS;
- enviada por writer PAPER one-shot;
- reconciliada contra broker truth;
- vigilada por Health/kill switch/loss limits.

## Negative tests permanentes
Mantener y ampliar:
- acciones fuera de orden;
- stale/tampered evidence;
- wrong account / credential drift;
- unsupported asset;
- over-notional / loss / drawdown / Health / kill switch;
- broker ambiguity / UNKNOWN restart;
- reconciliation mismatch;
- duplicate POST;
- direct writer access;
- qualification/research intentando importar writer/red;
- estrategia/model output intentando saltar Strategy Runtime/Safety/OMS;
- cualquier intento LIVE.

## Capital
Existe exposición PAPER derivada del first canary y PR #49 mantiene su tratamiento como una obligación operacional separada. W78 no envía órdenes externas y no puede alterar esa exposición.

**LIVE TRADING: BLOQUEADO.**
