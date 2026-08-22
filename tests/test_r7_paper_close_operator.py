from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.domain import MarketSnapshot
from autotrade.paper_close_attempt import pending_burned_close_attempts
from autotrade.paper_close_lifecycle import PaperCloseLifecycleStatus
from autotrade.paper_close_operator import (
    CLOSE_WRITE_ENV,
    PaperCloseOperator,
    PaperCloseOperatorBlocked,
    ReadOnlyCanonicalSafetyStateStore,
)
from autotrade.paper_close_writer import PaperCloseWriteReceipt, PaperCloseWriter
from autotrade.state import InMemorySafetyStateStore
from test_r7_paper_close_control_plane import _lifecycle, _portfolio, _source_order


# Keep the operator clock aligned with the canonical R7 Portfolio fixture. The
# production close plan deliberately rejects broker truth older than its strict
# TTL; tests must exercise the close authority rather than bypass freshness.
NOW = datetime(2026, 8, 21, 15, 55, tzinfo=timezone.utc)
CREDS = AlpacaPaperCredentials("paper-close-key", "paper-close-secret")


class _OperationsReader:
    def __init__(self, operations) -> None:
        self.operations = operations
        self.calls = 0

    def snapshot(self, **_kwargs):
        self.calls += 1
        return self.operations


class _AssetReader:
    def attest_asset(self, **_kwargs):
        return SimpleNamespace(price_increment=Decimal("0.1"))


class _MarketReader:
    def __init__(self, market: MarketSnapshot) -> None:
        self.market = market

    def attest_snapshot(self, **_kwargs):
        return SimpleNamespace(market=self.market)


class _PortfolioReader:
    def __init__(self, portfolio) -> None:
        self.portfolio = portfolio
        self.calls = 0

    def snapshot(self, **_kwargs):
        self.calls += 1
        return self.portfolio


class _OneShotWriter(PaperCloseWriter):
    def __init__(self, *, fail_after_unknown: bool = False) -> None:
        self.calls = 0
        self.fail_after_unknown = fail_after_unknown

    def submit_once(self, **kwargs):
        self.calls += 1
        lifecycle = kwargs["lifecycle"]
        lifecycle.mark_submission_unknown(kwargs["attempt_id"], at=kwargs["now"])
        if self.fail_after_unknown:
            raise RuntimeError("simulated transport ambiguity after durable PRE_IO")
        return PaperCloseWriteReceipt(
            attempt_id=kwargs["attempt_id"],
            plan_hash=kwargs["plan"].plan_hash,
            decision_hash=kwargs["decision"].decision_hash,
            client_order_id="atr7-close-unit",
            request_payload_sha256="a" * 64,
            broker_order_id="broker-close-unit",
            broker_status="accepted",
            request_id="req-close-unit",
            response_sha256="b" * 64,
            submitted_at=kwargs["now"],
        )


class _FlatReconciliationGateway:
    def __init__(self) -> None:
        self.calls = 0

    def reconcile(self, **kwargs):
        self.calls += 1
        plan = kwargs["plan"]
        lifecycle = kwargs["lifecycle"]
        lifecycle.reconcile(
            kwargs["attempt_id"],
            broker_order_id="broker-close-unit",
            broker_status="filled",
            filled_quantity=plan.quantity,
            remaining_position=Decimal("0"),
            at=kwargs["now"],
        )
        return SimpleNamespace(fingerprint="c" * 64)


class _PendingReconciliationGateway:
    def reconcile(self, **_kwargs):
        raise RuntimeError("broker order not visible yet")


def _market() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="BTC/USD",
        bid=Decimal("72790"),
        ask=Decimal("72810"),
        last=Decimal("72800"),
        observed_at=NOW,
    )


def _operations():
    portfolio = _portfolio()
    account = replace(portfolio.account, credential_reference=CREDS.credential_reference)
    portfolio = replace(portfolio, account=account)
    source_order = _source_order()
    source_lifecycle = _lifecycle(source_order)
    source = SimpleNamespace(
        attempt_id="first-canary-source",
        strategy_id=source_order.intent.strategy_id,
        source_order=source_order,
        source_lifecycle=source_lifecycle,
    )
    close_source = SimpleNamespace(source=source)
    return SimpleNamespace(
        ready_for_close_preparation=True,
        blockers=(),
        portfolio=portfolio,
        account_anchor=SimpleNamespace(attestation=account),
        close_source=close_source,
    )


