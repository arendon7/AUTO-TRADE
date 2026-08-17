from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import mac_crypto_canary_preview as preview

from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionContext,
    crypto_operator_confirmation_challenge,
)


APPROVAL_DECISION_TTL_MS = 90_000
APPROVAL_STRATEGY_ID = "R6_CRYPTO_PAPER_ONE_SHOT_APPROVAL_UAT"


class CryptoPaperApprovalPrepareError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fresh BTC/USD PAPER one-shot human-approval rehearsal. Reuses the certified read/Safety/OMS/"
            "canary preparation path, lengthens only the UAT decision window to make human review usable, "
            "and emits a challenge/context that may be recorded as UAT-only approval. No approval is "
            "recorded here and no broker POST surface exists."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--symbol", default=preview.CRYPTO_PAIR)
    parser.add_argument("--allow-paper-crypto-read", action="store_true")
    return parser


def run(
    *,
    workspace_path: Path,
    credentials,
    now: datetime,
    symbol: str = preview.CRYPTO_PAIR,
) -> dict[str, object]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    captured: dict[str, CryptoOperatorDecisionContext] = {}
    original_context_type = preview.CryptoOperatorDecisionContext
    original_safety_limits = preview.SafetyLimits
    original_strategy_id = preview.STRATEGY_ID

    class _CaptureContext:
        @classmethod
        def from_prepared_package(cls, package, *, attempt_id: str):
            del attempt_id
            context = CryptoOperatorDecisionContext.from_prepared_package(
                package,
                attempt_id=f"approval-uat-{package.package_hash[:24]}",
            )
            captured["context"] = context
            return context

    def _ApprovalSafetyLimits(*args, **kwargs):
        kwargs["decision_ttl_ms"] = APPROVAL_DECISION_TTL_MS
        return original_safety_limits(*args, **kwargs)

    preview.CryptoOperatorDecisionContext = _CaptureContext
    preview.SafetyLimits = _ApprovalSafetyLimits
    preview.STRATEGY_ID = APPROVAL_STRATEGY_ID
    try:
        result = preview.run(
            workspace_path=workspace_path,
            credentials=credentials,
            now=now.astimezone(timezone.utc),
            symbol=symbol,
        )
    finally:
        preview.CryptoOperatorDecisionContext = original_context_type
        preview.SafetyLimits = original_safety_limits
        preview.STRATEGY_ID = original_strategy_id

    context = captured.get("context")
    if context is None:
        raise CryptoPaperApprovalPrepareError("approval rehearsal did not capture exact operator context")
    challenge = crypto_operator_confirmation_challenge(context)

    operator = result.get("operator")
    if not isinstance(operator, dict):
        raise CryptoPaperApprovalPrepareError("approval rehearsal operator payload is missing")
    operator.update(
        {
            "dry_run_attempt_id": context.attempt_id,
            "approval_attempt_id": context.attempt_id,
            "approval_challenge": challenge,
            "approval_context": context.to_dict(),
            "approval_recorded": False,
            "decision_consumed": False,
            "uat_only": True,
            "reusable_for_real_execution": False,
            "decision_ttl_ms": APPROVAL_DECISION_TTL_MS,
            "note": (
                "This exact context may only be used to rehearse issuance of human approval. "
                "The lifecycle runtime used to prepare it is destroyed before any execution surface exists."
            ),
        }
    )
    result.update(
        {
            "status": "CRYPTO_PAPER_ONE_SHOT_APPROVAL_PREPARED",
            "mode": "ONE_SHOT_APPROVAL_REHEARSAL_NO_POST",
            "operator_approval_authority": "PREPARED_NOT_RECORDED",
            "broker_write_performed": False,
            "external_post_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
            "profitability_claim": False,
            "next_action": "TYPE_EXACT_CHALLENGE_TO_RECORD_UAT_ONLY_APPROVAL",
        }
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_crypto_read:
        raise SystemExit("approval preparation requires explicit --allow-paper-crypto-read")
    try:
        result = run(
            workspace_path=args.workspace,
            credentials=preview._credentials(),
            now=datetime.now(timezone.utc),
            symbol=args.symbol,
        )
    except Exception as exc:
        blocked = {
            "status": "CRYPTO_PAPER_ONE_SHOT_APPROVAL_PREPARE_BLOCKED",
            "mode": "ONE_SHOT_APPROVAL_REHEARSAL_NO_POST",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "broker_write_performed": False,
            "external_post_authorized": False,
            "operator_approval_authority": "NONE",
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        print(json.dumps(blocked, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
