# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado: **R5 post-merge certified; R6 active**

## Base certificada
`main` `75dcbef65b061f742745ba7be0665521967e0587` is exact post-R5 green base.
- Core Safety `31466198629` PASS — 606 tests / 86.49%.
- Knowledge Contract `31466198624` PASS.
- Contract Registry / Research Authority / R5 Authority / Debt Register PASS.

## R6
Branch: `reconstruction/r6-external-paper-protection`.
Blocking debt registered before implementation: `TD-R6-001..008`, all P1 OPEN.

Order:
1. exact PAPER-only gateway/environment attestation without submit enabled;
2. durable client_order_id + ambiguity/reconciliation state machine;
3. bounded canary preflight/cap;
4. equity bracket protection validation;
5. PAPER trade_updates evidence;
6. terminality/fill/slippage/reconciliation qualification;
7. bounded external PAPER evidence only after prior gates pass;
8. adversarial certification.

## R6 external facts locked from official Alpaca docs
- PAPER Trading API uses `paper-api.alpaca.markets` and separate paper credentials from LIVE.
- client_order_id can identify/retrieve an order and is required for safe retry reconciliation.
- bracket class is supported for equities; crypto order class is simple only.
- PAPER `trade_updates` is available on the PAPER trading WebSocket and uses binary frames.

## Inherited invariants
- Safety + OMS mandatory and deterministic.
- Kill switch/reconciliation/UNKNOWN semantics remain fail-closed.
- No stale/missing/ambiguous evidence increases exposure.
- PAPER simulation is not profitability proof.
- LIVE host/authority remains prohibited.

## Deuda no bloqueante
`TD-OPS-001` Graphify P3/OPS remains OPEN.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER is still disabled until R6 gates and certification explicitly permit a bounded canary.
