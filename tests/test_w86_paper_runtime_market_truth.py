from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

import autotrade.paper_runtime_broker_truth as broker_module
import autotrade.paper_runtime_candidate_identity as candidate_module
import autotrade.paper_runtime_market_truth as market_truth_module
from autotrade.brokers.alpaca_paper_crypto_asset import (
    AlpacaPaperCryptoAssetAttestation,
    crypto_asset_path,
)
from autotrade.brokers.alpaca_paper_crypto_market_data import (
    LATEST_QUOTE_PATH,
    LATEST_TRADE_PATH,
    AlpacaPaperCryptoMarketAttestation,
    AlpacaPaperCryptoMarketDataConfig,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_market_data import AlpacaPaperMarketDataHttpResponse
from autotrade.domain import MarketSnapshot
from autotrade.paper_runtime_asset_truth import bind_paper_runtime_asset_truth
from autotrade.paper_runtime_market_truth import (
    PaperRuntimeMarketTruthIntegrityError,
    PaperRuntimeMarketTruthPolicy,
    PaperRuntimeMarketTruthProof,
    bind_paper_runtime_market_truth,
    read_and_bind_paper_runtime_market_truth,
)


AT = datetime(2026, 8, 25, 19, 45, tzinfo=timezone.utc)
ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
ACCOUNT_FINGERPRINT = "1" * 64
CREDENTIAL_REFERENCE = "2" * 64


def _candidate(*, symbol: str = "TEST-USD", base: str = "TEST", quote: str = "USD"):
    values = {
        "proof_id": "w86-candidate-market-test",
        "contract_version": candidate_module.PAPER_RUNTIME_CANDIDATE_IDENTITY_VERSION,
        "w85_source_snapshot_hash": "1" * 64,
        "authority_key": "2" * 64,
        "admission_id": "admission-market-test",
        "admission_hash": "3" * 64,
        "final_admission_verification_hash": "4" * 64,
        "w83_resolution_id": "w83-resolution-market-test",
        "w83_resolution_hash": "5" * 64,
        "w83_binding_hash": "6" * 64,
        "selected_trial_fingerprint": "7" * 64,
        "selected_strategy_id": "strategy-market-test",
        "selected_strategy_version": "v1",
        "strategy_spec_hash": "8" * 64,
        "loaded_runtime_code_hash": "9" * 64,
        "fee_product_economics_hash": "a" * 64,
        "intent_fingerprint": "b" * 64,
        "product_id": "product-market-test",
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


def _broker(candidate=None, *, credential_reference: str = CREDENTIAL_REFERENCE):
    candidate = candidate or _candidate()
    values = {
        "proof_id": "w86-broker-market-test",
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
        "account_request_id": "account-market-req",
        "account_attested_at": AT,
        "crypto_status_fingerprint": "e" * 64,
        "crypto_status_request_id": "crypto-market-req",
        "crypto_status_response_sha256": "f" * 64,
        "crypto_status_observed_at": AT,
        "flat_account_fingerprint": "0" * 64,
        "position_count": 0,
        "open_order_count": 0,
        "positions_response_hash": "1" * 64,
        "orders_response_hash": "2" * 64,
        "positions_request_id": "positions-market-req",
        "orders_request_id": "orders-market-req",
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


def _asset_truth(candidate=None, broker=None):
    candidate = candidate or _candidate()
    broker = broker or _broker(candidate)
    attestation = AlpacaPaperCryptoAssetAttestation(
        symbol="TEST/USD",
        asset_id="asset-market-test",
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
        request_id="asset-market-req",
        response_sha256="3" * 64,
        source_path=crypto_asset_path("TEST/USD"),
    )
    return bind_paper_runtime_asset_truth(
        proof_id="asset-truth-market-test",
        candidate_identity=candidate,
        broker_truth=broker,
        asset=attestation,
        observed_at=AT,
    )


def _market(
    *,
    symbol: str = "TEST/USD",
    received_at: datetime | None = None,
    quote_at: datetime | None = None,
    trade_at: datetime | None = None,
    bid: Decimal = Decimal("100"),
    ask: Decimal = Decimal("101"),
    last: Decimal = Decimal("100.5"),
):
    received = received_at or AT + timedelta(seconds=1)
    quote_time = quote_at or AT
    trade_time = trade_at or AT
    return AlpacaPaperCryptoMarketAttestation(
        market=MarketSnapshot(
            symbol=symbol,
            bid=bid,
            ask=ask,
            last=last,
            observed_at=received,
        ),
        location="us",
        quote_observed_at=quote_time,
        trade_observed_at=trade_time,
        received_at=received,
        quote_response_sha256="4" * 64,
        trade_response_sha256="5" * 64,
    )


def _chain():
    candidate = _candidate()
    broker = _broker(candidate)
    asset = _asset_truth(candidate, broker)
    return candidate, broker, asset


def _bind(*, market=None, observed_at=None, policy=None):
    candidate, broker, asset = _chain()
    market = market or _market()
    observed_at = observed_at or market.received_at
    return bind_paper_runtime_market_truth(
        proof_id="market-truth-test",
        candidate_identity=candidate,
        broker_truth=broker,
        asset_truth=asset,
        market=market,
        observed_at=observed_at,
        policy=policy,
    )


def _rehash_market_truth(value: PaperRuntimeMarketTruthProof, **changes):
    values = {
        name: getattr(value, name)
        for name in PaperRuntimeMarketTruthProof.__dataclass_fields__
        if name != "proof_hash"
    }
    values.update(changes)
    return PaperRuntimeMarketTruthProof(
        **values,
        proof_hash=market_truth_module._hash(
            market_truth_module._payload_from_values(values)
        ),
    )


def test_market_truth_requires_both_fresh_and_grants_no_authority():
    proof = _bind()

    assert proof.candidate_symbol == "TEST-USD"
    assert proof.canonical_broker_pair == "TEST/USD"
    assert proof.quote_age_seconds == Decimal("1")
    assert proof.trade_age_seconds == Decimal("1")
    assert proof.quote_fresh is True
    assert proof.trade_fresh is True
    assert proof.both_sides_fresh is True
    assert proof.market_truth_verified is True
    assert proof.read_only_market_truth is True
    assert proof.network_write_performed is False
    assert proof.paper_runtime_ready is False
    assert proof.paper_execution_authorized is False
    assert proof.external_execution_authorized is False
    assert proof.runtime_execution_authorized is False
    assert proof.capital_authority == "NONE"
    assert proof.live_trading == "BLOCKED"


def test_stale_quote_is_rejected_even_when_trade_is_fresh():
    market = _market(
        received_at=AT + timedelta(seconds=7),
        quote_at=AT,
        trade_at=AT + timedelta(seconds=6),
    )
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="quote is stale"):
        _bind(
            market=market,
            observed_at=market.received_at,
            policy=PaperRuntimeMarketTruthPolicy(max_asset_market_skew_seconds=15),
        )


def test_stale_trade_is_rejected_even_when_quote_is_fresh():
    market = _market(
        received_at=AT + timedelta(seconds=7),
        quote_at=AT + timedelta(seconds=6),
        trade_at=AT,
    )
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="trade is stale"):
        _bind(
            market=market,
            observed_at=market.received_at,
            policy=PaperRuntimeMarketTruthPolicy(max_asset_market_skew_seconds=15),
        )


def test_wrong_market_symbol_fails_closed():
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="symbol differs"):
        _bind(market=_market(symbol="OTHER/USD"))


