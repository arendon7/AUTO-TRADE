# CONTEXTO RÁPIDO — AUTO-TRADE

Estado actual: **R0–R5 certified; R6 first real Alpaca PAPER canary broker-truth closed; R7 PAPER Operations activo; W78 execution qualification certificado; W79 Strategy Promotion Governance certificado; W80 Durable Promotion Assessment técnicamente certificado; W81 execution-cost continuity es el siguiente hito.**

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/debt_register.json`
5. `knowledge/00_CANON/debt_register_r7d_auto_paper.json`
6. `knowledge/30_DECISIONES/ADR-0010-w78-deterministic-paper-execution-model.md`
7. `knowledge/30_DECISIONES/ADR-0011-w79-strategy-promotion-governance.md`
8. `knowledge/30_DECISIONES/ADR-0012-w80-durable-promotion-assessment.md`
9. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`

## Hitos certificados
- R6 first-canary: `0cbb782015eeed200b9851b53764ac6389c3d9ff`;
- W78 execution qualification: `2924456e33c2cc9e6579301b176267513a90861f`;
- W79 behavioral implementation: `c5c264e64e931ef380801b1e0d1508ea2cac0dfa`;
- W80 behavioral implementation: `492ca4a621b263324b2cb5322490d74beda66a9c`.

## W80 en una frase
W80 convierte la evaluación W79 en un **journal científico append-only, hash-chained y policy-bound**, y Strategy Lab puede leer ese historial mediante un reader independiente `mode=ro/query_only` sin convertir un assessment en autoridad de trading.

### Evidencia W80
Dedicated `32671751555`:
- 46/46 W80 PASS;
- writer boundary PASS;
- independent-reader boundary PASS;
- Strategy Lab durable projection boundary PASS;
- W79/W78/Research/Mac boundaries PASS.

Core Safety `32671751544`:
- 2890/2890 PASS;
- exact coverage `85.1061367161277%` >= 85%;
- writer 82%;
- assessment reader 84%;
- Strategy Lab read-model 84%;
- Debt + Knowledge PASS.

## Strategy Lab después de W80
Sigue siendo:
- `/strategy-lab`;
- `GET /api/strategy-lab`;
- sin `SAFE_ACTIONS`;
- sin POST;
- sin credenciales;
- sin broker network;
- sin `OrderIntent` execution path;
- `PAPER candidate=false`;
- `CAPITAL=NONE`;
- `LIVE=BLOCKED`.

Muestra dos provenance domains separados:
1. **W79 governance**: thresholds + frozen candidate y `gate_evidence_state=NOT_PERSISTED_BY_W79`;
2. **W80 assessments**: durable receipt history, gates/reasons/evidence hashes y provenance propio, o `NO_DURABLE_W80_ASSESSMENT`.

`EVIDENCE_QUALIFIED` sigue significando sólo evidencia científica completa para los gates implementados; **no** significa PAPER candidate.

## Broker truth real sigue separado
First canary observado:
- BTC/USD BUY LIMIT IOC PAPER;
- exactamente un POST de entrada;
- broker `filled`;
- fill bruto `0.00014432 BTC`;
- posición neta GET `0.000143959 BTC`;
- `RECOVERED_GET_ONLY`;
- no retry POST;
- credenciales no persistidas;
- LIVE bloqueado.

PR #49 conserva de forma separada la obligación real risk-reducing sobre esa exposición. W78/W79/W80 no poseen ese writer.

## W81 — siguiente hito
Cerrar `TD-R7D-001` / `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN`.

Debe demostrar, sin elegir supuestos ex post, que la qualification no usa **price impact** menos adverso que Research:

`Research half-spread + slippage -> observed quote spread + W78 adverse slippage -> compatible PAPER evidence`

Si una observación es más favorable que el supuesto preregistrado debe quedar marcada como favorable/no-conservadora o compensada por un escenario preregistrado suficientemente adverso; nunca debe pasar como conservative por interpretación.

## Blockers posteriores a W80
- `TD-R7D-001` / `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN` — W81;
- `TD-R7D-002` / `FEE_ACCOUNTING_INCOMPLETE` — P1 separado;
- `TD-R7D-003` partial-fill remaining-quantity reservation — P2;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `EXECUTION_STRATEGY_VERSION_UNBOUND`.

## No-claims
- broker canary != strategy edge;
- W78 simulated execution != future broker fill;
- W79/W80 evidence != PAPER candidate;
- W80 hash-chain != externally signed transparency log;
- W81 price-impact continuity != fee-complete profitability;
- PAPER != LIVE.

**LIVE TRADING: BLOQUEADO.**