from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal
import json

import pytest

import autotrade.paper_runtime_broker_truth as broker_truth
import autotrade.paper_runtime_candidate_identity as identity
from autotrade.brokers.alpaca_paper_crypto_account_status import (
    AlpacaPaperCryptoAccountStatusAttestation,
)
from autotrade.brokers.alpaca_paper_flat_account import PaperFlatAccountAttestation
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
)
from test_w86_paper_runtime_candidate_identity import _identity_bundle


ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
CREDENTIAL_REFERENCE = "a" * 64
ACCOUNT_REFERENCE = "b" * 64


class RecordingTransport:
    def __init__(self, responder):
        self.requests = []
        self._responder = responder

    def read(self, request):
        self.requests.append(request)
        return self._responder(request)


def _candidate(
    tmp_path,
    monkeypatch,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    bundle, _, verified, _, _, source_proof, product = _identity_bundle(
        tmp_path,
        monkeypatch,
        limits,
        market,
        empty_portfolio,
        market_buy_intent,
    )
    return identity.bind_paper_runtime_candidate_identity(
        proof_id="w86-broker-candidate",
        source_proof=source_proof,
        final_verification=verified,
        w83_resolution=bundle["w83"],
        product_economics=product,
    )


def _account(at):
    return AlpacaPaperAccountAttestation(
        account_id=ACCOUNT_ID,
        account_reference=ACCOUNT_REFERENCE,
        credential_reference=CREDENTIAL_REFERENCE,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("1000"),
        portfolio_value=Decimal("1000"),
        shorting_enabled=False,
        attested_at=at,
        request_id="acct-request",
        source_host=ALPACA_PAPER_TRADING_HOST,
        source_path=ALPACA_PAPER_ACCOUNT_PATH,
    )


def _crypto(at, *, account_id=ACCOUNT_ID, status="ACTIVE"):
    return AlpacaPaperCryptoAccountStatusAttestation(
        account_id=account_id,
        crypto_status=status,
        observed_at=at,
        request_id="crypto-request",
        response_sha256="c" * 64,
    )


def _flat(at, account, *, positions=0, orders=0, credential_reference=None):
    return PaperFlatAccountAttestation(
        account_attestation_fingerprint=account.fingerprint,
        credential_reference=credential_reference or account.credential_reference,
        position_count=positions,
        open_order_count=orders,
        positions_response_hash="d" * 64,
        orders_response_hash="e" * 64,
        positions_request_id="positions-request",
        orders_request_id="orders-request",
        attested_at=at,
    )


def _bind(candidate, at, *, account=None, crypto=None, flat=None, policy=None):
    account = account or _account(at)
    return broker_truth.bind_paper_runtime_broker_truth(
        proof_id="w86-broker-truth",
        candidate_identity=candidate,
        account=account,
        crypto_status=crypto or _crypto(at),
        flat_account=flat or _flat(at, account),
        observed_at=at,
        policy=policy,
    )


def _rehash_candidate(value, **changes):
    values = {
        field.name: getattr(value, field.name)
        for field in fields(value)
        if field.name != "proof_hash"
    }
    values.update(changes)
    return type(value)(
        **values,
        proof_hash=identity._hash(identity._payload_from_values(values)),
    )


def test_w86_broker_truth_binds_exact_crypto_candidate_account_and_portfolio(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    candidate = _candidate(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    proof = _bind(candidate, market.observed_at)

    assert candidate.asset_class == "crypto"
    assert candidate.venue == "alpaca-paper-model"
    assert candidate.symbol == "TEST-USD"
    assert candidate.quote_currency == "USD"
    assert proof.candidate_identity_hash == candidate.proof_hash
    assert proof.account_id == ACCOUNT_ID
    assert proof.account_environment_verified is True
    assert proof.crypto_entitlement_verified is True
    assert proof.portfolio_truth_verified is True
    assert proof.position_count == 0
    assert proof.open_order_count == 0
    assert proof.clean_for_candidate_start is True
    assert proof.read_only_broker_truth is True
    assert proof.network_write_performed is False
    assert proof.paper_runtime_ready is False
    assert proof.paper_execution_authorized is False
    assert proof.external_execution_authorized is False
    assert proof.runtime_execution_authorized is False
    assert proof.capital_authority == "NONE"
    assert proof.live_trading == "BLOCKED"
    assert proof.to_dict()["proof_hash"] == proof.proof_hash


def test_w86_nonflat_account_is_truth_but_never_readiness(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    candidate = _candidate(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    at = market.observed_at
    account = _account(at)
    proof = _bind(
        candidate,
        at,
        account=account,
        flat=_flat(at, account, positions=1, orders=2),
    )
    assert proof.portfolio_truth_verified is True
    assert proof.position_count == 1
    assert proof.open_order_count == 2
    assert proof.clean_for_candidate_start is False
    assert proof.paper_runtime_ready is False
    assert proof.capital_authority == "NONE"


def test_w86_rejects_cross_account_crypto_status(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    candidate = _candidate(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    other = "22222222-2222-4222-8222-222222222222"
    with pytest.raises(
        broker_truth.PaperRuntimeBrokerTruthIntegrityError,
        match="account.*differs.*account",
    ):
        _bind(candidate, market.observed_at, crypto=_crypto(market.observed_at, account_id=other))


def test_w86_rejects_portfolio_bound_to_different_account_or_credentials(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    candidate = _candidate(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    at = market.observed_at
    account = _account(at)
    wrong_account = replace(account, account_reference="9" * 64)
    with pytest.raises(
        broker_truth.PaperRuntimeBrokerTruthIntegrityError,
        match="different account attestation",
    ):
        _bind(candidate, at, account=account, flat=_flat(at, wrong_account))

    with pytest.raises(
        broker_truth.PaperRuntimeBrokerTruthIntegrityError,
        match="different credential",
    ):
        _bind(
            candidate,
            at,
            account=account,
            flat=_flat(at, account, credential_reference="8" * 64),
        )


@pytest.mark.parametrize(
    ("component", "age", "message"),
    (
        ("account", 31, "account.*stale"),
        ("crypto", 31, "crypto status.*stale"),
        ("flat", 31, "portfolio.*stale"),
    ),
)
def test_w86_rejects_stale_broker_components(
    tmp_path,
    monkeypatch,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    component,
    age,
    message,
):
    candidate = _candidate(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    now = market.observed_at
    account_at = now - timedelta(seconds=age) if component == "account" else now
    crypto_at = now - timedelta(seconds=age) if component == "crypto" else now
    flat_at = now - timedelta(seconds=age) if component == "flat" else now
    account = _account(account_at)
    with pytest.raises(
        broker_truth.PaperRuntimeBrokerTruthIntegrityError,
        match=message,
    ):
        broker_truth.bind_paper_runtime_broker_truth(
            proof_id=f"w86-stale-{component}",
            candidate_identity=candidate,
            account=account,
            crypto_status=_crypto(crypto_at),
            flat_account=_flat(flat_at, account),
            observed_at=now,
        )


def test_w86_rejects_future_read_and_cross_read_skew(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    candidate = _candidate(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    now = market.observed_at
    account = _account(now + timedelta(microseconds=1))
    with pytest.raises(
        broker_truth.PaperRuntimeBrokerTruthIntegrityError,
        match="process future",
    ):
        _bind(candidate, now, account=account, flat=_flat(now, account))

    account = _account(now)
    policy = broker_truth.PaperRuntimeBrokerTruthPolicy(
        max_account_age_seconds=30,
        max_crypto_status_age_seconds=30,
        max_portfolio_age_seconds=30,
        max_cross_read_skew_seconds=5,
    )
    with pytest.raises(
        broker_truth.PaperRuntimeBrokerTruthIntegrityError,
        match="cross-read skew",
    ):
        broker_truth.bind_paper_runtime_broker_truth(
            proof_id="w86-skew",
            candidate_identity=candidate,
            account=account,
            crypto_status=_crypto(now - timedelta(seconds=6)),
            flat_account=_flat(now, account),
            observed_at=now,
            policy=policy,
        )


def test_w86_rejects_non_alpaca_crypto_candidate_even_when_rehashed(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    candidate = _candidate(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    for changes in (
        {"asset_class": "equity"},
        {"venue": "other-paper"},
        {"quote_currency": "EUR"},
    ):
        forged = _rehash_candidate(candidate, **changes)
        with pytest.raises(
            broker_truth.PaperRuntimeBrokerTruthIntegrityError,
            match="supported Alpaca PAPER crypto|supported Alpaca PAPER crypto/USD",
        ):
            _bind(forged, market.observed_at)


def test_w86_rejects_account_source_currency_and_crypto_entitlement_drift(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    candidate = _candidate(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    at = market.observed_at
    account = _account(at)
    cases = (
        (replace(account, source_host="api.alpaca.markets"), _crypto(at), "source"),
        (replace(account, source_path="/v2/orders"), _crypto(at), "source"),
        (replace(account, currency="EUR"), _crypto(at), "currency"),
        (account, _crypto(at, status="INACTIVE"), "entitlement"),
    )
    for changed_account, changed_crypto, message in cases:
        with pytest.raises(
            broker_truth.PaperRuntimeBrokerTruthIntegrityError,
            match=message,
        ):
            _bind(
                candidate,
                at,
                account=changed_account,
                crypto=changed_crypto,
                flat=_flat(at, changed_account),
            )


def test_w86_broker_truth_constructor_rejects_authority_and_hash_tamper(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    candidate = _candidate(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    proof = _bind(candidate, market.observed_at)
    for changes in (
        {"network_write_performed": True},
        {"paper_runtime_ready": True},
        {"paper_execution_authorized": True},
        {"external_execution_authorized": True},
        {"runtime_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
        {"read_only_broker_truth": False},
        {"clean_for_candidate_start": False},
        {"proof_hash": "0" * 64},
    ):
        with pytest.raises(broker_truth.PaperRuntimeBrokerTruthIntegrityError):
            replace(proof, **changes)


def test_w86_read_runner_performs_only_exact_paper_gets_and_returns_no_authority(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    candidate = _candidate(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    now = market.observed_at
    credentials = AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret")
    assert credentials.credential_reference != CREDENTIAL_REFERENCE

    def account_response(request):
        payload = {
            "id": ACCOUNT_ID,
            "account_number": "PA-001",
            "status": "ACTIVE",
            "currency": "USD",
            "buying_power": "1000",
            "portfolio_value": "1000",
            "shorting_enabled": False,
            "trading_blocked": False,
            "account_blocked": False,
            "trade_suspended_by_user": False,
        }
        return AlpacaPaperHttpResponse(
            status_code=200,
            body=json.dumps(payload).encode(),
            final_url=f"https://{ALPACA_PAPER_TRADING_HOST}/v2/account",
            headers={"content-type": "application/json", "x-request-id": "acct-live"},
        )

    def crypto_response(request):
        return AlpacaPaperHttpResponse(
            status_code=200,
            body=json.dumps({"id": ACCOUNT_ID, "crypto_status": "ACTIVE"}).encode(),
            final_url=f"https://{ALPACA_PAPER_TRADING_HOST}/v2/account",
            headers={"content-type": "application/json", "x-request-id": "crypto-live"},
        )

    def flat_response(request):
        if request.url.endswith("/v2/positions"):
            request_id = "positions-live"
        else:
            request_id = "orders-live"
        return AlpacaPaperHttpResponse(
            status_code=200,
            body=b"[]",
            final_url=request.url,
            headers={"content-type": "application/json", "x-request-id": request_id},
        )

    account_transport = RecordingTransport(account_response)
    crypto_transport = RecordingTransport(crypto_response)
    flat_transport = RecordingTransport(flat_response)
    proof = broker_truth.read_and_bind_paper_runtime_broker_truth(
        proof_id="w86-read-broker-truth",
        candidate_identity=candidate,
        credentials=credentials,
        expected_account_id=ACCOUNT_ID,
        config=AlpacaPaperGatewayConfig(enabled=True),
        observed_at=now,
        account_transport=account_transport,
        crypto_status_transport=crypto_transport,
        flat_account_transport=flat_transport,
    )

    requests = (
        account_transport.requests
        + crypto_transport.requests
        + flat_transport.requests
    )
    assert len(requests) == 4
    assert all(request.method == "GET" for request in requests)
    assert all(
        request.url.startswith(f"https://{ALPACA_PAPER_TRADING_HOST}/")
        for request in requests
    )
    assert proof.credential_reference == credentials.credential_reference
    assert proof.clean_for_candidate_start is True
    assert proof.network_write_performed is False
    assert proof.paper_runtime_ready is False
    assert proof.capital_authority == "NONE"
    assert proof.live_trading == "BLOCKED"


def test_w86_read_runner_requires_explicit_paper_config_before_any_transport_call(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    candidate = _candidate(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    credentials = AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret")
    recorder = RecordingTransport(lambda _request: pytest.fail("transport must not run"))

    with pytest.raises(
        broker_truth.PaperRuntimeBrokerTruthIntegrityError,
        match="explicitly enabled",
    ):
        broker_truth.read_and_bind_paper_runtime_broker_truth(
            proof_id="w86-disabled",
            candidate_identity=candidate,
            credentials=credentials,
            expected_account_id=ACCOUNT_ID,
            config=AlpacaPaperGatewayConfig(enabled=False),
            observed_at=market.observed_at,
            account_transport=recorder,
            crypto_status_transport=recorder,
            flat_account_transport=recorder,
        )
    assert recorder.requests == []

    with pytest.raises(
        broker_truth.PaperRuntimeBrokerTruthIntegrityError,
        match="exact Alpaca PAPER base URL",
    ):
        broker_truth.read_and_bind_paper_runtime_broker_truth(
            proof_id="w86-live-host",
            candidate_identity=candidate,
            credentials=credentials,
            expected_account_id=ACCOUNT_ID,
            config=AlpacaPaperGatewayConfig(
                enabled=True,
                base_url="https://api.alpaca.markets",
            ),
            observed_at=market.observed_at,
            account_transport=recorder,
            crypto_status_transport=recorder,
            flat_account_transport=recorder,
        )
    assert recorder.requests == []


def test_w86_broker_truth_policy_and_type_guards(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    for kwargs in (
        {"max_account_age_seconds": 0},
        {"max_crypto_status_age_seconds": 301},
        {"max_portfolio_age_seconds": True},
        {"max_cross_read_skew_seconds": 0},
    ):
        with pytest.raises(ValueError):
            broker_truth.PaperRuntimeBrokerTruthPolicy(**kwargs)

    candidate = _candidate(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    at = market.observed_at
    account = _account(at)
    common = {
        "proof_id": "w86-types",
        "candidate_identity": candidate,
        "account": account,
        "crypto_status": _crypto(at),
        "flat_account": _flat(at, account),
        "observed_at": at,
    }
    for field_name in ("candidate_identity", "account", "crypto_status", "flat_account"):
        changed = dict(common)
        changed[field_name] = object()
        with pytest.raises(TypeError):
            broker_truth.bind_paper_runtime_broker_truth(**changed)
