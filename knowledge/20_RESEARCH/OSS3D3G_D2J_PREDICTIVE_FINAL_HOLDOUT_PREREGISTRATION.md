# OSS-3D3G — D2J Predictive FINAL_HOLDOUT Preregistration

## Purpose

OSS-3D3G closes the protocol-registration frontier that remained after the real DEVELOPMENT campaign and protected Q1/Q2 materialization. It binds the exact certified DEVELOPMENT winner to the exact value-opaque Q1 commitment under the already-frozen OSS-3D2J one-shot predictive policy.

D3G is **preregistration only**. It does not evaluate Q1 and does not create authority to evaluate it.

The boundary is:

```text
certified D3C durable DEVELOPMENT outcome
  -> exact typed D2H preregistration reconstruction
  -> exact typed D2H completed-batch reconstruction
  -> deterministic D2I seal reproduction

certified D3F public evidence
  -> public hashes + geometry only
  -> exact Q1 D2J commitment reproduction

exact D2I + exact value-opaque Q1 commitment
  -> append-only D2J preregistration
  -> zero FINAL_HOLDOUT evaluations
  -> no permit / no checkout / no promotion / no execution
```

## Frozen real identities

D3G accepts only:

- D3C durable bundle: `1d271c31f506be21d2a99c857a1007cc5676cfcb5ab83fb6a524171a38dbaf43`;
- D3C stable scientific outcome: `5b4261b186e30cbdc30ec2c3025305fbb96650247a066cdf470ce53e80f86ea7`;
- D3C D2H preregistration: `1e90ee15a3ac26b6aaa8dd0703efde7e7046f34e9cc36e9e6f338d802af08238`;
- D3C D2H batch: `064883e0e8c1e5e5ef11595b5cdacdd73e4a3dd2668fe2e8ba005eba71413ce9`;
- D3C coherent D2I seal: `65537f610766852c834893604192e1fe5562d0385fa2b00f4c692b6b074d7cb1`;
- selected DEVELOPMENT trial: `linear-ridge-a10`;
- D3F public evidence: `a9681ff31f4d74176d072c2076d129516d8db19cfb8aea093432ec6f7ac9d5b6`;
- Q1 predictive commitment: `a6fef49419ef22b75be45385a805f8f3242653a3fcb74cb708be6548ac865bde`.

The historical original D3A D2I container fingerprint is not substituted for the D3C coherent D2I lineage. D3G uses the complete D2H/D2I objects embedded in the certified D3C artifact and reproduces that current seal exactly.

## No DEVELOPMENT replay

D3G does not refit or predict any model and does not rerun DEVELOPMENT evaluation. The D3C bundle already contains canonical JSON for D2H preregistration, completed D2H batch evidence and D2I winner evidence.

D3G reconstructs the typed objects from that JSON and requires exact equality of:

- rebuilt D2E plan payload and fingerprint;
- D2H preregistration payload and fingerprint;
- D2E tournament payload and evidence fingerprint;
- D2H batch payload and fingerprint;
- reproduced D2I payload and fingerprint.

Any cross-wire or changed lineage fails closed before D2J is touched.

## Q1 remains value-opaque

The certified D3F public artifact contains no feature rows, label rows, prediction rows, OHLCV values or outcomes. D3G uses only its public commitments:

- research split and universe hashes;
- label definition hash;
- protected feature-artifact hash;
- protected label-artifact hash;
- evaluation-keyset hash;
- cross-section-key hash;
- half-open Q1 partition geometry;
- row/cross-section counts;
- explicit non-observation flags.

Those fields deterministically reproduce the exact D2J Q1 commitment fingerprint. D3G rejects any changed D3F public evidence before protocol registration.

## DEVELOPMENT/Q1 chronological boundary

D2R represents a supervised half-open partition end as:

`last label availability + 1 microsecond`.

The certified DEVELOPMENT bound is therefore `2026-01-01T00:00:00.000001+00:00`, while Q1 begins at the actual final-label boundary `2026-01-01T00:00:00+00:00`.

D2J now recognizes only that exact one-microsecond D2R sentinel as adjacency. A backward gap of two microseconds or more remains a hard chronological-overlap failure. Dedicated tests freeze both cases.

## D2J policy remains unchanged

D3G does not change the statistical policy. D2J remains frozen to one future candidate evaluation with:

- mean cross-sectional Rank IC >= `0.02`;
- one-sided exact sign-test p-value <= `0.05`;
- at least `30` holdout cross-sections;
- at least `90` total observations;
- at least `3` observations per cross-section;
- at least `20` non-zero Rank-IC cross-sections;
- no retuning;
- no reselection;
- no fallback candidate;
- no second attempt;
- terminal failure.

Preregistration itself does not calculate any of those future Q1 metrics.

## Append-only durability and restart safety

The D2J SQLite registry already enforces one winner/one holdout identity with no updates or deletes. D3G records the exact real protocol twice in the Dedicated workflow and requires the second call to return the same receipt. A read-only reconstruction must equal the recorded receipt and the registry must contain exactly one row.

The public D3G evidence stores only protocol/root identities and denial flags. It contains no Q1 values.

## Authority after D3G

After a successful D3G run all of the following remain true:

- FINAL_HOLDOUT evaluations performed: `0`;
- FINAL_HOLDOUT observed: `false`;
- FINAL_HOLDOUT consumed: `false`;
- holdout permit issued: `false`;
- holdout permit consumed: `false`;
- FINAL_HOLDOUT checkout authorized: `false`;
- predictive validation passed: `false`;
- profitability claim authorized: `false`;
- promotion authorized: `false`;
- execution authorized: `false`;
- PAPER execution authorized: `false`;
- capital authority: `NONE`;
- LIVE trading: `BLOCKED`.

The D2J `expected_holdout_authorization_id` is only a frozen future identity. Its existence is not a permit and does not authorize checkout or evaluation.

## Next frontier

D3G deliberately stops before any D2K evaluation surface. Any later one-shot predictive FINAL_HOLDOUT evaluation requires a separate authorization block and separate exact-head certification. D3G itself contains no D2K evaluator import, no Qlib runtime and no protected value-loading path.
