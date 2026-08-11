# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado canónico: **v0.28R reconstruction — R0–R5 CERTIFIED; R6 ACTIVE**

## Base certificada
R5 quedó integrado y post-merge recertificado en exact `main` `75dcbef65b061f742745ba7be0665521967e0587`.
- Core Safety `31466198629`: PASS — **606 tests / 86.49% coverage**.
- Knowledge Contract `31466198624`: PASS.
- Contract Registry: 10 PASS.
- Research/Advisory Authority Boundary: PASS.
- R5 Stream/Shadow/Forward Authority Boundary: PASS.
- Debt Register Contract: PASS.

## R6 activo
Branch: `reconstruction/r6-external-paper-protection`.
Base exacta: `75dcbef65b061f742745ba7be0665521967e0587`.
Antes de implementar se registraron `TD-R6-001..008`, todas P1 OPEN.

Alcance:
- exact Alpaca PAPER gateway + paper environment attestation;
- durable client_order_id/idempotency + UNKNOWN/reconciliation semantics;
- tightly bounded external PAPER canary;
- PAPER terminality/fill/slippage/reconciliation qualification evidence;
- broker-side equity bracket protection;
- PAPER trade_updates protection evidence;
- unsupported products fail closed;
- permanent PAPER-only/LIVE-deny authority boundary.

## Deuda
- R6 P1 OPEN: **8** (`TD-R6-001..008`).
- `TD-OPS-001` Graphify P3/OPS: OPEN, non-blocking.

## Capital
**LIVE TRADING: BLOQUEADO.**
R6 is PAPER-only. Paper simulation results are not profitability proof and cannot promote LIVE authority.
