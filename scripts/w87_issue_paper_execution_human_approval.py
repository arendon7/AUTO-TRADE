from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import secrets

from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    FirstCanaryAttemptWorkspace,
)
from autotrade.paper_execution_human_review import (
    PaperExecutionHumanReviewResult,
)
from scripts import mac_crypto_first_canary_approval as canonical_issuer


class W87HumanApprovalError(RuntimeError):
    pass


def issue_w87_human_approval(
    *,
    workspace_path: Path,
    review: PaperExecutionHumanReviewResult,
    operator_id: str,
    confirmation: str,
) -> dict[str, object]:
    """Record the exact human decision through the sole audited R6 issuer.

    This wrapper has no credentials, broker/network transport, Final Guard,
    decision-consumption, OMS staging or POST surface. A successful result is
    durable ISSUED authority only; it is not executable authority.
    """

    if not isinstance(workspace_path, Path):
        raise TypeError("workspace_path must be pathlib.Path")
    if not isinstance(review, PaperExecutionHumanReviewResult):
        raise TypeError("review must be PaperExecutionHumanReviewResult")
    review.__post_init__()
    if (
        review.receipt.symbol != canonical_issuer.EXPECTED_SYMBOL
        or review.operator_context.symbol != canonical_issuer.EXPECTED_SYMBOL
    ):
        raise W87HumanApprovalError(
            "W87-E accepts canonical BTC/USD first-canary human review only"
        )
    operator = operator_id.strip()
    if not operator:
        raise W87HumanApprovalError("operator_id is required")
    if not secrets.compare_digest(confirmation, review.receipt.approval_challenge):
        raise W87HumanApprovalError(
            "confirmation does not match the exact W87 human-review challenge"
        )

    instant = _now_utc().astimezone(timezone.utc)
    if instant < review.receipt.review_prepared_at.astimezone(timezone.utc):
        raise W87HumanApprovalError("approval clock precedes human review")
    if instant >= review.receipt.package_execution_deadline.astimezone(timezone.utc):
        raise W87HumanApprovalError("prepared package expired before approval issuance")

    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace_path,
        attempt_id=review.receipt.attempt_id,
    )
    attempt.assert_unexecuted()
    receipt = canonical_issuer.issue_approval(
        workspace_path=workspace_path,
        attempt_id=review.receipt.attempt_id,
        context_payload=review.operator_context.to_dict(),
        operator_id=operator,
        confirmation=confirmation,
        now=instant,
    )
    _validate_receipt(receipt=receipt, review=review, operator=operator)
    return receipt


def _validate_receipt(
    *,
    receipt: dict[str, object],
    review: PaperExecutionHumanReviewResult,
    operator: str,
) -> None:
    expected = {
        "status": "CRYPTO_PAPER_FIRST_CANARY_EXECUTION_APPROVAL_RECORDED",
        "environment": "PAPER",
        "symbol": review.receipt.symbol,
        "decision_status": "ISSUED",
        "operator_id": operator,
        "attempt_id": review.receipt.attempt_id,
        "preparation_hash": review.receipt.operator_preparation_hash,
        "prepared_package_hash": review.receipt.package_hash,
        "decision_consumed": False,
        "uat_only": False,
        "reusable_for_uat": False,
        "reusable_for_other_attempt": False,
        "execution_authority": "NONE_UNTIL_PRE_CONSUME_OMS_PRE_IO",
        "broker_write_performed": False,
        "external_post_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "profitability_claim": False,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise W87HumanApprovalError(
                f"canonical issuer receipt violates W87-E invariant: {key}"
            )
    for key in ("decision_hash", "event_hash", "approval_receipt_hash"):
        value = receipt.get(key)
        if not isinstance(value, str) or len(value) != 64:
            raise W87HumanApprovalError(
                f"canonical issuer receipt lacks exact {key}"
            )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "W87HumanApprovalError",
    "issue_w87_human_approval",
]