def _operator(
    tmp_path: Path,
    *,
    writer: _OneShotWriter | None = None,
    reconciliation=None,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    operations = _operations()
    market = _market()
    portfolio_reader = _PortfolioReader(operations.portfolio)
    writer = writer or _OneShotWriter()
    reconciliation = reconciliation or _FlatReconciliationGateway()
    operator = PaperCloseOperator(
        workspace_path=workspace,
        now_provider=lambda: NOW,
        sleep=lambda _seconds: None,
        operations_reader=_OperationsReader(operations),
        asset_reader=_AssetReader(),
        market_reader=_MarketReader(market),
        portfolio_reader=portfolio_reader,
        writer_factory=lambda: writer,
        reconciliation_factory=lambda: reconciliation,
        safety_store_factory=lambda _workspace, _clock: InMemorySafetyStateStore(),
    )
    return operator, writer, reconciliation


def test_prepare_is_full_risk_reducing_and_has_no_post_authority(tmp_path: Path) -> None:
    operator, writer, _ = _operator(tmp_path)
    prepared = operator.prepare_full_close(credentials=CREDS)
    summary = prepared.summary()
    assert summary["environment"] == "PAPER"
    assert summary["symbol"] == "BTC/USD"
    assert summary["mode"] == "FULL"
    assert summary["side"] == "SELL"
    assert summary["quantity"] == "0.000143959"
    assert summary["limit_price"] == "72790"
    assert summary["max_slippage_bps"] == "25"
    assert summary["network_write_authorized"] is False
    assert summary["retry_post"] is False
    assert prepared.control_plane.decision.risk_reducing is True
    assert prepared.lifecycle.snapshot(prepared.attempt.attempt_id).state.status is PaperCloseLifecycleStatus.PREPARED
    assert writer.calls == 0


def test_write_gate_disabled_blocks_before_unknown_and_writer(tmp_path: Path, monkeypatch) -> None:
    operator, writer, _ = _operator(tmp_path)
    prepared = operator.prepare_full_close(credentials=CREDS)
    operator.approve(prepared=prepared)
    monkeypatch.delenv(CLOSE_WRITE_ENV, raising=False)
    with pytest.raises(PaperCloseOperatorBlocked, match="write gate is disabled"):
        operator.execute_once(prepared=prepared, credentials=CREDS)
    state = prepared.lifecycle.snapshot(prepared.attempt.attempt_id).state
    assert state.status is PaperCloseLifecycleStatus.PREPARED
    assert state.submission_attempt_count == 0
    assert writer.calls == 0


def test_successful_close_has_exactly_one_writer_call_and_get_reconciliation(tmp_path: Path, monkeypatch) -> None:
    operator, writer, reconciliation = _operator(tmp_path)
    prepared = operator.prepare_full_close(credentials=CREDS)
    operator.approve(prepared=prepared)
    monkeypatch.setenv(CLOSE_WRITE_ENV, "ENABLED")
    result = operator.execute_once(prepared=prepared, credentials=CREDS)
    assert result["ok"] is True
    assert result["phase"] == "CLOSE_RECONCILED"
    assert result["broker_post_attempt_burned"] is True
    assert result["settlement"]["flat"] is True
    assert result["settlement"]["next_action"] == "DONE_FLAT"
    assert result["retry_post"] is False
    assert writer.calls == 1
    assert reconciliation.calls == 1
    state = prepared.lifecycle.snapshot(prepared.attempt.attempt_id).state
    assert state.status is PaperCloseLifecycleStatus.FLAT_RECONCILED
    assert state.submission_attempt_count == 1
    assert pending_burned_close_attempts(workspace_path=operator.workspace) == ()
    receipt = prepared.attempt.write_receipt_path.read_text()
    assert "paper-close-key" not in receipt
    assert "paper-close-secret" not in receipt


def test_ambiguity_after_unknown_returns_recovery_only_and_never_reposts(tmp_path: Path, monkeypatch) -> None:
    writer = _OneShotWriter(fail_after_unknown=True)
    operator, writer, _ = _operator(
        tmp_path,
        writer=writer,
        reconciliation=_PendingReconciliationGateway(),
    )
    prepared = operator.prepare_full_close(credentials=CREDS)
    operator.approve(prepared=prepared)
    monkeypatch.setenv(CLOSE_WRITE_ENV, "ENABLED")
    result = operator.execute_once(prepared=prepared, credentials=CREDS)
    assert result["ok"] is False
    assert result["phase"] == "RECOVERY_ONLY"
    assert result["broker_write_performed"] == "UNKNOWN_AFTER_DURABLE_PRE_IO"
    assert result["broker_post_attempt_burned"] is True
    assert result["settlement"]["next_action"] == "RECONCILE_GET_ONLY_NEVER_RETRY_POST"
    assert writer.calls == 1
    assert pending_burned_close_attempts(workspace_path=operator.workspace) == (prepared.attempt.attempt_id,)
    with pytest.raises(PaperCloseOperatorBlocked, match="no longer eligible"):
        operator.execute_once(prepared=prepared, credentials=CREDS)
    assert writer.calls == 1


def test_restart_recovery_uses_only_get_and_resolves_same_attempt(tmp_path: Path, monkeypatch) -> None:
    writer = _OneShotWriter(fail_after_unknown=True)
    first, writer, _ = _operator(
        tmp_path,
        writer=writer,
        reconciliation=_PendingReconciliationGateway(),
    )
    prepared = first.prepare_full_close(credentials=CREDS)
    first.approve(prepared=prepared)
    monkeypatch.setenv(CLOSE_WRITE_ENV, "ENABLED")
    first.execute_once(prepared=prepared, credentials=CREDS)
    assert writer.calls == 1

    recovery = _FlatReconciliationGateway()
    restarted = PaperCloseOperator(
        workspace_path=first.workspace,
        now_provider=lambda: NOW,
        sleep=lambda _seconds: None,
        operations_reader=_OperationsReader(_operations()),
        asset_reader=_AssetReader(),
        market_reader=_MarketReader(_market()),
        portfolio_reader=_PortfolioReader(_operations().portfolio),
        writer_factory=lambda: pytest.fail("recovery must never instantiate or invoke a writer"),
        reconciliation_factory=lambda: recovery,
        safety_store_factory=lambda _workspace, _clock: InMemorySafetyStateStore(),
    )
    result = restarted.recover(credentials=CREDS)
    assert result["phase"] == "CLOSE_RECONCILED"
    assert result["broker_write_performed"] is False
    assert result["settlement"]["flat"] is True
    assert recovery.calls == 1
    assert pending_burned_close_attempts(workspace_path=first.workspace) == ()


def test_pending_burned_attempt_blocks_any_new_close_preparation(tmp_path: Path, monkeypatch) -> None:
    writer = _OneShotWriter(fail_after_unknown=True)
    operator, _, _ = _operator(tmp_path, writer=writer, reconciliation=_PendingReconciliationGateway())
    prepared = operator.prepare_full_close(credentials=CREDS)
    operator.approve(prepared=prepared)
    monkeypatch.setenv(CLOSE_WRITE_ENV, "ENABLED")
    operator.execute_once(prepared=prepared, credentials=CREDS)
    with pytest.raises(PaperCloseOperatorBlocked, match="unresolved"):
        operator.prepare_full_close(credentials=CREDS)


def test_canonical_safety_store_refuses_mutations_without_touching_disk(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = ReadOnlyCanonicalSafetyStateStore(workspace_path=workspace, now_provider=lambda: NOW)
    for call in (
        lambda: store.activate(reason="x", now=NOW),
        lambda: store.reset(now=NOW),
        lambda: store.activate_circuit(reason="x", now=NOW),
        lambda: store.acknowledge_circuit(reason="x", now=NOW),
    ):
        with pytest.raises(PaperCloseOperatorBlocked, match="read-only"):
            call()
