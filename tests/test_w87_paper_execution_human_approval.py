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
    CryptoOperatorDecisionContext,
    CryptoOperatorDecisionStatus,
    SQLiteCryptoOperatorDecisionRegistry,
    crypto_operator_confirmation_challenge,
)
from autotrade.paper_execution_human_review import (
    PAPER_EXECUTION_HUMAN_REVIEW_VERSION,
    PaperExecutionHumanReviewReceipt,
    PaperExecutionHumanReviewResult,
    PaperExecutionHumanReviewStatus,
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
from test_r6_paper_crypto_canary_coordinator import NOW as R6_NOW, _prepare as _r6_prepare
from test_w87_paper_execution_human_review import _long_review_stack


def _btc_review(tmp_path):
    """Build the exact W87-E input from the existing offline R6 BTC canary contract.

    W87-D has its own end-to-end W86→W87 review tests. W87-E intentionally
    exercises only the review→canonical-R6-issuer boundary with the real R6
    BTC/USD LIMIT IOC package rather than weakening the execution issuer to
    accept the generic TEST/USD fixtures used elsewhere.
    """

    prepared, _ = _r6_prepare(tmp_path / "btc-r6-package")
    package = prepared.package
    canary_preparation_hash = package.package_hash
    attempt_id = f"first-canary-{canary_preparation_hash[:32]}"
    context = CryptoOperatorDecisionContext.from_prepared_package(
        package,
        attempt_id=attempt_id,
    )
    review_at = package.prepared_at + timedelta(seconds=1)
    values = {
        "review_id": "w87-e-btc-human-review",
        "contract_version": PAPER_EXECUTION_HUMAN_REVIEW_VERSION,
        "canary_preparation_hash": canary_preparation_hash,
        "risk_handoff_hash": "1" * 64,
        "source_risk_contract_hash": "2" * 64,
        "package_hash": package.package_hash,
        "attempt_id": attempt_id,
        "operator_preparation_hash": context.preparation_hash,
        "account_id": "12345678-abcd-abcd-abcd-123456789012",
        "symbol": package.symbol,
        "quantity": package.quantity,
        "limit_price": package.limit_price,
        "notional_usd": package.notional,
        "review_prepared_at": review_at,
        "package_execution_deadline": package.execution_deadline,
        "approval_challenge": crypto_operator_confirmation_challenge(context),
        "status": PaperExecutionHumanReviewStatus.REVIEW_PREPARED,
        "exact_canary_binding_verified": True,
        "exact_risk_handoff_binding_verified": True,
        "sufficient_human_window_verified": True,
        "human_operator_approval_required": True,
        "operator_decision_status": "NOT_ISSUED",
        "operator_decision_issued": False,
        "operator_decision_consumed": False,
        "oms_handoff_permitted": False,
        "capital_reserved": False,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "paper_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "next_action": "HUMAN_OPERATOR_APPROVAL_REQUIRED",
    }
    receipt = PaperExecutionHumanReviewReceipt(
        **values,
        receipt_hash=review_module._hash(review_module._payload_values(values)),
    )
    result = PaperExecutionHumanReviewResult(
        receipt=receipt,
        operator_context=context,
    )
    result.__post_init__()
    assert package.symbol == canonical_issuer.EXPECTED_SYMBOL == "BTC/USD"
    assert review_at >= R6_NOW
    return result, review_at


def _non_btc_review(monkeypatch, tmp_path):
    sealed, _, _, handoff, preparation, _, _, broker, _ = _long_review_stack(
        monkeypatch, tmp_path
    )
    review_at = sealed.seal.valid_until + timedelta(seconds=1)
    monkeypatch.setattr(review_module, "_now_utc", lambda: review_at)
    review = prepare_paper_execution_human_review(
        review_id="w87-e-non-btc-review",
        preparation=preparation,
        risk_handoff=handoff,
    )
    assert review.receipt.symbol == "TEST/USD"
    return review, review_at, broker


def _registry(workspace: Path, attempt_id: str) -> SQLiteCryptoOperatorDecisionRegistry:
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id=attempt_id,
    )
    return SQLiteCryptoOperatorDecisionRegistry(SQLiteRuntime(attempt.database_path))


