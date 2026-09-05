# OSS-2F — Durable pre-holdout freeze

Status: research-only boundary after certified OSS-2E.

OSS-2F does **not** read FINAL_HOLDOUT. Its sole purpose is to persist the mechanical OSS-2E `HOLDOUT_ELIGIBLE` or `REJECT` decision before any future holdout boundary exists.

## Durable contract

Each campaign may have exactly one freeze receipt. The receipt binds:

- campaign identity;
- selected trial identity;
- exact OSS-2D evidence fingerprint;
- exact OSS-2E policy fingerprint;
- exact OSS-2E evidence fingerprint;
- candidate freeze fingerprint;
- mechanical eligibility decision;
- failed gate ids;
- explicit no-holdout/no-execution authority fields;
- canonical receipt hash.

A repeated call is idempotent only when every bound element is identical. Any attempt to change receipt id, candidate, evidence, policy, decision, or failed gates after freeze fails closed.

## Append-only storage

The SQLite table uses unique constraints for campaign, receipt and evidence identities. `BEFORE UPDATE` and `BEFORE DELETE` triggers abort mutations, making the journal append-only at the database layer as well as the API layer.

An independent verifier opens the existing SQLite file with `mode=ro` and `PRAGMA query_only = ON`, reconstructs the receipt, verifies its hash and cross-checks durable side-columns against the canonical JSON.

## Authority boundary

Every OSS-2F receipt permanently states, for this boundary:

- `final_holdout_observed = false`;
- `paper_execution_authorized = false`;
- `capital_authority = NONE`;
- `live_trading = BLOCKED`.

OSS-2F has no broker, credential, network, OMS, Capital Safety writer, OrderIntent, PAPER execution or LIVE authority.

A `HOLDOUT_ELIGIBLE` receipt is therefore only a prerequisite for a future, separate, single-use FINAL_HOLDOUT evaluation boundary. It is not a profitability claim and does not promote a strategy to capital.
