from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import autotrade.paper_runtime_asset_truth as asset_module
import autotrade.paper_runtime_broker_truth as broker_module
import autotrade.paper_runtime_candidate_identity as candidate_module
import autotrade.paper_runtime_market_truth as market_module
from autotrade.brokers.alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation, crypto_asset_path
from autotrade.brokers.alpaca_paper_crypto_market_data import AlpacaPaperCryptoMarketAttestation
from autotrade.domain import MarketSnapshot
from autotrade.paper_runtime_asset_truth import bind_paper_runtime_asset_truth
from autotrade.paper_runtime_market_truth import (
    PaperRuntimeMarketTruthIntegrityError,
    PaperRuntimeMarketTruthPolicy,
    PaperRuntimeMarketTruthProof,
    bind_paper_runtime_market_truth,
)


AT = datetime(2026, 8, 25, 19, 45, tzinfo=timezone.utc)
ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"


def _candidate():
    values = {
        "proof_id": "w86-market-hardening-candidate",
        "contract_version": candidate_module.PAPER_RUNTIME_CANDIDATE_IDENTITY_VERSION,
        "w85_source_snapshot_hash": "1" * 64,
        "authority_key": "2" * 64,
        "admission_id": "w86-market-hardening-admission",
        "admission_hash": "3" * 64,
        "final_admission_verification_hash": "4" * 64,
        "w83_resolution_id": "w83-market-hardening-resolution",
        "w83_resolution_hash": "5" * 64,
        "w83_binding_hash": "6" * 64,
        "selected_trial_fingerprint": "7" * 64,
        "selected_strategy_id": "strategy-market-hardening",
        "selected_strategy_version": "v1",
        "strategy_spec_hash": "8" * 64,
        "loaded_runtime_code_hash": "9" * 64,
        "fee_product_economics_hash": "a" * 64,
        "intent_fingerprint": "b" * 64,
        "product_id": "product-market-hardening",
        "asset_class": "crypto",
        "venue": "alpaca-paper-model",
        "symbol": "TEST-USD",
        "side": "BUY",
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
        proof_hash=candidate_module._hash(values),
    )


