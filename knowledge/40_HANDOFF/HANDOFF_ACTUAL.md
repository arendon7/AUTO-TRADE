# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado: **R4 post-merge certified; R5 active**

## Base certificada
`main` `c294aa69f35b64559e3aea58a1c0661e66599db8` es la base exacta verde después de reparar la primera integración R4 mediante PR #12.
- Core Safety `31463746764`: PASS — 483 tests / 86.45%.
- Knowledge Contract `31463746745`: PASS.
- Contract Registry / Research Authority / Debt Register: PASS.

Incidente y reparación: `knowledge/60_EVIDENCE/R4_POST_MERGE_INTEGRATION_AUDIT.json`.

## R5
Branch: `reconstruction/r5-stream-shadow-forward`.
Deuda registrada antes de implementar: `TD-R5-001..006`, todas P1 OPEN.

Orden:
1. closed-kline read-only stream;
2. duplicate/gap/order integrity;
3. DEGRADED socket/reconnect semantics;
4. synchronized hash-bound portfolio shadow;
5. append-only forward evidence separado de FINAL_HOLDOUT;
6. permanent authority gate + adversarial certification.

## Invariantes heredados
- Safety + OMS son autoridad determinista obligatoria.
- Portfolio Manager sigue advisory-only.
- R5 nunca aumenta riesgo por evidencia stale/missing/gapped.
- No silent imputation.
- HOLDOUT no se reutiliza para recalibrar forward decisions.
- External PAPER/LIVE queda fuera de R5.

## Deuda no bloqueante
`TD-OPS-001` Graphify P3/OPS sigue OPEN; no fabricar artefactos semánticos/deep.

## Capital
**LIVE TRADING: BLOQUEADO.**
