# TAREA ACTIVA — AUTO-TRADE

## Objetivo inmediato
**R6 — external Alpaca PAPER gateway + bounded canary + protection/qualification evidence.**

Base obligatoria: post-R5-green `main` `75dcbef65b061f742745ba7be0665521967e0587`.
Branch activa: `reconstruction/r6-external-paper-protection`.

## Deuda registrada antes de programar
- `TD-R6-001` — exact PAPER gateway/environment attestation.
- `TD-R6-002` — submit ambiguity + durable client_order_id idempotency/reconciliation.
- `TD-R6-003` — bounded PAPER canary prerequisites/cap.
- `TD-R6-004` — PAPER terminality/fills/slippage/reconciliation qualification evidence.
- `TD-R6-005` — broker-side equity bracket protection.
- `TD-R6-006` — PAPER trade_updates protection evidence.
- `TD-R6-007` — unsupported products/protection modes fail closed.
- `TD-R6-008` — permanent PAPER-only/LIVE-deny authority boundary.
- `TD-R6-010` — **CLOSED** — OMS-owned external PAPER handoff certified; no direct OrderStore status mutation.

## Orden de implementación
1. PAPER gateway policy + account/environment attestation, **without submit path enabled**;
2. durable submit intent/client_order_id state machine + ambiguous transport reconciliation;
3. deterministic canary preflight and tighter notional cap;
4. equity bracket request/response protection validation;
5. PAPER trade_updates ingestion/correlation;
6. qualification evidence store and reconciliation proofs;
7. OMS-owned external PAPER handoff: durable `VALIDATED -> SUBMITTING` without internal-broker I/O or direct store mutation;
8. bounded external PAPER evidence only after all prior gates are green; `TD-R6-010` is certified CLOSED;
9. adversarial certification + debt closure.

## Negative tests obligatorios para R6
- gateway disabled by default => zero network and zero order submission;
- exact LIVE host, arbitrary host, redirect, credentials in URL, unsafe proxy or path/method not allowlisted => reject before I/O;
- missing/wrong paper credentials or account/environment attestation => no write;
- `trading_blocked`, stale/unknown account or malformed account state => no write;
- no deterministic Safety/OMS approval => gateway cannot submit;
- direct R6 mutation of OMS order status to `SUBMITTING`, or staging without fresh control-plane identity, => fail closed;
- same local order + same client_order_id => idempotent; same client_order_id + changed payload => conflict;
- timeout/connection reset/ambiguous submit => UNKNOWN + GET by client_order_id/reconciliation; no blind POST retry;
- restart during UNKNOWN preserves ambiguity and blocks new exposure until resolved;
- canary prerequisites missing/stale/DEGRADED/UNKNOWN => blocked;
- canary notional at exact boundary allowed only when all other gates pass; one quantum above rejected; no auto-upsize to venue minimum;
- missing/partial/contradictory terminal/fill/slippage/reconciliation evidence => PAPER qualification FAIL;
- bracket equity parent must have exactly one take-profit and one stop-loss; missing/extra/crossed/side/qty/price/TIF/extended-hours mismatch => reject;
- broker nested response must prove exactly two coherent protection legs before bracket is considered protected;
- crypto/unknown product bracket, OCO or OTO => fail closed; R6 crypto remains simple-order only;
- PAPER `trade_updates` wrong host/auth, malformed/binary decode failure, event gap/order mismatch/disconnect ambiguity => protection evidence FAIL;
- no R6 source may contain/use LIVE trading host or convert PAPER qualification into LIVE authority;
- AI/research output is never an execution authorization; Safety + OMS remain mandatory deterministic authority.

## Restricciones
- Coverage gate >=85% intacto.
- No reduce/relax negative tests to close R6.
- No external PAPER submit until gateway + ambiguity + canary + authority gates are implemented and green, and `TD-R6-010` proves the OMS-owned external handoff.
- Any real PAPER test must be explicitly enabled, bounded and evidenced; no unbounded loops or broad market activity.
- `TD-OPS-001` remains visible; never fabricate Graphify.
- No profitability claim from paper simulation.

## Capital
**LIVE TRADING: BLOQUEADO.**
R6 may authorize bounded PAPER only after separate certification; LIVE remains outside v0.28R.