def test_market_receipt_must_follow_asset_and_respect_collection_skew():
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="predates"):
        _bind(
            market=_market(
                received_at=AT - timedelta(seconds=1),
                quote_at=AT - timedelta(seconds=2),
                trade_at=AT - timedelta(seconds=2),
            ),
            observed_at=AT,
        )
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="skew"):
        _bind(
            market=_market(
                received_at=AT + timedelta(seconds=16),
                quote_at=AT + timedelta(seconds=15),
                trade_at=AT + timedelta(seconds=15),
            ),
            observed_at=AT + timedelta(seconds=16),
        )


def test_market_receipt_and_event_future_fail_closed():
    market = _market(received_at=AT + timedelta(seconds=1))
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="process future"):
        _bind(market=market, observed_at=AT)

    future_quote = _market(
        received_at=AT + timedelta(seconds=1),
        quote_at=AT + timedelta(seconds=2),
        trade_at=AT,
    )
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="quote timestamp"):
        _bind(market=future_quote, observed_at=AT + timedelta(seconds=2))


def test_stale_asset_truth_is_rejected_before_market_use():
    candidate, broker, asset = _chain()
    market = _market(
        received_at=AT + timedelta(seconds=31),
        quote_at=AT + timedelta(seconds=30),
        trade_at=AT + timedelta(seconds=30),
    )
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="asset truth is stale"):
        bind_paper_runtime_market_truth(
            proof_id="market-stale-asset",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=market,
            observed_at=market.received_at,
        )


def test_tampered_asset_hash_is_rejected_before_market_use():
    candidate, broker, asset = _chain()
    object.__setattr__(asset, "proof_hash", "9" * 64)
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="asset truth proof hash"):
        bind_paper_runtime_market_truth(
            proof_id="market-tampered-asset",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            market=_market(),
            observed_at=AT + timedelta(seconds=1),
        )