def _broker(candidate):
    values = {
        "proof_id": "w86-market-hardening-broker",
        "contract_version": broker_module.PAPER_RUNTIME_BROKER_TRUTH_VERSION,
        "candidate_identity_hash": candidate.proof_hash,
        "policy_hash": "c" * 64,
        "authority_key": candidate.authority_key,
        "admission_hash": candidate.admission_hash,
        "product_id": candidate.product_id,
        "asset_class": candidate.asset_class,
        "venue": candidate.venue,
        "symbol": candidate.symbol,
        "quote_currency": candidate.quote_currency,
        "account_id": ACCOUNT_ID,
        "account_reference": "d" * 64,
        "credential_reference": "e" * 64,
        "account_attestation_fingerprint": "f" * 64,
        "account_request_id": "hardening-account-request",
        "account_attested_at": AT,
        "crypto_status_fingerprint": "0" * 64,
        "crypto_status_request_id": "hardening-crypto-request",
        "crypto_status_response_sha256": "1" * 64,
        "crypto_status_observed_at": AT,
        "flat_account_fingerprint": "2" * 64,
        "position_count": 0,
        "open_order_count": 0,
        "positions_response_hash": "3" * 64,
        "orders_response_hash": "4" * 64,
        "positions_request_id": "hardening-positions-request",
        "orders_request_id": "hardening-orders-request",
        "portfolio_attested_at": AT,
        "source_host": broker_module.ALPACA_PAPER_TRADING_HOST,
        "account_path": broker_module.ALPACA_PAPER_ACCOUNT_PATH,
        "positions_path": broker_module.POSITIONS_PATH,
        "orders_path": f"{broker_module.ORDERS_PATH}?{broker_module.ORDERS_QUERY}",
        "observed_at": AT,
        "broker_truth_valid_until": AT + timedelta(seconds=30),
        "account_environment_verified": True,
        "crypto_entitlement_verified": True,
        "portfolio_truth_verified": True,
        "clean_for_candidate_start": True,
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


def _asset(candidate, broker):
    attestation = AlpacaPaperCryptoAssetAttestation(
        symbol="TEST/USD",
        asset_id="asset-market-hardening",
        asset_class="crypto",
        exchange="CRYPTO",
        status="active",
        tradable=True,
        fractionable=True,
        marginable=False,
        shortable=False,
        min_order_size=Decimal("0.001"),
        min_trade_increment=Decimal("0.001"),
        price_increment=Decimal("0.01"),
        account_attestation_fingerprint=broker.account_attestation_fingerprint,
        credential_reference=broker.credential_reference,
        observed_at=AT,
        request_id="hardening-asset-request",
        response_sha256="5" * 64,
        source_path=crypto_asset_path("TEST/USD"),
    )
    return bind_paper_runtime_asset_truth(
        proof_id="w86-market-hardening-asset",
        candidate_identity=candidate,
        broker_truth=broker,
        asset=attestation,
        observed_at=AT,
    )


def _market(*, received_at=None, quote_at=None, trade_at=None):
    received = received_at or AT + timedelta(seconds=1)
    quote = quote_at or AT
    trade = trade_at or AT
    return AlpacaPaperCryptoMarketAttestation(
        market=MarketSnapshot(
            symbol="TEST/USD",
            bid=Decimal("100"),
            ask=Decimal("101"),
            last=Decimal("100.5"),
            observed_at=received,
        ),
        location="us",
        quote_observed_at=quote,
        trade_observed_at=trade,
        received_at=received,
        quote_response_sha256="6" * 64,
        trade_response_sha256="7" * 64,
    )


def _chain():
    candidate = _candidate()
    broker = _broker(candidate)
    asset = _asset(candidate, broker)
    return candidate, broker, asset


def _proof():
    candidate, broker, asset = _chain()
    market = _market()
    return bind_paper_runtime_market_truth(
        proof_id="w86-market-hardening-proof",
        candidate_identity=candidate,
        broker_truth=broker,
        asset_truth=asset,
        market=market,
        observed_at=market.received_at,
    )


def _rehashed(value: PaperRuntimeMarketTruthProof, **changes):
    values = {
        name: getattr(value, name)
        for name in PaperRuntimeMarketTruthProof.__dataclass_fields__
        if name != "proof_hash"
    }
    values.update(changes)
    return PaperRuntimeMarketTruthProof(
        **values,
        proof_hash=market_module._hash(market_module._payload_from_values(values)),
    )


def _rehash_candidate(value):
    object.__setattr__(
        value,
        "proof_hash",
        candidate_module._hash(candidate_module._payload(value, include_hash=False)),
    )


def _rehash_broker(value):
    object.__setattr__(
        value,
        "proof_hash",
        broker_module._hash(broker_module._proof_payload(value, include_hash=False)),
    )


def _rehash_asset(value):
    object.__setattr__(
        value,
        "proof_hash",
        asset_module._hash(asset_module._proof_payload(value, include_hash=False)),
    )


@pytest.mark.parametrize("field,value", [
    ("max_quote_age_seconds", True),
    ("max_trade_age_seconds", 0),
    ("max_market_receipt_age_seconds", 31),
    ("max_asset_market_skew_seconds", -1),
])
def test_policy_rejects_noncanonical_windows(field, value):
    with pytest.raises(ValueError, match="integer seconds"):
        PaperRuntimeMarketTruthPolicy(**{field: value})


def test_policy_fingerprint_is_deterministic_and_sensitive():
    left = PaperRuntimeMarketTruthPolicy()
    right = PaperRuntimeMarketTruthPolicy()
    changed = PaperRuntimeMarketTruthPolicy(max_quote_age_seconds=6)
    assert left.fingerprint == right.fingerprint
    assert left.fingerprint != changed.fingerprint


def test_receipt_staleness_and_trade_future_are_independently_blocked():
    candidate, broker, asset = _chain()
    market = _market(received_at=AT + timedelta(seconds=1))
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="market receipt is stale"):
        bind_paper_runtime_market_truth(
            proof_id="stale-receipt",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=market,
            observed_at=AT + timedelta(seconds=7),
        )

    market = _market(
        received_at=AT + timedelta(seconds=1),
        quote_at=AT,
        trade_at=AT + timedelta(seconds=2),
    )
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="trade timestamp"):
        bind_paper_runtime_market_truth(
            proof_id="future-trade",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=market,
            observed_at=AT + timedelta(seconds=2),
        )


