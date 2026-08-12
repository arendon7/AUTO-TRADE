from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

from autotrade.domain import (
    MarketSnapshot,
    RiskDecision,
    RiskDecisionStatus,
    market_fingerprint,
    risk_decision_fingerprint,
)

from .alpaca_paper_canary import PaperCanaryApproval
from .alpaca_paper_canary_coordinator import PreparedPaperCanaryPackage
from .alpaca_paper_operational import PaperOperationalIntegrityError, PaperOperationalWorkspace


SNAPSHOT_NAME = "preparation_snapshot.json"


def snapshot_path(workspace: PaperOperationalWorkspace) -> Path:
    if not isinstance(workspace, PaperOperationalWorkspace):
        raise TypeError("operational workspace is required")
    return workspace.root / SNAPSHOT_NAME


def preparation_snapshot_payload(
    *,
    package: PreparedPaperCanaryPackage,
    decision: RiskDecision,
    market: MarketSnapshot,
    approval: PaperCanaryApproval,
) -> dict[str, object]:
    _verify_bindings(package=package, decision=decision, market=market, approval=approval)
    payload = {
        "schema_version": 1,
        "environment": "PAPER",
        "package_hash": package.package_hash,
        "risk_decision": {
            "decision_id": decision.decision_id,
            "risk_decision_fingerprint": risk_decision_fingerprint(decision),
            "intent_id": decision.intent_id,
            "status": decision.status.value,
            "reason_code": decision.reason_code,
            "reason_detail": decision.reason_detail,
            "evaluated_at": decision.evaluated_at.isoformat(),
            "valid_until": decision.valid_until.isoformat(),
            "limits_version": decision.limits_version,
            "intent_fingerprint": decision.intent_fingerprint,
            "market_fingerprint": decision.market_fingerprint,
            "approved_notional": (
                str(decision.approved_notional) if decision.approved_notional is not None else None
            ),
            "risk_reducing": decision.risk_reducing,
            "safety_state_version": decision.safety_state_version,
        },
        "market": {
            "symbol": market.symbol,
            "bid": str(market.bid),
            "ask": str(market.ask),
            "last": str(market.last),
            "observed_at": market.observed_at.isoformat(),
            "market_fingerprint": market_fingerprint(market),
        },
        "approval": {
            "order_id": approval.order_id,
            "client_order_id": approval.client_order_id,
            "binding_hash": approval.binding_hash,
            "account_attestation_fingerprint": approval.account_attestation_fingerprint,
            "risk_decision_id": approval.risk_decision_id,
            "notional": str(approval.notional),
            "effective_notional_cap": str(approval.effective_notional_cap),
            "issued_at": approval.issued_at.isoformat(),
            "expires_at": approval.expires_at.isoformat(),
            "approval_hash": approval.approval_hash,
        },
        "credentials_persisted": False,
        "network_write_authorized": False,
        "next_action": "OPERATOR_DECISION_REQUIRED",
        "live_trading": "BLOCKED",
    }
    payload["snapshot_hash"] = _hash_payload(payload)
    return payload


def write_preparation_snapshot(
    workspace: PaperOperationalWorkspace,
    *,
    package: PreparedPaperCanaryPackage,
    decision: RiskDecision,
    market: MarketSnapshot,
    approval: PaperCanaryApproval,
) -> Path:
    payload = preparation_snapshot_payload(
        package=package,
        decision=decision,
        market=market,
        approval=approval,
    )
    path = snapshot_path(workspace)
    raw = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if path.exists() and path.is_symlink():
        raise PaperOperationalIntegrityError("preparation snapshot path cannot be symlink")
    if path.exists():
        if path.read_bytes() != raw:
            raise PaperOperationalIntegrityError("refusing to overwrite different preparation snapshot")
        return path
    fd, temp_name = tempfile.mkstemp(prefix=f".{SNAPSHOT_NAME}.", suffix=".tmp", dir=workspace.root)
    temp = Path(temp_name)
    try:
        os.close(fd)
        temp.write_bytes(raw)
        sync_fd = os.open(temp, os.O_RDONLY)
        try:
            os.fsync(sync_fd)
        finally:
            os.close(sync_fd)
        temp.chmod(0o600)
        os.replace(temp, path)
        path.chmod(0o600)
    finally:
        if temp.exists():
            temp.unlink()
    return path