def _assert_no_execution_evidence(attempt: FirstCanaryAttemptWorkspace) -> None:
    for path in (
        attempt.execution_started_path,
        attempt.execution_result_path,
        attempt.reconciliation_failure_path,
        attempt.reconciliation_pending_path,
        attempt.reconciliation_path,
        attempt.recovery_resolution_path,
    ):
        assert not path.exists()


def test_w87_e_issues_exact_durable_human_approval_without_consuming_or_posting(
    monkeypatch, tmp_path
):
    review, review_at = _btc_review(tmp_path)
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
    assert receipt["symbol"] == "BTC/USD"
    assert receipt["decision_status"] == "ISSUED"
    assert receipt["decision_consumed"] is False
    assert receipt["execution_authority"] == "NONE_UNTIL_PRE_CONSUME_OMS_PRE_IO"
    assert receipt["broker_write_performed"] is False
    assert receipt["external_post_authorized"] is False
    assert receipt["capital_authority"] == "NONE"
    assert receipt["live_trading"] == "BLOCKED"

    registry = _registry(workspace, review.receipt.attempt_id)
    state = registry.get(review.receipt.operator_preparation_hash)
    assert state.status is CryptoOperatorDecisionStatus.ISSUED
    assert state.decision.context == review.operator_context
    assert state.decision.operator_id == "operator-001"
    assert state.consumed_at is None
    assert state.consumed_attempt_id is None
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id=review.receipt.attempt_id,
    )
    _assert_no_execution_evidence(attempt)


def test_w87_e_rejects_non_btc_review_before_attempt_persistence(monkeypatch, tmp_path):
    review, review_at, broker = _non_btc_review(monkeypatch, tmp_path)
    workspace = tmp_path / "non-btc-workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        approval_module,
        "_now_utc",
        lambda: review_at + timedelta(milliseconds=100),
    )

    with pytest.raises(W87HumanApprovalError, match="BTC/USD"):
        issue_w87_human_approval(
            workspace_path=workspace,
            review=review,
            operator_id="operator-001",
            confirmation=review.receipt.approval_challenge,
        )
    assert not (workspace / "first_canary_execution").exists()
    assert broker.submit_calls == 0


def test_w87_e_wrong_confirmation_creates_no_attempt_or_authority(monkeypatch, tmp_path):
    review, review_at = _btc_review(tmp_path)
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


def test_w87_e_write_enablement_is_fail_closed_before_authority_mint(monkeypatch, tmp_path):
    review, review_at = _btc_review(tmp_path)
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
    _assert_no_execution_evidence(attempt)


def test_canonical_issuer_recovers_missing_receipt_from_exact_durable_issued_state(
    monkeypatch, tmp_path
):
    review, review_at = _btc_review(tmp_path)
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
    _assert_no_execution_evidence(attempt)


def test_canonical_issuer_rejects_different_operator_on_durable_recovery(
    monkeypatch, tmp_path
):
    review, review_at = _btc_review(tmp_path)
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
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id=review.receipt.attempt_id,
    )
    _assert_no_execution_evidence(attempt)


def test_w87_e_attempt_identity_is_stable_and_exactly_bound_to_review(monkeypatch, tmp_path):
    del monkeypatch
    review, _ = _btc_review(tmp_path)
    assert review.receipt.symbol == "BTC/USD"
    assert review.receipt.attempt_id.startswith("first-canary-")
    assert ATTEMPT_ID_RE.fullmatch(review.receipt.attempt_id)
    assert review.operator_context.attempt_id == review.receipt.attempt_id
    assert review.receipt.attempt_id == (
        f"first-canary-{review.receipt.canary_preparation_hash[:32]}"
    )
