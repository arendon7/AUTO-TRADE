from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

import autotrade.paper_runtime_broker_truth as broker_module
import autotrade.paper_runtime_final_readiness as final_module
import autotrade.paper_runtime_funding_capacity as funding_module
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
)
from autotrade.paper_runtime_final_readiness import (
    PaperRuntimeFinalReadinessPolicy,
    PaperRuntimeReadinessStatus,
    finalize_paper_runtime_readiness,
)
from autotrade.paper_runtime_funding_capacity import (
    PaperRuntimeFundingCapacityBlocker,
    PaperRuntimeFundingCapacityIntegrityError,
    PaperRuntimeFundingCapacityPolicy,
    PaperRuntimeFundingCapacityStatus,
    bind_paper_runtime_funding_capacity,
)
from autotrade.paper_runtime_market_truth import bind_paper_runtime_market_truth
from test_w86_paper_runtime_final_readiness import (
    NOW,
    _bind_candidate,
    _safety,
    _source,
)
from test_w86_paper_runtime_market_truth import (
    ACCOUNT_ID,
    AT,
    CREDENTIAL_REFERENCE,
    _asset_truth,
    _broker,
    _market,
)


FUNDING_NOW = NOW + timedelta(milliseconds=250)


def _account(*, buying_power: Decimal = Decimal("1000")) -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id=ACCOUNT_ID,
        account_reference="d" * 64,
        credential_reference=CREDENTIAL_REFERENCE,
        status="ACTIVE",
        currency="USD",
        buying_power=buying_power,
        portfolio_value=Decimal("1000"),
        shorting_enabled=False,
        attested_at=AT,
        request_id="account-market-req",
        source_host=ALPACA_PAPER_TRADING_HOST,
        source_path=ALPACA_PAPER_ACCOUNT_PATH,
    )


def _chain(
    monkeypatch,
    *,
    buying_power: Decimal = Decimal("1000"),
    bid: Decimal = Decimal("100"),
    ask: Decimal = Decimal("101"),
    final_policy: PaperRuntimeFinalReadinessPolicy | None = None,
):
    source = _source()
    candidate = _bind_candidate(source)
    account = _account(buying_power=buying_power)

    broker = _broker(candidate, credential_reference=account.credential_reference)
    object.__setattr__(broker, "account_reference", account.account_reference)
    object.__setattr__(broker, "account_attestation_fingerprint", account.fingerprint)
    object.__setattr__(broker, "account_attested_at", account.attested_at)
    object.__setattr__(
        broker,
        "proof_hash",
        broker_module._hash(broker_module._proof_payload(broker, include_hash=False)),
    )

    asset = _asset_truth(candidate, broker)
    market = bind_paper_runtime_market_truth(
        proof_id="market-funding-capacity-test",
        candidate_identity=candidate,
        broker_truth=broker,
        asset_truth=asset,
        market=_market(
            bid=bid,
            ask=ask,
            last=(bid + ask) / Decimal("2"),
        ),
        observed_at=AT + timedelta(seconds=1),
    )
    safety = _safety(candidate)

    monkeypatch.setattr(final_module, "_now_utc", lambda: NOW)
    final = finalize_paper_runtime_readiness(
        receipt_id="final-funding-capacity-test",
        source_snapshot=source,
        candidate_identity=candidate,
        broker_truth=broker,
        asset_truth=asset,
        market_truth=market,
        safety_health_truth=safety,
        policy=final_policy,
    )
    return account, broker, final


def _fund(
    monkeypatch,
    chain,
    *,
    now=FUNDING_NOW,
    policy: PaperRuntimeFundingCapacityPolicy | None = None,
):
    monkeypatch.setattr(funding_module, "_now_utc", lambda: now)
    return bind_paper_runtime_funding_capacity(
        proof_id="funding-capacity-test",
        final_readiness=chain[2],
        broker_truth=chain[1],
        account_attestation=chain[0],
        policy=policy,
    )


