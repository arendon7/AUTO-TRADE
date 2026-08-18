from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import FirstCanaryAttemptWorkspace
from autotrade.brokers.alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus, SQLiteCryptoPaperLifecycle
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime
from autotrade.risk_state import SQLiteR2SafetyStateStore
from test_r6_paper_crypto_cold_start_final_guard import NOW, _flat_attestation, _setup


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mac_crypto_first_canary_prepare.py"
ATTEMPT_ID = "first-canary-0123456789abcdef0123456789abcdef"


def _module():
    return runpy.run_path(str(SCRIPT))


def _credentials(account):
    return SimpleNamespace(credential_reference=account.credential_reference)


def test_first_canary_prepare_persists_new_non_executable_attempt(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("R6_EXTERNAL_PAPER_WRITE", raising=False)
    namespace = _module()
    workspace = tmp_path / "workspace"
    ctx = _setup(workspace)

    session = namespace["prepare_from_evidence"](
        workspace_path=workspace,
        attempt_id=ATTEMPT_ID,
        credentials=_credentials(ctx.fresh_account),
        account=ctx.fresh_account,
        asset=ctx.fresh_asset,
        flat_account=ctx.fresh_flat,
        market_attestation=ctx.fresh_market,
        now=NOW + timedelta(seconds=4),
    )

    result = session.preparation_document
    assert result["status"] == "CRYPTO_PAPER_FIRST_CANARY_EXECUTION_PREPARED_NO_POST"
    assert result["environment"] == "PAPER"
    assert result["symbol"] == "BTC/USD"
    assert result["scope"] == "FIRST_TECHNICAL_CANARY_ONLY"
    assert result["attempt_id"] == ATTEMPT_ID
    assert result["broker_reads"] == 6
    assert Decimal(str(result["prepared_notional"])) >= Decimal("1")
    assert Decimal(str(result["prepared_notional"])) <= Decimal("5")
    assert result["operator_decision_recorded"] is False
    assert result["operator_decision_consumed"] is False
    assert result["final_guard_pre_consume_authorized"] is False
    assert result["oms_submitting"] is False
    assert result["lifecycle_unknown"] is False
    assert result["broker_write_performed"] is False
    assert result["external_post_authorized"] is False
    assert result["credentials_persisted"] is False
    assert result["live_trading"] == "BLOCKED"
    assert session.operator_context.attempt_id == ATTEMPT_ID
    assert session.preparation.package.client_order_id == result["prepared_package"]["client_order_id"]
    assert session.preparation.broker_order.to_payload() == result["broker_order"]["payload"]

    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id=ATTEMPT_ID,
    )
    assert attempt.preparation_path.is_file()
    assert attempt.approval_receipt_path.exists() is False
    assert attempt.execution_result_path.exists() is False
    assert attempt.reconciliation_path.exists() is False
    assert attempt.read(path=attempt.preparation_path) == result
    lifecycle_state = SQLiteCryptoPaperLifecycle(SQLiteRuntime(attempt.database_path)).snapshot(
        session.preparation.package.lifecycle_id
    ).state
    assert lifecycle_state.status is CryptoLifecycleStatus.ENTRY_PREPARED
    assert lifecycle_state.entry_attempt_count == 0
    order = SQLiteOrderStore(SQLiteRuntime(attempt.database_path)).get_by_order_id(
        session.preparation.package.order_id
    )
    assert order is not None
    assert order.status.value == "VALIDATED"

    safety = SQLiteR2SafetyStateStore(SQLiteRuntime(workspace / "core.sqlite3")).get()
    assert safety.kill_switch_active is True
    assert safety.kill_switch_reason == "R6_HEALTH_R4_EVIDENCE_REQUIRED"


def test_first_canary_prepare_rejects_replay_of_same_attempt_identity(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("R6_EXTERNAL_PAPER_WRITE", raising=False)
    namespace = _module()
    workspace = tmp_path / "workspace"
    ctx = _setup(workspace)
    kwargs = dict(
        workspace_path=workspace,
        attempt_id=ATTEMPT_ID,
        credentials=_credentials(ctx.fresh_account),
        account=ctx.fresh_account,
        asset=ctx.fresh_asset,
        flat_account=ctx.fresh_flat,
        market_attestation=ctx.fresh_market,
        now=NOW + timedelta(seconds=4),
    )
    namespace["prepare_from_evidence"](**kwargs)
    with pytest.raises(namespace["CryptoFirstCanaryPreparationError"], match="already exists"):
        namespace["prepare_from_evidence"](**kwargs)


def test_first_canary_prepare_rejects_non_exact_authoritative_core(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("R6_EXTERNAL_PAPER_WRITE", raising=False)
    namespace = _module()
    workspace = tmp_path / "workspace"
    ctx = _setup(workspace)
    ctx.safety.activate_circuit(
        reason="SYNTHETIC_LATE_CIRCUIT",
        now=NOW + timedelta(seconds=3, milliseconds=500),
    )
    with pytest.raises(namespace["CryptoFirstCanaryPreparationError"], match="authoritative cold-start"):
        namespace["prepare_from_evidence"](
            workspace_path=workspace,
            attempt_id=ATTEMPT_ID,
            credentials=_credentials(ctx.fresh_account),
            account=ctx.fresh_account,
            asset=ctx.fresh_asset,
            flat_account=ctx.fresh_flat,
            market_attestation=ctx.fresh_market,
            now=NOW + timedelta(seconds=4),
        )


def test_first_canary_prepare_rejects_nonflat_broker_and_wrong_credential(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("R6_EXTERNAL_PAPER_WRITE", raising=False)
    namespace = _module()

    workspace = tmp_path / "nonflat"
    ctx = _setup(workspace)
    nonflat = _flat_attestation(
        ctx.fresh_account,
        at=ctx.fresh_flat.attested_at,
        positions=1,
    )
    with pytest.raises(namespace["CryptoFirstCanaryPreparationError"], match="not flat"):
        namespace["prepare_from_evidence"](
            workspace_path=workspace,
            attempt_id=ATTEMPT_ID,
            credentials=_credentials(ctx.fresh_account),
            account=ctx.fresh_account,
            asset=ctx.fresh_asset,
            flat_account=nonflat,
            market_attestation=ctx.fresh_market,
            now=NOW + timedelta(seconds=4),
        )

    workspace = tmp_path / "credential"
    ctx = _setup(workspace)
    wrong = SimpleNamespace(credential_reference="f" * 64)
    with pytest.raises(namespace["CryptoFirstCanaryPreparationError"], match="effective PAPER credential"):
        namespace["prepare_from_evidence"](
            workspace_path=workspace,
            attempt_id="first-canary-ffffffffffffffffffffffffffffffff",
            credentials=wrong,
            account=ctx.fresh_account,
            asset=ctx.fresh_asset,
            flat_account=ctx.fresh_flat,
            market_attestation=ctx.fresh_market,
            now=NOW + timedelta(seconds=4),
        )


def test_first_canary_prepare_refuses_write_enabled_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "ENABLED")
    namespace = _module()
    workspace = tmp_path / "workspace"
    ctx = _setup(workspace)
    with pytest.raises(namespace["CryptoFirstCanaryPreparationError"], match="refuses broker-write"):
        namespace["prepare_from_evidence"](
            workspace_path=workspace,
            attempt_id=ATTEMPT_ID,
            credentials=_credentials(ctx.fresh_account),
            account=ctx.fresh_account,
            asset=ctx.fresh_asset,
            flat_account=ctx.fresh_flat,
            market_attestation=ctx.fresh_market,
            now=NOW + timedelta(seconds=4),
        )
