# AUTO-TRADE Skill — Crypto Trading

## Purpose
Use this skill whenever a change touches crypto instruments, crypto market data, research, sizing, order construction, execution, reconciliation, monitoring, or UI claims. The objective is not to make crypto look like equities. The objective is to preserve one capital-safe control plane while modeling the crypto product honestly.

## Authority
This skill is engineering guidance only. It grants no trading authority. Runtime authority remains deterministic and fail-closed through Capital Safety, OMS, durable state, reconciliation, explicit PAPER promotion gates, and human approval where required. LIVE remains blocked unless a later certified track explicitly changes that state.

## Mandatory source discipline
Before changing broker-facing crypto capabilities, verify the current official broker documentation and the exact broker asset metadata returned for the account. Never rely on remembered order types, decimal precision, pair symbology, minimum size, trading status, or protection behavior.

For Alpaca PAPER, the current engineering baseline must be checked against the official pages for Crypto Spot Trading, Crypto Orders, Orders at Alpaca, Assets, and User Protection. Broker documentation is evidence of the API contract; the live/paper asset response is authoritative for per-instrument increments and current tradability.

## Product truths that must be encoded, not assumed
1. Asset class is explicit. Do not infer crypto merely because a symbol contains `/`, `USD`, `USDT`, `BTC`, or another token.
2. Pair identity is canonical and broker-attested. Legacy aliases may be accepted only at an input boundary and must normalize to one canonical internal symbol.
3. Crypto is fractional. Quantity precision and minimum order size come from authoritative asset metadata.
4. Price precision comes from authoritative asset metadata. Never hard-code a BTC tick because it happened to be valid in one response.
5. Crypto market availability uses its own market-hours model. Do not apply the US-equity open/close clock to crypto.
6. Marginability and shortability are explicit capabilities. A strategy cannot create authority that the broker asset contract denies.
7. Broker-supported order type and time-in-force combinations are product capabilities, not generic OMS defaults.
8. Equity bracket/OCO behavior must never be silently reused for crypto.

## Alpaca crypto capability baseline
At the time this skill was authored, official Alpaca documentation described crypto orders as supporting `market`, `limit`, and `stop_limit`, with `gtc` and `ioc` time-in-force values, and fractional `qty` or `notional`. The implementation must still re-verify these facts before widening authority because broker behavior can change.

The current R6 policy is deliberately narrower than the broker maximum:
- PAPER only.
- no leverage;
- no opening short exposure;
- no arbitrary pair until the pair has passed the exact asset/market-data contract;
- no broker write until a crypto-specific lifecycle is certified;
- no LIVE.

## Stop-limit protection warning
A stop-limit is not a guaranteed liquidation mechanism. Once triggered, it becomes a limit order and can remain unfilled if the market moves through the limit. Treat this as residual gap/liquidity risk, not as equivalent to a broker-side market stop or equity bracket.

Any crypto protection design must therefore model at least:
- trigger price;
- limit price and allowed slippage envelope;
- price increment normalization;
- partial entry fills;
- partial protective fills;
- protection quantity exactly bounded by confirmed net position;
- disconnect after entry but before protection acknowledgement;
- ambiguous POST/ACK state;
- stale protection after position reduction;
- cancel/replace races;
- account-side competing orders and wash-trade rejection;
- reconciliation after restart;
- emergency defensive behavior that may reduce risk but never increase it.

## First crypto PAPER canary policy
The first external crypto PAPER canary must not be a generic trading feature. It is an evidence experiment with a tiny hard cap.

Required order of operations:
1. fresh PAPER account attestation;
2. fresh exact crypto asset attestation;
3. explicit ProductCapabilities profile bound to the asset fingerprint;
4. fresh flat-account/open-order reconciliation;
5. fresh crypto market snapshot with bid/ask/last integrity and age/skew bounds;
6. deterministic quantity/price normalization from broker increments;
7. Capital Safety APPROVED for the exact intent and profile;
8. OMS durable VALIDATED identity;
9. explicit bounded canary approval and human execution decision under the certified R6 authority model;
10. entry submission using durable idempotency/client-order identity;
11. UNKNOWN before I/O and no blind retry on ambiguity;
12. reconciliation to confirmed entry fill state;
13. protective order created only for confirmed filled quantity;
14. protective-order acknowledgement/reconciliation before the experiment may be considered protected;
15. terminal evidence package proving fills, fees/spread/slippage, protection state, reconciliation and zero unexplained exposure.

Until steps 10–15 are implemented and adversarially certified, crypto remains read/rehearsal only.

## Strategy research requirements for crypto
Crypto research must not reuse an equity backtest unchanged. At minimum model:
- 24/7 chronology, including weekends;
- exchange/broker-specific liquidity and spread changes by hour/day;
- fees, spread, slippage and latency;
- volatility clustering and jump risk;
- regime sensitivity;
- turnover and capacity;
- fractional sizing and minimum notional/quantity constraints;
- missing/stale data without silent imputation;
- walk-forward/OOS behavior;
- protected holdout;
- multiple-testing control across all preregistered trials;
- drawdown, tail loss and gap/liquidity stress;
- forward/PAPER evidence before promotion.

A strategy may have positive historical expectancy and still be unqualified for execution. Profitability claims require OOS/forward evidence after realistic costs and are never inferred from a successful connectivity or execution test.

## Data requirements
Use one canonical timezone internally (UTC). Preserve source timestamps. Reject naive timestamps in capital-sensitive paths. Market snapshots used for execution must be fresh at the point of decision and rechecked at the final write boundary.

For multi-source research, record:
- source;
- symbol/pair mapping;
- interval;
- exact coverage;
- retrieval time;
- raw-content or manifest hash;
- transformation version;
- missing-interval policy.

## Fail-closed checklist
A crypto action must produce zero new risk if any of the following is unknown, stale, mismatched, unsupported, or ambiguous:
- account identity;
- PAPER/LIVE environment;
- asset class;
- pair identity;
- asset status/tradability;
- min size/quantity increment/price increment;
- ProductCapabilities fingerprint;
- current position/open-order state;
- market data freshness/integrity;
- Safety state/version;
- OMS identity/state;
- human/canary authority where required;
- broker submission state;
- protective-order state;
- reconciliation result.

## Review questions
Before merging a crypto change, answer explicitly:
1. What new capital authority, if any, does this change create?
2. Which asset/profile fingerprint is it bound to?
3. What happens after a timeout at every broker I/O point?
4. Can restart lead to duplicate entry or duplicate protection?
5. Can a protective order exceed the confirmed position?
6. Can a crypto branch accidentally call an equity bracket path?
7. Can an equity branch accidentally use 24/7 crypto assumptions?
8. Are fees/spread/slippage included in strategy evidence?
9. Which negative tests prove fail-closed behavior?
10. Is LIVE still structurally denied?
