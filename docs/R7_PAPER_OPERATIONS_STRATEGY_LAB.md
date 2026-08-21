# R7 — PAPER Operations + Strategy Lab + Auto-Paper Runner

Fecha de apertura: 2026-08-21
Base: `0cbb782015eeed200b9851b53764ac6389c3d9ff` (R6 first-canary exact-head certified)
Branch: `work/r7-paper-operations-strategy-lab`

## Por qué R7 empieza ahora

El primer canary BTC/USD compatible con el mínimo real de Alpaca quedó resuelto contra broker truth:

- `attempt_id=first-canary-57d01d35e8b25f4babc57695ac87d962`;
- un único POST de entrada;
- `broker_order_status=filled`;
- `broker_filled_quantity=0.00014432`;
- posición neta observada `0.000143959 BTC`;
- `entry_attempt_count=1`;
- recovery posterior exclusivamente GET;
- `retry_post=false`;
- `credentials_persisted=false`;
- `LIVE=BLOCKED`;
- lifecycle resultante `ENTRY_FILLED_UNPROTECTED`.

Esto demuestra el plumbing de entrada/recovery PAPER, no rentabilidad. La siguiente deuda operacional real es gestionar broker truth y exposición abierta de manera usable.

## Objetivo de producto

Convertir AUTO-TRADE de una ceremonia de primer canary en un **trading workbench PAPER completo**:

1. ver cuenta, posiciones, órdenes, fills, P&L y exposición desde Alpaca;
2. abrir, reducir, proteger y cerrar posiciones PAPER con reconciliación durable;
3. crear y comparar estrategias usando los motores R1–R5 ya certificados;
4. correr estrategias automáticamente en PAPER sólo después de promoción reproducible;
5. medir resultados reales PAPER y degradar/detener estrategias que pierdan Health;
6. mantener LIVE fuera de autoridad hasta una promoción posterior explícita y separada.

## Arquitectura R7

### A. Broker Truth / Portfolio Center

Fuente de verdad operacional: Alpaca PAPER por GET.

Debe mostrar:
- account/portfolio value/buying power;
- posiciones con `qty`, `qty_available`, entry, mark, market value, cost basis, unrealized P&L;
- órdenes abiertas y sus estados;
- fills/órdenes recientes en una siguiente iteración;
- broker truth vs estado local;
- lifecycle y cobertura/protección de cada exposición;
- alertas de `UNPROTECTED`, `UNKNOWN`, stale evidence y drift.

Primera implementación R7: `AlpacaPaperPortfolioGateway`, estrictamente GET-only.

### B. Trading Console PAPER

Operaciones previstas:
- abrir long PAPER;
- reducir una posición existente;
- cerrar 100%;
- crear protección;
- cancelar/reemplazar una orden abierta sólo mediante flujos explícitos y reconciliados.

Toda mutación debe conservar:
- PAPER host exacto;
- `UNKNOWN` durable antes de la I/O irreversible;
- un POST por attempt;
- jamás blind retry;
- reconciliación GET-only por `client_order_id` y posición;
- quantity ligada a broker truth fresco;
- Capital Safety + Portfolio + OMS antes del writer;
- credenciales sólo en memoria;
- kill switch global;
- LIVE deny.

Prioridad inmediata: **Close/Reduce PAPER** para la posición BTC real que quedó `ENTRY_FILLED_UNPROTECTED`.

### C. Strategy Lab

R1–R5 ya contienen los motores; R7 debe convertirlos en una experiencia usable.

Flujo:
`Idea -> definición -> dataset/provenance -> backtest -> walk-forward -> robustness -> holdout -> tournament -> shadow/forward -> PAPER candidate`.

El Lab debe permitir:
- crear estrategia desde plantillas y DSL;
- parámetros y espacios de búsqueda explícitos;
- costos, spread, slippage y latency;
- resultados por régimen;
- drawdown, Sharpe/Sortino, expectancy, hit rate, turnover, exposure;
- bootstrap/robustness y multiple-testing evidence;
- comparación lado a lado;
- versionado reproducible de estrategia/dataset/config;
- promoción/rechazo con evidencia.

El holdout final no se reutiliza para tuning.

### D. Research Agent / experimentación automática

La IA puede trabajar automáticamente en la capa de investigación:
- formular hipótesis;
- generar variantes dentro de primitivas permitidas;
- programar experimentos;
- resumir fallos y regímenes;
- proponer candidatos;
- recomendar retirar/degradar estrategias.

**La IA no recibe autoridad directa de broker.** Una salida textual/model-generated no puede convertirse directamente en POST.

Para llegar a PAPER una estrategia debe compilar a una versión determinista, ser reproducible y superar gates mecánicos.

### E. Auto-Paper Runner

Una estrategia promovida puede operar automáticamente en PAPER bajo un `PaperStrategyPermit` acotado.

El permit debe fijar como mínimo:
- `strategy_version` + fingerprint;
- universo/símbolos autorizados;
- side/order-types permitidos;
- max notional por trade;
- max gross/net exposure;
- max posiciones concurrentes;
- max pérdida diaria;
- max drawdown de sesión;
- cooldown y max trades/día;
- Health mínimo;
- freshness máxima de datos;
- expiración del permit;
- PAPER only;
- LIVE false.

El loop automático será:
`Market Data -> Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> durable UNKNOWN -> PAPER writer -> broker truth reconciliation -> Health/P&L`.

Cualquier `UNKNOWN`, stale data, reconciliation gap, kill switch, Health red o breach de pérdida detiene nuevas entradas.

## Escalera de promoción

1. `RESEARCH_CANDIDATE`
2. `BACKTEST_PASS`
3. `WALK_FORWARD_PASS`
4. `HOLDOUT_PASS`
5. `SHADOW_PASS`
6. `PAPER_PROBATION`
7. `PAPER_AUTOMATED`
8. futura `LIVE_CANDIDATE`

No existe salto directo de idea/IA a PAPER ni de PAPER a LIVE.

## Tramos de implementación acelerada

### R7A — Portfolio Truth + posición actual
- gateway GET-only de account/positions/open orders;
- UI Portfolio/Positions;
- detectar la BTC actual como long PAPER y `UNPROTECTED`;
- mostrar P&L/exposure y broker identifiers sin secretos.

### R7B — Reduce/Close + Protection
- close 100% y reduce parcial;
- protección STOP_LIMIT existente integrada a UI;
- one-shot exit attempts + reconciliation;
- resolver posición actual a protegido o `FLAT_RECONCILED`.

### R7C — Strategy Lab usable
- catálogo de estrategias;
- editor/config;
- backtest/walk-forward/holdout/tournament;
- reportes y comparador;
- promotion state visible.

### R7D — Auto-Paper Runner
- permisos por estrategia;
- scheduler/event loop;
- estrategia determinista -> OrderIntent;
- Safety/Portfolio/OMS;
- position sizing;
- entry/exit lifecycle;
- Health, kill switch, loss limits;
- observabilidad y replay.

### R7E — Paper probation
- correr días/semanas en PAPER;
- comparar expected vs realized slippage/fill/P&L;
- validar operational Health;
- no promover LIVE por calendario: sólo por evidencia.

## Principios que no se negocian

- PAPER no es LIVE.
- Un canary exitoso no es proof de edge.
- `UNKNOWN => reconciliation only`.
- no blind retry.
- no secretos persistidos.
- no LLM-to-order direct path.
- Capital Safety y OMS son deterministas y fail-closed.
- cada estrategia ejecutada debe tener versión y evidencia reproducible.
- exposición broker real prevalece sobre inferencias locales.

**LIVE TRADING: BLOQUEADO.**
