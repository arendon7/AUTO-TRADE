# OSS-2H — Single-use FINAL_HOLDOUT evaluation

## Purpose

OSS-2H is the first OSS-2 boundary that may consume protected `FINAL_HOLDOUT` material. Its sole scientific purpose is to judge the exact candidate frozen by OSS-2F under the exact ex-ante rule preregistered by OSS-2G.

OSS-2H is not an optimizer, promotion engine, execution gate, broker adapter or capital allocator. A scientific PASS does not by itself authorize PAPER, capital or LIVE.

## Inputs frozen before holdout observation

OSS-2H requires:

1. a canonical OSS-2G protocol receipt;
2. the exact completed DEVELOPMENT winner independently re-read from the durable trial ledger in SQLite `mode=ro` with `PRAGMA query_only = ON`;
3. the exact cross-sectional backtest configuration reconstructed from that frozen trial;
4. an opaque `ProtectedOSS2FinalHoldout` object exposing only its hash before consumption.

The evaluator does not accept caller-supplied thresholds, metrics, scores, weights, alternate trial ids, alternate parameters or alternate backtest configuration.

## One-shot authorization

The OSS-2G `holdout_authorization_id` is materialized as the canonical historical `HoldoutPermit` identity with purpose `final_validation` and issuer `OSS2H_FINAL_HOLDOUT_EVALUATOR`.

Before the protected universe is checked out, OSS-2H opens `BEGIN IMMEDIATE` and atomically:

- verifies the campaign/protocol/authorization has never started before;
- inserts the exact permit into the canonical `holdout_permits` table;
- appends an immutable OSS-2H START receipt binding protocol, candidate, holdout hash and frozen config hash.

Only after that durable transaction commits can the protected universe be returned to the evaluator.

The authorization is therefore burned before observation. A second process, new wrapper object, new evaluation id or identical replay cannot create a second attempt.

## Exact evaluation

OSS-2H reconstructs `CrossSectionalBacktestConfig` from the selected DEVELOPMENT `TrialSpec` using the existing `backtest_config_from_oss2_trial` contract and runs the existing deterministic `CrossSectionalBacktestEngine`.

The final decision uses only the three gates preregistered by OSS-2G:

| Gate | Rule |
| --- | --- |
| `FINAL_NET_RETURN_MIN` | `net_return >= 0.0` |
| `FINAL_SHARPE_MIN` | `sharpe >= 0.0` |
| `FINAL_DRAWDOWN_MAX` | `max_drawdown <= 0.35` |

No composite score, weighting, override or post-hoc threshold change exists.

A metric-backed evaluation is `PASS` only when all three gates pass. Any failed gate yields terminal `FAIL`.

## Evaluation errors are terminal

Once checkout has occurred, an engine/config/integrity error is not a reason to retry. OSS-2H emits a terminal fail-closed receipt with an `EVALUATION_ERROR:<type>` failure code, no fabricated metrics and no fabricated gate evidence.

If the process dies after START consumption but before terminalization, the read-only status surface reports the campaign as consumed/incomplete and explicitly denies retry. Scientific availability is subordinate to holdout integrity.

## Durable evidence

OSS-2H adds two append-only tables:

- `oss2_final_holdout_evaluation_starts`;
- `oss2_final_holdout_evaluations`.

Both have physical `BEFORE UPDATE` and `BEFORE DELETE` denial triggers. START and terminal receipts are canonical-JSON hash-bound and duplicated into critical side-columns. The independent reader reopens the file with SQLite `mode=ro` and `query_only`, revalidates hashes, side-columns, START→terminal linkage and the canonical consumed-permit record.

A terminal receipt binds at minimum:

- campaign;
- selected trial;
- OSS-2G protocol id/hash;
- candidate binding fingerprint;
- single-use authorization id;
- protected holdout universe hash;
- frozen backtest config hash;
- backtest result hash when available;
- exactly three gate results when evaluation succeeded structurally;
- terminal PASS/FAIL;
- observed/consumed state;
- permanent no-retune/no-reselection/no-second-attempt state.

## Authority boundary

OSS-2H authority is strictly scientific:

- protected FINAL_HOLDOUT observation: **ONE CONSUMED ATTEMPT ONLY**;
- retuning: **FALSE**;
- candidate reselection: **FALSE**;
- second attempt/replay: **FALSE**;
- broker: **NONE**;
- external network: **NONE**;
- OMS: **NONE**;
- Safety writer: **NONE**;
- OrderIntent: **NONE**;
- PAPER execution: **FALSE**;
- capital authority: **NONE**;
- LIVE: **BLOCKED**.

The dedicated CI workflow itself contains no credentials, external data retrieval, broker endpoint, `workflow_dispatch` or real holdout material. Its tests use synthetic aligned universes only.

## Interpretation of PASS

An OSS-2H PASS means only:

> the exact frozen OSS-2 candidate passed the three preregistered final scientific gates on its one permitted FINAL_HOLDOUT evaluation.

It does **not** mean guaranteed profitability, a target daily return, production readiness, capital allocation approval, PAPER execution authority or LIVE authorization.

Any subsequent promotion or operationalization must be implemented as a separate boundary that consumes the immutable OSS-2H terminal receipt and re-proves all execution-side safety, cost, version, broker-truth and human-approval requirements independently.