def test_market_truth_constructor_rejects_authority_and_self_tamper():
    proof = _bind()
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="may not grant"):
        _rehash_market_truth(proof, paper_runtime_ready=True)
    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="hash mismatch"):
        replace(proof, proof_hash="9" * 64)


class RecordingMarketTransport:
    def __init__(self, quote_body: bytes, trade_body: bytes):
        self.quote_body = quote_body
        self.trade_body = trade_body
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        if LATEST_QUOTE_PATH in request.url:
            body = self.quote_body
        elif LATEST_TRADE_PATH in request.url:
            body = self.trade_body
        else:
            raise AssertionError(f"unexpected market-data URL: {request.url}")
        return AlpacaPaperMarketDataHttpResponse(
            status_code=200,
            body=body,
            final_url=request.url,
            headers={"content-type": "application/json"},
        )


def _runner_chain(credentials: AlpacaPaperCredentials):
    candidate = _candidate()
    broker = _broker(candidate, credential_reference=credentials.credential_reference)
    asset = _asset_truth(candidate, broker)
    return candidate, broker, asset


def test_runner_performs_exactly_two_gets_for_frozen_pair():
    credentials = AlpacaPaperCredentials(
        key_id="paper-market-key",
        secret_key="paper-market-secret",
    )
    candidate, broker, asset = _runner_chain(credentials)
    quote_body = json.dumps(
        {
            "quotes": {
                "TEST/USD": {
                    "bp": "100",
                    "ap": "101",
                    "t": "2026-08-25T19:45:00Z",
                }
            }
        },
        separators=(",", ":"),
    ).encode()
    trade_body = json.dumps(
        {
            "trades": {
                "TEST/USD": {
                    "p": "100.5",
                    "t": "2026-08-25T19:45:00Z",
                }
            }
        },
        separators=(",", ":"),
    ).encode()
    transport = RecordingMarketTransport(quote_body, trade_body)
    now = AT + timedelta(seconds=1)

    proof = read_and_bind_paper_runtime_market_truth(
        proof_id="market-runner-proof",
        candidate_identity=candidate,
        broker_truth=broker,
        asset_truth=asset,
        credentials=credentials,
        observed_at=now,
        gateway_config=AlpacaPaperCryptoMarketDataConfig(
            enabled=True,
            fresh_activity_age_seconds=5,
            max_reference_age_seconds=30,
        ),
        transport=transport,
    )

    assert len(transport.requests) == 2
    urls = [request.url for request in transport.requests]
    assert urls == [
        "https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes?symbols=TEST/USD",
        "https://data.alpaca.markets/v1beta3/crypto/us/latest/trades?symbols=TEST/USD",
    ]
    assert all(request.method == "GET" for request in transport.requests)
    assert all(request.headers["Accept"] == "application/json" for request in transport.requests)
    assert proof.canonical_broker_pair == "TEST/USD"
    assert proof.quote_path == LATEST_QUOTE_PATH
    assert proof.trade_path == LATEST_TRADE_PATH
    assert proof.exact_query == "symbols=TEST/USD"
    assert proof.both_sides_fresh is True
    assert proof.network_write_performed is False
    assert proof.paper_runtime_ready is False


def test_runner_rejects_disabled_config_and_wrong_credentials_before_transport():
    credentials = AlpacaPaperCredentials(
        key_id="paper-market-key",
        secret_key="paper-market-secret",
    )
    wrong = AlpacaPaperCredentials(
        key_id="paper-market-other",
        secret_key="paper-market-other-secret",
    )
    candidate, broker, asset = _runner_chain(credentials)
    transport = RecordingMarketTransport(b"{}", b"{}")

    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="explicitly enabled"):
        read_and_bind_paper_runtime_market_truth(
            proof_id="market-disabled-proof",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            credentials=credentials,
            observed_at=AT + timedelta(seconds=1),
            gateway_config=AlpacaPaperCryptoMarketDataConfig(enabled=False),
            transport=transport,
        )
    assert transport.requests == []

    with pytest.raises(PaperRuntimeMarketTruthIntegrityError, match="credentials differ"):
        read_and_bind_paper_runtime_market_truth(
            proof_id="market-wrong-creds-proof",
            candidate_identity=candidate,
            broker_truth=broker,
            asset_truth=asset,
            credentials=wrong,
            observed_at=AT + timedelta(seconds=1),
            gateway_config=AlpacaPaperCryptoMarketDataConfig(enabled=True),
            transport=transport,
        )
    assert transport.requests == []
