from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import inspect

import pytest

import autotrade.paper_runtime_final_readiness as final_module
import autotrade.paper_runtime_funding_capacity as funding_module
import autotrade.paper_runtime_read_only_pipeline as pipeline_module
import autotrade.paper_runtime_readiness_source_snapshot as source_module
from autotrade.brokers.alpaca_paper_crypto_account_status import (
    AlpacaPaperCryptoAccountStatusAttestation,
)
from autotrade.brokers.alpaca_paper_crypto_asset import (
    AlpacaPaperCryptoAssetAttestation,
    crypto_asset_path,
)
from autotrade.brokers.alpaca_paper_crypto_market_data import (
    AlpacaPaperCryptoMarketAttestation,
    AlpacaPaperCryptoMarketDataConfig,
)
from autotrade.brokers.alpaca_paper_flat_account import PaperFlatAccountAttestation
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.domain import MarketSnapshot
from autotrade.paper_runtime_asset_truth import bind_paper_runtime_asset_truth
from autotrade.paper_runtime_market_truth import bind_paper_runtime_market_truth
from autotrade.paper_runtime_read_only_pipeline import (
    PaperRuntimeReadOnlyPipelineIntegrityError,
    collect_paper_runtime_readiness,
)
from test_w86_paper_runtime_final_readiness import NOW, _bind_candidate, _safety, _source
from test_w86_paper_runtime_market_truth import ACCOUNT_ID, AT, _candidate


PIPELINE_FINAL_NOW = NOW
PIPELINE_FUNDING_NOW = NOW + timedelta(milliseconds=250)


def _source_candidate():
    candidate_seed = _candidate()
    source = _source()
    for field in (
        "selected_trial_fingerprint",
        "strategy_spec_hash",
        "loaded_runtime_code_hash",
        "fee_product_economics_hash",
        "intent_fingerprint",
    ):
        object.__setattr__(source, field, getattr(candidate_seed, field))
    object.__setattr__(
        source,
        "proof_hash",
        source_module._hash(source_module._proof_payload(source, include_hash=False)),
    )
    return source, _bind_candidate(source)


def _account(credentials, *, buying_power=Decimal("1000")):
    return AlpacaPaperAccountAttestation(
        account_id=ACCOUNT_ID,
        account_reference="d" * 64,
        credential_reference=credentials.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=buying_power,
        portfolio_value=Decimal("1000"),
        shorting_enabled=False,
        attested_at=AT,
        request_id="pipeline-account-req",
        source_host=ALPACA_PAPER_TRADING_HOST,
        source_path=ALPACA_PAPER_ACCOUNT_PATH,
    )


def _crypto_status():
    return AlpacaPaperCryptoAccountStatusAttestation(
        account_id=ACCOUNT_ID,
        crypto_status="ACTIVE",
        observed_at=AT,
        request_id="pipeline-crypto-req",
        response_sha256="e" * 64,
    )


def _flat(account):
    return PaperFlatAccountAttestation(
        account_attestation_fingerprint=account.fingerprint,
        credential_reference=account.credential_reference,
        position_count=0,
        open_order_count=0,
        positions_response_hash="1" * 64,
        orders_response_hash="2" * 64,
        positions_request_id="pipeline-positions-req",
        orders_request_id="pipeline-orders-req",
        attested_at=AT,
    )


