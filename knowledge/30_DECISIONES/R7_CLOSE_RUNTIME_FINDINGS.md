# R7 PAPER close runtime findings

## Scope

This note records operator-observed PAPER-only runtime evidence for the first R7 risk-reducing BTC/USD close flow. It is evidence and safety guidance, not authority to retry broker writes.

LIVE remains blocked. Credentials remain memory-only. A burned POST attempt is never retried.

## Observation 1 — first real close POST reached Alpaca and reconciled terminal

Observed broker truth:
- symbol: `BTC/USD`
- side: `SELL`
- type: `LIMIT`
- time in force: `IOC`
- broker order id: `cd3bfc53-0001-413b-9c8b-eca20a721546`
- final broker status: `canceled`
- broker filled quantity: `0`
- remaining broker position: `0.000143959 BTC`
- lifecycle: `TERMINAL_RECONCILED`
- submission attempt count: `1`
- retry POST: `false`
- residual policy: `STOP_AND_CERTIFY_RESIDUAL_EXPOSURE`

The one-shot safety property held: one POST only, then GET-only reconciliation. No automatic second SELL was sent.

## Observation 2 — second reviewed close expired before broker POST

A later operator review prepared a new close plan with:
- quantity: `0.000143959 BTC`
- reference: `USD 78295.1`
- limit: `USD 78266.61`
- max slippage: `25 bps`

The final action was blocked with:

`risk decision expired`

This was a **pre-POST block**. No second broker SELL was sent and no new submission attempt was burned.

The current control plane creates a short-lived Capital Safety decision during preparation. Human review/confirmation can outlive that decision. Expiry must remain fail-closed; the system must never bypass it or silently extend stale authority.

## UI defect discovered

After the pre-POST expiry, the operator screen displayed the terminal reconciliation details from the previous broker order (`cd3bfc53-0001-413b-9c8b-eca20a721546`). That historical data did not belong to the newly blocked attempt.

Required UI behavior:
1. a pre-POST error must clear all prior close-result fields;
2. it must display `POST NO ENVIADO` / `CLOSE_BLOCKED_BEFORE_POST`;
3. `broker_post_attempt_burned` must be `false` for that blocked action;
4. no previous broker order id, status, fill quantity or reconciliation fingerprint may remain visible as the current result;
5. the operator must be instructed to refresh broker truth and prepare/review a new plan;
6. the UI should expose plan/risk-decision expiry or remaining validity so a human can see when re-preparation is required.

## Safety decision

Do not change the short-lived Safety decision merely to make the button easier to press. Expiry is a safety control.

Before any future PAPER close attempt is authorized, the product should first fix stale-result isolation and make authority freshness explicit to the operator. Any fresh execution design must preserve all of these invariants:
- fresh PAPER account identity;
- fresh broker Portfolio truth;
- fresh market truth;
- current Safety state/version;
- exact reviewed quantity and bounded price terms;
- strict risk reduction only;
- durable UNKNOWN immediately before the sole POST;
- no retry POST;
- ambiguous result => GET-only reconciliation;
- residual exposure => stop and certify again;
- LIVE blocked.
