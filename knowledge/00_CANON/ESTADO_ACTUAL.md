# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado canónico: **v0.28R reconstruction — R0–R4 CERTIFIED; R5 ACTIVE**

## Base certificada
R4 quedó integrado y post-merge recertificado en el SHA exacto de `main` `c294aa69f35b64559e3aea58a1c0661e66599db8`.
- Core Safety `31463746764`: PASS — **483 tests / 86.45% coverage**.
- Knowledge Contract `31463746745`: PASS.
- Contract Registry: 10 PASS.
- Research/Advisory Authority Boundary: PASS.
- Debt Register Contract: PASS.

El primer merge R4 `aa6d80dc...` falló recertificación por dos defectos de integración y fue reparado por PR #12. Evidencia: `knowledge/60_EVIDENCE/R4_POST_MERGE_INTEGRATION_AUDIT.json`.

## R5 activo
Branch: `reconstruction/r5-stream-shadow-forward`.
Base exacta: `c294aa69f35b64559e3aea58a1c0661e66599db8`.
Antes de implementar se registraron `TD-R5-001..006` como P1 OPEN.

Alcance:
- closed-kline read-only streaming;
- duplicate/idempotency + gap/out-of-order fail-closed;
- DEGRADED socket lifecycle sin reconnect que esconda gaps;
- synchronized portfolio shadow con frozen weights/config/timestamps;
- forward evidence post-activation separado de FINAL_HOLDOUT;
- CI permanente de no execution-authority creep.

## Deuda
- R5 P1 OPEN: **6** (`TD-R5-001..006`).
- `TD-OPS-001` Graphify P3/OPS: OPEN, no bloqueante.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER/LIVE authority en R5: NONE.