def test_funding_capacity_ready_is_finite_and_has_no_money_movement_authority(monkeypatch):
    proof = _fund(monkeypatch, _chain(monkeypatch))

    assert proof.status is PaperRuntimeFundingCapacityStatus.READY
    assert proof.blocker_codes == ()
    assert proof.paper_runtime_ready is True
    assert proof.buying_power_usd == Decimal("1000")
    assert proof.minimum_executable_notional_usd == Decimal("0.101")
    assert proof.buying_power_headroom_usd == Decimal("999.899")
    assert proof.buying_power_sufficient is True
    assert proof.account_binding_verified is True
    assert proof.account_fresh is True
    assert proof.observed_at < proof.valid_until <= proof.observed_at + timedelta(seconds=2)
    assert proof.separate_execution_approval_required is True
    assert proof.capital_reserved is False
    assert proof.broker_write_performed is False
    assert proof.paper_execution_authorized is False
    assert proof.external_execution_authorized is False
    assert proof.runtime_execution_authorized is False
    assert proof.capital_authority == "NONE"
    assert proof.live_trading == "BLOCKED"
    assert proof.to_dict()["proof_hash"] == proof.proof_hash


@pytest.mark.parametrize(
    ("buying_power", "expected_ready"),
    (
        (Decimal("0.101"), True),
        (Decimal("0.100999"), False),
    ),
)
def test_exact_minimum_executable_notional_is_the_funding_threshold(
    monkeypatch,
    buying_power,
    expected_ready,
):
    final_chain = _chain(monkeypatch, buying_power=buying_power)
    assert final_chain[2].status is PaperRuntimeReadinessStatus.READY

    proof = _fund(monkeypatch, final_chain)
    assert proof.paper_runtime_ready is expected_ready
    assert proof.buying_power_sufficient is expected_ready
    if expected_ready:
        assert proof.blocker_codes == ()
        assert proof.buying_power_headroom_usd == Decimal("0")
    else:
        assert (
            PaperRuntimeFundingCapacityBlocker.INSUFFICIENT_BUYING_POWER
            in proof.blocker_codes
        )
        assert proof.status is PaperRuntimeFundingCapacityStatus.BLOCKED
        assert proof.valid_until == proof.observed_at
        assert proof.paper_execution_authorized is False


def test_funding_gate_never_raises_w85_probation_cap_to_fit_account(monkeypatch):
    chain = _chain(
        monkeypatch,
        buying_power=Decimal("1000"),
        bid=Decimal("5000.01"),
        ask=Decimal("5000.01"),
    )
    assert chain[2].status is PaperRuntimeReadinessStatus.BLOCKED
    assert chain[2].probation_notional_cap_usd == Decimal("5")

    proof = _fund(monkeypatch, chain)
    assert (
        PaperRuntimeFundingCapacityBlocker.FINAL_RUNTIME_NOT_READY
        in proof.blocker_codes
    )
    assert proof.buying_power_sufficient is True
    assert proof.paper_runtime_ready is False
    assert proof.capital_authority == "NONE"


def test_stale_account_attestation_blocks_even_while_final_receipt_is_valid(monkeypatch):
    chain = _chain(monkeypatch)
    proof = _fund(
        monkeypatch,
        chain,
        now=NOW,
        policy=PaperRuntimeFundingCapacityPolicy(max_account_age_seconds=1),
    )
    assert (
        PaperRuntimeFundingCapacityBlocker.ACCOUNT_ATTESTATION_STALE
        in proof.blocker_codes
    )
    assert proof.account_fresh is False
    assert proof.paper_runtime_ready is False