def _install_read_only_stubs(monkeypatch, *, buying_power=Decimal("1000")):
    credentials = AlpacaPaperCredentials(
        key_id="pipeline-test-key",
        secret_key="pipeline-test-secret",
    )
    account = _account(credentials, buying_power=buying_power)
    observed: list[tuple[str, object]] = []

    class AccountGateway:
        def __init__(self, *, config, transport=None):
            assert config.enabled is True

        def attest_account(self, *, credentials, expected_account_id, now):
            observed.append(("account", now))
            assert expected_account_id == ACCOUNT_ID
            return account

    class FlatGateway:
        def __init__(self, *, config, transport=None):
            assert config.enabled is True

        def attest_flatness(
            self,
            *,
            credentials,
            account_attestation_fingerprint,
            expected_credential_reference,
            now,
        ):
            observed.append(("flat", now))
            assert account_attestation_fingerprint == account.fingerprint
            assert expected_credential_reference == account.credential_reference
            return _flat(account)

    def crypto_status(**kwargs):
        observed.append(("crypto", kwargs["now"]))
        assert kwargs["expected_account_id"] == ACCOUNT_ID
        return _crypto_status()

    def asset_truth(**kwargs):
        observed.append(("asset", kwargs["observed_at"]))
        broker = kwargs["broker_truth"]
        candidate = kwargs["candidate_identity"]
        attestation = AlpacaPaperCryptoAssetAttestation(
            symbol="TEST/USD",
            asset_id="pipeline-asset",
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
            observed_at=kwargs["observed_at"],
            request_id="pipeline-asset-req",
            response_sha256="3" * 64,
            source_path=crypto_asset_path("TEST/USD"),
        )
        return bind_paper_runtime_asset_truth(
            proof_id=kwargs["proof_id"],
            candidate_identity=candidate,
            broker_truth=broker,
            asset=attestation,
            observed_at=kwargs["observed_at"],
            policy=kwargs["policy"],
        )

    def market_truth(**kwargs):
        observed.append(("market", kwargs["observed_at"]))
        at = kwargs["observed_at"]
        attestation = AlpacaPaperCryptoMarketAttestation(
            market=MarketSnapshot(
                symbol="TEST/USD",
                bid=Decimal("100"),
                ask=Decimal("101"),
                last=Decimal("100.5"),
                observed_at=at,
            ),
            location="us",
            quote_observed_at=at,
            trade_observed_at=at,
            received_at=at,
            quote_response_sha256="4" * 64,
            trade_response_sha256="5" * 64,
        )
        return bind_paper_runtime_market_truth(
            proof_id=kwargs["proof_id"],
            candidate_identity=kwargs["candidate_identity"],
            broker_truth=kwargs["broker_truth"],
            asset_truth=kwargs["asset_truth"],
            market=attestation,
            observed_at=at,
            policy=kwargs["policy"],
        )

    class SafetyReader:
        def __init__(self, core_path):
            observed.append(("core_path", str(core_path)))

        def verify_current(self, *, proof_id, candidate_identity, observed_at, policy=None):
            observed.append(("safety", observed_at))
            assert observed_at == AT
            return _safety(candidate_identity)

    monkeypatch.setattr(pipeline_module, "AlpacaPaperAccountGateway", AccountGateway)
    monkeypatch.setattr(pipeline_module, "AlpacaPaperFlatAccountGateway", FlatGateway)
    monkeypatch.setattr(pipeline_module, "attest_active_crypto_account", crypto_status)
    monkeypatch.setattr(
        pipeline_module,
        "read_and_bind_paper_runtime_asset_truth",
        asset_truth,
    )
    monkeypatch.setattr(
        pipeline_module,
        "read_and_bind_paper_runtime_market_truth",
        market_truth,
    )
    monkeypatch.setattr(
        pipeline_module,
        "PaperRuntimeSafetyHealthTruthReader",
        SafetyReader,
    )
    monkeypatch.setattr(pipeline_module, "_now_utc", lambda: AT)
    monkeypatch.setattr(final_module, "_now_utc", lambda: PIPELINE_FINAL_NOW)
    monkeypatch.setattr(funding_module, "_now_utc", lambda: PIPELINE_FUNDING_NOW)
    return credentials, account, observed


def _collect(monkeypatch, *, buying_power=Decimal("1000")):
    source, candidate = _source_candidate()
    credentials, account, observed = _install_read_only_stubs(
        monkeypatch,
        buying_power=buying_power,
    )
    result = collect_paper_runtime_readiness(
        collection_id="pipeline-test",
        source_snapshot=source,
        candidate_identity=candidate,
        credentials=credentials,
        expected_account_id=ACCOUNT_ID,
        trading_config=AlpacaPaperGatewayConfig(enabled=True),
        core_path="/test/read-only-core.sqlite3",
        market_data_config=AlpacaPaperCryptoMarketDataConfig(enabled=True),
    )
    return result, credentials, account, observed


