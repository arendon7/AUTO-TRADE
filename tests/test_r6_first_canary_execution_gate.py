from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import runpy

import pytest

from autotrade.first_canary_execution_gate import (
    FirstCanaryExecutionBlocked,
    FirstCanaryExecutionInputs,
    FirstCanaryFinalEvidence,
    execute_first_canary_once,
)
from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    CryptoFirstCanaryAttemptConflict,
)
from autotrade.brokers.alpaca_paper_crypto_pre_io import (
    DeterministicCryptoPaperSimulationTransport,
)
import autotrade.brokers.alpaca_paper_crypto_cold_start_pre_io as cold_pre_io
from autotrade.brokers.alpaca_paper_crypto_reconciliation import (
    CryptoBrokerOrderAbsenceEvidence,
    CryptoBrokerOrderSnapshot,
    CryptoBrokerPositionSnapshot,
    CryptoBrokerReconciliation,
    CryptoBrokerUnknownReconciliation,
)
from autotrade.brokers.alpaca_paper_crypto_writer import AlpacaPaperCryptoWriteResponse
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.product_profile import ProductCapabilities
from test_r6_paper_crypto_canary_coordinator import NOW, _account, _asset, _market
from test_r6_paper_crypto_cold_start_final_guard import _flat_attestation, _setup


ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / "scripts/mac_crypto_first_canary_prepare.py"
APPROVE = ROOT / "scripts/mac_crypto_first_canary_approval.py"
ATTEMPT_ID = "first-canary-0123456789abcdef0123456789abcdef"
KEY_ID = "simulation-paper-key"
SECRET = "simulation-paper-secret"
CREDENTIAL_REFERENCE = sha256(KEY_ID.encode("utf-8")).hexdigest()


class _CountingSimulationDelegate(DeterministicCryptoPaperSimulationTransport):
    pass


class _AmbiguousDelegate:
    def __init__(self) -> None:
        self.calls = 0

    def post(self, **_kwargs) -> AlpacaPaperCryptoWriteResponse:
        self.calls += 1
        raise TimeoutError("synthetic possible receive")


class _FoundReconciler:
    def __init__(self, *, status: str = "canceled", filled: Decimal = Decimal("0")) -> None:
        self.calls = 0
        self.status = status
        self.filled = filled

    def reconcile(self, *, credentials, order, now):
        self.calls += 1
        return CryptoBrokerReconciliation(
            order=CryptoBrokerOrderSnapshot(
                broker_order_id="broker-first-canary-001",
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side="buy",
                order_type="limit",
                time_in_force="ioc",
                status=self.status,
                quantity=order.quantity,
                filled_quantity=self.filled,
                limit_price=order.limit_price,
                stop_price=None,
                request_id="req-first-canary-reconcile-order",
                response_sha256="e" * 64,
                observed_at=now,
            ),
            position=CryptoBrokerPositionSnapshot(
                symbol=order.symbol,
                quantity=self.filled,
                market_value=None,
                average_entry_price=order.limit_price if self.filled > 0 else None,
                credential_reference=credentials.credential_reference,
                request_id="req-first-canary-reconcile-position",
                response_sha256="f" * 64,
                observed_at=now,
                absent=self.filled == 0,
            ),
            observed_at=now,
        )


class _UnknownReconciler:
    def __init__(self) -> None:
        self.calls = 0

    def reconcile(self, *, credentials, order, now):
        self.calls += 1
        return CryptoBrokerUnknownReconciliation(
            order_absence=CryptoBrokerOrderAbsenceEvidence(
                client_order_id=order.client_order_id,
                credential_reference=credentials.credential_reference,
                request_id="req-first-canary-order-404",
                response_sha256="c" * 64,
                observed_at=now,
            ),
            position=CryptoBrokerPositionSnapshot(
                symbol=order.symbol,
                quantity=Decimal("0"),
                market_value=None,
                average_entry_price=None,
                credential_reference=credentials.credential_reference,
                request_id="req-first-canary-position-after-404",
                response_sha256="d" * 64,
                observed_at=now,
                absent=True,
            ),
            observed_at=now,
        )


def _small_asset(account, *, observed):
    return replace(
        _asset(account, observed=observed),
        min_order_size=Decimal("0.00001"),
        min_trade_increment=Decimal("0.00001"),
    )


