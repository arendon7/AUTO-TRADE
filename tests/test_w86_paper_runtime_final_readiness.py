from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

import autotrade.paper_candidate_admission_lifecycle as lifecycle
import autotrade.paper_runtime_asset_truth as asset_module
import autotrade.paper_runtime_broker_truth as broker_module
import autotrade.paper_runtime_candidate_identity as candidate_module
import autotrade.paper_runtime_final_readiness as final_module
import autotrade.paper_runtime_market_truth as market_module
import autotrade.paper_runtime_readiness_source_snapshot as source_module
import autotrade.paper_runtime_safety_health_truth as safety_module
from autotrade.paper_runtime_final_readiness import (
    PaperRuntimeFinalReadinessIntegrityError,
    PaperRuntimeFinalReadinessPolicy,
    PaperRuntimeReadinessBlocker,
    PaperRuntimeReadinessStatus,
    finalize_paper_runtime_readiness,
)
from autotrade.paper_runtime_market_truth import bind_paper_runtime_market_truth
from test_w86_paper_runtime_market_truth import (
    AT,
    _asset_truth,
    _broker,
    _candidate,
    _market,
)

NOW = AT + timedelta(seconds=2)


def _source(*, state=lifecycle.PaperCandidateEligibilityState.ACTIVE, valid_until=AT + timedelta(minutes=10)):
    values = {
        "proof_id": "source-final-readiness",
        "contract_version": source_module.W85_DURABLE_ELIGIBILITY_SNAPSHOT_VERSION,
        "authority_key": "2" * 64,
        "admission_id": "admission-market-test",
        "admission_hash": "3" * 64,
        "policy_id": "policy-final-readiness",
        "policy_hash": "4" * 64,
        "policy_registration_hash": "5" * 64,
        "final_admission_verification_hash": "4" * 64,
        "supplied_final_eligibility_hash": "6" * 64,
        "w84_admission_source_proof_hash": "7" * 64,
        "selected_trial_fingerprint": "8" * 64,
        "strategy_spec_hash": "9" * 64,
        "loaded_runtime_code_hash": "a" * 64,
        "fee_product_economics_hash": "b" * 64,
        "intent_fingerprint": "c" * 64,
        "admission_valid_until": valid_until,
        "probation_notional_cap_usd": Decimal("5"),
        "probation_order_cap": 1,
        "lifecycle_head_hash": "d" * 64,
        "lifecycle_events_count": 0,
        "current_state": state,
        "supplied_eligibility_observed_at": AT - timedelta(seconds=1),
        "reproved_at": AT,
        "sqlite_data_version": 1,
        "candidate_currently_eligible": state is lifecycle.PaperCandidateEligibilityState.ACTIVE,
        "durable_admission_verified": True,
        "durable_lifecycle_verified": True,
        "sqlite_read_only": True,
        "sqlite_snapshot_consistent": True,
        "concurrent_durable_change_detected": False,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return source_module.W85DurableEligibilitySnapshotProof(
        **values,
        proof_hash=source_module._hash(source_module._proof_payload_from_values(values)),
    )


def _bind_candidate(source, *, side="BUY"):
    candidate = _candidate()
    object.__setattr__(candidate, "w85_source_snapshot_hash", source.proof_hash)
    object.__setattr__(candidate, "side", side)
    object.__setattr__(
        candidate,
        "proof_hash",
        candidate_module._hash(candidate_module._payload(candidate, include_hash=False)),
    )
    return candidate


def _safety(candidate):
    values = {
        "proof_id": "safety-final-readiness",
        "contract_version": safety_module.PAPER_RUNTIME_SAFETY_HEALTH_TRUTH_VERSION,
        "candidate_identity_hash": candidate.proof_hash,
        "policy_hash": "d" * 64,
        "authority_key": candidate.authority_key,
        "admission_hash": candidate.admission_hash,
        "selected_strategy_id": candidate.selected_strategy_id,
        "portfolio_health_entity_id": safety_module.PORTFOLIO_HEALTH_ENTITY_ID,
        "sqlite_data_version": 1,
        "ledger_event_count": 4,
        "ledger_head_hash": "e" * 64,
        "safety_version": 4,
        "safety_state_fingerprint": "f" * 64,
        "kill_switch_active": False,
        "kill_switch_reason": "",
        "circuit_active": False,
        "circuit_reason": "",
        "safety_updated_at": AT,
        "strategy_health_version": 1,
        "strategy_health_fingerprint": "0" * 64,
        "strategy_health_updated_at": AT,
        "strategy_recovery_ack_head": "1" * 64,
        "strategy_recovery_ack_count": 1,
        "strategy_bridge_version": 1,
        "strategy_bridge_fingerprint": "2" * 64,
        "strategy_bridge_updated_at": AT,
        "portfolio_health_version": 1,
        "portfolio_health_fingerprint": "3" * 64,
        "portfolio_health_updated_at": AT,
        "portfolio_recovery_ack_head": "GENESIS",
        "portfolio_recovery_ack_count": 0,
        "portfolio_bridge_version": 1,
        "portfolio_bridge_fingerprint": "4" * 64,
        "portfolio_bridge_updated_at": AT,
        "observed_at": AT,
        "safety_health_valid_until": AT + timedelta(seconds=30),
        "ledger_integrity_verified": True,
        "safety_projection_verified": True,
        "strategy_health_verified": True,
        "portfolio_health_verified": True,
        "read_only_core_truth": True,
        "sqlite_snapshot_consistent": True,
        "concurrent_durable_change_detected": False,
        "paper_runtime_ready": False,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return safety_module.PaperRuntimeSafetyHealthTruthProof(
        **values,
        proof_hash=safety_module._hash(safety_module._proof_payload_from_values(values)),
    )


def _chain(*, side="BUY", state=lifecycle.PaperCandidateEligibilityState.ACTIVE, valid_until=AT + timedelta(minutes=10), dirty=False, min_order=None, bid=Decimal("100"), ask=Decimal("101")):
    source = _source(state=state, valid_until=valid_until)
    candidate = _bind_candidate(source, side=side)
    broker = _broker(candidate)
    if dirty:
        object.__setattr__(broker, "position_count", 1)
        object.__setattr__(broker, "clean_for_candidate_start", False)
        object.__setattr__(
            broker,
            "proof_hash",
            broker_module._hash(broker_module._proof_payload(broker, include_hash=False)),
        )
    asset = _asset_truth(candidate, broker)
    if min_order is not None:
        object.__setattr__(asset, "min_order_size", min_order)
        object.__setattr__(
            asset,
            "proof_hash",
            asset_module._hash(asset_module._proof_payload(asset, include_hash=False)),
        )
    market = bind_paper_runtime_market_truth(
        proof_id="market-final-readiness",
        candidate_identity=candidate,
        broker_truth=broker,
        asset_truth=asset,
        market=_market(bid=bid, ask=ask, last=(bid + ask) / Decimal("2")),
        observed_at=AT + timedelta(seconds=1),
    )
    safety = _safety(candidate)
    return source, candidate, broker, asset, market, safety


def _final(monkeypatch, chain, *, now=NOW, policy=None):
    monkeypatch.setattr(final_module, "_now_utc", lambda: now)
    return finalize_paper_runtime_readiness(
        receipt_id="final-readiness-test",
        source_snapshot=chain[0],
        candidate_identity=chain[1],
        broker_truth=chain[2],
        asset_truth=chain[3],
        market_truth=chain[4],
        safety_health_truth=chain[5],
        policy=policy,
    )


def test_ready_receipt_is_finite_and_has_no_money_movement_authority(monkeypatch):
    receipt = _final(monkeypatch, _chain())
    assert receipt.status is PaperRuntimeReadinessStatus.READY
    assert receipt.blocker_codes == ()
    assert receipt.paper_runtime_ready is True
    assert receipt.minimum_executable_quantity == Decimal("0.001")
    assert receipt.conservative_unit_price == Decimal("101")
    assert receipt.minimum_executable_notional_usd == Decimal("0.101")
    assert receipt.observed_at < receipt.valid_until <= receipt.observed_at + timedelta(seconds=5)
    assert receipt.separate_execution_approval_required is True
    assert receipt.order_intent_created is False
    assert receipt.oms_handoff_performed is False
    assert receipt.capital_reserved is False
    assert receipt.broker_write_performed is False
    assert receipt.paper_execution_authorized is False
    assert receipt.external_execution_authorized is False
    assert receipt.runtime_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    assert receipt.to_dict()["receipt_hash"] == receipt.receipt_hash


@pytest.mark.parametrize("ask,expected_ready", ((Decimal("5000"), True), (Decimal("5000.01"), False)))
def test_usd5_probation_cap_is_exact_and_never_expanded(monkeypatch, ask, expected_ready):
    receipt = _final(monkeypatch, _chain(bid=ask, ask=ask))
    assert receipt.paper_runtime_ready is expected_ready
    if expected_ready:
        assert receipt.minimum_executable_notional_usd == Decimal("5.000")
    else:
        assert PaperRuntimeReadinessBlocker.MINIMUM_NOTIONAL_EXCEEDS_PROBATION_CAP in receipt.blocker_codes
        assert receipt.minimum_notional_compatible is False
        assert receipt.freshness_verified is True


def test_quantity_and_price_are_rounded_conservatively(monkeypatch):
    receipt = _final(monkeypatch, _chain(min_order=Decimal("0.0011"), bid=Decimal("100"), ask=Decimal("100.001")))
    assert receipt.minimum_executable_quantity == Decimal("0.002")
    assert receipt.conservative_unit_price == Decimal("100.01")
    assert receipt.minimum_executable_notional_usd == Decimal("0.20002")


def test_valid_nonflat_or_sell_state_returns_blocked_not_authority(monkeypatch):
    dirty = _final(monkeypatch, _chain(dirty=True))
    assert PaperRuntimeReadinessBlocker.BROKER_ACCOUNT_NOT_FLAT in dirty.blocker_codes
    sell = _final(monkeypatch, _chain(side="SELL"))
    assert PaperRuntimeReadinessBlocker.SIDE_NOT_SUPPORTED_FOR_FLAT_CANARY in sell.blocker_codes
    for receipt in (dirty, sell):
        assert receipt.status is PaperRuntimeReadinessStatus.BLOCKED
        assert receipt.valid_until == receipt.observed_at
        assert receipt.paper_execution_authorized is False


@pytest.mark.parametrize(
    "policy,now,blocker",
    (
        (PaperRuntimeFinalReadinessPolicy(source_age_seconds=1), NOW, PaperRuntimeReadinessBlocker.W85_SOURCE_STALE),
        (PaperRuntimeFinalReadinessPolicy(broker_age_seconds=1), NOW, PaperRuntimeReadinessBlocker.BROKER_TRUTH_STALE),
        (PaperRuntimeFinalReadinessPolicy(asset_age_seconds=1), NOW, PaperRuntimeReadinessBlocker.ASSET_TRUTH_STALE),
        (PaperRuntimeFinalReadinessPolicy(market_age_seconds=1), AT + timedelta(seconds=3), PaperRuntimeReadinessBlocker.MARKET_TRUTH_STALE),
        (PaperRuntimeFinalReadinessPolicy(safety_health_age_seconds=1), NOW, PaperRuntimeReadinessBlocker.SAFETY_HEALTH_TRUTH_STALE),
    ),
)
def test_each_evidence_family_has_a_fail_closed_freshness_blocker(monkeypatch, policy, now, blocker):
    receipt = _final(monkeypatch, _chain(), now=now, policy=policy)
    assert blocker in receipt.blocker_codes
    assert receipt.freshness_verified is False
    assert receipt.paper_runtime_ready is False


def test_inactive_or_expired_w85_candidate_blocks(monkeypatch):
    inactive = _final(monkeypatch, _chain(state=lifecycle.PaperCandidateEligibilityState.SUSPENDED))
    assert PaperRuntimeReadinessBlocker.W85_CANDIDATE_NOT_ACTIVE in inactive.blocker_codes
    expired = _final(monkeypatch, _chain(valid_until=AT + timedelta(seconds=1)))
    assert PaperRuntimeReadinessBlocker.W85_ADMISSION_EXPIRED in expired.blocker_codes


def test_cross_account_and_cross_strategy_evidence_are_integrity_errors(monkeypatch):
    chain = list(_chain())
    market = chain[4]
    object.__setattr__(market, "account_id", "22222222-2222-4222-8222-222222222222")
    object.__setattr__(market, "proof_hash", market_module._hash(market_module._proof_payload(market, include_hash=False)))
    with pytest.raises(PaperRuntimeFinalReadinessIntegrityError, match="cross-account"):
        _final(monkeypatch, tuple(chain))

    chain = list(_chain())
    safety = chain[5]
    object.__setattr__(safety, "selected_strategy_id", "other-strategy")
    object.__setattr__(safety, "proof_hash", safety_module._hash(safety_module._proof_payload(safety, include_hash=False)))
    with pytest.raises(PaperRuntimeFinalReadinessIntegrityError, match="admission/strategy"):
        _final(monkeypatch, tuple(chain))


def test_hash_tamper_and_rehashed_authority_escalation_are_rejected(monkeypatch):
    chain = list(_chain())
    object.__setattr__(chain[3], "proof_hash", "0" * 64)
    with pytest.raises(PaperRuntimeFinalReadinessIntegrityError, match="asset proof hash"):
        _final(monkeypatch, tuple(chain))

    chain = list(_chain())
    broker = chain[2]
    object.__setattr__(broker, "paper_execution_authorized", True)
    object.__setattr__(broker, "proof_hash", broker_module._hash(broker_module._proof_payload(broker, include_hash=False)))
    with pytest.raises(PaperRuntimeFinalReadinessIntegrityError, match="authority escalation"):
        _final(monkeypatch, tuple(chain))


def test_internal_process_clock_rejects_future_evidence(monkeypatch):
    with pytest.raises(PaperRuntimeFinalReadinessIntegrityError, match="process future"):
        _final(monkeypatch, _chain(), now=AT - timedelta(seconds=1))


def test_receipt_self_guards_hash_and_authority(monkeypatch):
    receipt = _final(monkeypatch, _chain())
    with pytest.raises(PaperRuntimeFinalReadinessIntegrityError, match="receipt hash mismatch"):
        replace(receipt, receipt_hash="0" * 64)
    with pytest.raises(PaperRuntimeFinalReadinessIntegrityError, match="may not grant"):
        replace(receipt, paper_execution_authorized=True)


def test_final_policy_can_only_tighten_freshness_and_ttl():
    assert PaperRuntimeFinalReadinessPolicy().market_age_seconds == 5
    assert PaperRuntimeFinalReadinessPolicy(ready_ttl_seconds=1).fingerprint
    with pytest.raises(ValueError):
        PaperRuntimeFinalReadinessPolicy(market_age_seconds=6)
    with pytest.raises(ValueError):
        PaperRuntimeFinalReadinessPolicy(source_age_seconds=True)
