# ADR-0010 — Deterministic PAPER Execution Model Reuses the Existing Control Plane

- Status: **PROPOSED / W78 DRAFT**
- Date: 2026-08-23
- Scope: R7 Strategy Lab / future Auto-Paper qualification
- External capital authority: **NONE**
- Alpaca write authority: **NONE**
- LIVE: **BLOCKED**

## Context

R7 already contains multiple mature execution primitives inherited from R2–R6:

- Capital Safety Kernel;
- OMS durable order identity and idempotency;
- fill-level integrity and cumulative order state;
- cancel/replace lifecycle;
- durable portfolio projection and reservations;
- UNKNOWN/reconciliation semantics;
- product-specific Alpaca PAPER one-shot writers;
- R7 broker-truth Portfolio and risk-reducing close lifecycle.

An initial W78 design proposed new `order_manager`, `fill_handler`, `position_manager`, `portfolio_manager` and `reconciliation` components. Implementing those as a parallel stack would duplicate certified authority and create a realistic risk of two inconsistent order-control planes.

The existing `PaperBroker` is intentionally minimal: market orders fill at touch and marketable limits fill completely at touch. That is useful for unit tests, but insufficient as an execution stress model for Strategy Lab qualification because it cannot represent adverse slippage, partial fills or market-quality rejection.

## Decision

W78 SHALL NOT introduce a second OMS, portfolio manager or reconciliation engine.

W78 adds a new **no-network deterministic PAPER execution model** behind the existing `ExecutionBroker` protocol.

The canonical simulated path is:

`OrderIntent -> Capital Safety -> existing OMS -> DeterministicPaperExecutionBroker -> existing FillStore/EventLedger -> existing Portfolio projection`

External PAPER remains a separate product-specific path:

`OrderIntent -> Capital Safety -> existing OMS external handoff -> product writer -> broker truth reconciliation`

The two paths share deterministic control-plane semantics but do not share network authority.

## W78 execution assumptions

The first W78 model includes only assumptions that can be made deterministic from one current `MarketSnapshot` plus explicit configuration:

1. **adverse slippage** in basis points from current touch;
2. **bounded partial fill fraction**;
3. **stale/future snapshot rejection**;
4. **crossed quote rejection**;
5. **maximum spread gate**;
6. **limit marketability re-evaluated after slippage**;
7. **deterministic fill identity**;
8. **idempotent exact local order replay**;
9. **cancel preserving already observed fills**.

No random fills are used. Reproducibility is more important than producing superficially realistic noise.

## Explicit non-claims

This model is not a prediction of Alpaca execution quality.

It does not yet model:

- venue/order-book depth;
- queue priority;
- time-to-fill;
- probabilistic fill arrival;
- maker/taker fee schedules;
- crypto received-asset fee semantics;
- latency distributions;
- jump/gap chronology across multiple market snapshots;
- broker-specific minimum notional/precision (those remain ProductCapabilities concerns);
- protection lifecycle.

Therefore a Strategy Lab result using this model cannot by itself qualify a strategy for automated external PAPER.

## Safety boundary

`paper_execution.py` must remain free of:

- HTTP/socket/websocket imports;
- broker credentials;
- Alpaca host/path constants;
- external writer imports;
- environment-controlled write gates;
- LIVE flags or promotion logic.

A dedicated static CI boundary checker enforces this restriction.

Any future change that gives this module network or credential access requires a new ADR and re-review of the authority model; it may not be introduced as an incremental convenience.

## Why deterministic adverse assumptions

A simulator can create false edge if fills are optimistic. W78 therefore defaults to adverse slippage and rejects low-quality market snapshots rather than assuming executable liquidity.

Partial fills are explicit order states. The model never converts a partial fill into a full fill merely to simplify downstream P&L.

Limit orders are tested against the post-slippage price. A limit that looks marketable at raw touch but is not marketable after the configured adverse execution assumption remains open (`SUBMITTED`) rather than being granted a favorable fill.

## Relationship to Strategy Lab

Strategy Lab should use the W78 model as one execution scenario, not the only scenario.

Future qualification should evaluate a strategy across a matrix such as:

- baseline slippage;
- stressed slippage;
- partial-liquidity scenario;
- wider-spread scenario;
- delayed/no-fill limit scenario;
- jump/liquidity stress.

A promoted strategy must remain acceptable under preregistered stress assumptions and realistic fee/latency models. HOLDOUT remains protected.

## Consequences

Positive:

- no duplicated capital authority;
- existing R2 fill/OMS invariants receive additional exercise;
- deterministic reproducibility;
- Strategy Lab can test execution sensitivity before external PAPER;
- no new broker credentials or network attack surface.

Costs:

- more sophisticated time/volume/order-book simulation remains future work;
- fees need a separate explicit accounting extension because the current core `Fill` type carries quantity/price but no fee field;
- execution quality must eventually be calibrated against realized external PAPER evidence, not guessed.

## Required evidence before ADR acceptance

1. W78 tests PASS.
2. Static no-network authority boundary PASS.
3. Core Safety remains green and coverage floor is not reduced.
4. Existing R6/R7 authority gates remain green.
5. Documentation states that simulated execution is not profitability proof.

**LIVE TRADING: BLOQUEADO.**
