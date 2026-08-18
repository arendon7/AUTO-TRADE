from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import runpy

import pytest

from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionContext,
    SQLiteCryptoOperatorDecisionRegistry,
    crypto_operator_confirmation_challenge,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_paper_crypto_cold_start_final_guard import NOW, _setup


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mac_crypto_first_canary_approval.py"
ATTEMPT_ID = "first-canary-0123456789abcdef0123456789abcdef"


def _module():
    return runpy.run_path(str(SCRIPT))


def _context(tmp_path):
    ctx = _setup(tmp_path / "source")
    context = CryptoOperatorDecisionContext.from_prepared_package(
        ctx.package,
        attempt_id=ATTEMPT_ID,
    )
    return ctx, context


def test_execution_approval_records_exact_issued_decision_in_attempt_db(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("R6_EXTERNAL_PAPER_WRITE", raising=False)
    namespace = _module()
    _, context = _context(tmp_path)
    challenge = crypto_operator_confirmation_challenge(context)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = namespace["issue_approval"](
        workspace_path=workspace,
        attempt_id=ATTEMPT_ID,
        context_payload=context.to_dict(),
        operator_id="operator-001",
        confirmation=challenge,
        now=NOW + timedelta(seconds=3),
    )

    assert result["status"] == "CRYPTO_PAPER_FIRST_CANARY_EXECUTION_APPROVAL_RECORDED"
    assert result["attempt_id"] == ATTEMPT_ID
    assert result["prepared_package_hash"] == context.prepared_package_hash
    assert result["client_order_id"] == context.client_order_id
    assert result["decision_status"] == "ISSUED"
    assert result["decision_consumed"] is False
    assert result["uat_only"] is False
    assert result["reusable_for_uat"] is False
    assert result["reusable_for_other_attempt"] is False
    assert result["broker_write_performed"] is False
    assert result["external_post_authorized"] is False
    assert result["live_trading"] == "BLOCKED"

    database = namespace["attempt_database"](
        workspace_path=workspace,
        attempt_id=ATTEMPT_ID,
    )
    state = SQLiteCryptoOperatorDecisionRegistry(SQLiteRuntime(database)).get(
        context.preparation_hash
    )
    assert state.status.value == "ISSUED"
    assert state.decision.attempt_id == ATTEMPT_ID
    assert state.consumed_at is None
    assert state.consumed_attempt_id is None


def test_execution_approval_rejects_wrong_challenge_without_durable_decision(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("R6_EXTERNAL_PAPER_WRITE", raising=False)
    namespace = _module()
    _, context = _context(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(namespace["CryptoFirstCanaryApprovalError"], match="does not exactly match"):
        namespace["issue_approval"](
            workspace_path=workspace,
            attempt_id=ATTEMPT_ID,
            context_payload=context.to_dict(),
            operator_id="operator-001",
            confirmation="WRONG",
            now=NOW + timedelta(seconds=3),
        )


def test_execution_approval_rejects_uat_or_cross_attempt_context(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("R6_EXTERNAL_PAPER_WRITE", raising=False)
    namespace = _module()
    ctx = _setup(tmp_path / "source")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    uat = CryptoOperatorDecisionContext.from_prepared_package(
        ctx.package,
        attempt_id="approval-uat-0123456789abcdef01234567",
    )
    with pytest.raises(namespace["CryptoFirstCanaryApprovalError"], match="not bound to this execution attempt"):
        namespace["issue_approval"](
            workspace_path=workspace,
            attempt_id=ATTEMPT_ID,
            context_payload=uat.to_dict(),
            operator_id="operator-001",
            confirmation=crypto_operator_confirmation_challenge(uat),
            now=NOW + timedelta(seconds=3),
        )

    other = CryptoOperatorDecisionContext.from_prepared_package(
        ctx.package,
        attempt_id="first-canary-ffffffffffffffffffffffffffffffff",
    )
    with pytest.raises(namespace["CryptoFirstCanaryApprovalError"], match="not bound to this execution attempt"):
        namespace["issue_approval"](
            workspace_path=workspace,
            attempt_id=ATTEMPT_ID,
            context_payload=other.to_dict(),
            operator_id="operator-001",
            confirmation=crypto_operator_confirmation_challenge(other),
            now=NOW + timedelta(seconds=3),
        )


def test_execution_approval_isolated_from_write_enabled_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "ENABLED")
    namespace = _module()
    _, context = _context(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(namespace["CryptoFirstCanaryApprovalError"], match="isolated from broker-write enablement"):
        namespace["issue_approval"](
            workspace_path=workspace,
            attempt_id=ATTEMPT_ID,
            context_payload=context.to_dict(),
            operator_id="operator-001",
            confirmation=crypto_operator_confirmation_challenge(context),
            now=NOW + timedelta(seconds=3),
        )


def test_execution_approval_rejects_unsafe_workspace_and_attempt_identity(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("R6_EXTERNAL_PAPER_WRITE", raising=False)
    namespace = _module()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    unsafe = tmp_path / "unsafe"
    unsafe.symlink_to(target, target_is_directory=True)

    with pytest.raises(namespace["CryptoFirstCanaryApprovalError"], match="non-symlink workspace"):
        namespace["attempt_database"](
            workspace_path=unsafe,
            attempt_id=ATTEMPT_ID,
        )
    with pytest.raises(namespace["CryptoFirstCanaryApprovalError"], match="attempt_id is invalid"):
        namespace["attempt_database"](
            workspace_path=workspace,
            attempt_id="first-canary-not-hex",
        )
