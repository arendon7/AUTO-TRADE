# LEGACY v0.28 — RECUPERACIÓN CANÓNICA

Fecha de recuperación: 2026-08-10
Estado: evidencia histórica recuperada; source package pendiente

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

### v0.28: protección broker-side
Camino certificado:
`Safety-approved TradeIntent -> External PAPER Permit -> Alpaca PAPER asset check -> equity bracket -> nested parent/legs verification -> PAPER trade_updates evidence -> protected Canary reconciliation -> External PAPER Evidence`

Controles documentados:
- host PAPER REST fijo `paper-api.alpaca.markets`;
- WebSocket PAPER fijo `wss://paper-api.alpaca.markets/stream`;
- credenciales solo por entorno;
- asset activo/tradable + binding de símbolo;
- bracket de equity con cantidad conservadora whole-share;
- geometría stop/take-profit validada antes de POST;
- permit ligado criptográficamente al perfil de orden;
- lookup-before-POST/idempotencia;
- parent `order_class=bracket` y exactamente dos legs protectoras válidas;
- evidencia `trade_updates` requerida cuando la política lo exige;
- mismatch estructural puede escalar a `NO_NEW_RISK`;
- WebSocket inesperadamente terminado => `DEGRADED`, sin auto-reconnect;
- crypto bracket explícitamente unsupported/fail-closed.

Regla histórica de veracidad:
`Strategy DSL stop != broker-side protection` y `External PAPER submitted != broker-side protection verified`.

## Capas históricas verificadas por reportes previos

### Foundation / Market Data / Strategy DSL
- Foundation deny-by-default y broker ausente inicialmente.
- Event Ledger tamper-detect e inmutabilidad.
- TRAIN/VALIDATION/HOLDOUT con aislamiento y checksums.
- Strategy DSL safe YAML, hash canónico, `BAR_CLOSE -> NEXT_BAR`, stop inicial obligatorio y sin autoridad de broker/network/risk-policy.

### Research / Validation
- fast + canonical backtests y reconciliación entre motores;
- sample adequacy, cost stress, chronological walk-forward;
- moving-block bootstrap Monte Carlo;
- Experiment/Trial accounting y preregistration;
- PBO / Deflated Sharpe cuando existió trial accounting completo;
- protected Final HOLDOUT;
- Strategy Tournament;
- read-only Research Control Center.

### Real market evidence
- intake histórico Binance Spot read-only, GET-only, host controlado, disabled by default;
- malformed rows y resultados de red ambiguos fail-closed;
- stream read-only de closed klines, duplicados idempotentes, gaps degradan y no se imputan silenciosamente.

### Portfolio / robustness / forward evidence
- portfolio research + correlation constraints;
- chronological portfolio robustness, allocation perturbation, leave-one-out y regimes calibrados en TRAIN;
- Strategy/Portfolio Health & Drift sin autoridad de capital;
- Defensive Health Bridge asimétrico: automatización puede reducir/bloquear riesgo, nunca aumentarlo ni auto-recuperarlo;
- synchronized Portfolio Shadow con pesos congelados;
- individual y portfolio Forward Evidence posteriores a activación, sin HOLDOUT.

### PAPER execution evidence
- Local Paper Sandbox;
- external Alpaca PAPER gateway instalado/disabled by default;
- External PAPER Canary bounded tras Forward Evidence y preflight;
- reconciliation antes de cada canary order;
- no blind retries ante UNKNOWN;
- External PAPER Evidence mide terminalidad, fills, slippage y reconciliación;
- v0.27 decía honestamente `broker_side_protection_verified=false` hasta que v0.28 añadió la verificación de bracket/legs/trade_updates.

## Evidencia de estrategia/datos históricos recuperada
Los reportes Strategy Lab documentan un universo preregistrado de 45 combinaciones sobre BTC/USDT, BTC/USDC, SOL/USDC, AVAX/USDC y DOGE/USDT; timeframes 1m/5m/15m y ventanas 30/90/180 días. Solo una parte de las combinaciones tenía datos locales utilizables y las policies bloquearon promoción OOS/HOLDOUT. No debe reinterpretarse ese resultado como edge probado.

## Lo que NO se ha recuperado aún
- ZIP/source tree v0.28;
- SHA256 exacto del ZIP;
- historial Git original, si existió;
- archivos completos de contracts/tests/módulos del package.

No reconstruir esos detalles inventándolos desde reportes. Usar `RECOVER_LEGACY_V028.md` cuando aparezca el paquete.

## Capital
**LIVE TRADING: BLOQUEADO.**
