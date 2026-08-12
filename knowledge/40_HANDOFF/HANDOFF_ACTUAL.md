# HANDOFF ACTUAL — AUTO-TRADE

Fecha: 2026-08-11
Estado: **R0–R5 certified; R6 structurally closed / PRE-FIRST-CANARY**

## Base
`main` `75dcbef65b061f742745ba7be0665521967e0587` = exact post-R5 green base.
Branch activa: `reconstruction/r6-external-paper-protection`.
PR #14: DRAFT, no merge.

Último checkpoint de código triple-certificado:
`b0419c682a1af2907cbb559610fe021c93467859`
- Core Safety `31556266622` PASS — **1292 tests / 85.18180897396941% coverage**.
- R6 Authority `31556266743` PASS.
- Knowledge Contract `31556266619` PASS.
- Debt register PASS — 52 total / 7 OPEN / 6 blocking.

## Deuda
CLOSED structurally: `TD-R6-007..013`.

OPEN/blocking — requieren evidencia externa real:
- `TD-R6-001` PAPER account/environment;
- `TD-R6-002` real submit ambiguity/idempotency/reconciliation;
- `TD-R6-003` bounded real PAPER canary;
- `TD-R6-004` fills/terminality/slippage/qualification;
- `TD-R6-005` broker nested equity bracket;
- `TD-R6-006` authenticated PAPER trade_updates.

OPEN/nonblocking:
- `TD-OPS-001` Graphify semantic/deep evidence.

## R6 safety architecture implemented
- PAPER-only exact host + permanent LIVE deny;
- deterministic client_order_id + durable submit binding;
- durable UNKNOWN before POST and no blind retry;
- GET-only reconciliation;
- bounded one-shot canary permit;
- US equity LIMIT/DAY bracket protection;
- authenticated PAPER trade_updates receive-only stream;
- qualification evaluator;
- OMS-owned external handoff;
- PreparedPackage with full RiskDecision/market/Safety binding;
- durable short-lived human decision;
- execution bridge consumes human decision before OMS staging;
- writer has PRE_CONSUME + UNKNOWN + PRE_IO ordering;
- same-attempt crash resume only;
- same-core operational provenance;
- separate prepare / human / execute / evidence commands;
- read-only readiness inspector;
- no AI/research authority in capital path.

## First-canary account/market protections
A first canary may not assume the PAPER account is empty.

Implemented:
1. account preflight — exact `GET /v2/account`;
2. flat-account preflight — exact two GETs:
   - `/v2/positions`;
   - `/v2/orders?status=open&limit=500&direction=asc&nested=true`;
3. IEX market-data GET preflight.

Flat evidence:
- binds exact account fingerprint + credential reference;
- stores no secret;
- requires 0 positions + 0 open orders;
- max age 30s through every pre-execution phase;
- stale/dirty/missing => fail closed;
- execution runtime independently rechecks it before creating writable stores or consuming operator authority.

## Mac operator experience
Safe entry point:
```bash
bash scripts/mac_start.sh
```

Already usable without credentials/broker I/O:
```bash
bash scripts/mac_start.sh rehearsal
bash scripts/mac_start.sh init-workspace "$HOME/AUTO-TRADE-R6/workspace-001"
bash scripts/mac_start.sh readiness "$HOME/AUTO-TRADE-R6/workspace-001"
```

Safe Start exposes no order execution command and forces external write DISABLED.

With Alpaca PAPER credentials, separate GET-only commands exist for account, flat-account and market preflight. Execution remains outside Safe Start and requires a separate final human decision.

## Important semantic separation
R6 connectivity/protection canary ≠ profitable strategy.

R3/R5 certify research/governance/forward mechanisms, not a profitable US-equity strategy ready for R6. Do not create synthetic Strategy Health or manually fabricate an APPROVED RiskDecision merely to get to POST.

Next implementation:
- a Mac **candidate → Capital Safety rehearsal**, local-only;
- operator may describe a candidate but only Capital Safety Kernel may create the RiskDecision;
- no external execution authority;
- then design the correct semantic bridge for either a connectivity canary or a genuinely research-promoted US-equity strategy.

## Non-claims / Capital
- external PAPER order sent: **0**;
- capital authority: **NONE**;
- PAPER is not profitability proof;
- connectivity canary is not strategy edge;
- LIVE remains outside v0.28R.

**LIVE TRADING: BLOQUEADO.**
