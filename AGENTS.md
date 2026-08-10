# AGENTS.md — AUTO-TRADE

## Mission
Build a profitable, testable and capital-safe algorithmic trading system. Optimize for sustainable net returns subject to hard risk constraints; never optimize raw return by weakening safety.

## Authority model
- ChatGPT/AI: research, coding, analysis, strategy hypotheses, diagnostics, documentation.
- Deterministic components: validation, sizing limits, permissions, execution gates, reconciliation, kill switches.
- Human operator: promotion between research -> backtest -> paper -> limited live -> scaled live.

No AI-generated output is itself an executable trading authorization.

## Mandatory startup sequence
1. Read `knowledge/00_CANON/CONTEXTO_RAPIDO.md`.
2. Read `knowledge/00_CANON/ESTADO_ACTUAL.md`.
3. Read `knowledge/00_CANON/TAREA_ACTIVA.md`.
4. Read latest relevant ADR and handoff.
5. If `graphify-out/graph.json` exists, query Graphify before broad source reads.
6. Inspect only impacted files and their graph neighbors.

## Mandatory close sequence
1. Run relevant tests and safety contracts.
2. Record evidence and unresolved risks.
3. Update `ESTADO_ACTUAL.md` and `TAREA_ACTIVA.md`.
4. Add/modify ADR if a durable architectural decision changed.
5. Update `HANDOFF_ACTUAL.md`.
6. Regenerate Graphify incrementally.

## Non-negotiable capital controls
- Fail closed on missing/invalid market data, stale prices, broker ambiguity, reconciliation mismatch or risk-engine error.
- Enforce max order notional, max position, max strategy exposure, max portfolio exposure, max leverage, max daily loss and max drawdown.
- Reject duplicate/idempotency-conflicting orders.
- Reject prices/quantities outside instrument precision and configured sanity bands.
- No naked order path bypassing OMS and Capital Safety Kernel.
- Emergency kill switch must cancel eligible open orders and block new risk.
- Promotion to live requires explicit human approval and passing gates.

## Research integrity
- No look-ahead leakage.
- Protected holdout is not used for iterative tuning.
- Include fees, spread, slippage, latency assumptions and realistic fills.
- Prefer portfolio of independent/reasonably diversified edges over one fragile strategy.
- Track out-of-sample behavior, turnover, capacity, drawdown and regime sensitivity.

## Change discipline
- Small reversible changes.
- Every trading-sensitive change needs tests.
- Never alter risk thresholds merely to make a strategy pass.
- Memory/docs can describe limits but executable configuration/code is authoritative.
