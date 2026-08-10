# LEGACY v0.28 — HISTORICAL EVIDENCE

Fecha: 2026-08-10
Estado: source histórico no recuperado; evidencia preservada para reconstrucción v0.28R

## Propósito
El source histórico v0.28 se considera irrecuperable para el plan vigente. Este documento preserva invariantes y capacidades verificadas para la reconstrucción `v0.28R`; no define ya una espera por el ZIP.

## Último release históricamente certificado
**AUTO TRADING IA v0.28.0 — Broker-Side Protection Sandbox**

Certificación registrada:
- runtime: SIMULATION;
- 302/302 tests PASS;
- 207 JSON Schemas;
- capital real usado: $0;
- LIVE capital authority: NONE;
- Event Ledger válido;
- clean ZIP extraction + compile + health: PASS;
- PAPER/LIVE startup fail-closed.

### v0.28 broker-side protection
Camino histórico certificado:
`Safety-approved TradeIntent -> External PAPER Permit -> Alpaca PAPER asset check -> equity bracket -> nested parent/legs verification -> PAPER trade_updates evidence -> protected Canary reconciliation -> External PAPER Evidence`

Controles documentados:
- PAPER REST fijo `paper-api.alpaca.markets`;
- PAPER WebSocket fijo `wss://paper-api.alpaca.markets/stream`;
- credenciales solo por entorno;
- asset activo/tradable + symbol binding;
- bracket equity con whole-share quantity conservadora;
- stop/take-profit geometry validada antes del POST;
- permit ligado al perfil de orden;
- lookup-before-POST/idempotencia;
- parent `order_class=bracket` + exactamente dos protective legs válidas;
- `trade_updates` evidence cuando policy lo exige;
- structural mismatch puede escalar a `NO_NEW_RISK`;
- WebSocket inesperadamente terminado => `DEGRADED`;
- crypto bracket unsupported/fail-closed.

Regla histórica:
`Strategy DSL stop != broker-side protection` y `External PAPER submitted != broker-side protection verified`.

## Capas históricas verificadas

### Foundation / Market Data / Strategy DSL
- deny-by-default;
- Event Ledger tamper-detect;
- TRAIN/VALIDATION/HOLDOUT aislados;
- safe Strategy DSL, canonical hash, `BAR_CLOSE -> NEXT_BAR`, initial stop obligatorio y sin broker/network/risk authority.

### Research / Validation
- fast + canonical backtests;
- sample adequacy, cost stress, chronological walk-forward;
- moving-block bootstrap Monte Carlo;
- Experiment/Trial accounting + preregistration;
- PBO / Deflated Sharpe cuando el trial accounting era completo;
- protected Final HOLDOUT;
- Strategy Tournament;
- read-only Research Control Center.

### Real market evidence
- Binance Spot historical intake read-only, GET-only, fixed host, disabled by default;
- malformed rows y ambiguous network results fail-closed;
- closed-kline read-only stream; duplicates idempotent; gaps degrade; no silent imputation.

### Portfolio / robustness / forward evidence
- correlation-aware portfolio research;
- chronological robustness, allocation perturbation, leave-one-out y TRAIN-calibrated regimes;
- Strategy/Portfolio Health & Drift sin capital authority;
- Defensive Health Bridge asimétrico: puede reduce/block risk, nunca auto-increase/auto-recover;
- synchronized Portfolio Shadow con frozen weights;
- individual/portfolio Forward Evidence sin HOLDOUT.

### PAPER execution evidence
- Local Paper Sandbox;
- external Alpaca PAPER gateway disabled by default;
- bounded External PAPER Canary tras Forward Evidence + preflight;
- reconciliation antes de canary orders;
- no blind retries ante UNKNOWN;
- External PAPER Evidence para terminality, fills, slippage y reconciliation;
- v0.27 mantenía `broker_side_protection_verified=false` hasta v0.28.

## Historical Strategy Lab
Los reportes registraron un universo preregistrado de 45 combinaciones sobre BTC/USDT, BTC/USDC, SOL/USDC, AVAX/USDC y DOGE/USDT; 1m/5m/15m; 30/90/180 días. Solo una parte tenía datos locales utilizables y los gates bloquearon promoción OOS/HOLDOUT. No existe evidencia histórica válida de una estrategia ganadora/live-ready.

## Uso permitido
- derivar invariantes;
- diseñar tests de equivalencia;
- completar `RECONSTRUCTION_V028R_MATRIX.md`;
- comparar cobertura funcional.

## Uso prohibido
- inventar source/API exacto perdido;
- marcar una capacidad PASS solo porque aparezca aquí;
- usar PAPER histórico como autorización actual.

## Capital
**LIVE TRADING: BLOQUEADO.**
