from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

import autotrade.paper_runtime_asset_truth as asset_truth_module
import autotrade.paper_runtime_broker_truth as broker_module
import autotrade.paper_runtime_candidate_identity as candidate_module
from autotrade.brokers.alpaca_paper_crypto_asset import (
    AlpacaPaperCryptoAssetAttestation,
    crypto_asset_path,
)
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
)
from autotrade.paper_runtime_asset_truth import (
    PaperRuntimeAssetTruthIntegrityError,
    PaperRuntimeAssetTruthPolicy,
    PaperRuntimeAssetTruthProof,
    bind_paper_runtime_asset_truth,
    derive_alpaca_crypto_pair,
    read_and_bind_paper_runtime_asset_truth,
)


AT = datetime(2026, 8, 25, 19, 45, tzinfo=timezone.utc)
ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
ACCOUNT_FINGERPRINT = "1" * 64
CREDENTIAL_REFERENCE = "2" * 64


def _candidate(*, symbol: str = "TEST-USD", base: str = "TEST", quote: str = "USD"):
    values = {
        "proof_id": "w86-candidate-test",
        "contract_version": candidate_module.PAPER_RUNTIME_CANDIDATE_IDENTITY_VERSION,
        "w85_source_snapshot_hash": "1" * 64,
        "authority_key": "2" * 64,
        "admission_id": "admission-test",
        "admission_hash": "3" * 64,
        "final_admission_verification_hash": "4" * 64,
        "w83_resolution_id": "w83-resolution-test",
        "w83_resolution_hash": "5" * 64,
        "w83_binding_hash": "6" * 64,
        "selected_trial_fingerprint": "7" * 64,
        "selected_strategy_id": "strategy-test",
        "selected_strategy_version": "v1",
        "strategy_spec_hash": "8" * 64,
        "loaded_runtime_code_hash": "9" * 64,
        "fee_product_economics_hash": "a" * 64,
        "intent_fingerprint": "b" * 64,
        "product_id": "product-test",
        "asset_class": "crypto",
        "venue": "alpaca-paper-model",
        "symbol": symbol,
        "side": "BUY",
        "base_currency": base,
        "quote_currency": quote,
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


def _broker(candidate=None, *, at: datetime = AT, credential_reference: str = CREDENTIAL_REFERENCE):
    candidate = candidate or _candidate()
    values = {
        "proof_id": "w86-broker-test",
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
        "credential_reference": credential_reference,
        "account_attestation_fingerprint": ACCOUNT_FINGERPRINT,
        "account_request_id": "account-req",
        "account_attested_at": at,
        "crypto_status_fingerprint": "e" * 64,
        "crypto_status_request_id": "crypto-req",
        "crypto_status_response_sha256": "f" * 64,
        "crypto_status_observed_at": at,
        "flat_account_fingerprint": "0" * 64,
        "position_count": 0,
        "open_order_count": 0,
        "positions_response_hash": "1" * 64,
        "orders_response_hash": "2" * 64,
        "positions_request_id": "positions-req",
        "orders_request_id": "orders-req",
        "portfolio_attested_at": at,
        "source_host": broker_module.ALPACA_PAPER_TRADING_HOST,
        "account_path": broker_module.ALPACA_PAPER_ACCOUNT_PATH,
        "positions_path": broker_module.POSITIONS_PATH,
        "orders_path": f"{broker_module.ORDERS_PATH}?{broker_module.ORDERS_QUERY}",
        "observed_at": at,
        "broker_truth_valid_until": at + timedelta(seconds=30),
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


def _asset(
    broker=None,
    *,
    at: datetime = AT,
    symbol: str = "TEST/USD",
    account_fingerprint: str | None = None,
    credential_reference: str | None = None,
):
    broker = broker or _broker()
    return AlpacaPaperCryptoAssetAttestation(
        symbol=symbol,
        asset_id="asset-test",
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
        account_attestation_fingerprint=(
            account_fingerprint
            if account_fingerprint is not None
            else broker.account_attestation_fingerprint
        ),
        credential_reference=(
            credential_reference
            if credential_reference is not None
            else broker.credential_reference
        ),
        observed_at=at,
        request_id="asset-req",
        response_sha256="3" * 64,
        source_path=crypto_asset_path(symbol),
    )


def _bind(*, candidate=None, broker=None, asset=None, observed_at: datetime = AT, policy=None):
    candidate = candidate or _candidate()
    broker = broker or _broker(candidate)
    asset = asset or _asset(broker, at=observed_at)
    return bind_paper_runtime_asset_truth(
        proof_id="asset-truth-test",
        candidate_identity=candidate,
        broker_truth=broker,
        asset=asset,
        observed_at=observed_at,
        policy=policy,
    )


def _rehash_asset_truth(value: PaperRuntimeAssetTruthProof, **changes):
    values = {
        name: getattr(value, name)
        for name in PaperRuntimeAssetTruthProof.__dataclass_fields__
        if name != "proof_hash"
    }
    values.update(changes)
    return PaperRuntimeAssetTruthProof(
        **values,
        proof_hash=asset_truth_module._hash(asset_truth_module._payload_from_values(values)),
    )


def test_asset_truth_explicitly_maps_candidate_symbol_and_grants_no_authority():
    proof = _bind()

    assert derive_alpaca_crypto_pair(_candidate()) == "TEST/USD"
    assert proof.candidate_symbol == "TEST-USD"
    assert proof.canonical_broker_pair == "TEST/USD"
    assert proof.symbol_mapping_verified is True
    assert proof.min_order_size == Decimal("0.001")
    assert proof.min_trade_increment == Decimal("0.001")
    assert proof.price_increment == Decimal("0.01")
    assert proof.asset_metadata_verified is True
    assert proof.read_only_asset_truth is True
    assert proof.network_write_performed is False
    assert proof.paper_runtime_ready is False
    assert proof.paper_execution_authorized is False
    assert proof.external_execution_authorized is False
    assert proof.runtime_execution_authorized is False
    assert proof.capital_authority == "NONE"
    assert proof.live_trading == "BLOCKED"


def test_candidate_symbol_cannot_be_slash_form_even_if_rehashed():
    candidate = _candidate(symbol="TEST/USD")
    with pytest.raises(PaperRuntimeAssetTruthIntegrityError, match="BASE-QUOTE"):
        derive_alpaca_crypto_pair(candidate)


def test_asset_symbol_must_match_derived_pair():
    candidate = _candidate()
    broker = _broker(candidate)
    asset = _asset(broker, symbol="OTHER/USD")
    with pytest.raises(PaperRuntimeAssetTruthIntegrityError, match="symbol differs"):
        _bind(candidate=candidate, broker=broker, asset=asset)


def test_asset_account_and_credential_binding_are_exact():
    candidate = _candidate()
    broker = _broker(candidate)

    with pytest.raises(PaperRuntimeAssetTruthIntegrityError, match="different account"):
        _bind(
            candidate=candidate,
            broker=broker,
            asset=_asset(broker, account_fingerprint="4" * 64),
        )
    with pytest.raises(PaperRuntimeAssetTruthIntegrityError, match="different credential"):
        _bind(
            candidate=candidate,
            broker=broker,
            asset=_asset(broker, credential_reference="5" * 64),
        )


def test_asset_freshness_future_and_directional_skew_fail_closed():
    candidate = _candidate()
    broker = _broker(candidate)

    with pytest.raises(PaperRuntimeAssetTruthIntegrityError, match="stale"):
        _bind(
            candidate=candidate,
            broker=broker,
            asset=_asset(broker, at=AT),
            observed_at=AT + timedelta(seconds=6),
            policy=PaperRuntimeAssetTruthPolicy(max_asset_age_seconds=5),
        )
    with pytest.raises(PaperRuntimeAssetTruthIntegrityError, match="process future"):
        _bind(
            candidate=candidate,
            broker=broker,
            asset=_asset(broker, at=AT + timedelta(seconds=1)),
            observed_at=AT,
        )
    with pytest.raises(PaperRuntimeAssetTruthIntegrityError, match="predates"):
        _bind(
            candidate=candidate,
            broker=broker,
            asset=_asset(broker, at=AT - timedelta(seconds=1)),
            observed_at=AT,
        )
    with pytest.raises(PaperRuntimeAssetTruthIntegrityError, match="skew"):
        _bind(
            candidate=candidate,
            broker=broker,
            asset=_asset(broker, at=AT + timedelta(seconds=16)),
            observed_at=AT + timedelta(seconds=16),
        )


def test_expired_broker_truth_is_rejected_before_asset_truth():
    candidate = _candidate()
    broker = _broker(candidate)
    with pytest.raises(PaperRuntimeAssetTruthIntegrityError, match="broker truth is stale"):
        _bind(
            candidate=candidate,
            broker=broker,
            asset=_asset(broker, at=AT + timedelta(seconds=31)),
            observed_at=AT + timedelta(seconds=31),
        )


def test_asset_truth_constructor_rejects_authority_escalation_and_hash_tamper():
    proof = _bind()
    with pytest.raises(PaperRuntimeAssetTruthIntegrityError, match="may not grant"):
        _rehash_asset_truth(proof, paper_runtime_ready=True)
    with pytest.raises(PaperRuntimeAssetTruthIntegrityError, match="hash mismatch"):
        replace(proof, proof_hash="9" * 64)


class RecordingTransport:
    def __init__(self, response: AlpacaPaperHttpResponse):
        self.response = response
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        return self.response


def _runner_broker(credentials: AlpacaPaperCredentials):
    return _broker(credential_reference=credentials.credential_reference)


def test_runner_performs_one_exact_get_for_derived_pair_only():
    credentials = AlpacaPaperCredentials(key_id="paper-key-test", secret_key="paper-secret-test")
    candidate = _candidate()
    broker = _runner_broker(credentials)
    body = json.dumps(
        {
            "id": "asset-test",
            "symbol": "TEST/USD",
            "class": "crypto",
            "exchange": "CRYPTO",
            "status": "active",
            "tradable": True,
            "fractionable": True,
            "marginable": False,
            "shortable": False,
            "min_order_size": "0.001",
            "min_trade_increment": "0.001",
            "price_increment": "0.01",
        },
        separators=(",", ":"),
    ).encode()
    expected_url = "https://paper-api.alpaca.markets/v2/assets/TEST%2FUSD"
    transport = RecordingTransport(
        AlpacaPaperHttpResponse(
            status_code=200,
            body=body,
            final_url=expected_url,
            headers={"content-type": "application/json", "x-request-id": "asset-runner-req"},
        )
    )

    proof = read_and_bind_paper_runtime_asset_truth(
        proof_id="asset-runner-proof",
        candidate_identity=candidate,
        broker_truth=broker,
        credentials=credentials,
        config=AlpacaPaperGatewayConfig(enabled=True),
        observed_at=AT,
        transport=transport,
    )

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "GET"
    assert request.url == expected_url
    assert request.headers["Accept"] == "application/json"
    assert proof.canonical_broker_pair == "TEST/USD"
    assert proof.source_path == "/v2/assets/TEST%2FUSD"
    assert proof.network_write_performed is False
    assert proof.paper_runtime_ready is False


def test_runner_rejects_disabled_config_or_wrong_credentials_before_transport():
    credentials = AlpacaPaperCredentials(key_id="paper-key-test", secret_key="paper-secret-test")
    wrong_credentials = AlpacaPaperCredentials(key_id="other-paper-key", secret_key="other-paper-secret")
    candidate = _candidate()
    broker = _runner_broker(credentials)
    transport = RecordingTransport(
        AlpacaPaperHttpResponse(
            status_code=500,
            body=b"{}",
            final_url="https://paper-api.alpaca.markets/v2/assets/TEST%2FUSD",
            headers={"content-type": "application/json", "x-request-id": "unused-req"},
        )
    )

    with pytest.raises(PaperRuntimeAssetTruthIntegrityError, match="explicitly enabled"):
        read_and_bind_paper_runtime_asset_truth(
            proof_id="asset-disabled-proof",
            candidate_identity=candidate,
            broker_truth=broker,
            credentials=credentials,
            config=AlpacaPaperGatewayConfig(enabled=False),
            observed_at=AT,
            transport=transport,
        )
    assert transport.requests == []

    with pytest.raises(PaperRuntimeAssetTruthIntegrityError, match="credentials differ"):
        read_and_bind_paper_runtime_asset_truth(
            proof_id="asset-wrong-credential-proof",
            candidate_identity=candidate,
            broker_truth=broker,
            credentials=wrong_credentials,
            config=AlpacaPaperGatewayConfig(enabled=True),
            observed_at=AT,
            transport=transport,
        )
    assert transport.requests == []