def test_complete_pipeline_retains_exact_account_receipt_and_never_grants_execution(monkeypatch):
    result, credentials, account, observed = _collect(monkeypatch)

    assert result.account_attestation is account
    assert result.receipt.account_attestation_fingerprint == account.fingerprint
    assert result.broker_truth.account_attestation_fingerprint == account.fingerprint
    assert result.funding_capacity.account_attestation_fingerprint == account.fingerprint
    assert result.final_readiness.paper_runtime_ready is True
    assert result.funding_capacity.paper_runtime_ready is True
    assert result.receipt.paper_runtime_ready is True
    assert result.receipt.internal_process_clock is True
    assert result.receipt.read_only_collection is True
    assert result.receipt.network_reads_performed is True
    assert result.receipt.network_write_performed is False
    assert result.receipt.separate_execution_approval_required is True
    assert result.receipt.paper_execution_authorized is False
    assert result.receipt.external_execution_authorized is False
    assert result.receipt.runtime_execution_authorized is False
    assert result.receipt.capital_authority == "NONE"
    assert result.receipt.live_trading == "BLOCKED"
    assert credentials.key_id not in repr(result)
    assert credentials.secret_key not in repr(result)

    stage_times = [
        value
        for name, value in observed
        if name in {"account", "crypto", "flat", "asset", "market", "safety"}
    ]
    assert stage_times == [AT, AT, AT, AT, AT, AT]


def test_pipeline_ready_is_funding_ready_not_just_final_readiness(monkeypatch):
    result, _, _, _ = _collect(monkeypatch, buying_power=Decimal("0.05"))

    assert result.final_readiness.paper_runtime_ready is True
    assert result.funding_capacity.paper_runtime_ready is False
    assert result.receipt.paper_runtime_ready is False
    assert result.receipt.paper_execution_authorized is False
    assert result.receipt.capital_authority == "NONE"
    assert result.receipt.live_trading == "BLOCKED"


def test_runtime_entrypoint_has_no_caller_clock_parameter():
    parameters = inspect.signature(collect_paper_runtime_readiness).parameters
    assert "now" not in parameters
    assert "observed_at" not in parameters
    assert "received_at" not in parameters


def test_tampered_candidate_fails_before_first_network_read(monkeypatch):
    source, candidate = _source_candidate()
    object.__setattr__(candidate, "proof_hash", "0" * 64)
    called = False

    class MustNotRun:
        def __init__(self, *args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("network gateway constructed before preflight")

    monkeypatch.setattr(pipeline_module, "AlpacaPaperAccountGateway", MustNotRun)
    credentials = AlpacaPaperCredentials("pipeline-test-key", "pipeline-test-secret")
    with pytest.raises(
        PaperRuntimeReadOnlyPipelineIntegrityError,
        match="candidate identity hash mismatch before network read",
    ):
        collect_paper_runtime_readiness(
            collection_id="tampered-candidate",
            source_snapshot=source,
            candidate_identity=candidate,
            credentials=credentials,
            expected_account_id=ACCOUNT_ID,
            trading_config=AlpacaPaperGatewayConfig(enabled=True),
            core_path="/never-opened.sqlite3",
            market_data_config=AlpacaPaperCryptoMarketDataConfig(enabled=True),
        )
    assert called is False


def test_internal_clock_moving_backward_fails_before_network(monkeypatch):
    source, candidate = _source_candidate()
    credentials = AlpacaPaperCredentials("pipeline-test-key", "pipeline-test-secret")
    times = iter((AT, AT - timedelta(microseconds=1)))
    monkeypatch.setattr(pipeline_module, "_now_utc", lambda: next(times))
    called = False

    class MustNotRun:
        def __init__(self, *args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("network gateway constructed after backward clock")

    monkeypatch.setattr(pipeline_module, "AlpacaPaperAccountGateway", MustNotRun)
    with pytest.raises(PaperRuntimeReadOnlyPipelineIntegrityError, match="clock moved backward"):
        collect_paper_runtime_readiness(
            collection_id="backward-clock",
            source_snapshot=source,
            candidate_identity=candidate,
            credentials=credentials,
            expected_account_id=ACCOUNT_ID,
            trading_config=AlpacaPaperGatewayConfig(enabled=True),
            core_path="/never-opened.sqlite3",
            market_data_config=AlpacaPaperCryptoMarketDataConfig(enabled=True),
        )
    assert called is False


def test_pipeline_receipt_hash_and_authority_guards(monkeypatch):
    result, _, _, _ = _collect(monkeypatch)
    receipt = result.receipt

    with pytest.raises(PaperRuntimeReadOnlyPipelineIntegrityError, match="receipt hash mismatch"):
        replace(receipt, receipt_hash="0" * 64)
    with pytest.raises(PaperRuntimeReadOnlyPipelineIntegrityError, match="may not grant"):
        replace(receipt, paper_execution_authorized=True)