def test_chain_public_type_guards_fail_closed():
    candidate, broker, asset = _chain()
    market = _market()
    with pytest.raises(TypeError, match="candidate_identity"):
        bind_paper_runtime_market_truth(
            proof_id="bad-candidate-type",
            candidate_identity=object(),
            broker_truth=broker,
            asset_truth=asset,
            market=market,
            observed_at=market.received_at,
        )
    with pytest.raises(TypeError, match="broker_truth"):
        bind_paper_runtime_market_truth(
            proof_id="bad-broker-type",
            candidate_identity=candidate,
            broker_truth=object(),
            asset_truth=asset,
            market=market,
            observed_at=market.received_at,
        )
    with pytest.raises(TypeError, match="asset_truth"):
        bind_paper_runtime_market_truth(
            proof_id="bad-asset-type",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=object(),
            market=market,
            observed_at=market.received_at,
        )
    with pytest.raises(TypeError, match="market must"):
        bind_paper_runtime_market_truth(
            proof_id="bad-market-type",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=object(),
            observed_at=market.received_at,
        )


def test_upstream_hash_tamper_is_detected_for_candidate_and_broker():
    candidate, broker, asset = _chain()
    object.__setattr__(candidate, "proof_hash", "8" * 64)
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="candidate identity proof hash"):
        bind_paper_runtime_market_truth(
            proof_id="tampered-candidate",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=_market(),
            observed_at=AT + timedelta(seconds=1),
        )

    candidate, broker, asset = _chain()
    object.__setattr__(broker, "proof_hash", "8" * 64)
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="broker truth proof hash"):
        bind_paper_runtime_market_truth(
            proof_id="tampered-broker",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=_market(),
            observed_at=AT + timedelta(seconds=1),
        )


def test_verified_candidate_product_contract_is_rechecked_not_trusted():
    candidate, broker, asset = _chain()
    object.__setattr__(candidate, "asset_class", "equity")
    _rehash_candidate(candidate)
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="exact verified"):
        bind_paper_runtime_market_truth(
            proof_id="candidate-product-drift",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=_market(),
            observed_at=AT + timedelta(seconds=1),
        )


def test_cross_candidate_chain_mismatch_is_rejected_even_with_valid_local_hashes():
    candidate, broker, asset = _chain()
    object.__setattr__(broker, "candidate_identity_hash", "9" * 64)
    _rehash_broker(broker)
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="broker candidate hash"):
        bind_paper_runtime_market_truth(
            proof_id="cross-candidate-broker",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=_market(),
            observed_at=AT + timedelta(seconds=1),
        )


def test_unverified_upstream_truth_and_upstream_authority_are_rejected():
    candidate, broker, asset = _chain()
    object.__setattr__(asset, "read_only_asset_truth", False)
    _rehash_asset(asset)
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="fully verified read-only"):
        bind_paper_runtime_market_truth(
            proof_id="asset-not-read-only",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=_market(),
            observed_at=AT + timedelta(seconds=1),
        )

    candidate, broker, asset = _chain()
    object.__setattr__(broker, "paper_runtime_ready", True)
    _rehash_broker(broker)
    object.__setattr__(asset, "broker_truth_hash", broker.proof_hash)
    _rehash_asset(asset)
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="may not grant"):
        bind_paper_runtime_market_truth(
            proof_id="upstream-authority",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=_market(),
            observed_at=AT + timedelta(seconds=1),
        )


def test_candidate_symbol_and_asset_pair_are_rederived():
    candidate, broker, asset = _chain()
    object.__setattr__(candidate, "symbol", "TEST/USD")
    _rehash_candidate(candidate)
    object.__setattr__(broker, "candidate_identity_hash", candidate.proof_hash)
    object.__setattr__(broker, "symbol", candidate.symbol)
    _rehash_broker(broker)
    object.__setattr__(asset, "candidate_identity_hash", candidate.proof_hash)
    object.__setattr__(asset, "candidate_symbol", candidate.symbol)
    object.__setattr__(asset, "broker_truth_hash", broker.proof_hash)
    _rehash_asset(asset)
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="BASE-QUOTE"):
        bind_paper_runtime_market_truth(
            proof_id="bad-symbol-form",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=_market(),
            observed_at=AT + timedelta(seconds=1),
        )

    candidate, broker, asset = _chain()
    object.__setattr__(asset, "canonical_broker_pair", "OTHER/USD")
    _rehash_asset(asset)
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="canonical pair"):
        bind_paper_runtime_market_truth(
            proof_id="bad-asset-pair",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=_market(),
            observed_at=AT + timedelta(seconds=1),
        )


