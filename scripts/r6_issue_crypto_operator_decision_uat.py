from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import sys

from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionContext,
    CryptoOperatorDecisionStatus,
    SQLiteCryptoOperatorDecisionRegistry,
    crypto_operator_confirmation_challenge,
)
from autotrade.persistence import SQLiteRuntime


APPROVAL_DB_DIR = "qualification_uat"
APPROVAL_DB_NAME = "crypto_one_shot_approval_uat.sqlite3"
_MAX_UAT_APPROVAL_TTL = timedelta(seconds=60)
_MIN_REMAINING_PACKAGE_LIFE = timedelta(seconds=5)
_ATTEMPT_PREFIX = "approval-uat-"


class CryptoOperatorApprovalUATError(RuntimeError):
    pass


def _workspace_database(workspace_path: Path) -> Path:
    raw = workspace_path.expanduser()
    if raw.is_symlink() or not raw.is_dir():
        raise CryptoOperatorApprovalUATError("approval workspace is unavailable or unsafe")
    workspace = raw.resolve()
    evidence_dir = workspace / APPROVAL_DB_DIR
    if evidence_dir.exists() and evidence_dir.is_symlink():
        raise CryptoOperatorApprovalUATError("approval evidence directory may not be a symlink")
    evidence_dir.mkdir(mode=0o700, parents=False, exist_ok=True)
    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        raise CryptoOperatorApprovalUATError("approval evidence directory is unsafe")
    database = evidence_dir / APPROVAL_DB_NAME
    if database.is_symlink():
        raise CryptoOperatorApprovalUATError("approval evidence database may not be a symlink")
    return database


def issue(
    *,
    workspace_path: Path,
    context_payload: dict[str, object],
    operator_id: str,
    confirmation: str,
    now: datetime,
) -> dict[str, object]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    operator = operator_id.strip()
    if not operator:
        raise CryptoOperatorApprovalUATError("Operator ID is required")
    if len(operator) > 128:
        raise CryptoOperatorApprovalUATError("Operator ID is unexpectedly long")

    context = CryptoOperatorDecisionContext.from_dict(context_payload)
    if context.symbol != "BTC/USD" or not context.attempt_id.startswith(_ATTEMPT_PREFIX):
        raise CryptoOperatorApprovalUATError("approval context is not the exact BTC/USD UAT context")
    challenge = crypto_operator_confirmation_challenge(context)
    if not secrets.compare_digest(confirmation, challenge):
        raise CryptoOperatorApprovalUATError(
            "human confirmation does not exactly match the one-shot challenge"
        )

    instant = now.astimezone(timezone.utc)
    deadline = context.execution_deadline.astimezone(timezone.utc)
    if deadline <= instant + _MIN_REMAINING_PACKAGE_LIFE:
        raise CryptoOperatorApprovalUATError(
            "approval package is too close to expiry; prepare a fresh challenge"
        )
    expires_at = min(deadline, instant + _MAX_UAT_APPROVAL_TTL)

    database = _workspace_database(workspace_path)
    registry = SQLiteCryptoOperatorDecisionRegistry(SQLiteRuntime(database))
    state = registry.record_operator_approval(
        context=context,
        operator_id=operator,
        issued_at=instant,
        expires_at=expires_at,
    )
    verified = registry.get(context.preparation_hash)
    if verified != state or state.status is not CryptoOperatorDecisionStatus.ISSUED:
        raise CryptoOperatorApprovalUATError(
            "durable crypto approval registry did not verify exact ISSUED state"
        )
    if state.consumed_at is not None or state.consumed_attempt_id is not None:
        raise CryptoOperatorApprovalUATError("UAT approval unexpectedly appears consumed")

    return {
        "status": "CRYPTO_PAPER_ONE_SHOT_APPROVAL_RECORDED_UAT",
        "decision_status": state.status.value,
        "operator_id": state.decision.operator_id,
        "attempt_id": context.attempt_id,
        "preparation_hash": context.preparation_hash,
        "decision_hash": state.decision.decision_hash,
        "event_hash": state.event_hash,
        "event_sequence": state.event_sequence,
        "issued_at": state.decision.issued_at.isoformat(),
        "expires_at": state.decision.expires_at.isoformat(),
        "decision_consumed": False,
        "approval_database": f"{APPROVAL_DB_DIR}/{APPROVAL_DB_NAME}",
        "uat_only": True,
        "reusable_for_real_execution": False,
        "execution_authority": "NONE",
        "broker_write_performed": False,
        "external_post_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "next_action": "BUILD_SEPARATE_RECERTIFIED_EXECUTION_GATE_WITH_NEW_FRESH_APPROVAL",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Canonical local issuer for crypto one-shot human approval UAT. Reads a sanitized context + "
            "human confirmation from stdin, records ISSUED only, and has no approval-consumption or broker surface."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise CryptoOperatorApprovalUATError("issuer input must be a JSON object")
        context_payload = payload.get("context")
        if not isinstance(context_payload, dict):
            raise CryptoOperatorApprovalUATError("issuer context is required")
        receipt = issue(
            workspace_path=args.workspace,
            context_payload=context_payload,
            operator_id=str(payload.get("operator_id") or ""),
            confirmation=str(payload.get("confirmation") or ""),
            now=datetime.now(timezone.utc),
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "CRYPTO_PAPER_ONE_SHOT_APPROVAL_ISSUER_BLOCKED",
                    "reason": str(exc),
                    "error_type": type(exc).__name__,
                    "decision_consumed": False,
                    "execution_authority": "NONE",
                    "broker_write_performed": False,
                    "external_post_authorized": False,
                    "capital_authority": "NONE",
                    "live_trading": "BLOCKED",
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
