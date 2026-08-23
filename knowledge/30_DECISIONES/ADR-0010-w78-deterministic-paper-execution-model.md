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

### Deterministic rejection is not UNKNOWN

A no-network simulator can know that no external I/O occurred. Therefore market-quality failures that are known before simulated execution — stale quote, future quote, crossed market, or configured spread breach — resolve to terminal `REJECTED` with a deterministic broker reason code.

They MUST NOT be mislabeled `UNKNOWN` merely because the normal OMS treats arbitrary broker exceptions conservatively. `UNKNOWN` remains reserved for genuinely ambiguous execution state and requires reconciliation rather than qualification evidence.

Contract/programming errors such as an order/market symbol mismatch remain exceptions and fail closed.

## Scenario matrix and qualification continuity

A single fill assumption is insufficient for execution qualification. W78 therefore introduces hash-bound `PaperExecutionScenario` and `PaperExecutionScenarioMatrix` contracts.

A qualification matrix must contain:

- at least two distinct scenarios;
- at least one full-liquidity case;
- at least one stressed execution case;
- no scenario with configured slippage below the R1 research slippage assumption.

`PaperExecutionQualificationContract` binds the scenario matrix to the R1 `ExecutionCostModel` and records research fee, half-spread and slippage assumptions. The contract grants no external execution authority and always records LIVE as blocked.

Fees are not fabricated inside the W78 `Fill`. The canonical core `Fill` type currently carries quantity and price, not fee. Research fee assumptions therefore remain explicit accounting evidence until a separately reviewed fee-aware execution accounting extension exists.

## Execution Sensitivity Lab

`paper_execution_lab.py` evaluates the **same canonical `OrderIntent`, MarketSnapshot, PortfolioSnapshot and SafetyLimits** independently across every preregistered execution scenario.

Each scenario receives a fresh in-memory ledger, Capital Safety kernel, OMS and deterministic broker instance so scenario outcomes cannot contaminate each other through fills, orders, Safety state or idempotency state.

The lab records:

- RiskDecision outcome and reason;
- OMS order terminal/open status;
- deterministic broker rejection reason when applicable;
- fill ratio;
- adverse slippage versus touch;
- scenario measurement hash;
- trace evidence hash;
- aggregate full-fill / partial-fill / zero-fill / broker-rejection / risk-rejection counts;
- worst observed fill ratio and adverse slippage.

The report explicitly hard-codes:

- `external_execution_authorized = false`;
- `live_trading = BLOCKED`.

The lab is a qualification measurement surface, never an execution handoff.

## Scientific hash vs audit hash

W78 separates two identities that must not be conflated:

### Measurement hash

The measurement hash excludes runtime-random OMS identities such as generated order IDs. Repeating the same intent, market, portfolio, Safety limits and scenario assumptions must reproduce the same measurement hash.

This is the identity used to compare execution sensitivity scientifically.

### Trace/evidence hash

The trace hash includes concrete execution evidence such as the generated order identity. Two separate runs of the same scientific experiment therefore normally have different trace hashes even though their measurement hashes match.

This preserves both reproducibility and auditability.

## Durable integration and reconciliation

W78 reuses the existing durable R2 machinery rather than creating a simulator-specific portfolio book.

Tests exercise:

- SQLite fill persistence;
- partial-fill portfolio projection;
- reservation lifecycle;
- cancellation after partial fill without erasing filled exposure;
- terminal full-fill reservation release;
- `InspectableBroker` account truth;
- canonical `ReconciliationEngine` comparison of simulated broker state against durable portfolio state;
- `POSITION_MISMATCH` detection when local state is deliberately drifted.

The deterministic broker remains no-network while implementing the same inspectable broker contract needed by canonical reconciliation.

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

All W78 execution-qualification modules must remain free of:

- HTTP/socket/websocket imports;
- broker credentials;
- Alpaca host/path constants;
- external writer imports;
- environment-controlled write gates;
- LIVE flags or promotion logic;
- direct external OMS handoff calls.

A dedicated static CI boundary checker scans the broker, scenario, evidence, qualification and Sensitivity Lab modules.

