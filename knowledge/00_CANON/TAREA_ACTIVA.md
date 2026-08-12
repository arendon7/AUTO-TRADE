# TAREA ACTIVA — AUTO-TRADE

## Objetivo inmediato
**R6 — dejar el sistema listo para ensayos seguros en Mac y preparar correctamente el primer external Alpaca PAPER canary, sin convertir una prueba de conectividad en una falsa prueba de rentabilidad.**

Base obligatoria R0–R5: post-R5-green `main` `75dcbef65b061f742745ba7be0665521967e0587`.
Branch activa: `reconstruction/r6-external-paper-protection`.
PR #14: DRAFT, sin merge.
Último checkpoint de código triple-certificado: `b0419c682a1af2907cbb559610fe021c93467859`.
- Core Safety `31556266622`: PASS — **1292 tests / 85.18180897396941% coverage**.
- R6 Authority `31556266743`: PASS.
- Knowledge Contract `31556266619`: PASS.

## Estado de deuda
CLOSED estructuralmente:
- `TD-R6-007` — unsupported products/protection modes fail closed.
- `TD-R6-008` — permanent PAPER-only/LIVE-deny.
- `TD-R6-009` — final Safety/OMS PRE_CONSUME + PRE_IO recheck.
- `TD-R6-010` — OMS-owned external PAPER handoff.
- `TD-R6-011` — durable explicit human one-shot execution authority.
- `TD-R6-012` — crash-safe same-attempt resume; UNKNOWN remains reconciliation-only.
- `TD-R6-013` — durable operational external PAPER lifecycle, same-core provenance, separate preparation/execution/evidence capture and manual launcher.

OPEN/blocking — sólo evidencia real externa puede cerrarlas:
- `TD-R6-001` — exact PAPER account/environment attestation evidence.
- `TD-R6-002` — real submit ambiguity + durable idempotency/reconciliation evidence.
- `TD-R6-003` — bounded real PAPER canary evidence.
- `TD-R6-004` — terminality/fills/slippage/reconciliation qualification evidence.
- `TD-R6-005` — broker-side nested US-equity bracket protection evidence.
- `TD-R6-006` — authenticated PAPER `trade_updates` protection evidence.

OPEN nonblocking:
- `TD-OPS-001` — Graphify semantic/deep evidence. Nunca fabricar output.

## Lo ya ensayable en Mac sin riesgo de órdenes
1. `bash scripts/mac_start.sh` — bootstrap seguro + Doctor.
2. `bash scripts/mac_start.sh rehearsal` — rehearsal local sin credenciales ni broker I/O.
3. `bash scripts/mac_start.sh init-workspace "$HOME/AUTO-TRADE-R6/workspace-001"` — workspace privado fuera del repo.
4. `bash scripts/mac_start.sh readiness <WORKSPACE>` — inspector local/read-only.
5. Con credenciales exclusivamente PAPER y opt-in explícito:
   - account preflight `GET /v2/account`;
   - flat-account preflight: exactamente `GET /v2/positions` + `GET /v2/orders?status=open...`;
   - IEX market-data GET.

Todos esos caminos mantienen:
- external write gate DISABLED;
- no order POST;
- capital authority NONE;
- LIVE BLOCKED.

## Flat-account gate del primer canary
La cuenta PAPER no puede asumirse vacía sólo por `GET /v2/account`.

La evidencia de flat-account:
- queda ligada a la account attestation persistida y al credential reference;
- usa exactamente dos GET auditados;
- requiere 0 posiciones y 0 órdenes abiertas para el primer canary;
- se persiste sanitizada incluso cuando la cuenta no está limpia;
- dura máximo 30 segundos en cualquier fase que todavía pueda conducir a ejecución;
- si falta, está sucia o está vencida, readiness bloquea;
- el execution runtime vuelve a comprobarla antes de crear stores writable, consumir la decisión humana o llamar al Execution Bridge.

## Siguiente implementación
### A. Mac candidate → Capital Safety rehearsal
Construir un comando **estrictamente local/offline** para que el operador pueda ensayar cómo un `OrderIntent` candidato atraviesa el Capital Safety Kernel.

Reglas:
- el CLI puede describir un candidato acotado, pero no construir manualmente un `RiskDecision(status=APPROVED)`;
- `CapitalSafetyKernel.evaluate(...)` debe ser quien produzca el RiskDecision;
- no broker network;
- no writer;
- no Execution Bridge;
- no operator decision;
- no external PAPER authority;
- salida explícita: rehearsal/simulation, `capital_authority=NONE`, `external_execution_authorized=false`, `profitability_claim=false`, LIVE BLOCKED;
- negative tests para stale market, reconciliation false, unknown broker state, excessive notional/exposure, kill switch y unsupported symbol/order type.

### B. No confundir conectividad con estrategia
El primer external PAPER canary R6 puede probar infraestructura/broker/protection, pero **no debe presentarse como estrategia rentable**.

La promoción de una estrategia autónoma deberá seguir dependiendo de evidence separado de research/backtest/holdout/shadow/forward + Health. R3/R5 certifican mecanismos y datos, no una estrategia US-equity rentable para R6.

No crear Health sintético ni falsificar `core.sqlite3` para hacer avanzar readiness.

## Después del Safety rehearsal
Diseñar el puente semántico correcto hacia la preparación offline real:
- si se trata de un connectivity canary, darle un authority/policy explícito y auditado que no suplante Strategy Health;
- si se trata de trading de estrategia, exigir una estrategia US-equity realmente promovida por research/forward/Health;
- en ambos casos, usar Capital Safety Kernel + OMS reales y detenerse otra vez antes de cualquier POST.

## Restricciones permanentes
- Coverage real >=85%, fail-closed.
- No relajar negative tests/boundaries para avanzar.
- No fabricar `RiskDecision`, Health, Portfolio, DBs o artifacts manualmente para saltar gates.
- No enviar external PAPER order sin una decisión final separada y explícita del operador.
- Cualquier `UNKNOWN` => reconciliation-only; nunca POST retry ciego.
- US equity bracket only.
- PAPER evidence no es profitability proof y no promueve LIVE.

## Capital
External PAPER order enviado: **0**.
Capital authority actual: **NONE**.
**LIVE TRADING: BLOQUEADO.**
