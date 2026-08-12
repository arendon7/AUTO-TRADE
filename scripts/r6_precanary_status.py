from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Mapping

from autotrade.brokers.alpaca_paper_market_readiness import (
    PaperReadinessError,
    inspect_market_aware_readiness,
)

_WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
_KEY_ENV = "APCA_API_KEY_ID"
_SECRET_ENV = "APCA_API_SECRET_KEY"

_ARTIFACTS = {
    "candidate": "connectivity_candidate.json",
    "preparation": "connectivity_preparation.json",
    "first_operator_decision": "connectivity_operator_decision.json",
    "review_receipt": "connectivity_operator_review_receipt.json",
    "second_execution_intent": "connectivity_execution_intent.json",
    "execution_review_binding": "connectivity_execution_review_binding.json",
    "execution_freshness_binding": "connectivity_execution_freshness_binding.json",
    "reviewed_final_freshness": "connectivity_review_final_freshness_binding.json",
    "staging": "connectivity_staging.json",
    "post_observation": "connectivity_post_observation.json",
    "post_ambiguity": "connectivity_post_ambiguity.json",
}

_BLOCKED_PHASES = {
    "BLOCKED_INCONSISTENT_STATE",
    "BLOCKED_STALE_ASSET_EVIDENCE",
    "BLOCKED_STALE_FLAT_ACCOUNT_EVIDENCE",
    "BLOCKED_EXISTING_PAPER_EXPOSURE",
}


class PreCanaryStatusError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read one R6 CONNECTIVITY_CANARY workspace without broker I/O or mutation and "
            "report the next safe gate. READY here never means POST-authorized."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    return parser


def _validate_offline_environment() -> None:
    if os.environ.get(_WRITE_ENV) == "ENABLED":
        raise PreCanaryStatusError(
            "pre-canary status refuses R6_EXTERNAL_PAPER_WRITE=ENABLED"
        )
    if os.environ.get(_KEY_ENV) or os.environ.get(_SECRET_ENV):
        raise PreCanaryStatusError(
            "pre-canary status refuses Alpaca credentials; this inspection is local-only"
        )


