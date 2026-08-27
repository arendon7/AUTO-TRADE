from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    ATTEMPT_ID_RE,
    FirstCanaryAttemptWorkspace,
)
from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionStatus,
    SQLiteCryptoOperatorDecisionRegistry,
)
from autotrade.paper_execution_human_review import (
    prepare_paper_execution_human_review,
)
from autotrade.persistence import SQLiteRuntime
import autotrade.paper_execution_human_review as review_module
from scripts import mac_crypto_first_canary_approval as canonical_issuer
from scripts import w87_issue_paper_execution_human_approval as approval_module
from scripts.w87_issue_paper_execution_human_approval import (
    W87HumanApprovalError,
    issue_w87_human_approval,
)
from test_w87_paper_execution_human_review import _long_review_stack


def _review(monkeypatch, tmp_path):
    sealed, _, _, handoff, preparation, _, _, broker, _ = _long_review_stack(
        monkeypatch, tmp_path
    )
    review_at = sealed.seal.valid_until + timedelta(seconds=1)
    monkeypatch.setattr(review_module, "_now_utc", lambda: review_at)
    review = prepare_paper_execution_human_review(
        review_id="w87-e-human-review",
        preparation=preparation,
        risk_handoff=handoff,
    )
    return review, review_at, broker


def _registry(workspace: Path, attempt_id: str) -> SQLiteCryptoOperatorDecisionRegistry:
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id=attempt_id,
    )
    return SQLiteCryptoOperatorDecisionRegistry(SQLiteRuntime(attempt.database_path))


def test_w87_e_issues_exact_durable_human_approval_without_consuming_or_posting(
    monkeypatch, tmp_path
):
    review, review_at, broker = _review(monkeypatch, tmp_path)
    workspace = tmp_path / "operator-workspace"
    workspace.mkdir()
    issue_at = review_at + timedelta(milliseconds=100)
    monkeypatch.setattr(approval_module, "_now_utc", lambda: issue_at)

    receipt = issue_w87_human_approval(
        workspace_path=workspace,
        review=review,
        operator_id="operator-001",
        confirmation=review.receipt.approval_challenge,
    )

    assert ATTEMPT_ID_RE.fullmatch(review.receipt.attempt_id)
    assert receipt["status"] == "CRYPTO_PAPER_FIRST_CANARY_EXECUTION_APPROVAL_RECORDED"
    assert receipt["decision_status"] == "ISSUED"
    assert receipt["decision_consumed"] is False
    assert receipt["execution_authority"] == "NONE_UNTIL_PRE_CONSUME_OMS_PRE_IO"
    assert receipt["broker_write_performed"] is False
    assert receipt["external_post_authorized"] is False
    assert receipt["capital_authority"] == "NONE"
    assert receipt["live_trading"] == "BLOCKED"
    assert broker.submit_calls == 0

    registry = _registry(workspace, review.receipt.attempt_id)
    state = registry.get(review.receipt.operator_preparation_hash)
    assert state.status is CryptoOperatorDecisionStatus.ISSUED
    assert state.decision.context == review.operator_context
    assert state.decision.operator_id == "operator-001"
    assert state.consumed_at is None
    assert state.consumed_attempt_id is None


def test_w87_e_wrong_confirmation_creates_no_attempt_or_authority(monkeypatch, tmp_path):
    review, review_at, broker = _review(monkeypatch, tmp_path)
    workspace = tmp_path / "wrong-confirmation-workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        approval_module,
        "_now_utc",
        lambda: review_at + timedelta(milliseconds=100),
    )

    with pytest.raises(W87HumanApprovalError, match="exact W87 human-review challenge"):
        issue_w87_human_approval(
            workspace_path=workspace,
            review=review,
            operator_id="operator-001",
            confirmation="APPROVE SOMETHING ELSE",
        )
    assert not (workspace / "first_canary_execution").exists()
    assert broker.submit_calls == 0