def read_preparation_snapshot(
    workspace: PaperOperationalWorkspace,
    *,
    package: PreparedPaperCanaryPackage,
) -> tuple[RiskDecision, MarketSnapshot, PaperCanaryApproval]:
    path = snapshot_path(workspace)
    if path.is_symlink():
        raise PaperOperationalIntegrityError("preparation snapshot path cannot be symlink")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperOperationalIntegrityError("cannot read preparation snapshot") from exc
    if not isinstance(raw, dict):
        raise PaperOperationalIntegrityError("preparation snapshot root must be object")
    if raw.get("schema_version") != 1 or raw.get("environment") != "PAPER":
        raise PaperOperationalIntegrityError("preparation snapshot header is invalid")
    if raw.get("package_hash") != package.package_hash:
        raise PaperOperationalIntegrityError("preparation snapshot package mismatch")
    if raw.get("credentials_persisted") is not False:
        raise PaperOperationalIntegrityError("preparation snapshot cannot persist credentials")
    if raw.get("network_write_authorized") is not False:
        raise PaperOperationalIntegrityError("preparation snapshot cannot authorize network writes")
    if raw.get("next_action") != "OPERATOR_DECISION_REQUIRED":
        raise PaperOperationalIntegrityError("preparation snapshot action changed")
    if raw.get("live_trading") != "BLOCKED":
        raise PaperOperationalIntegrityError("preparation snapshot cannot unblock LIVE")
    snapshot_hash = raw.get("snapshot_hash")
    if not isinstance(snapshot_hash, str):
        raise PaperOperationalIntegrityError("preparation snapshot hash is missing")
    without_hash = dict(raw)
    without_hash.pop("snapshot_hash", None)
    if snapshot_hash != _hash_payload(without_hash):
        raise PaperOperationalIntegrityError("preparation snapshot hash mismatch")

    decision_raw = _mapping(raw, "risk_decision")
    market_raw = _mapping(raw, "market")
    approval_raw = _mapping(raw, "approval")
    try:
        decision = RiskDecision(
            decision_id=_string(decision_raw, "decision_id"),
            intent_id=_string(decision_raw, "intent_id"),
            status=RiskDecisionStatus(_string(decision_raw, "status")),
            reason_code=_string(decision_raw, "reason_code"),
            reason_detail=_string(decision_raw, "reason_detail"),
            evaluated_at=_datetime(decision_raw, "evaluated_at"),
            valid_until=_datetime(decision_raw, "valid_until"),
            limits_version=_string(decision_raw, "limits_version"),
            intent_fingerprint=_string(decision_raw, "intent_fingerprint"),
            market_fingerprint=_string(decision_raw, "market_fingerprint"),
            approved_notional=(
                None
                if decision_raw.get("approved_notional") is None
                else _decimal(decision_raw, "approved_notional")
            ),
            risk_reducing=_boolean(decision_raw, "risk_reducing"),
            safety_state_version=_integer(decision_raw, "safety_state_version"),
        )
        if decision_raw.get("risk_decision_fingerprint") != risk_decision_fingerprint(decision):
            raise PaperOperationalIntegrityError("snapshot RiskDecision fingerprint mismatch")
        market = MarketSnapshot(
            symbol=_string(market_raw, "symbol"),
            bid=_decimal(market_raw, "bid"),
            ask=_decimal(market_raw, "ask"),
            last=_decimal(market_raw, "last"),
            observed_at=_datetime(market_raw, "observed_at"),
        )
        if market_raw.get("market_fingerprint") != market_fingerprint(market):
            raise PaperOperationalIntegrityError("snapshot MarketSnapshot fingerprint mismatch")
        approval = PaperCanaryApproval(
            order_id=_string(approval_raw, "order_id"),
            client_order_id=_string(approval_raw, "client_order_id"),
            binding_hash=_string(approval_raw, "binding_hash"),
            account_attestation_fingerprint=_string(
                approval_raw, "account_attestation_fingerprint"
            ),
            risk_decision_id=_string(approval_raw, "risk_decision_id"),
            notional=_decimal(approval_raw, "notional"),
            effective_notional_cap=_decimal(approval_raw, "effective_notional_cap"),
            issued_at=_datetime(approval_raw, "issued_at"),
            expires_at=_datetime(approval_raw, "expires_at"),
            approval_hash=_string(approval_raw, "approval_hash"),
        )
    except (TypeError, ValueError, InvalidOperation) as exc:
        if isinstance(exc, PaperOperationalIntegrityError):
            raise
        raise PaperOperationalIntegrityError("preparation snapshot is invalid") from exc
    canonical = preparation_snapshot_payload(
        package=package,
        decision=decision,
        market=market,
        approval=approval,
    )
    if canonical != raw:
        raise PaperOperationalIntegrityError("preparation snapshot is not canonical")
    return decision, market, approval


