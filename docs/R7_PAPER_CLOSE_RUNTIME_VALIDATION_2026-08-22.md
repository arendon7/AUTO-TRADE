# R7 PAPER close runtime validation — 2026-08-22

## Runtime environment

- Environment: Alpaca PAPER only
- Symbol: BTC/USD
- Position before close: 0.000143959 BTC long
- Available quantity: 0.000143959 BTC
- Open orders before close: 0
- Safety kill switch: ACTIVE (`R6_HEALTH_R4_EVIDENCE_REQUIRED`)
- Safety circuit: INACTIVE
- Source provenance: certified first-canary source available
- Close readiness: true

## Prepared close

- Mode: FULL
- Side: SELL
- Type: LIMIT
- TIF: IOC
- Quantity: 0.000143959 BTC
- Portfolio reference price: USD 78529.74
- Limit price: USD 78411.025
- Estimated notional: USD 11.287972747975
- Max slippage: 25 bps
- Strict risk reduction: true

## Broker result

Exactly one PAPER close POST was attempted.

- Broker order id: `cd3bfc53-0001-413b-9c8b-eca20a721546`
- Initial POST status: `pending_new`
- Reconciled terminal broker status: `canceled`
- Filled quantity: `0`
- Remaining position: `0.000143959 BTC`
- Lifecycle: `TERMINAL_RECONCILED`
- Submission attempt count: `1`
- Retry POST: false
- LIVE: blocked
- Reconciliation fingerprint: `c79d7fc9637c8ffb025798e02a6483b69742cfbf472223b76385557e952db5bf`

## Safety conclusion

The close path behaved fail-closed as designed:

1. one POST only;
2. broker result reconciled through GET-only truth;
3. no blind retry;
4. zero fill preserved the original long quantity;
5. the terminal canceled attempt is burned and may never be reposted;
6. residual exposure requires a NEW close attempt from fresh broker truth and new human approval.

This result is an execution-quality finding, not a reconciliation or authority failure.

## Follow-up

Before a second SELL, refresh broker Portfolio/market truth and prepare a new close plan. Do not reuse the prior attempt. The next runtime review should evaluate whether the current IOC limit construction is sufficiently marketable inside the 25 bps hard slippage envelope.