def test_w87_e_write_enablement_is_fail_closed_before_authority_mint(monkeypatch, tmp_path):
    review, review_at, broker = _review(monkeypatch, tmp_path)
    workspace = tmp_path / "write-enabled-workspace"
    workspace.mkdir()
    monkeypatch.setenv(canonical_issuer.WRITE_ENV, "ENABLED")
    monkeypatch.setattr(
        approval_module,
        "_now_utc",
        lambda: review_at + timedelta(milliseconds=100),
    )

    with pytest.raises(canonical_issuer.CryptoFirstCanaryApprovalError, match="isolated"):
        issue_w87_human_approval(
            workspace_path=workspace,
            review=review,
            operator_id="operator-001",
            confirmation=review.receipt.approval_challenge,
        )
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id=review.receipt.attempt_id,
    )
    assert not attempt.database_path.exists()
    assert not attempt.approval_receipt_path.exists()
    assert broker.submit_calls == 0


def test_canonical_issuer_recovers_missing_receipt_from_exact_durable_issued_state(
    monkeypatch, tmp_path
):
    review, review_at, broker = _review(monkeypatch, tmp_path)
    workspace = tmp_path / "crash-recovery-workspace"
    workspace.mkdir()
    first_at = review_at + timedelta(milliseconds=100)
    monkeypatch.setattr(approval_module, "_now_utc", lambda: first_at)
    first = issue_w87_human_approval(
        workspace_path=workspace,
        review=review,
        operator_id="operator-001",
        confirmation=review.receipt.approval_challenge,
    )
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id=review.receipt.attempt_id,
    )
    attempt.approval_receipt_path.unlink()
    later = first_at + timedelta(seconds=1)
    monkeypatch.setattr(approval_module, "_now_utc", lambda: later)

    recovered = issue_w87_human_approval(
        workspace_path=workspace,
        review=review,
        operator_id="operator-001",
        confirmation=review.receipt.approval_challenge,
    )

    for key in (
        "decision_hash",
        "event_hash",
        "event_sequence",
        "issued_at",
        "expires_at",
        "approval_receipt_hash",
    ):
        assert recovered[key] == first[key]
    assert attempt.approval_receipt_path.is_file()
    with sqlite3.connect(attempt.database_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM alpaca_crypto_operator_decision_events WHERE event_type='ISSUED'"
        ).fetchone()[0]
    assert count == 1
    assert broker.submit_calls == 0


def test_canonical_issuer_rejects_different_operator_on_durable_recovery(
    monkeypatch, tmp_path
):
    review, review_at, broker = _review(monkeypatch, tmp_path)
    workspace = tmp_path / "operator-conflict-workspace"
    workspace.mkdir()
    first_at = review_at + timedelta(milliseconds=100)
    monkeypatch.setattr(approval_module, "_now_utc", lambda: first_at)
    issue_w87_human_approval(
        workspace_path=workspace,
        review=review,
        operator_id="operator-001",
        confirmation=review.receipt.approval_challenge,
    )
    monkeypatch.setattr(
        approval_module,
        "_now_utc",
        lambda: first_at + timedelta(seconds=1),
    )
    with pytest.raises(canonical_issuer.CryptoFirstCanaryApprovalError, match="another operator"):
        issue_w87_human_approval(
            workspace_path=workspace,
            review=review,
            operator_id="operator-002",
            confirmation=review.receipt.approval_challenge,
        )
    assert broker.submit_calls == 0


def test_w87_e_attempt_identity_is_stable_and_exactly_bound_to_review(monkeypatch, tmp_path):
    review, _, _ = _review(monkeypatch, tmp_path)
    assert review.receipt.attempt_id.startswith("first-canary-")
    assert ATTEMPT_ID_RE.fullmatch(review.receipt.attempt_id)
    assert review.operator_context.attempt_id == review.receipt.attempt_id
    assert review.receipt.attempt_id == (
        f"first-canary-{review.receipt.canary_preparation_hash[:32]}"
    )
