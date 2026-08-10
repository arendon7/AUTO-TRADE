# ADR-0006 — Reconstruct v0.28 Equivalent Instead of Waiting for Lost Source

Status: Accepted
Date: 2026-08-10

## Context
El source histórico AUTO TRADING IA v0.28.0 no pudo recuperarse tras búsqueda en File Library, Google Drive, SharePoint/OneDrive y GitHub. El usuario confirma que tampoco dispone de una copia recuperable.

Existe, sin embargo, evidencia histórica suficiente para reconstruir los invariantes y capability tracks que fueron certificados entre v0.1 y v0.28. Continuar esperando el ZIP crea una deuda operativa mayor que rehacer el sistema de forma explícita y verificable.

## Decision
Se abandona la recuperación del árbol exacto como camino principal y se adopta una **reconstrucción equivalente, limpia y auditable**, denominada provisionalmente `v0.28R`.

No se intentará reproducir nombres internos, archivos o APIs perdidas. Se reconstruirán comportamientos, invariantes, contratos de seguridad y evidencia de certificación.

## Principles
1. **No hidden debt**: cada deuda o diferencia conocida se registra antes del merge; no se pospone silenciosamente.
2. **Capability equivalence over file equivalence**: importa reproducir garantías verificadas, no la forma exacta del código perdido.
3. **Fail closed by default**: SIMULATION por defecto; PAPER/LIVE y network capabilities disabled salvo activación explícita y gates.
4. **Safety cannot be traded for performance**: ningún threshold de riesgo se relaja para hacer pasar una estrategia.
5. **No HOLDOUT leakage**: tuning, portfolio construction, shadow y PAPER evidence no consumen el protected holdout.
6. **No blind broker retry** ante estados ambiguos.
7. **Automation may reduce risk, never autonomously increase/recover it**.
8. **Every milestone ships with tests + negative tests + contracts + docs + CI evidence**.
9. **Graphify + Obsidian + Git** forman el sistema de memoria; el grafo siempre se etiqueta con source SHA.
10. **LIVE remains blocked** hasta una promoción futura explícita separada de esta reconstrucción.

## Reconstruction tracks

### R0 — Canon + Foundation durable (already present)
Foundation v0.3 reconstruida: durable state, ledger, OMS, atomic reservations, persistent kill switch, reconciliation.

### R1 — Market Data + Strategy DSL + Research Engine
Recupera equivalentes históricos v0.3–v0.5:
- canonical market-data contract;
- safe Strategy DSL / deterministic strategy contract;
- BAR_CLOSE -> NEXT_BAR anti-look-ahead;
- fees/spread/slippage/latency;
- protected temporal splits;
- walk-forward, sample adequacy, cost stress, moving-block bootstrap;
- immutable experiment/validation registry.

### R2 — Capital Safety + OMS maturity
Equivalente histórico v0.10:
- fat-finger/price sanity;
- order/position/strategy/portfolio exposure;
- leverage, daily loss, drawdown and circuit gates;
- UNKNOWN/reconciliation semantics;
- PAPER/LIVE startup disabled/fail-closed;
- versioned JSON contracts.

### R3 — Real market data intake + research governance
Equivalente v0.15–v0.16:
- read-only Binance historical intake with fixed hosts/GET-only;
- malformed/ambiguous network data fail-closed;
- checksums/provenance;
- preregistration + Trial Ledger;
- Tournament;
- PBO/Deflated Sharpe where trial accounting supports it;
- Research Control Center read-only.

### R4 — Portfolio robustness + health
Equivalente v0.18–v0.20:
- correlation-aware portfolio research;
- chronological portfolio folds;
- allocation perturbation + leave-one-out;
- TRAIN-calibrated regimes;
- immutable health/drift profiles;
- asymmetric Defensive Health Bridge with human recovery.

### R5 — Streaming + shadow/forward evidence
Equivalente v0.22–v0.25:
- read-only closed-kline stream;
- duplicate idempotency;
- gap detection, no silent imputation;
- terminated stream -> DEGRADED;
- synchronized portfolio shadow with frozen weights;
- individual + portfolio forward evidence without HOLDOUT.

### R6 — External PAPER + broker-side protection
Equivalente v0.26–v0.28:
- explicit external PAPER gateway disabled by default;
- account/asset/reconciliation prerequisites;
- bounded canary notional;
- ambiguity fail-closed;
- PAPER evidence for terminality/fills/slippage/reconciliation;
- equity bracket parent + exactly two verified protective legs;
- PAPER `trade_updates` evidence when required;
- unsupported products fail closed;
- no LIVE endpoints in production path.

## Certification policy
Historical test counts (302 tests / 207 schemas in v0.28) are **reference evidence, not quotas**. v0.28R may use a different number of tests/contracts, but it must cover every recovered invariant with equal or stronger behavioral evidence.

Each reconstruction track closes only with:
- code complete;
- positive + negative tests;
- coverage gate not reduced;
- contract/schema validation;
- threat/failure-path review;
- canonical docs/handoff updated;
- no unresolved P0/P1 debt;
- explicit list of any P2+ debt;
- CI green on merge SHA.

## Naming
Until all tracks R0–R6 close, versions remain normal development milestones. `v0.28R` is reserved for the first release whose capability-equivalence matrix is fully PASS.

## Capital
**LIVE TRADING: BLOQUEADO.**
