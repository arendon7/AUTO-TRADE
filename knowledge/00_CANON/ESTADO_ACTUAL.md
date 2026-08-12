# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-12
Estado canónico: **v0.28R reconstruction — R0–R5 CERTIFIED; R6 PRE-FIRST-CANARY / MAC GUIDED UAT**

## Base
R5 quedó integrado y post-merge recertificado en exact `main` `75dcbef65b061f742745ba7be0665521967e0587`.

R6 sigue sin merge sobre `reconstruction/r6-external-paper-protection`. La experiencia Mac se desarrolla en:
- branch `work/r6-mac-control-center`;
- PR #29 DRAFT;
- último checkpoint UX completamente certificado antes de esta sincronización de canon: `2c2e5ebef9bf01f13cf9bed477e670ba79fa2b29`.

Certificación de ese checkpoint:
- Core Safety `31649170421`: PASS;
- Knowledge Contract `31649170446`: PASS;
- Mac Rehearsal Artifact `31649170429`: PASS;
- R6 PAPER Authority `31649170448`: PASS;
- Mac FULL Standalone `31649170468`: PASS;
- FULL boot/install/Control Center/self-heal: PASS en arm64 y x86_64.

## R6 implementado estructuralmente
- exact Alpaca PAPER account gateway;
- durable client_order_id/idempotency + UNKNOWN/reconciliation semantics;
- bounded PAPER canary gate and one-shot permit;
- US-equity bracket builder + nested-bracket validation;
- PAPER `trade_updates` receive-only protection evidence;
- qualification evaluator;
- durable human-only execution decision;
- OMS-owned `VALIDATED -> SUBMITTING` external handoff;
- crash-safe same-attempt resume;
- operational workspace + same-core provenance;
- separate preparation / execution / evidence-capture surfaces;
- manual single-shot execution runtime, disabled by default;
- local/read-only readiness inspector;
- Mac FULL standalone dual-arch with embedded CPython 3.12.13;
- safe installer relocation outside Downloads/quarantine;
- localhost-only browser Control Center;
- account, asset, flat-account and IEX GET-only preflights;
- connectivity candidate produced through real Capital Safety Kernel + OMS;
- offline deterministic preparation/review surfaces.

## Mac guided UAT — cambio 2026-08-12
La prueba real del paquete anterior mostró que la seguridad técnica funcionaba, pero la UI permitía pulsar acciones fuera de orden y exponía tracebacks de implementación al operador.

Corregido en PR #29:
- onboarding explícito `Empieza aquí`;
- `1 · Probar la app sin broker`: workspace -> Doctor -> Capital Safety rehearsal;
- `2 · Conectar Alpaca PAPER`: Account -> Asset -> Flat Account -> Market IEX;
- acciones posteriores bloqueadas hasta cumplir el gate anterior;
- errores frecuentes traducidos a explicación operacional y traceback relegado a diagnóstico técnico;
- separación visible entre:
  - ensayo local;
  - Alpaca PAPER read-only;
  - futura experiencia `Strategy Lab`;
- TradingView no es requisito de R6; cualquier futura integración será visual/advisory y no podrá saltar Safety/OMS/reconciliation.

## Qué prueba R6 y qué no
R6 valida infraestructura de broker, account/asset/market evidence, Capital Safety, OMS, bracket preparation, authority boundaries y futuro canary PAPER.

R6 **no** demuestra edge/rentabilidad. R1–R5 ya certifican motores de research/backtest/holdout/walk-forward/shadow/forward/Health, pero todavía falta integrarlos en una experiencia Mac de estrategia usable. Ese `Strategy Lab` queda como siguiente capa de producto después de cerrar la UAT guiada R6, no como bypass del proceso de promoción.

## Deuda R6
CLOSED structurally: `TD-R6-007..013`.

OPEN/blocking — sólo evidencia PAPER externa real puede cerrarlas:
- `TD-R6-001` — account/environment attestation evidence;
- `TD-R6-002` — real submit ambiguity/idempotency/reconciliation evidence;
- `TD-R6-003` — bounded real PAPER canary evidence;
- `TD-R6-004` — terminality/fills/slippage/qualification evidence;
- `TD-R6-005` — broker-side nested US-equity bracket evidence;
- `TD-R6-006` — authenticated PAPER `trade_updates` evidence.

OPEN/nonblocking:
- `TD-OPS-001` Graphify P3/OPS.

## Próximo gate
1. usar el nuevo FULL guiado en el Mac real;
2. completar UAT local sin broker;
3. completar, si el operador lo decide, los cuatro GET-only de Alpaca PAPER en orden;
4. corregir cualquier defecto de UX/operación que aparezca;
5. sólo después decidir la primera canary PAPER real mediante la ceremonia separada ya certificada;
6. después de estabilizar R6, abrir la integración `Strategy Lab` de R1–R5 en la app Mac.

## Capital y no-claims
- External PAPER order enviado por el proyecto: **0**.
- Capital authority desde Control Center: **NONE**.
- PAPER evidence no es profitability proof.
- Una connectivity canary no es una estrategia rentable.
- LIVE permanece fuera de R6/v0.28R.

**LIVE TRADING: BLOQUEADO.**
