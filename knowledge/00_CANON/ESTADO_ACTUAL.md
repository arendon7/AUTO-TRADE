# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado canónico: **v0.28R reconstruction — R0–R5 CERTIFIED; R6 ACTIVE / PRE-FIRST-CANARY**

## Base certificada
R5 quedó integrado y post-merge recertificado en exact `main` `75dcbef65b061f742745ba7be0665521967e0587`.

## Checkpoint estructural R6 más reciente
Branch: `reconstruction/r6-external-paper-protection`.
PR #14: DRAFT, no merge.
Checkpoint exacto certificado: `b0419c682a1af2907cbb559610fe021c93467859`.

- Core Safety `31556266622`: PASS — **1292 tests / 85.18180897396941% coverage**.
- R6 PAPER Authority `31556266743`: PASS.
- Knowledge Contract `31556266619`: PASS.
- Contract Registry: 10 PASS.
- Research/Advisory Authority Boundary: PASS.
- R5 Stream/Shadow/Forward Authority Boundary: PASS.
- R6 permanent LIVE-deny: PASS.
- R6 flat-account first-canary boundary: PASS.
- R6 IEX market-data boundary: PASS.
- R6 OMS handoff / human decision / Execution Bridge / writer gate: PASS.
- R6 operational lifecycle / same-core provenance / execution / readiness: PASS.
- Mac bootstrap/rehearsal/Safe Start/workspace boundaries: PASS.
- Debt Register: 52 items, 7 OPEN total, 6 blocking.

## R6 implementado estructuralmente
- exact Alpaca PAPER account gateway;
- durable client_order_id/idempotency + UNKNOWN/reconciliation semantics;
- bounded PAPER canary gate and one-shot permit;
- US-equity bracket builder + broker nested-bracket validation;
- PAPER `trade_updates` receive-only protection evidence;
- qualification evaluator;
- durable human-only execution decision;
- OMS-owned `VALIDATED -> SUBMITTING` external handoff;
- crash-safe same-attempt resume;
- operational workspace + same-core provenance;
- separate preparation / execution / evidence-capture surfaces;
- manual single-shot execution runtime, disabled by default;
- local/read-only readiness inspector;
- Mac bootstrap, doctor, rehearsal, Safe Start and private workspace initializer;
- explicit account GET, flat-account two-GET and IEX market-data GET preflights;
- first-canary flat-account evidence is account-bound, must prove 0 positions + 0 open orders and is fresh for all pre-execution phases;
- execution runtime independently rechecks fresh clean flat-account evidence before creating writable stores or consuming human authority.

## Deuda R6
CLOSED structurally: `TD-R6-007..013` (including `TD-R6-009`).

Still OPEN/blocking and requiring **real external PAPER evidence**, never mocks:
- `TD-R6-001` — account/environment attestation evidence;
- `TD-R6-002` — real submit ambiguity/idempotency/reconciliation evidence;
- `TD-R6-003` — bounded real PAPER canary evidence;
- `TD-R6-004` — terminality/fills/slippage/qualification evidence;
- `TD-R6-005` — broker-side nested equity bracket evidence;
- `TD-R6-006` — authenticated PAPER `trade_updates` evidence.

Nonblocking:
- `TD-OPS-001` Graphify P3/OPS — OPEN. Never fabricate semantic/deep output.

## Mac trial readiness
Ya se puede ensayar de forma segura:
1. bootstrap/rehearsal local sin credenciales y sin broker I/O;
2. crear workspace privado fuera del repo;
3. readiness local/read-only;
4. con credenciales PAPER y decisión explícita: account GET;
5. flat-account GET positions + GET open orders;
6. IEX market-data GET.

Todavía no se debe fabricar manualmente `RiskDecision`, Health, Portfolio o `core.sqlite3` para forzar preparación. El siguiente bloque es un launcher de candidatura/prueba que produzca cualquier `RiskDecision` exclusivamente mediante Capital Safety Kernel y preserve la separación entre **canary de infraestructura** y **estrategia con edge validado**.

## Capital y no-claims
- External PAPER order enviado por el proyecto: **0**.
- Capital authority: **NONE**.
- PAPER evidence no es profitability proof.
- Una prueba de conectividad PAPER no es una estrategia rentable.
- La promoción de una estrategia seguirá requiriendo research/backtest/holdout/shadow/forward independientes de R6.

**LIVE TRADING: BLOQUEADO.**