def test_expired_final_readiness_blocks_without_conflating_execution_authority(monkeypatch):
    chain = _chain(
        monkeypatch,
        final_policy=PaperRuntimeFinalReadinessPolicy(ready_ttl_seconds=1),
    )
    proof = _fund(monkeypatch, chain, now=NOW + timedelta(seconds=2))
    assert (
        PaperRuntimeFundingCapacityBlocker.FINAL_RUNTIME_RECEIPT_EXPIRED
        in proof.blocker_codes
    )
    assert proof.paper_runtime_ready is False
    assert proof.paper_execution_authorized is False
    assert proof.capital_reserved is False


def test_different_account_attestation_cannot_be_substituted_after_final_readiness(monkeypatch):
    account, broker, final = _chain(monkeypatch)
    forged = replace(account, buying_power=Decimal("999999"))
    monkeypatch.setattr(funding_module, "_now_utc", lambda: FUNDING_NOW)
    with pytest.raises(
        PaperRuntimeFundingCapacityIntegrityError,
        match="differs from broker-bound account attestation",
    ):
        bind_paper_runtime_funding_capacity(
            proof_id="forged-funding-account",
            final_readiness=final,
            broker_truth=broker,
            account_attestation=forged,
        )


def test_cross_broker_or_tampered_upstream_hash_is_integrity_error(monkeypatch):
    account, broker, final = _chain(monkeypatch)

    object.__setattr__(broker, "proof_hash", "0" * 64)
    monkeypatch.setattr(funding_module, "_now_utc", lambda: FUNDING_NOW)
    with pytest.raises(PaperRuntimeFundingCapacityIntegrityError, match="broker truth proof hash"):
        bind_paper_runtime_funding_capacity(
            proof_id="tampered-broker-funding",
            final_readiness=final,
            broker_truth=broker,
            account_attestation=account,
        )


def test_rehashed_execution_authority_escalation_is_rejected(monkeypatch):
    account, broker, final = _chain(monkeypatch)
    object.__setattr__(final, "paper_execution_authorized", True)
    object.__setattr__(
        final,
        "receipt_hash",
        final_module._hash(final_module._payload(final, include_hash=False)),
    )
    monkeypatch.setattr(funding_module, "_now_utc", lambda: FUNDING_NOW)
    with pytest.raises(
        PaperRuntimeFundingCapacityIntegrityError,
        match="execution/capital/LIVE authority escalation",
    ):
        bind_paper_runtime_funding_capacity(
            proof_id="authority-escalation-funding",
            final_readiness=final,
            broker_truth=broker,
            account_attestation=account,
        )


def test_internal_clock_rejects_future_account_attestation(monkeypatch):
    chain = _chain(monkeypatch)
    with pytest.raises(PaperRuntimeFundingCapacityIntegrityError, match="process future"):
        _fund(monkeypatch, chain, now=AT - timedelta(microseconds=1))


def test_proof_self_guards_hash_headroom_and_authority(monkeypatch):
    proof = _fund(monkeypatch, _chain(monkeypatch))
    with pytest.raises(PaperRuntimeFundingCapacityIntegrityError, match="proof hash mismatch"):
        replace(proof, proof_hash="0" * 64)
    with pytest.raises(PaperRuntimeFundingCapacityIntegrityError, match="headroom"):
        replace(proof, buying_power_headroom_usd=Decimal("0"))
    with pytest.raises(PaperRuntimeFundingCapacityIntegrityError, match="may not grant"):
        replace(proof, capital_reserved=True)


def test_funding_policy_is_strict_and_cannot_be_widened():
    assert PaperRuntimeFundingCapacityPolicy().max_account_age_seconds == 5
    assert PaperRuntimeFundingCapacityPolicy().ready_ttl_seconds == 2
    with pytest.raises(ValueError):
        PaperRuntimeFundingCapacityPolicy(max_account_age_seconds=6)
    with pytest.raises(ValueError):
        PaperRuntimeFundingCapacityPolicy(ready_ttl_seconds=3)
    with pytest.raises(ValueError):
        PaperRuntimeFundingCapacityPolicy(max_account_age_seconds=True)
