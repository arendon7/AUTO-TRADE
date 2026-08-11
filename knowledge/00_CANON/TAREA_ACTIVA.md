# TAREA ACTIVA — AUTO-TRADE

## Objetivo inmediato
**R5 — closed-kline read-only streaming + synchronized shadow + forward evidence.**

Base obligatoria: post-merge-green `main` `c294aa69f35b64559e3aea58a1c0661e66599db8`.
Branch activa: `reconstruction/r5-stream-shadow-forward`.

## Deuda registrada antes de programar
- `TD-R5-001` — closed-kline read-only streaming boundary.
- `TD-R5-002` — duplicate/order/gap integrity.
- `TD-R5-003` — DEGRADED lifecycle + reconnect safety.
- `TD-R5-004` — synchronized portfolio shadow integrity.
- `TD-R5-005` — forward evidence separation from FINAL_HOLDOUT.
- `TD-R5-006` — permanent execution-authority boundary.

## Orden de implementación
1. modelo de estado + contrato del stream closed-kline read-only;
2. duplicate idempotency, order/sequence/gap validation;
3. socket DEGRADED lifecycle y reconnect continuity gate;
4. synchronized portfolio shadow hash-bound;
5. forward evidence append-only separado de HOLDOUT;
6. authority scan + adversarial certification + debt closure.

## Negative tests obligatorios para R5
- stream deshabilitado por defecto no abre conexiones ni hace I/O;
- host/path/protocolo no permitido => reject antes de I/O;
- vela abierta, malformed, stale, futura o fuera de orden => fail closed;
- duplicado idéntico => idempotent no-op; duplicado conflictivo => fail closed;
- gap temporal/sequence gap => DEGRADED, sin imputación ni avance optimista;
- socket EOF/error/timeout/ambigüedad => DEGRADED;
- reconnect no puede borrar ni ocultar un gap no resuelto;
- shadow con weights/config/timestamp/source hash mismatch => reject;
- repeated identical shadow/forward evidence => idempotent, sin doble conteo;
- forward evidence no puede leer FINAL_HOLDOUT ni recalibrar thresholds/pesos congelados;
- stale/missing/gapped evidence no puede incrementar allocation/risk;
- ningún path R5 puede importar/invocar broker order submission, OMS authority o LIVE execution.

## Restricciones
- No bajar coverage gate de 85%.
- No borrar/relajar negative tests para cerrar deuda.
- `TD-OPS-001` permanece visible; no fabricar `graphify-out`.
- No declarar rentabilidad por infraestructura o forward observability.

## Capital
**LIVE TRADING: BLOQUEADO.**
External PAPER pertenece a R6, no a R5.
