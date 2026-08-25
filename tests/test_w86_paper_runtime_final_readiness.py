from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
from autotrade.domain import MarketSnapshot, market_fingerprint
from autotrade.paper_runtime_final_readiness import (
    PaperRuntimeFinalReadinessIntegrityError,
    PaperRuntimeFinalReadinessPolicy,
    PaperRuntimeReadinessBlocker,
    PaperRuntimeReadinessStatus,
    finalize_paper_runtime_readiness,
)

T0 = datetime(2026, 8, 25, 21, 30, tzinfo=timezone.utc)
NOW = T0 + timedelta(seconds=2)
ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
CREDENTIAL = "c" * 64
ACCOUNT_ATTESTATION = "d" * 64
ASSET_ATTESTATION = "e" * 64
AUTHORITY_KEY = "1" * 64
ADMISSION_HASH = "2" * 64
STRATEGY_ID = "strategy-final-readiness"
PRODUCT_ID = "product-final-readiness"


def _source(*, reproved_at=T0 + timedelta(seconds=1), valid_until=T0 + timedelta(minutes=10), cap=Decimal("5"), order_cap=1, state=lifecycle.PaperCandidateEligibilityState.ACTIVE):
    values = {
        "proof_id": "source-final-readiness",
        "contract_version": source_module.W85_DURABLE_ELIGIBILITY_SNAPSHOT_VERSION,
        "authority_key": AUTHORITY_KEY,
        "admission_id": "admission-final-readiness",
        "admission_hash": ADMISSION_HASH,
        "policy_id": "policy-final-readiness",
        "policy_hash": "3" * 64,
        "policy_registration_hash": "4" * 64,
        "final_admission_verification_hash": "5" * 64,
        "supplied_final_eligibility_hash": "6" * 64,
        "w84_admission_source_proof_hash": "7" * 64,
        "selected_trial_fingerprint": "8" * 64,
        "strategy_spec_hash": "9" * 64,
        "loaded_runtime_code_hash": "a" * 64,
        "fee_product_economics_hash": "b" * 64,
        "intent_fingerprint": "c" * 64,
        "admission_valid_until": valid_until,
        "probation_notional_cap_usd": cap,
        "probation_order_cap": order_cap,
        "lifecycle_head_hash": "d" * 64,
        "lifecycle_events_count": 0,
        "current_state": state,
        "supplied_eligibility_observed_at": T0,
        "reproved_at": reproved_at,
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


def _candidate(source, *, side="BUY"):
    values = {
        "proof_id": "candidate-final-readiness",
        "contract_version": candidate_module.PAPER_RUNTIME_CANDIDATE_IDENTITY_VERSION,
        "w85_source_snapshot_hash": source.proof_hash,
        "authority_key": AUTHORITY_KEY,
        "admission_id": source.admission_id,
        "admission_hash": ADMISSION_HASH,
        "final_admission_verification_hash": source.final_admission_verification_hash,
        "w83_resolution_id": "w83-final-readiness",
        "w83_resolution_hash": "e" * 64,
        "w83_binding_hash": "f" * 64,
        "selected_trial_fingerprint": source.selected_trial_fingerprint,
        "selected_strategy_id": STRATEGY_ID,
        "selected_strategy_version": "v1",
        "strategy_spec_hash": source.strategy_spec_hash,
        "loaded_runtime_code_hash": source.loaded_runtime_code_hash,
        "fee_product_economics_hash": source.fee_product_economics_hash,
        "intent_fingerprint": source.intent_fingerprint,
        "product_id": PRODUCT_ID,
        "asset_class": "crypto",
        "venue": "alpaca-paper-model",
        "symbol": "TEST-USD",
        "side": side,
        "base_currency": "TEST",
        "quote_currency": "USD",
        "product_identity_verified": True,
        "strategy_runtime_identity_verified": True,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return candidate_module.PaperRuntimeCandidateIdentityProof(
        **values,
        proof_hash=candidate_module._hash(candidate_module._payload_from_values(values)),
    )


def _broker(candidate, *, observed_at=T0 + timedelta(seconds=1), positions=0, orders=0):
    values = {
        "proof_id": "broker-final-readiness",
        "contract_version": broker_module.PAPER_RUNTIME_BROKER_TRUTH_VERSION,
        "candidate_identity_hash": candidate.proof_hash,
        "policy_hash": "0" * 64,
        "authority_key": AUTHORITY_KEY,
        "admission_hash": ADMISSION_HASH,
        "product_id": PRODUCT_ID,
        "asset_class": "crypto",
        "venue": "alpaca-paper-model",
        "symbol": "TEST-USD",
        "quote_currency": "USD",
        "account_id": ACCOUNT_ID,
        "account_reference": "1" * 64,
        "credential_reference": CREDENTIAL,
        "account_attestation_fingerprint": ACCOUNT_ATTESTATION,
        "account_request_id": "account-final",
        "account_attested_at": observed_at,
        "crypto_status_fingerprint": "2" * 64,
        "crypto_status_request_id": "crypto-final",
        "crypto_status_response_sha256": "3" * 64,
        "crypto_status_observed_at": observed_at,
        "flat_account_fingerprint": "4" * 64,
        "position_count": positions,
        "open_order_count": orders,
        "positions_response_hash": "5" * 64,
        "orders_response_hash": "6" * 64,
        "positions_request_id": "positions-final",
        "orders_request_id": "orders-final",
        "portfolio_attested_at": observed_at,
        "source_host": broker_module.ALPACA_PAPER_TRADING_HOST,
        "account_path": broker_module.ALPACA_PAPER_ACCOUNT_PATH,
        "positions_path": broker_module.POSITIONS_PATH,
        "orders_path": f"{broker_module.ORDERS_PATH}?{broker_module.ORDERS_QUERY}",
        "observed_at": observed_at,
        "broker_truth_valid_until": observed_at + timedelta(seconds=30),
        "account_environment_verified": True,
        "crypto_entitlement_verified": True,
        "portfolio_truth_verified": True,
        "clean_for_candidate_start": positions == 0 and orders == 0,
        "read_only_broker_truth": True,
        "network_write_performed": False,
        "paper_runtime_ready": False,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return broker_module.PaperRuntimeBrokerTruthProof(
        **values,
        proof_hash=broker_module._hash(broker_module._payload_from_values(values)),
    )


def _asset(candidate, broker, *, observed_at=T0 + timedelta(seconds=1), min_order=Decimal("0.0011"), trade_increment=Decimal("0.001"), price_increment=Decimal("0.01")):
    values = {
        "proof_id": "asset-final-readiness",
        "contract_version": asset_module.PAPER_RUNTIME_ASSET_TRUTH_VERSION,
        "candidate_identity_hash": candidate.proof_hash,
        "broker_truth_hash": broker.proof_hash,
        "policy_hash": "7" * 64,
        "authority_key": AUTHORITY_KEY,
        "admission_hash": ADMISSION_HASH,
        "product_id": PRODUCT_ID,
        "venue": "alpaca-paper-model",
        "candidate_symbol": "TEST-USD",
        "base_currency": "TEST",
        "quote_currency": "USD",
        "canonical_broker_pair": "TEST/USD",
        "symbol_mapping_verified": True,
        "account_id": ACCOUNT_ID,
        "account_attestation_fingerprint": ACCOUNT_ATTESTATION,
        "credential_reference": CREDENTIAL,
        "asset_attestation_fingerprint": ASSET_ATTESTATION,
        "asset_contract_fingerprint": "8" * 64,
        "asset_response_sha256": "9" * 64,
        "asset_request_id": "asset-final",
        "asset_id": "asset-final-readiness",
        "asset_class": "crypto",
        "exchange": "CRYPTO",
        "status": "active",
        "tradable": True,
        "fractionable": True,
        "marginable": False,
        "shortable": False,
        "min_order_size": min_order,
        "min_trade_increment": trade_increment,
        "price_increment": price_increment,
        "source_host": asset_module.ALPACA_PAPER_TRADING_HOST,
        "source_path": asset_module.crypto_asset_path("TEST/USD"),
        "broker_observed_at": broker.observed_at,
        "asset_observed_at": observed_at,
        "observed_at": observed_at,
        "asset_truth_valid_until": observed_at + timedelta(seconds=30),
        "asset_metadata_verified": True,
        "read_only_asset_truth": True,
        "network_write_performed": False,
        "paper_runtime_ready": False,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return asset_module.PaperRuntimeAssetTruthProof(
        **values,
        proof_hash=asset_module._hash(asset_module._payload_from_values(values)),
    )


def _market(candidate, broker, asset, *, observed_at=T0 + timedelta(seconds=2), bid=Decimal("99.999"), ask=Decimal("100.001")):
    quote_at = observed_at - timedelta(seconds=1)
    trade_at = observed_at - timedelta(seconds=1)
    snapshot = MarketSnapshot(
        symbol="TEST/USD",
        bid=bid,
        ask=ask,
        last=(bid + ask) / Decimal("2"),
        observed_at=observed_at,
    )
    snapshot_fp = market_fingerprint(snapshot)
    attestation_payload = {
        "market_fingerprint": snapshot_fp,
        "location": market_module.CRYPTO_LOCATION,
        "quote_observed_at": quote_at.isoformat(),
        "trade_observed_at": trade_at.isoformat(),
        "received_at": observed_at.isoformat(),
        "quote_response_sha256": "a" * 64,
        "trade_response_sha256": "b" * 64,
        "source_host": market_module.ALPACA_MARKET_DATA_HOST,
    }
    values = {
        "proof_id": "market-final-readiness",
        "contract_version": market_module.PAPER_RUNTIME_MARKET_TRUTH_VERSION,
        "candidate_identity_hash": candidate.proof_hash,
        "broker_truth_hash": broker.proof_hash,
        "asset_truth_hash": asset.proof_hash,
        "policy_hash": "c" * 64,
        "authority_key": AUTHORITY_KEY,
        "admission_hash": ADMISSION_HASH,
        "product_id": PRODUCT_ID,
        "venue": "alpaca-paper-model",
        "candidate_symbol": "TEST-USD",
        "base_currency": "TEST",
        "quote_currency": "USD",
        "canonical_broker_pair": "TEST/USD",
        "account_id": ACCOUNT_ID,
        "credential_reference": CREDENTIAL,
        "asset_attestation_fingerprint": ASSET_ATTESTATION,
        "market_attestation_fingerprint": market_module._hash(attestation_payload),
        "market_snapshot_fingerprint": snapshot_fp,
        "source_host": market_module.ALPACA_MARKET_DATA_HOST,
        "location": market_module.CRYPTO_LOCATION,
        "quote_path": market_module.LATEST_QUOTE_PATH,
        "trade_path": market_module.LATEST_TRADE_PATH,
        "exact_query": market_module.crypto_exact_query("TEST/USD"),
        "quote_response_sha256": "a" * 64,
        "trade_response_sha256": "b" * 64,
        "bid_price": bid,
        "ask_price": ask,
        "trade_price": snapshot.last,
        "quote_observed_at": quote_at,
        "trade_observed_at": trade_at,
        "market_received_at": observed_at,
        "asset_observed_at": asset.observed_at,
        "observed_at": observed_at,
        "quote_age_seconds": Decimal("1"),
        "trade_age_seconds": Decimal("1"),
        "market_receipt_age_seconds": Decimal("0"),
        "market_truth_valid_until": observed_at + timedelta(seconds=5),
        "quote_fresh": True,
        "trade_fresh": True,
        "both_sides_fresh": True,
        "market_truth_verified": True,
        "read_only_market_truth": True,
        "network_write_performed": False,
        "paper_runtime_ready": False,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return market_module.PaperRuntimeMarketTruthProof(
        **values,
        proof_hash=market_module._hash(market_module._payload_from_values(values)),
    )


def _safety(candidate, *, observed_at=T0 + timedelta(seconds=1)):
    values = {
        "proof_id": "safety-final-readiness",
        "contract_version": safety_module.PAPER_RUNTIME_SAFETY_HEALTH_TRUTH_VERSION,
        "candidate_identity_hash": candidate.proof_hash,
        "policy_hash": "d" * 64,
        "authority_key": AUTHORITY_KEY,
        "admission_hash": ADMISSION_HASH,
        "selected_strategy_id": STRATEGY_ID,
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
        "safety_updated_at": observed_at,
        "strategy_health_version": 1,
        "strategy_health_fingerprint": "0" * 64,
        "strategy_health_updated_at": observed_at,
        "strategy_recovery_ack_head": "1" * 64,
        "strategy_recovery_ack_count": 1,
        "strategy_bridge_version": 1,
        "strategy_bridge_fingerprint": "2" * 64,
        "strategy_bridge_updated_at": observed_at,
        "portfolio_health_version": 1,
        "portfolio_health_fingerprint": "3" * 64,
        "portfolio_health_updated_at": observed_at,
        "portfolio_recovery_ack_head": "GENESIS",
        "portfolio_recovery_ack_count": 0,
        "portfolio_bridge_version": 1,
        "portfolio_bridge_fingerprint": "4" * 64,
        "portfolio_bridge_updated_at": observed_at,
        "observed_at": observed_at,
        "safety_health_valid_until": observed_at + timedelta(seconds=30),
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


def _chain(*, side="BUY", source_kwargs=None, broker_kwargs=None, asset_kwargs=None, market_kwargs=None, safety_kwargs=None):
    source = _source(**(source_kwargs or {}))
    candidate = _candidate(source, side=side)
    broker = _broker(candidate, **(broker_kwargs or {}))
    asset = _asset(candidate, broker, **(asset_kwargs or {}))
    market = _market(candidate, broker, asset, **(market_kwargs or {}))
    safety = _safety(candidate, **(safety_kwargs or {}))
    return source, candidate, broker, asset, market, safety


def _final(monkeypatch, chain):
    monkeypatch.setattr(final_module, "_now_utc", lambda: NOW)
    return finalize_paper_runtime_readiness(
        receipt_id="final-readiness-test",
        source_snapshot=chain[0],
        candidate_identity=chain[1],
        broker_truth=chain[2],
        asset_truth=chain[3],
        market_truth=chain[4],
        safety_health_truth=chain[5],
    )


def test_ready_receipt_is_finite_and_never_execution_authority(monkeypatch):
    receipt = _final(monkeypatch, _chain())
    assert receipt.status is PaperRuntimeReadinessStatus.READY
    assert receipt.blocker_codes == ()
    assert receipt.paper_runtime_ready is True
    assert receipt.minimum_executable_quantity == Decimal("0.002")
    assert receipt.conservative_unit_price == Decimal("100.01")
    assert receipt.minimum_executable_notional_usd == Decimal("0.20002")
    assert receipt.probation_headroom_usd == Decimal("4.79998")
    assert receipt.valid_until == NOW + timedelta(seconds=5)
    assert receipt.freshness_verified is True
    assert receipt.minimum_notional_compatible is True
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


@pytest.mark.parametrize("min_order,expected_ready", ((Decimal("0.05"), True), (Decimal("0.0501"), False)))
def test_probation_cap_exact_fit_and_overflow(monkeypatch, min_order, expected_ready):
    receipt = _final(
        monkeypatch,
        _chain(
            asset_kwargs={"min_order": min_order, "trade_increment": Decimal("0.001"), "price_increment": Decimal("0.01")},
            market_kwargs={"bid": Decimal("100"), "ask": Decimal("100")},
        ),
    )
    assert receipt.paper_runtime_ready is expected_ready
    if expected_ready:
        assert receipt.minimum_executable_notional_usd == Decimal("5.00")
    else:
        assert PaperRuntimeReadinessBlocker.MINIMUM_NOTIONAL_EXCEEDS_PROBATION_CAP in receipt.blocker_codes
        assert receipt.valid_until == NOW
        assert receipt.minimum_notional_compatible is False
        assert receipt.freshness_verified is True


def test_non_flat_account_is_valid_truth_but_final_readiness_blocks(monkeypatch):
    receipt = _final(monkeypatch, _chain(broker_kwargs={"positions": 1}))
    assert receipt.status is PaperRuntimeReadinessStatus.BLOCKED
    assert PaperRuntimeReadinessBlocker.BROKER_ACCOUNT_NOT_FLAT in receipt.blocker_codes
    assert receipt.freshness_verified is True


def test_sell_candidate_is_not_flat_start_canary_readiness(monkeypatch):
    receipt = _final(monkeypatch, _chain(side="SELL"))
    assert receipt.blocker_codes == (PaperRuntimeReadinessBlocker.SIDE_NOT_SUPPORTED_FOR_FLAT_CANARY,)
    assert receipt.paper_runtime_ready is False
    assert receipt.paper_execution_authorized is False


@pytest.mark.parametrize(
    "kwargs,blocker",
    (
        ({"source_kwargs": {"reproved_at": T0 - timedelta(seconds=40)}}, PaperRuntimeReadinessBlocker.W85_SOURCE_STALE),
        ({"broker_kwargs": {"observed_at": T0 - timedelta(seconds=40)}, "asset_kwargs": {"observed_at": T0 - timedelta(seconds=39)}}, PaperRuntimeReadinessBlocker.BROKER_TRUTH_STALE),
        ({"broker_kwargs": {"observed_at": T0 - timedelta(seconds=41)}, "asset_kwargs": {"observed_at": T0 - timedelta(seconds=40)}}, PaperRuntimeReadinessBlocker.ASSET_TRUTH_STALE),
        ({"broker_kwargs": {"observed_at": T0 - timedelta(seconds=12)}, "asset_kwargs": {"observed_at": T0 - timedelta(seconds=11)}, "market_kwargs": {"observed_at": T0 - timedelta(seconds=10)}}, PaperRuntimeReadinessBlocker.MARKET_TRUTH_STALE),
        ({"safety_kwargs": {"observed_at": T0 - timedelta(seconds=40)}}, PaperRuntimeReadinessBlocker.SAFETY_HEALTH_TRUTH_STALE),
    ),
)
def test_each_runtime_truth_has_its_own_freshness_blocker(monkeypatch, kwargs, blocker):
    receipt = _final(monkeypatch, _chain(**kwargs))
    assert blocker in receipt.blocker_codes
    assert receipt.freshness_verified is False
    assert receipt.paper_runtime_ready is False


def test_expired_or_inactive_w85_source_blocks(monkeypatch):
    expired = _final(monkeypatch, _chain(source_kwargs={"valid_until": NOW - timedelta(seconds=1)}))
    assert PaperRuntimeReadinessBlocker.W85_ADMISSION_EXPIRED in expired.blocker_codes
    suspended = _final(monkeypatch, _chain(source_kwargs={"state": lifecycle.PaperCandidateEligibilityState.SUSPENDED}))
    assert PaperRuntimeReadinessBlocker.W85_CANDIDATE_NOT_ACTIVE in suspended.blocker_codes


def test_canonical_probation_envelope_cannot_be_expanded(monkeypatch):
    chain = _chain(source_kwargs={"cap": Decimal("5.01")})
    monkeypatch.setattr(final_module, "_now_utc", lambda: NOW)
    with pytest.raises(PaperRuntimeFinalReadinessIntegrityError, match="exceeds USD 5"):
        finalize_paper_runtime_readiness(
            receipt_id="bad-cap",
            source_snapshot=chain[0], candidate_identity=chain[1], broker_truth=chain[2],
            asset_truth=chain[3], market_truth=chain[4], safety_health_truth=chain[5],
        )


def test_cross_account_and_strategy_truth_are_integrity_failures(monkeypatch):
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


def test_tampered_proof_or_upstream_authority_escalation_fails_integrity(monkeypatch):
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


def test_future_component_timestamp_is_integrity_failure(monkeypatch):
    chain = _chain(safety_kwargs={"observed_at": NOW + timedelta(seconds=1)})
    with pytest.raises(PaperRuntimeFinalReadinessIntegrityError, match="process future"):
        _final(monkeypatch, chain)


def test_receipt_self_guard_rejects_hash_and_execution_escalation(monkeypatch):
    receipt = _final(monkeypatch, _chain())
    with pytest.raises(PaperRuntimeFinalReadinessIntegrityError, match="receipt hash mismatch"):
        replace(receipt, receipt_hash="0" * 64)
    with pytest.raises(PaperRuntimeFinalReadinessIntegrityError, match="may not grant"):
        replace(receipt, paper_execution_authorized=True)


def test_policy_is_tightening_only():
    assert PaperRuntimeFinalReadinessPolicy().market_age_seconds == 5
    assert PaperRuntimeFinalReadinessPolicy(ready_ttl_seconds=1).fingerprint
    with pytest.raises(ValueError):
        PaperRuntimeFinalReadinessPolicy(market_age_seconds=6)
    with pytest.raises(ValueError):
        PaperRuntimeFinalReadinessPolicy(source_age_seconds=True)