Any future change that gives this layer network or credential access requires a new ADR and re-review of the authority model; it may not be introduced as an incremental convenience.

## Why deterministic adverse assumptions

A simulator can create false edge if fills are optimistic. W78 therefore defaults to adverse slippage and rejects low-quality market snapshots rather than assuming executable liquidity.

Partial fills are explicit order states. The model never converts a partial fill into a full fill merely to simplify downstream P&L.

Limit orders are tested against the post-slippage price. A limit that looks marketable at raw touch but is not marketable after the configured adverse execution assumption remains open (`SUBMITTED`) rather than being granted a favorable fill.

## Known debt discovered during W78

### 1. Partial-fill reservation remains intentionally conservative

The durable pipeline currently keeps the original OPEN risk reservation after a partial fill while also projecting the confirmed fill into the portfolio. This can temporarily reserve more capital than the true remaining unfilled quantity.

This behavior is **capital-safe but capital-inefficient**: it may block a valid subsequent opportunity, but it does not create hidden excess capacity.

W78 SHALL NOT weaken this reservation in the qualification wave. Before Auto-Paper can rely on repeated partial-fill activity, a separately tested change must bind reservation reduction to the **proven remaining broker-open quantity** without opening a race that releases capital before execution truth is known.

### 2. Research half-spread continuity is not yet fully proven

`PaperExecutionQualificationContract` currently proves that W78 configured slippage is not less adverse than R1 research slippage. W78 execution starts from observed bid/ask touch, whereas R1 research explicitly models `half_spread_bps`.

A narrow real/simulated snapshot can therefore have less observed half-spread than the research model assumed even though configured W78 slippage is equal or worse.

This is a **scientific qualification debt, not a capital-authority defect**. Before a strategy can use W78 evidence for Auto-Paper promotion, the qualification pipeline must prove that total effective price impact (observed spread + W78 slippage) does not silently weaken the preregistered research execution-cost assumption, or explicitly classify the run as a favorable-regime observation rather than a conservative qualification scenario.

### 3. Fee realization remains external to core Fill

Research fees are bound into qualification metadata but are not realized as W78 core Fill cash flows. No W78 P&L metric may claim fee-complete realized profitability until fee-aware accounting is implemented and reconciled.

## Relationship to Strategy Lab

Strategy Lab should use the W78 model as an execution-sensitivity layer, not as profitability proof.

Future qualification should evaluate a strategy across a preregistered matrix such as:

- baseline execution;
- stressed slippage;
- partial-liquidity scenario;
- wider-spread scenario;
- delayed/no-fill limit scenario;
- stale/freshness boundary;
- jump/liquidity stress when a chronological model exists.

A promoted strategy must remain acceptable under preregistered stress assumptions, realistic fee/latency accounting, protected HOLDOUT, shadow/forward evidence and realized external PAPER behavior.

## Consequences

Positive:

- no duplicated capital authority;
- existing R2 fill/OMS/reconciliation invariants receive additional exercise;
- deterministic reproducibility;
- separate scientific and audit identities;
- Strategy Lab can test execution sensitivity before external PAPER;
- no new broker credentials or network attack surface.

Costs:

- more sophisticated time/volume/order-book simulation remains future work;
- partial-fill reservations are conservatively over-reserved until a safe remaining-quantity design is certified;
- research half-spread continuity needs an explicit promotion gate;
- fees need a separate explicit accounting extension;
- execution quality must eventually be calibrated against realized external PAPER evidence, not guessed.

## Required evidence before ADR acceptance

1. W78 dedicated tests PASS, including Sensitivity Lab.
2. Static no-network/no-writer authority boundary PASS across all W78 modules.
3. Scientific measurement hashes reproduce while trace hashes preserve distinct run identity.
4. Durable partial-fill/cancel/reconciliation tests PASS.
5. Core Safety remains green and coverage floor is not reduced.
6. Existing R6/R7 authority gates remain green.
7. Documentation states that simulated execution is not profitability proof.
8. Known reservation/spread/fee qualification debts remain explicit and cannot be silently interpreted as closed.

**LIVE TRADING: BLOQUEADO.**
