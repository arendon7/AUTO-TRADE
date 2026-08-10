# AGENTS.md — AUTO-TRADE

## Mission
Build a profitable, testable and capital-safe algorithmic trading system. Optimize sustainable net returns subject to hard risk constraints; never optimize raw return by weakening safety.

## Authority model
- ChatGPT/AI: research, coding, analysis, strategy hypotheses, diagnostics, documentation.
- Deterministic components: validation, sizing limits, permissions, execution gates, reconciliation, kill switches/circuits.
- Human operator: promotion between research -> backtest -> paper -> limited live -> scaled live.

No AI-generated output is itself an executable trading authorization.

## Source-of-truth rule
The historical v0.28 source is considered unavailable. The active program is **v0.28R capability reconstruction** under ADR-0006.

Precedence:
1. executable code/config/tests/contracts in current `main`;
2. `RECONSTRUCTION_V028R_MATRIX.md` + current ADRs;
3. `DEBT_REGISTER.md` for unresolved technical debt;
4. historical v0.1–v0.28 reports as invariant evidence only;
5. fresh Graphify artifacts when `SOURCE_SHA` matches;
6. Obsidian/Markdown canon and handoff;
7. conversational memory last.

Never invent missing historical source/APIs from reports. Rebuild capabilities with current tests/evidence instead.

## Mandatory startup sequence
1. Read `knowledge/00_CANON/SOURCE_OF_TRUTH.md`.
2. Read `knowledge/00_CANON/ESTADO_ACTUAL.md`.
3. Read `knowledge/00_CANON/TAREA_ACTIVA.md`.
4. Read `knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md`.
5. Read `knowledge/00_CANON/DEBT_REGISTER.md`.
6. Read latest relevant ADR and `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`.
7. If `graphify-out/graph.json` exists and `SOURCE_SHA` matches the relevant tree, query Graphify before broad source reads.
8. Inspect only impacted files and graph/source neighbors.

## Mandatory close sequence
1. Run relevant positive/negative tests and safety contracts.
2. Record evidence, scope boundaries and unresolved risks.
3. Update `DEBT_REGISTER.md`: close fixed debt, add newly discovered debt, never hide severity.
4. Update matrix status only from verified evidence.
5. Update `ESTADO_ACTUAL.md` and `TAREA_ACTIVA.md`.
6. Add/modify ADR when durable architecture changes.
7. Update `HANDOFF_ACTUAL.md`.
8. Regenerate/stamp Graphify when a compatible runtime is available.
9. Recertify the merge SHA for a track close.

## Debt discipline
- A track cannot be `PASS` with a known P0/P1 assigned to that track.
- Do not downgrade severity to satisfy a milestone.
- Planned future-track capabilities are not hidden debt, but cannot be claimed complete early.
- Coverage gaps in capital-sensitive failure branches must be treated as evidence debt, not masked by a high global percentage.
- No TODO/FIXME may bypass OMS, Safety Kernel, reconciliation, HOLDOUT or broker/network restrictions.

## Non-negotiable capital controls
- Fail closed on missing/invalid/stale market data, broker ambiguity, reconciliation mismatch, stale portfolio/safety state or risk-engine error.
- Enforce max order notional, max position, max strategy exposure, max portfolio exposure, max leverage, max daily loss and max drawdown/circuit policy.
- Reject duplicate/idempotency-conflicting orders and duplicate fills.
- Reject prices/quantities outside instrument precision and configured sanity bands.
- No naked order path bypassing OMS and Capital Safety Kernel.
- Ambiguous broker I/O is potential risk until reconciled; never blind-retry.
- Emergency/defensive controls may reduce or block risk; automatic processes may not autonomously increase risk or clear a stricter restriction.
- Promotion to PAPER/LIVE requires the relevant future track and explicit approval; v0.28R reconstruction itself is not promotion.

## Research integrity
- No look-ahead leakage.
- Protected HOLDOUT is not used for iterative tuning.
- Include fees, spread, slippage and explicit latency assumptions.
- Account for every preregistered trial when applying multiple-testing controls.
- Prefer diversified independent/reasonably independent edges over one fragile strategy.
- Track OOS behavior, turnover, capacity, drawdown, regime sensitivity and forward evidence.
- A Strategy DSL stop is not broker-side protection.

## Change discipline
- Small reversible changes with explicit state migrations when needed.
- Every trading-sensitive change needs failure-path tests.
- Never alter risk thresholds merely to make a strategy/test pass.
- Never downgrade a historically verified invariant without explicit ADR and stronger evidence.
- Runtime code/config is authoritative for current behavior; docs must not overclaim it.

## Capital status
**LIVE TRADING: BLOQUEADO.**