def test_market_attestation_source_snapshot_and_price_are_rechecked():
    candidate, broker, asset = _chain()
    market = _market()
    object.__setattr__(market, "source_host", "example.invalid")
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="source is not canonical"):
        bind_paper_runtime_market_truth(
            proof_id="bad-market-host",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=market,
            observed_at=market.received_at,
        )

    market = _market()
    object.__setattr__(
        market,
        "market",
        replace(market.market, observed_at=market.received_at - timedelta(seconds=1)),
    )
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="observed_at"):
        bind_paper_runtime_market_truth(
            proof_id="bad-market-observed-at",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=market,
            observed_at=market.received_at,
        )

    market = _market()
    object.__setattr__(market, "market", replace(market.market, bid=Decimal("0")))
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="finite positive"):
        bind_paper_runtime_market_truth(
            proof_id="bad-market-price",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=market,
            observed_at=market.received_at,
        )

    market = _market()
    object.__setattr__(
        market,
        "market",
        replace(market.market, bid=Decimal("102"), ask=Decimal("101")),
    )
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="bid exceeds ask"):
        bind_paper_runtime_market_truth(
            proof_id="crossed-market",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=market,
            observed_at=market.received_at,
        )


def test_market_attestation_hash_and_timestamp_guards_are_rechecked():
    candidate, broker, asset = _chain()
    market = _market()
    object.__setattr__(market, "quote_response_sha256", "not-a-hash")
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="lowercase sha256"):
        bind_paper_runtime_market_truth(
            proof_id="bad-response-hash",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=market,
            observed_at=market.received_at,
        )

    market = _market()
    object.__setattr__(market, "quote_observed_at", datetime(2026, 8, 25, 19, 45))
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="timezone-aware"):
        bind_paper_runtime_market_truth(
            proof_id="naive-market-time",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=market,
            observed_at=market.received_at,
        )


def test_final_proof_constructor_revalidates_semantics_even_with_recomputed_hash():
    proof = _proof()
    cases = [
        ({"contract_version": "wrong"}, "version"),
        ({"account_id": "bad"}, "account_id"),
        ({"venue": "other"}, "supports only"),
        ({"candidate_symbol": "OTHER-USD"}, "BASE-QUOTE"),
        ({"canonical_broker_pair": "OTHER/USD"}, "candidate currencies"),
        ({"source_host": "example.invalid"}, "source host"),
        ({"location": "eu"}, "location"),
        ({"quote_path": "/wrong"}, "endpoint provenance"),
        ({"bid_price": Decimal("0")}, "finite positive"),
        ({"bid_price": Decimal("102")}, "bid exceeds ask"),
        ({"quote_age_seconds": Decimal("99")}, "quote age is inconsistent"),
        ({"trade_age_seconds": Decimal("99")}, "trade age is inconsistent"),
        ({"market_receipt_age_seconds": Decimal("99")}, "market receipt age is inconsistent"),
        ({"quote_fresh": False}, "requires both quote and trade fresh"),
        ({"network_write_performed": True}, "may not grant"),
    ]
    for changes, match in cases:
        with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match=match):
            _rehashed(proof, **changes)


def test_final_proof_rejects_snapshot_and_attestation_fingerprint_drift():
    proof = _proof()
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="snapshot fingerprint"):
        _rehashed(proof, market_snapshot_fingerprint="9" * 64)
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="attestation fingerprint"):
        _rehashed(proof, market_attestation_fingerprint="9" * 64)


def test_final_proof_to_dict_is_complete_and_hash_bound():
    proof = _proof()
    payload = proof.to_dict()
    assert payload["proof_hash"] == proof.proof_hash
    assert payload["candidate_identity_hash"] == proof.candidate_identity_hash
    assert payload["quote_age_seconds"] == "1"
    assert payload["paper_runtime_ready"] is False
    assert payload["live_trading"] == "BLOCKED"
