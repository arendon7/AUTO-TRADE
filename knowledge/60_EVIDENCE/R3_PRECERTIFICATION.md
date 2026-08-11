# R3 v0.28R — Precertification

Status: **NOT CERTIFIED / PR #10 remains DRAFT**

This note exists to record the evidence boundary before final R3 certification. It does not authorize promotion, PAPER execution, broker connectivity or LIVE trading.

## Implemented candidate capabilities

- deny-by-default read-only public market-data boundary;
- GET-only HTTPS allowlist with redirect target validation before redirected network I/O;
- bounded response size, timeout and range/page limits;
- exact millisecond/timeframe coverage with no silent truncation or imputation;
- canonical external dataset artifact with source and dataset hashes;
- durable frozen campaign/trial preregistration and terminal accounting;
- FINAL_HOLDOUT trial binding to a one-use consumed `final_validation` permit;
- research authority static gate forbidding OMS/broker/safety/execution imports and execution-capable domain symbols;
- Holm multiple-testing evidence over the complete frozen trial universe;
- PBO/CSCV and Deflated Sharpe only when explicit preconditions are satisfied;
- read-only Research Control Center over immutable evidence;
- machine-readable Debt Register CI gate.

## Certification blockers

R3 stays DRAFT until all of the following are true:

1. full branch compile + pytest + branch coverage gate pass;
2. Contract Registry PASS;
3. Research Authority Boundary PASS;
4. Debt Register Contract PASS;
5. Knowledge Contract PASS;
6. all R3 P1 debt is CLOSED with concrete evidence;
7. bounded opt-in real-data campaign is executed and its immutable manifest/checksums are stored as evidence;
8. R3 certification artifact is created only after the above evidence exists.

## Safety boundary

Research code remains read-only with respect to capital. No R3 component may create execution authority or bypass Capital Safety Kernel / OMS. LIVE TRADING remains blocked.
