# ADR-0004 — Research Integrity, Anti-Look-Ahead and Protected Holdout

Status: Accepted / extended by ADR-0006
Date: 2026-08-10
Track: v0.28R / R1

## Context
AUTO-TRADE necesita buscar edge sin confundir performance histórica con rentabilidad futura. R1 reconstruye las garantías históricas de Market Data, Strategy DSL y Validation/Robustness sin habilitar capital.

Los riesgos principales son look-ahead, costos omitidos, fills imposibles, parameter mining, HOLDOUT contamination, insuficiencia de muestra y evidence mutable.

## Decision
Research permanece aislado del plano de ejecución y adopta contratos explícitos contra esas fuentes de autoengaño.

### Canonical market data
- instrument metadata + timezone-aware OHLCV bars;
- strict ordering/uniqueness;
- explicit gaps;
- reproducible dataset hash including provenance/source.

### Safe declarative Strategy DSL
R1 incorpora un `StrategySpec` declarativo restringido:
- JSON object only; no Python eval/import/callable/module path;
- strict allowlist de fields/parameters;
- canonical hash independiente del orden de keys;
- `initial_stop_pct` obligatorio como research metadata;
- produce únicamente `ResearchSignal`;
- sin broker, OMS, network o risk-policy authority.

`initial_stop_pct` **no** equivale a broker-side protection. Esa capacidad pertenece a R6.

### Anti-look-ahead + latency assumption
Una estrategia ve history solo hasta current close. La señal debe usar exactamente el timestamp del current-bar close y `execution_delay_bars >= 1`; por tanto no puede ejecutarse en la misma barra que la originó.

En R1, latency se modela de forma conservadora a granularidad de barras mediante `execution_delay_bars`. No se reclama microstructure/sub-bar latency realism todavía.

### Costs/capacity
- fee, half-spread y slippage explícitos;
- total zero cost requiere opt-in deliberado;
- max leverage + max volume participation;
- no future executable volume => no optimistic fill.

### Temporal splits / HOLDOUT
Train, development y protected holdout son cronológicos. El holdout requiere un permit durable de un solo uso y propósito `final_validation`. Esto evita tuning normal/repetitivo; no pretende ser una barrera criptográfica frente a un desarrollador malicioso.

### Walk-forward + sample adequacy
- rolling/expanding chronological folds;
- distinct evaluation datasets;
- minimum bars/fills/unique days;
- rejected-signal fraction y gap count explícitos;
- thresholds configurables por policy.

### Moving-block bootstrap
R1 usa contiguous-block resampling con block size, iterations y seed explícitos para preservar dependencia serial local mejor que IID resampling. Inputs no finitos o returns <= -1 fallan cerrado.

### Experiment + validation evidence
- experiment fingerprint = dataset + strategy/version/parameters + config + code version;
- same experiment spec + another result hash => reproducibility conflict;
- validation evidence tiene registry durable separado, append-only-by-fingerprint;
- same validation spec + different evidence => conflict, no overwrite.

## Scope limits
- single-symbol, bar-based research engine;
- direct bps spread/slippage, no dynamic order-book impact;
- deterministic bar-delay latency, no sub-bar queue model;
- no funding/borrow/corporate-actions/futures-roll model yet;
- safe DSL deliberadamente estrecho en R1.

Estos son límites explícitos de scope, no deuda oculta ni claims de realismo inexistente.

## Failure-path review
`knowledge/20_ARQUITECTURA/R1_RESEARCH_FAILURE_PATHS.md` documenta leakage, corrupted data, optimistic fills, HOLDOUT misuse, mutable evidence, unsafe configuration y límites de amenaza. No hay P0/P1 R1 conocido al cierre del head certificado.

## Certification evidence
Head funcional R1 antes de merge:
- **161 tests PASS**;
- **90.34% total coverage** con gate mínimo 85% intacto;
- research coverage: bootstrap 100%, DSL 96%, gates 96%, market 100%, validation 94%, splits 95%, strategy 100%, backtest 95%;
- compile PASS;
- Knowledge Contract PASS;
- Core Safety Tests PASS.

El conteo histórico v0.28 no se usa como cuota; estas pruebas cubren los invariants R1 reconstruidos.

## Capital
Research no autoriza ejecución.

**LIVE TRADING: BLOQUEADO.**
