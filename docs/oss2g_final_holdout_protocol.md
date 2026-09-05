# OSS-2G — preregistered single-use FINAL_HOLDOUT protocol

## Purpose

OSS-2G freezes the decision rule that may be applied to the one future `FINAL_HOLDOUT` evaluation of an OSS-2 candidate. It consumes only a durable OSS-2F receipt whose decision is `HOLDOUT_ELIGIBLE`.

OSS-2G does **not** read, inspect, checkout, accept or evaluate FINAL_HOLDOUT data. It also does not mint a `HoldoutPermit`. The protocol only freezes the exact authorization identity that a later, separately certified boundary would have to consume.

## Frozen scientific policy

Before any FINAL_HOLDOUT observation, OSS-2G preregisters exactly three gates:

1. `FINAL_NET_RETURN_MIN`: net return `>= 0.0`;
2. `FINAL_SHARPE_MIN`: Sharpe `>= 0.0`;
3. `FINAL_DRAWDOWN_MAX`: maximum drawdown `<= 0.35`.

All three gates are hard requirements. There is no score, weighting, discretionary override, partial pass or optimization against FINAL_HOLDOUT.

Additional frozen governance:

- split: `FINAL_HOLDOUT`;
- permit purpose: `final_validation`;
- maximum evaluations: `1`;
- retuning: forbidden;
- candidate reselection: forbidden;
- second attempt: forbidden;
- failed FINAL_HOLDOUT outcome: terminal for this campaign/candidate pair.

## OSS-2F prerequisite

The protocol writer accepts only:

- `protocol_id`;
- one `OSS2HoldoutFreezeReceipt`.

The freeze receipt must be canonical OSS-2F, decision `HOLDOUT_ELIGIBLE`, have no failed gates, preserve `final_holdout_observed=false`, preserve `paper_execution_authorized=false`, preserve `capital_authority=NONE` and preserve `live_trading=BLOCKED`.

A rejected OSS-2F candidate cannot acquire an OSS-2G protocol.

## Durable preregistration

OSS-2G stores one append-only SQLite receipt per campaign. The receipt binds:

- campaign and selected trial;
- OSS-2F receipt id and hash;
- candidate freeze fingerprint;
- OSS-2E policy fingerprint;
- OSS-2G policy fingerprint;
- deterministic future `holdout_authorization_id`;
- all frozen thresholds and single-use constraints;
- no-holdout-observation and no-execution authority invariants.

Identical replay is idempotent. Any attempt to change protocol id, candidate, freeze, policy, thresholds or authorization identity after preregistration conflicts fail-closed.

SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers make the table physically append-only through the normal database surface. The independent reader opens the existing database with `mode=ro` and `PRAGMA query_only = ON`, reconstructs the receipt, validates its cryptographic hash and cross-checks JSON against side columns.

## Future authorization identity

OSS-2G computes one deterministic `holdout_authorization_id` from the exact protocol id, campaign, selected trial, OSS-2F receipt hash, candidate freeze fingerprint, OSS-2G policy fingerprint and purpose `final_validation`.

This identifier is **not** a usable permit in OSS-2G. The module deliberately does not import `HoldoutPermit`, `ProtectedHoldout`, the holdout registry, `MarketDataset` or a backtest engine. A future boundary must independently verify the OSS-2G receipt and then prove one-time permit consumption before any result can exist.

## Authority boundary

OSS-2G grants none of the following:

- FINAL_HOLDOUT input/read/checkout: **NONE**;
- HoldoutPermit construction/consumption: **NONE**;
- broker/network access: **NONE**;
- OMS or Safety writer authority: **NONE**;
- OrderIntent: **NONE**;
- PAPER execution: **FALSE**;
- capital authority: **NONE**;
- LIVE: **BLOCKED**.

A successful OSS-2G protocol is a scientific preregistration artifact only. It is not profitability evidence and it is not trading authorization.

## Next boundary

The next admissible frontier is a separately reviewed single-use FINAL_HOLDOUT consumer. It must use the exact frozen OSS-2G protocol and authorization identity, consume one `final_validation` permit exactly once, bind the exact frozen candidate and dataset, compute only the preregistered metrics, write a terminal PASS/FAIL receipt, and permanently deny retuning, reselection, replay and second attempts.
