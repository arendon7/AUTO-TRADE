from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping

from autotrade.domain import (
    MarketSnapshot,
    RiskDecision,
    RiskDecisionStatus,
    market_fingerprint,
)
from autotrade.health_bridge import HealthBridgePolicy

from .alpaca_paper_canary import PaperCanaryApproval
from .alpaca_paper_canary_coordinator import PreparedPaperCanaryPackage
from .alpaca_paper_operational import PaperOperationalIntegrityError, PaperOperationalWorkspace


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class PaperOperationalRuntimeBinding:
    portfolio_health_entity_id: str
    health_bridge_policy: HealthBridgePolicy

    def __post_init__(self) -> None:
        if (
            not isinstance(self.portfolio_health_entity_id, str)
            or not self.portfolio_health_entity_id
            or self.portfolio_health_entity_id != self.portfolio_health_entity_id.strip()
        ):
            raise ValueError("portfolio_health_entity_id must be canonical non-empty text")
        if not isinstance(self.health_bridge_policy, HealthBridgePolicy):
            raise TypeError("health_bridge_policy must be HealthBridgePolicy")

    @property
    def fingerprint(self) -> str:
        return _hash_json(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        policy = self.health_bridge_policy
        return {
            "portfolio_health_entity_id": self.portfolio_health_entity_id,
            "health_bridge_policy": {
                "degraded_risk_multiplier": str(policy.degraded_risk_multiplier),
                "max_state_age_seconds": policy.max_state_age_seconds,
                "require_strategy_state": policy.require_strategy_state,
                "require_portfolio_state": policy.require_portfolio_state,
                "policy_fingerprint": policy.fingerprint,
            },
        }


@dataclass(frozen=True, slots=True)
class PaperOperationalExecutionContext:
    package_hash: str
    risk_decision: RiskDecision
    market: MarketSnapshot
    approval: PaperCanaryApproval
    runtime_binding: PaperOperationalRuntimeBinding
    context_hash: str

    def __post_init__(self) -> None:
        if not _HASH_RE.fullmatch(self.package_hash):
            raise ValueError("package_hash must be lowercase SHA-256")
        if not isinstance(self.risk_decision, RiskDecision):
            raise TypeError("risk_decision must be RiskDecision")
        if not isinstance(self.market, MarketSnapshot):
            raise TypeError("market must be MarketSnapshot")
        if not isinstance(self.approval, PaperCanaryApproval):
            raise TypeError("approval must be PaperCanaryApproval")
        if not isinstance(self.runtime_binding, PaperOperationalRuntimeBinding):
            raise TypeError("runtime_binding must be PaperOperationalRuntimeBinding")
        if not _HASH_RE.fullmatch(self.context_hash):
            raise ValueError("context_hash must be lowercase SHA-256")
        if self.context_hash != _hash_json(self.payload_without_hash()):
            raise ValueError("operational execution context hash mismatch")

    @classmethod
    def build(
        cls,
        *,
        package: PreparedPaperCanaryPackage,
        risk_decision: RiskDecision,
        market: MarketSnapshot,
        approval: PaperCanaryApproval,
        runtime_binding: PaperOperationalRuntimeBinding,
    ) -> "PaperOperationalExecutionContext":
        if not isinstance(package, PreparedPaperCanaryPackage):
            raise TypeError("prepared package is required")
        if risk_decision.decision_id != package.risk_decision_id:
            raise PaperOperationalIntegrityError("RiskDecision id differs from prepared package")
        if risk_decision.intent_fingerprint != package.intent_fingerprint:
            raise PaperOperationalIntegrityError("RiskDecision intent fingerprint differs from package")
        if risk_decision.market_fingerprint != package.market_fingerprint:
            raise PaperOperationalIntegrityError("RiskDecision market fingerprint differs from package")
        if risk_decision.safety_state_version != package.risk_decision_safety_state_version:
            raise PaperOperationalIntegrityError("RiskDecision Safety version differs from package")
        if risk_decision.valid_until != package.risk_decision_valid_until:
            raise PaperOperationalIntegrityError("RiskDecision expiry differs from prepared package")
        if market_fingerprint(market) != package.market_fingerprint:
            raise PaperOperationalIntegrityError("MarketSnapshot differs from prepared package")
        if approval.approval_hash != package.canary_approval_hash:
            raise PaperOperationalIntegrityError("canary approval differs from prepared package")
        if approval.order_id != package.order_id or approval.client_order_id != package.client_order_id:
            raise PaperOperationalIntegrityError("canary approval order identity differs from package")
        if approval.binding_hash != package.submission_binding_hash:
            raise PaperOperationalIntegrityError("canary approval binding differs from package")
        if approval.account_attestation_fingerprint != package.account_attestation_fingerprint:
            raise PaperOperationalIntegrityError("canary approval account differs from package")
        if approval.notional != package.notional or approval.effective_notional_cap != package.effective_notional_cap:
            raise PaperOperationalIntegrityError("canary approval notional differs from package")
        if approval.issued_at != package.approval_issued_at or approval.expires_at != package.approval_expires_at:
            raise PaperOperationalIntegrityError("canary approval validity window differs from package")

        provisional = cls(
            package_hash=package.package_hash,
            risk_decision=risk_decision,
            market=market,
            approval=approval,
            runtime_binding=runtime_binding,
            context_hash="0" * 64,
        )
        return cls(
            package_hash=package.package_hash,
            risk_decision=risk_decision,
            market=market,
            approval=approval,
            runtime_binding=runtime_binding,
            context_hash=_hash_json(provisional.payload_without_hash()),
        )

    def payload_without_hash(self) -> dict[str, object]:
        decision = self.risk_decision
        market = self.market
        approval = self.approval
        return {
            "schema_version": 1,
            "environment": "PAPER",
            "package_hash": self.package_hash,
            "risk_decision": {
                "decision_id": decision.decision_id,
                "intent_id": decision.intent_id,
                "status": decision.status.value,
                "reason_code": decision.reason_code,
                "reason_detail": decision.reason_detail,
                "evaluated_at": decision.evaluated_at.isoformat(),
                "valid_until": decision.valid_until.isoformat(),
                "limits_version": decision.limits_version,
                "intent_fingerprint": decision.intent_fingerprint,
                "market_fingerprint": decision.market_fingerprint,
                "approved_notional": str(decision.approved_notional) if decision.approved_notional is not None else None,
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
            "runtime_binding": self.runtime_binding.to_dict(),
            "credentials_persisted": False,
            "network_write_authorized": False,
            "live_trading": "BLOCKED",
        }

    def to_dict(self) -> dict[str, object]:
        payload = self.payload_without_hash()
        payload["context_hash"] = self.context_hash
        return payload


def execution_context_path(workspace: PaperOperationalWorkspace) -> Path:
    if not isinstance(workspace, PaperOperationalWorkspace):
        raise TypeError("operational workspace is required")
    return workspace.root / "execution_context.json"


def write_execution_context(
    workspace: PaperOperationalWorkspace,
    context: PaperOperationalExecutionContext,
) -> Path:
    if not isinstance(context, PaperOperationalExecutionContext):
        raise TypeError("operational execution context is required")
    path = execution_context_path(workspace)
    _write_json_idempotent(path, context.to_dict())
    return path


def read_execution_context(
    workspace: PaperOperationalWorkspace,
    *,
    package: PreparedPaperCanaryPackage,
) -> PaperOperationalExecutionContext:
    raw = _read_json_object(execution_context_path(workspace))
    if raw.get("schema_version") != 1 or raw.get("environment") != "PAPER":
        raise PaperOperationalIntegrityError("operational execution context header is invalid")
    if raw.get("credentials_persisted") is not False:
        raise PaperOperationalIntegrityError("operational execution context cannot persist credentials")
    if raw.get("network_write_authorized") is not False or raw.get("live_trading") != "BLOCKED":
        raise PaperOperationalIntegrityError("operational execution context authority changed")
    if raw.get("package_hash") != package.package_hash:
        raise PaperOperationalIntegrityError("operational execution context package hash mismatch")

    decision_raw = _mapping(raw, "risk_decision")
    market_raw = _mapping(raw, "market")
    approval_raw = _mapping(raw, "approval")
    runtime_raw = _mapping(raw, "runtime_binding")
    policy_raw = _mapping(runtime_raw, "health_bridge_policy")
    try:
        policy = HealthBridgePolicy(
            degraded_risk_multiplier=_decimal(policy_raw.get("degraded_risk_multiplier"), "degraded_risk_multiplier"),
            max_state_age_seconds=_integer(policy_raw.get("max_state_age_seconds"), "max_state_age_seconds"),
            require_strategy_state=_boolean(policy_raw.get("require_strategy_state"), "require_strategy_state"),
            require_portfolio_state=_boolean(policy_raw.get("require_portfolio_state"), "require_portfolio_state"),
        )
        if policy_raw.get("policy_fingerprint") != policy.fingerprint:
            raise PaperOperationalIntegrityError("Health bridge policy fingerprint mismatch")
        runtime_binding = PaperOperationalRuntimeBinding(
            portfolio_health_entity_id=_string(runtime_raw.get("portfolio_health_entity_id"), "portfolio_health_entity_id"),
            health_bridge_policy=policy,
        )
        decision = RiskDecision(
            decision_id=_string(decision_raw.get("decision_id"), "decision_id"),
            intent_id=_string(decision_raw.get("intent_id"), "intent_id"),
            status=RiskDecisionStatus(_string(decision_raw.get("status"), "status")),
            reason_code=_string(decision_raw.get("reason_code"), "reason_code"),
            reason_detail=_string(decision_raw.get("reason_detail"), "reason_detail"),
            evaluated_at=_datetime(decision_raw.get("evaluated_at"), "evaluated_at"),
            valid_until=_datetime(decision_raw.get("valid_until"), "valid_until"),
            limits_version=_string(decision_raw.get("limits_version"), "limits_version"),
            intent_fingerprint=_string(decision_raw.get("intent_fingerprint"), "intent_fingerprint"),
            market_fingerprint=_string(decision_raw.get("market_fingerprint"), "market_fingerprint"),
            approved_notional=(
                None
                if decision_raw.get("approved_notional") is None
                else _decimal(decision_raw.get("approved_notional"), "approved_notional")
            ),
            risk_reducing=_boolean(decision_raw.get("risk_reducing"), "risk_reducing"),
            safety_state_version=_integer(decision_raw.get("safety_state_version"), "safety_state_version"),
        )
        market = MarketSnapshot(
            symbol=_string(market_raw.get("symbol"), "symbol"),
            bid=_decimal(market_raw.get("bid"), "bid"),
            ask=_decimal(market_raw.get("ask"), "ask"),
            last=_decimal(market_raw.get("last"), "last"),
            observed_at=_datetime(market_raw.get("observed_at"), "observed_at"),
        )
        if market_raw.get("market_fingerprint") != market_fingerprint(market):
            raise PaperOperationalIntegrityError("persisted MarketSnapshot fingerprint mismatch")
        approval = PaperCanaryApproval(
            order_id=_string(approval_raw.get("order_id"), "order_id"),
            client_order_id=_string(approval_raw.get("client_order_id"), "client_order_id"),
            binding_hash=_string(approval_raw.get("binding_hash"), "binding_hash"),
            account_attestation_fingerprint=_string(
                approval_raw.get("account_attestation_fingerprint"), "account_attestation_fingerprint"
            ),
            risk_decision_id=_string(approval_raw.get("risk_decision_id"), "risk_decision_id"),
            notional=_decimal(approval_raw.get("notional"), "notional"),
            effective_notional_cap=_decimal(
                approval_raw.get("effective_notional_cap"), "effective_notional_cap"
            ),
            issued_at=_datetime(approval_raw.get("issued_at"), "issued_at"),
            expires_at=_datetime(approval_raw.get("expires_at"), "expires_at"),
            approval_hash=_string(approval_raw.get("approval_hash"), "approval_hash"),
        )
        context = PaperOperationalExecutionContext(
            package_hash=_string(raw.get("package_hash"), "package_hash"),
            risk_decision=decision,
            market=market,
            approval=approval,
            runtime_binding=runtime_binding,
            context_hash=_string(raw.get("context_hash"), "context_hash"),
        )
    except (TypeError, ValueError, InvalidOperation) as exc:
        if isinstance(exc, PaperOperationalIntegrityError):
            raise
        raise PaperOperationalIntegrityError("operational execution context is invalid") from exc

    rebuilt = PaperOperationalExecutionContext.build(
        package=package,
        risk_decision=context.risk_decision,
        market=context.market,
        approval=context.approval,
        runtime_binding=context.runtime_binding,
    )
    if rebuilt != context or context.to_dict() != raw:
        raise PaperOperationalIntegrityError("operational execution context is not canonical")
    return context


def read_account_attestation(
    workspace: PaperOperationalWorkspace,
) -> object:
    from .alpaca_paper_gateway import AlpacaPaperAccountAttestation

    raw = _read_json_object(workspace.account_attestation_path)
    if raw.get("schema_version") != 1 or raw.get("environment") != "PAPER":
        raise PaperOperationalIntegrityError("account attestation artifact header is invalid")
    if raw.get("credentials_persisted") is not False or raw.get("live_trading") != "BLOCKED":
        raise PaperOperationalIntegrityError("account attestation artifact authority changed")
    try:
        attestation = AlpacaPaperAccountAttestation(
            account_id=_string(raw.get("account_id"), "account_id"),
            account_reference=_string(raw.get("account_reference"), "account_reference"),
            credential_reference=_string(raw.get("credential_reference"), "credential_reference"),
            status=_string(raw.get("status"), "status"),
            currency=_string(raw.get("currency"), "currency"),
            buying_power=_decimal(raw.get("buying_power"), "buying_power"),
            portfolio_value=_decimal(raw.get("portfolio_value"), "portfolio_value"),
            shorting_enabled=_boolean(raw.get("shorting_enabled"), "shorting_enabled"),
            attested_at=_datetime(raw.get("attested_at"), "attested_at"),
            request_id=_string(raw.get("request_id"), "request_id"),
            source_host=_string(raw.get("source_host"), "source_host"),
            source_path=_string(raw.get("source_path"), "source_path"),
        )
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise PaperOperationalIntegrityError("account attestation artifact is invalid") from exc
    if raw.get("attestation_fingerprint") != attestation.fingerprint:
        raise PaperOperationalIntegrityError("account attestation fingerprint mismatch")
    return attestation


def _write_json_idempotent(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists() and path.is_symlink():
        raise PaperOperationalIntegrityError("operational context path cannot be symlink")
    raw = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False, ensure_ascii=True) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != raw:
            raise PaperOperationalIntegrityError("refusing to overwrite different operational execution context")
        return
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.close(fd)
        temp_path.write_bytes(raw)
        sync_fd = os.open(temp_path, os.O_RDONLY)
        try:
            os.fsync(sync_fd)
        finally:
            os.close(sync_fd)
        temp_path.chmod(0o600)
        os.replace(temp_path, path)
        path.chmod(0o600)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _read_json_object(path: Path) -> dict[str, object]:
    if path.is_symlink():
        raise PaperOperationalIntegrityError("operational context path cannot be symlink")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperOperationalIntegrityError("cannot read operational context JSON") from exc
    if not isinstance(raw, dict):
        raise PaperOperationalIntegrityError("operational context root must be object")
    return raw


def _mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise PaperOperationalIntegrityError(f"{key} must be object")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be string")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be bool")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be integer")
    return value


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise ValueError(f"{label} must be finite")
    return parsed


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be datetime string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed


def _hash_json(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode("utf-8")).hexdigest()
