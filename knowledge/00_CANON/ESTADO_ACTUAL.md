# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado canónico: **v0.28R reconstruction — R0–R5 CERTIFIED; R5 PR #13 integration pending**

## Base integrada
R4 está integrado y post-merge recertificado en `main` `c294aa69f35b64559e3aea58a1c0661e66599db8`.

## R5 certificado en branch
Branch: `reconstruction/r5-stream-shadow-forward`.
Certification basis: `0d4f75d083a055b83646bb861f08731aecace560`.
- Core Safety `31465755866`: PASS — **606 tests / 86.49% coverage**.
- Knowledge Contract `31465755855`: PASS.
- Contract Registry: 10 PASS — `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`.
- Research/Advisory Authority Boundary: PASS.
- R5 Stream/Shadow/Forward Authority Boundary: PASS.
- R5 P0/P1/P2 OPEN: **0**.

Capacidades:
- closed-kline market-data-only WSS stream, disabled by default and bounded;
- identical duplicate idempotency + conflicting duplicate/out-of-order/gap fail-closed;
- timeout/EOF/socket/integrity failure => sticky DEGRADED, no reconnect hiding gaps;
- synchronized research-only portfolio shadow with exact frozen weights/timestamps, recomputation and anchored hash chain;
- append-only post-activation forward evidence sourced from verified shadow and separated from FINAL_HOLDOUT;
- permanent CI execution-authority boundary.

Live transport evidence: `R5_LIVE_CLOSED_KLINE_STREAM_EVIDENCE.json`, run `31465471204`, BTCUSDT 1s from market-data-only endpoint.

## Integración pendiente
PR #13 must remain feature-frozen. After merge, recertify the exact resulting `main` SHA before creating R6.

## Deuda
`TD-OPS-001` Graphify P3/OPS remains OPEN and non-blocking.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER authority added by R5: NONE.
R5 certification is infrastructure/evidence integrity, not profitability proof.
