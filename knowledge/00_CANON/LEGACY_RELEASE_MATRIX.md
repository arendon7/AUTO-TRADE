# LEGACY RELEASE MATRIX — VERIFIED EVIDENCE

Fecha: 2026-08-10
Propósito: índice compacto de milestones históricamente verificados por reportes recuperados. Este archivo **no sustituye el source**.

| Release | Certificación recuperada | Capability track / evidencia clave |
|---|---:|---|
| v0.1 | 16 PASS | Foundation Safety Kernel deny-by-default, tamper-detect Event Ledger, PAPER/LIVE blocked, broker absent |
| v0.3 | 43 PASS | Strategy DSL Foundation; inherited Market Data Foundation; safe YAML/hash, BAR_CLOSE -> NEXT_BAR, mandatory initial stop, isolated HOLDOUT |
| v0.5 | 72 PASS | Validation & Robustness Foundation: sample adequacy, walk-forward, cost stress, moving-block bootstrap, immutable validation registry |
| v0.10 | 125 PASS / 77 schemas | Capital Safety & Paper Readiness: risk policy, fat-finger/price checks, exposure/loss/drawdown, circuit, OMS UNKNOWN reconciliation, PAPER/LIVE disabled |
| v0.15 | manifest evidence | Read-only Binance Spot historical intake, fixed market-data host/GET-only, malformed rows reject, audited checksums; external PAPER + ChatGPT research planes installed/disabled |
| v0.16 | 186 PASS / 124 schemas | Research Control Center + bounded real-data campaign; Trial accounting, Tournament, PBO/DSR chain, protected HOLDOUT retained |
| v0.18 | 201 PASS | Portfolio Robustness & Regime Lab: chronological folds, allocation perturbation, leave-one-out, TRAIN-calibrated regimes |
| v0.19 | 208 PASS | Strategy & Portfolio Health / Drift Monitor; validation-only baseline, immutable health profiles/reports, no capital action |
| v0.20 | 216 PASS | Defensive Health Bridge: automation may reduce/block risk, never increase/recover it automatically; human recovery required |
| v0.22 | 240 PASS | Read-only closed-kline WebSocket; duplicates idempotent, gaps fail-closed, socket termination -> DEGRADED, disabled default |
| v0.25 | 269 PASS | Synchronized Portfolio Shadow + portfolio Forward Evidence; frozen weights, exact timestamp synchronization, no imputation, no HOLDOUT |
| v0.26 | 279 PASS | External PAPER Canary; strict prerequisites, account/reconciliation gates, tighter notional ceilings, ambiguity fail-closed |
| v0.27 | 286 PASS / 200 contracts | External PAPER Evidence qualification: terminality, fills, slippage, reconciliation; explicitly `broker_side_protection_verified=false` |
| v0.28 | 302 PASS / 207 schemas | Broker-Side Protection Sandbox: equity bracket parent + exactly two protective legs + PAPER trade_updates evidence + Canary reconciliation |

## Release-chain invariants observed repeatedly
- Runtime defaults to SIMULATION / external capabilities disabled unless explicitly configured.
- HOLDOUT is isolated from iterative research and later operational evidence stages.
- PAPER evidence is not interpreted as profitability proof.
- Broker ambiguity and state mismatch fail closed.
- Automatic health/defensive mechanisms are asymmetric: may reduce/block risk, not increase it.
- LIVE startup/authority remains blocked throughout recovered evidence.

## Historical Strategy Lab evidence
Recovered reports show a preregistered 45-combination research universe across BTC/USDT, BTC/USDC, SOL/USDC, AVAX/USDC and DOGE/USDT; 1m/5m/15m and 30/90/180-day windows. Only a subset had usable local historical data, research gates blocked OOS/HOLDOUT promotion, and no strategy was certified as profitable/live-ready.

## Missing source
The exact v0.28 ZIP/tree remains unavailable. Use this matrix only for impact analysis and recovery planning; never generate exact module/API details solely from this table.

**LIVE TRADING: BLOQUEADO.**
