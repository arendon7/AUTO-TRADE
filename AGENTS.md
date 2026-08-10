# AGENTS.md — AUTO-TRADE

## Mission
Build a profitable, testable and capital-safe algorithmic trading system. Optimize for sustainable net returns subject to hard risk constraints; never optimize raw return by weakening safety.

## Authority model
- ChatGPT/AI: research, coding, analysis, strategy hypotheses, diagnostics, documentation.
- Deterministic components: validation, sizing limits, permissions, execution gates, reconciliation, kill switches.
- Human operator: promotion between research -> backtest -> paper -> limited live -> scaled live.

No AI-generated output is itself an executable trading authorization.

## Source-of-truth rule
Before planning or coding, read `knowledge/00_CANON/SOURCE_OF_TRUTH.md`.

Historical v0.28 evidence proves a more mature implementation existed than the currently imported GitHub tree. Until that source is recovered and recertified:
- current `main` is an executable fallback, not proof that later historical modules never existed;
- PR #4 is a fallback reconstruction and must not be merged as the canonical evolution path;
- do not recreate a historically certified capability merely because it is absent from current `main`;
- never invent historical source details from validation reports.

## Mandatory startup sequence
1. Read `knowledge/00_CANON/SOURCE_OF_TRUTH.md`.
2. Read `knowledge/00_CANON/CONTEXTO_RAPIDO.md`.
3. Read `knowledge/00_CANON/ESTADO_ACTUAL.md`.
4. Read `knowledge/00_CANON/TAREA_ACTIVA.md`.
5. Read `knowledge/00_CANON/LEGACY_V028_RECOVERY.md` while source recovery is active.
6. Read latest relevant ADR and `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`.
7. If `graphify-out/graph.json` exists and matches the working tree, query Graphify before broad source reads.
8. Inspect only impacted files and their graph neighbors.

## Mandatory close sequence
1. Run relevant tests and safety contracts.
2. Record evidence and unresolved risks.
3. Update `ESTADO_ACTUAL.md` and `TAREA_ACTIVA.md`.
4. Add/modify ADR if a durable architectural decision changed.
5. Update `HANDOFF_ACTUAL.md`.
6. Regenerate Graphify incrementally/deep when the runtime is available.
7. Verify source-of-truth precedence did not regress.

## Non-negotiable capital controls
- Fail closed on missing/invalid market data, stale prices, broker ambiguity, reconciliation mismatch or risk-engine error.
- Enforce max order notional, max position, max strategy exposure, max portfolio exposure, max leverage, max daily loss and max drawdown.
- Reject duplicate/idempotency-conflicting orders.
- Reject prices/quantities outside instrument precision and configured sanity bands.
- No naked order path bypassing OMS and Capital Safety Kernel.
- Emergency controls must block new risk and handle eligible open-order protection/cancellation according to the certified broker contract.
- Promotion to live requires explicit human approval and passing gates.
- Automatic processes may reduce/block risk; they may not autonomously increase risk or clear a defensive restriction.

## Research integrity
- No look-ahead leakage.
- Protected holdout is not used for iterative tuning.
- Include fees, spread, slippage, latency assumptions and realistic fills.
- Account for every preregistered trial when applying multiple-testing controls.
- Prefer portfolio of independent/reasonably diversified edges over one fragile strategy.
- Track out-of-sample behavior, turnover, capacity, drawdown, regime sensitivity and forward evidence.

## Change discipline
- Small reversible changes.
- Every trading-sensitive change needs tests.
- Never alter risk thresholds merely to make a strategy pass.
- Never downgrade a historically verified safety invariant without explicit ADR and stronger evidence.
- Memory/docs can describe limits but executable configuration/code is authoritative once the corresponding source tree is recovered.