def _verify_bindings(
    *,
    package: PreparedPaperCanaryPackage,
    decision: RiskDecision,
    market: MarketSnapshot,
    approval: PaperCanaryApproval,
) -> None:
    if decision.decision_id != package.risk_decision_id:
        raise PaperOperationalIntegrityError("RiskDecision id differs from package")
    if decision.intent_fingerprint != package.intent_fingerprint:
        raise PaperOperationalIntegrityError("RiskDecision intent differs from package")
    if decision.market_fingerprint != package.market_fingerprint:
        raise PaperOperationalIntegrityError("RiskDecision market differs from package")
    if decision.safety_state_version != package.risk_decision_safety_state_version:
        raise PaperOperationalIntegrityError("RiskDecision Safety version differs from package")
    if decision.valid_until != package.risk_decision_valid_until:
        raise PaperOperationalIntegrityError("RiskDecision expiry differs from package")
    if risk_decision_fingerprint(decision) != package.risk_decision_fingerprint:
        raise PaperOperationalIntegrityError("RiskDecision fingerprint differs from package")
    if market_fingerprint(market) != package.market_fingerprint:
        raise PaperOperationalIntegrityError("MarketSnapshot differs from package")
    if approval.approval_hash != package.canary_approval_hash:
        raise PaperOperationalIntegrityError("canary approval differs from package")
    if approval.order_id != package.order_id or approval.client_order_id != package.client_order_id:
        raise PaperOperationalIntegrityError("canary approval order identity differs from package")
    if approval.binding_hash != package.submission_binding_hash:
        raise PaperOperationalIntegrityError("canary approval binding differs from package")
    if approval.account_attestation_fingerprint != package.account_attestation_fingerprint:
        raise PaperOperationalIntegrityError("canary approval account differs from package")
    if approval.risk_decision_id != package.risk_decision_id:
        raise PaperOperationalIntegrityError("canary approval RiskDecision differs from package")
    if approval.notional != package.notional or approval.effective_notional_cap != package.effective_notional_cap:
        raise PaperOperationalIntegrityError("canary approval notional differs from package")
    if approval.issued_at != package.approval_issued_at or approval.expires_at != package.approval_expires_at:
        raise PaperOperationalIntegrityError("canary approval validity window differs from package")


def _mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise PaperOperationalIntegrityError(f"{key} must be object")
    return value


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be string")
    return value


def _boolean(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be bool")
    return value


def _integer(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be integer")
    return value


def _decimal(payload: Mapping[str, object], key: str) -> Decimal:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise ValueError(f"{key} must be finite")
    return parsed


def _datetime(payload: Mapping[str, object], key: str) -> datetime:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be datetime string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{key} must be timezone-aware")
    return parsed


def _hash_payload(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode("utf-8")).hexdigest()