def _workspace(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink() or not expanded.is_dir():
        raise PreCanaryStatusError(
            "workspace must be an existing non-symlink directory"
        )
    for child in expanded.iterdir():
        if child.is_symlink():
            raise PreCanaryStatusError(
                f"workspace contains forbidden symlink: {child.name}"
            )
    return expanded.resolve()


def _read_json(path: Path) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise PreCanaryStatusError(f"artifact is not a regular file: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreCanaryStatusError(f"cannot read canonical JSON: {path.name}") from exc
    if not isinstance(payload, dict):
        raise PreCanaryStatusError(f"artifact root must be object: {path.name}")
    for key, expected in (
        ("environment", "PAPER"),
        ("purpose", "CONNECTIVITY_CANARY"),
        ("capital_authority", "NONE"),
        ("live_trading", "BLOCKED"),
    ):
        if key in payload and payload.get(key) != expected:
            raise PreCanaryStatusError(f"unsafe {path.name} field: {key}")
    if "external_post_authorized" in payload and payload.get("external_post_authorized") is not False:
        raise PreCanaryStatusError(
            f"unsafe {path.name} field: external_post_authorized"
        )
    return payload


def _canonical(payload: Mapping[str, object]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: Mapping[str, object]) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _inspect_artifacts(root: Path, *, now: datetime) -> tuple[dict[str, bool], bool | None]:
    present = {name: (root / filename).is_file() for name, filename in _ARTIFACTS.items()}

    if present["review_receipt"]:
        receipt = _read_json(root / _ARTIFACTS["review_receipt"])
        receipt_hash = receipt.get("receipt_hash")
        body = dict(receipt)
        body.pop("receipt_hash", None)
        if not isinstance(receipt_hash, str) or receipt_hash != _hash(body):
            raise PreCanaryStatusError("operator review receipt hash mismatch")

    if present["execution_review_binding"]:
        _read_json(root / _ARTIFACTS["execution_review_binding"])
    if present["reviewed_final_freshness"]:
        reviewed = _read_json(root / _ARTIFACTS["reviewed_final_freshness"])
        binding_hash = reviewed.get("binding_hash")
        body = dict(reviewed)
        body.pop("binding_hash", None)
        if not isinstance(binding_hash, str) or binding_hash != _hash(body):
            raise PreCanaryStatusError("reviewed Final Freshness binding hash mismatch")

    freshness_valid: bool | None = None
    if present["execution_freshness_binding"]:
        artifact = _read_json(root / _ARTIFACTS["execution_freshness_binding"])
        binding = artifact.get("binding")
        if not isinstance(binding, dict):
            raise PreCanaryStatusError("execution freshness artifact binding is invalid")
        expires_at = binding.get("expires_at")
        if not isinstance(expires_at, str):
            raise PreCanaryStatusError("execution freshness expires_at is invalid")
        try:
            expiry = datetime.fromisoformat(expires_at)
        except ValueError as exc:
            raise PreCanaryStatusError("execution freshness expires_at is invalid") from exc
        if expiry.tzinfo is None or expiry.utcoffset() is None:
            raise PreCanaryStatusError("execution freshness expires_at must be timezone-aware")
        freshness_valid = now.astimezone(timezone.utc) < expiry.astimezone(timezone.utc)

    return present, freshness_valid


def _classify(
    report: Mapping[str, object],
    artifacts: Mapping[str, bool],
    *,
    freshness_valid: bool | None,
) -> tuple[str, str, str]:
    phase = str(report.get("phase") or "UNKNOWN")
    submission = report.get("submission_status")

    if submission == "UNKNOWN" or phase == "RECONCILIATION_REQUIRED":
        return (
            "RECONCILIATION_ONLY",
            "BROKER_STATE_AMBIGUOUS_OR_UNKNOWN",
            "RUN_SEPARATE_GET_ONLY_RECONCILIATION_AND_EVIDENCE_CAPTURE",
        )
    if phase in _BLOCKED_PHASES:
        return "NOT_READY", phase, str(report.get("next_action") or "STOP_AND_INVESTIGATE")
    if phase in {"EVIDENCE_CAPTURE_REQUIRED", "QUALIFICATION_REVIEW_REQUIRED"}:
        return "POST_ATTEMPT_REVIEW_ONLY", phase, str(report.get("next_action") or "REVIEW_EVIDENCE")

    if not bool(report.get("account_attested")):
        return "NOT_READY", "ACCOUNT_PREFLIGHT_REQUIRED", "RUN_GET_ONLY_ACCOUNT_PREFLIGHT"
    if not bool(report.get("asset_evidence_present")):
        return "NOT_READY", "ASSET_PREFLIGHT_REQUIRED", "RUN_GET_ONLY_ASSET_PREFLIGHT"
    if not bool(report.get("flat_account_evidence_present")):
        return "NOT_READY", "FLAT_ACCOUNT_PREFLIGHT_REQUIRED", "RUN_GET_ONLY_FLAT_ACCOUNT_PREFLIGHT"
    if report.get("flat_account_clean_for_first_canary") is not True:
        return "NOT_READY", "PAPER_ACCOUNT_NOT_PROVEN_FLAT", "STOP_AND_REVIEW_PAPER_EXPOSURE"
    if not bool(report.get("market_evidence_present")):
        return "NOT_READY", "MARKET_DATA_PREFLIGHT_REQUIRED", "RUN_GET_ONLY_IEX_MARKET_PREFLIGHT"

    if not artifacts["candidate"]:
        return "READY_FOR_NEXT_SAFE_GATE", "CONNECTIVITY_CANDIDATE_REQUIRED", "BUILD_LOCAL_CONNECTIVITY_CANDIDATE"
    if not artifacts["preparation"]:
        return "READY_FOR_NEXT_SAFE_GATE", "OFFLINE_PREPARATION_REQUIRED", "RUN_OFFLINE_CONNECTIVITY_PREPARATION"
    if not artifacts["first_operator_decision"]:
        return "READY_FOR_HUMAN_GATE", "FIRST_HUMAN_DECISION_REQUIRED", "RUN_INTERACTIVE_FIRST_HUMAN_DECISION"
    if not artifacts["review_receipt"]:
        return "READY_FOR_NEXT_SAFE_GATE", "REVIEW_RECEIPT_REQUIRED", "FREEZE_OFFLINE_OPERATOR_REVIEW_RECEIPT"

    second_pair = artifacts["second_execution_intent"] and artifacts["execution_review_binding"]
    if artifacts["second_execution_intent"] != artifacts["execution_review_binding"]:
        return "NOT_READY", "INCOMPLETE_SECOND_HUMAN_BINDING", "STOP_AND_INVESTIGATE_LOCAL_ARTIFACT_CHAIN"
    if not second_pair:
        return "READY_FOR_HUMAN_GATE", "SECOND_HUMAN_EXECUTION_INTENT_REQUIRED", "RUN_INTERACTIVE_RECEIPT_BOUND_SECOND_HUMAN_INTENT"

    fresh_pair = artifacts["execution_freshness_binding"] and artifacts["reviewed_final_freshness"]
    if artifacts["execution_freshness_binding"] != artifacts["reviewed_final_freshness"]:
        return "NOT_READY", "INCOMPLETE_REVIEWED_FRESHNESS_BINDING", "STOP_AND_INVESTIGATE_LOCAL_ARTIFACT_CHAIN"
    if not fresh_pair:
        return "READY_FOR_GET_ONLY_GATE", "REVIEWED_FINAL_FRESHNESS_REQUIRED", "RUN_EXPLICIT_FIVE_GET_REVIEWED_FINAL_FRESHNESS"
    if freshness_valid is not True:
        return "NOT_READY", "REVIEWED_FINAL_FRESHNESS_EXPIRED", "CREATE_NEW_ATTEMPT_OR_REPREPARE;_DO_NOT_STAGE_STALE_AUTHORITY"

    if artifacts["staging"] or artifacts["post_observation"] or artifacts["post_ambiguity"]:
        return "RECONCILIATION_ONLY", "POST_BOUNDARY_ARTIFACT_PRESENT", "RUN_SEPARATE_GET_ONLY_RECONCILIATION_AND_EVIDENCE_CAPTURE"

    return (
        "READY_FOR_SEPARATE_CERTIFIED_RUNTIME_REVIEW",
        "REVIEWED_FRESHNESS_PRESENT",
        "STOP_IN_SAFE_CONSOLE;_SEPARATE_EXPLICIT_HUMAN_PROCEDURE_REQUIRED",
    )


def build_status(root: Path, *, now: datetime) -> dict[str, object]:
    report = inspect_market_aware_readiness(root=root, now=now)
    artifacts, freshness_valid = _inspect_artifacts(root, now=now)
    status, blocker_or_stage, next_action = _classify(
        report,
        artifacts,
        freshness_valid=freshness_valid,
    )
    return {
        "status": status,
        "stage_or_blocker": blocker_or_stage,
        "next_safe_action": next_action,
        "workspace": str(root),
        "base_readiness_phase": report.get("phase"),
        "base_readiness_next_action": report.get("next_action"),
        "account_attested": bool(report.get("account_attested")),
        "asset_evidence_present": bool(report.get("asset_evidence_present")),
        "flat_account_evidence_present": bool(report.get("flat_account_evidence_present")),
        "flat_account_clean_for_first_canary": report.get("flat_account_clean_for_first_canary"),
        "market_evidence_present": bool(report.get("market_evidence_present")),
        "artifact_chain": dict(artifacts),
        "reviewed_final_freshness_time_valid": freshness_valid,
        "network_used": False,
        "credentials_used": False,
        "broker_write_performed": False,
        "execution_authorized": False,
        "external_post_authorized": False,
        "external_order_submitted_by_status": False,
        "max_external_post_attempts": 1,
        "capital_authority": "NONE",
        "profitability_claim": False,
        "live_trading": "BLOCKED",
        "ready_semantics": "READY means ready only for the named next gate; never POST authority",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _validate_offline_environment()
        root = _workspace(args.workspace)
        result = build_status(root, now=datetime.now(timezone.utc))
    except (PreCanaryStatusError, PaperReadinessError, OSError, TypeError, ValueError) as exc:
        result = {
            "status": "NOT_READY",
            "stage_or_blocker": "LOCAL_INTEGRITY_OR_READINESS_ERROR",
            "reason": str(exc),
            "network_used": False,
            "credentials_used": False,
            "broker_write_performed": False,
            "execution_authorized": False,
            "external_post_authorized": False,
            "external_order_submitted_by_status": False,
            "capital_authority": "NONE",
            "profitability_claim": False,
            "live_trading": "BLOCKED",
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