def _profile(asset):
    return ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=asset.fingerprint,
        observed_at=asset.observed_at,
        fractionable=asset.fractionable,
        marginable=asset.marginable,
        shortable=asset.shortable,
    )


def _prepare_session(tmp_path, monkeypatch, *, approve: bool = True):
    monkeypatch.delenv("R6_EXTERNAL_PAPER_WRITE", raising=False)
    workspace = tmp_path / "workspace"
    ctx = _setup(workspace)
    prepared_at = NOW + timedelta(seconds=4)
    account = replace(
        _account(observed=prepared_at),
        credential_reference=CREDENTIAL_REFERENCE,
    )
    asset = _small_asset(account, observed=prepared_at)
    credentials = AlpacaPaperCredentials(key_id=KEY_ID, secret_key=SECRET)
    prepare = runpy.run_path(str(PREPARE))
    session = prepare["prepare_from_evidence"](
        workspace_path=workspace,
        attempt_id=ATTEMPT_ID,
        credentials=credentials,
        account=account,
        asset=asset,
        flat_account=_flat_attestation(account, at=prepared_at),
        market_attestation=_market(observed=prepared_at),
        now=prepared_at,
    )
    if approve:
        approval = runpy.run_path(str(APPROVE))
        approval["issue_approval"](
            workspace_path=workspace,
            attempt_id=ATTEMPT_ID,
            context_payload=session.operator_context.to_dict(),
            operator_id="operator-first-canary-001",
            confirmation=session.preparation_document["operator_challenge"],
            now=prepared_at + timedelta(milliseconds=100),
        )
    inputs = FirstCanaryExecutionInputs(
        attempt=session.attempt,
        core_runtime=session.core_runtime,
        attempt_runtime=session.attempt_runtime,
        credentials=credentials,
        package=session.preparation.package,
        broker_order=session.preparation.broker_order,
        prepared_account=session.account,
        prepared_asset=session.asset,
        prepared_product_profile=session.product_profile,
        prepared_market=session.market_attestation,
        risk_decision=session.risk_decision,
        preparation_authority_state_fingerprint=session.authority_state_fingerprint,
    )
    return ctx, session, inputs


def _final(inputs, *, at):
    account = replace(
        _account(observed=at),
        credential_reference=inputs.credentials.credential_reference,
    )
    asset = _small_asset(account, observed=at)
    return FirstCanaryFinalEvidence(
        account=account,
        asset=asset,
        product_profile=_profile(asset),
        market=_market(observed=at),
        flat_account=_flat_attestation(account, at=at),
    )


def test_first_canary_gate_crosses_delegate_once_then_reconciles_terminal_no_fill(tmp_path, monkeypatch) -> None:
    _, session, inputs = _prepare_session(tmp_path, monkeypatch)
    execute_at = NOW + timedelta(seconds=4, milliseconds=300)
    final = _final(inputs, at=execute_at)
    monkeypatch.setattr(cold_pre_io, "_utc_now", lambda: execute_at + timedelta(milliseconds=35))
    delegate = _CountingSimulationDelegate()
    reconciler = _FoundReconciler()

    outcome = execute_first_canary_once(
        inputs=inputs,
        final_evidence=final,
        delegate=delegate,
        reconciler=reconciler,
        now=execute_at,
    )

    assert delegate.calls == 1
    assert reconciler.calls == 1
    assert outcome.retry_forbidden is True
    assert outcome.broker_post_outcome == "BROKER_RESPONSE_RECEIVED"
    assert outcome.lifecycle_status != "ENTRY_SUBMISSION_UNKNOWN"
    started = session.attempt.read(path=session.attempt.execution_started_path)
    assert started["operator_decision_consumed"] is True
    assert started["retry_forbidden"] is True
    result = session.attempt.read(path=session.attempt.execution_result_path)
    assert result["broker_delegate_boundary_crossed"] is True
    assert result["entry_attempt_count"] == 1
    reconciliation = session.attempt.read(path=session.attempt.reconciliation_path)
    assert reconciliation["status"] == "CRYPTO_PAPER_FIRST_CANARY_RECONCILED_NO_RETRY"
    assert reconciliation["retry_post"] is False

    with pytest.raises(CryptoFirstCanaryAttemptConflict, match="POST replay is forbidden"):
        execute_first_canary_once(
            inputs=inputs,
            final_evidence=final,
            delegate=_CountingSimulationDelegate(),
            reconciler=_FoundReconciler(),
            now=execute_at + timedelta(seconds=1),
        )


