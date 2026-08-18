from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sys

from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    ATTEMPT_DB_NAME,
    ATTEMPT_ID_RE,
    EXECUTION_DIR,
    FirstCanaryAttemptWorkspace,
)
from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionContext,
    CryptoOperatorDecisionStatus,
    SQLiteCryptoOperatorDecisionRegistry,
    crypto_operator_confirmation_challenge,
)
from autotrade.persistence import SQLiteRuntime


WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
ATTEMPT_PREFIX = "first-canary-"
MAX_APPROVAL_TTL = timedelta(seconds=90)
MIN_REMAINING_PACKAGE_LIFE = timedelta(seconds=5)
EXPECTED_SYMBOL = "BTC/USD"


class CryptoFirstCanaryApprovalError(RuntimeError):
    pass


def _aware(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def attempt_database(*, workspace_path: Path, attempt_id: str) -> Path:
    try:
        return FirstCanaryAttemptWorkspace.open(
            workspace_path=workspace_path,
            attempt_id=attempt_id,
        ).database_path
    except Exception as exc:
        raise CryptoFirstCanaryApprovalError(str(exc)) from exc


def issue_approval(
    *,
    workspace_path: Path,
    attempt_id: str,
    context_payload: dict[str, object],
    operator_id: str,
    confirmation: str,
    now: datetime,
) -> dict[str, object]:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise CryptoFirstCanaryApprovalError(
            "human approval issuance is isolated from broker-write enablement"
        )
    instant = _aware(now, label="now")
    if not ATTEMPT_ID_RE.fullmatch(attempt_id):
        raise CryptoFirstCanaryApprovalError("execution attempt_id is invalid")
    operator = operator_id.strip()
    if not operator:
        raise CryptoFirstCanaryApprovalError("Operator ID is required")
    if len(operator) > 128:
        raise CryptoFirstCanaryApprovalError("Operator ID is unexpectedly long")

    context = CryptoOperatorDecisionContext.from_dict(context_payload)
    if context.symbol != EXPECTED_SYMBOL:
        raise CryptoFirstCanaryApprovalError("approval context is not exact BTC/USD")
    if context.attempt_id != attempt_id or not context.attempt_id.startswith(ATTEMPT_PREFIX):
        raise CryptoFirstCanaryApprovalError("approval context is not bound to this execution attempt")
    if "uat" in context.attempt_id.lower():
        raise CryptoFirstCanaryApprovalError("UAT approval identity is forbidden for execution")
    challenge = crypto_operator_confirmation_challenge(context)
    if not secrets.compare_digest(confirmation, challenge):
        raise CryptoFirstCanaryApprovalError(
            "human confirmation does not exactly match the one-shot execution challenge"
        )

    deadline = _aware(context.execution_deadline, label="execution_deadline")
    if deadline <= instant + MIN_REMAINING_PACKAGE_LIFE:
        raise CryptoFirstCanaryApprovalError(
            "execution package is too close to expiry; prepare fresh broker evidence"
        )
    expires_at = min(deadline, instant + MAX_APPROVAL_TTL)
    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace_path,
        attempt_id=attempt_id,
    )
    attempt.assert_unexecuted()
    registry = SQLiteCryptoOperatorDecisionRegistry(SQLiteRuntime(attempt.database_path))
    state = registry.record_operator_approval(
        context=context,
        operator_id=operator,
        issued_at=instant,
        expires_at=expires_at,
    )
    verified = registry.get(context.preparation_hash)
    if verified != state or state.status is not CryptoOperatorDecisionStatus.ISSUED:
        raise CryptoFirstCanaryApprovalError(
            "durable execution approval registry did not verify exact ISSUED state"
        )
    if state.consumed_at is not None or state.consumed_attempt_id is not None:
        raise CryptoFirstCanaryApprovalError("new execution approval unexpectedly appears consumed")

    receipt: dict[str, object] = {
        "schema_version": 1,
        "status": "CRYPTO_PAPER_FIRST_CANARY_EXECUTION_APPROVAL_RECORDED",
        "environment": "PAPER",
        "symbol": EXPECTED_SYMBOL,
        "scope": "FIRST_TECHNICAL_CANARY_ONLY",
        "decision_status": state.status.value,
        "operator_id": state.decision.operator_id,
        "attempt_id": attempt_id,
        "preparation_hash": context.preparation_hash,
        "prepared_package_hash": context.prepared_package_hash,
        "client_order_id": context.client_order_id,
        "decision_hash": state.decision.decision_hash,
        "event_hash": state.event_hash,
        "event_sequence": state.event_sequence,
        "issued_at": state.decision.issued_at.isoformat(),
        "expires_at": state.decision.expires_at.isoformat(),
        "decision_consumed": False,
        "approval_database": f"{EXECUTION_DIR}/{attempt_id}/{ATTEMPT_DB_NAME}",
        "uat_only": False,
        "reusable_for_uat": False,
        "reusable_for_other_attempt": False,
        "execution_authority": "NONE_UNTIL_PRE_CONSUME_OMS_PRE_IO",
        "broker_write_performed": False,
        "external_post_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "profitability_claim": False,
        "next_action": "RUN_EXACT_PRE_CONSUME_AND_OMS_GATE_BEFORE_ANY_PAPER_POST",
    }
    receipt["approval_receipt_hash"] = attempt.document_hash(
        receipt,
        hash_key="approval_receipt_hash",
    )
    attempt.write_once(path=attempt.approval_receipt_path, document=receipt)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Record the NEW exact human approval for one BTC/USD PAPER first-canary execution attempt. "
            "This issuer has no credentials, no broker transport, no approval-consumption API and no POST authority."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise CryptoFirstCanaryApprovalError("issuer input must be a JSON object")
        context_payload = payload.get("context")
        if not isinstance(context_payload, dict):
            raise CryptoFirstCanaryApprovalError("execution approval context is required")
        result = issue_approval(
            workspace_path=args.workspace,
            attempt_id=str(args.attempt_id),
            context_payload=context_payload,
            operator_id=str(payload.get("operator_id") or ""),
            confirmation=str(payload.get("confirmation") or ""),
            now=datetime.now(timezone.utc),
        )
    except Exception as exc:
        result = {
            "status": "CRYPTO_PAPER_FIRST_CANARY_EXECUTION_APPROVAL_BLOCKED",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "decision_consumed": False,
            "execution_authority": "NONE",
            "broker_write_performed": False,
            "external_post_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())