def test_first_canary_gate_ambiguous_delegate_reconciles_404_and_never_retries(tmp_path, monkeypatch) -> None:
    _, session, inputs = _prepare_session(tmp_path, monkeypatch)
    execute_at = NOW + timedelta(seconds=4, milliseconds=300)
    monkeypatch.setattr(cold_pre_io, "_utc_now", lambda: execute_at + timedelta(milliseconds=35))
    delegate = _AmbiguousDelegate()
    reconciler = _UnknownReconciler()

    outcome = execute_first_canary_once(
        inputs=inputs,
        final_evidence=_final(inputs, at=execute_at),
        delegate=delegate,
        reconciler=reconciler,
        now=execute_at,
    )

    assert delegate.calls == 1
    assert reconciler.calls == 1
    assert outcome.status == "UNKNOWN_HALTED_NO_RETRY"
    assert outcome.lifecycle_status == "ENTRY_SUBMISSION_UNKNOWN"
    assert outcome.broker_post_outcome == "UNKNOWN_RECONCILIATION_REQUIRED"
    result = session.attempt.read(path=session.attempt.execution_result_path)
    assert result["broker_delegate_boundary_crossed"] is True
    reconciliation = session.attempt.read(path=session.attempt.reconciliation_path)
    assert reconciliation["status"] == "CRYPTO_PAPER_FIRST_CANARY_ORDER_404_UNKNOWN_HALT_NO_RETRY"
    assert reconciliation["retry_post"] is False
    assert reconciliation["reconciliation_retry_get_only"] is True


def test_first_canary_gate_missing_approval_blocks_before_delegate_and_latch(tmp_path, monkeypatch) -> None:
    _, session, inputs = _prepare_session(tmp_path, monkeypatch, approve=False)
    execute_at = NOW + timedelta(seconds=4, milliseconds=300)
    delegate = _CountingSimulationDelegate()
    with pytest.raises(FirstCanaryExecutionBlocked, match="approval"):
        execute_first_canary_once(
            inputs=inputs,
            final_evidence=_final(inputs, at=execute_at),
            delegate=delegate,
            reconciler=_FoundReconciler(),
            now=execute_at,
        )
    assert delegate.calls == 0
    assert session.attempt.execution_started_path.exists() is False


def test_first_canary_gate_stale_final_evidence_blocks_before_consumption_and_delegate(tmp_path, monkeypatch) -> None:
    _, session, inputs = _prepare_session(tmp_path, monkeypatch)
    execute_at = NOW + timedelta(seconds=11)
    stale = _final(inputs, at=NOW + timedelta(seconds=4, milliseconds=200))
    delegate = _CountingSimulationDelegate()
    with pytest.raises(FirstCanaryExecutionBlocked, match="five-second execution TTL"):
        execute_first_canary_once(
            inputs=inputs,
            final_evidence=stale,
            delegate=delegate,
            reconciler=_FoundReconciler(),
            now=execute_at,
        )
    assert delegate.calls == 0
    assert session.attempt.execution_started_path.exists() is False


def test_first_canary_gate_core_change_blocks_before_delegate(tmp_path, monkeypatch) -> None:
    ctx, session, inputs = _prepare_session(tmp_path, monkeypatch)
    execute_at = NOW + timedelta(seconds=4, milliseconds=300)
    ctx.safety.activate_circuit(
        reason="SYNTHETIC_LATE_CIRCUIT",
        now=execute_at - timedelta(milliseconds=50),
    )
    delegate = _CountingSimulationDelegate()
    with pytest.raises(FirstCanaryExecutionBlocked, match="state changed since preparation"):
        execute_first_canary_once(
            inputs=inputs,
            final_evidence=_final(inputs, at=execute_at),
            delegate=delegate,
            reconciler=_FoundReconciler(),
            now=execute_at,
        )
    assert delegate.calls == 0
    assert session.attempt.execution_started_path.exists() is False
