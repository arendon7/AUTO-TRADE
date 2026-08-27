from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Mapping

from autotrade.domain import RiskDecisionStatus
from autotrade.paper_execution_risk_contract import (
    PAPER_EXECUTION_RISK_CONTRACT_VERSION,
    PaperExecutionRiskContractBlocked,
    PaperExecutionRiskContractIntegrityError,
    PaperExecutionRiskContractReceipt,
    PaperExecutionRiskContractResult,
)
from autotrade.paper_runtime_readiness_seal import (
    PaperRuntimeReadinessSealedResult,
    PaperRuntimeReadinessSealStatus,
)


PAPER_EXECUTION_RISK_HANDOFF_VERSION = "W87_PAPER_EXECUTION_RISK_HANDOFF_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$")


class PaperExecutionRiskHandoffError(RuntimeError):
    pass


class PaperExecutionRiskHandoffIntegrityError(PaperExecutionRiskHandoffError):
    pass


class PaperExecutionRiskHandoffBlocked(PaperExecutionRiskHandoffError):
    pass


@dataclass(frozen=True, slots=True)
class PaperExecutionRiskHandoffReceipt(PaperExecutionRiskContractReceipt):
    handoff_id: str
    source_risk_contract_hash: str
    handoff_latched_at: datetime
    seal_fresh_at_handoff: bool
    risk_decision_window_retained: bool

    def __post_init__(self) -> None:
        _id(self.handoff_id, "handoff_id")
        _sha(self.source_risk_contract_hash, "source_risk_contract_hash")
        _aware(self.handoff_latched_at, "handoff_latched_at")
        if self.contract_version != PAPER_EXECUTION_RISK_HANDOFF_VERSION:
            raise PaperExecutionRiskHandoffIntegrityError(
                "W87 risk handoff version is not canonical"
            )

        source_values: dict[str, object] = {}
        for field in fields(PaperExecutionRiskContractReceipt):
            if field.name == "receipt_hash":
                continue
            value = getattr(self, field.name)
            if field.name == "contract_version":
                value = PAPER_EXECUTION_RISK_CONTRACT_VERSION
            elif field.name == "valid_until":
                value = min(
                    _utc(self.risk_decision_valid_until),
                    _utc(self.readiness_seal_valid_until),
                )
            source_values[field.name] = value
        try:
            source = PaperExecutionRiskContractReceipt(
                **source_values,
                receipt_hash=self.source_risk_contract_hash,
            )
            source.__post_init__()
        except (PaperExecutionRiskContractIntegrityError, TypeError, ValueError) as exc:
            raise PaperExecutionRiskHandoffIntegrityError(
                "W87 risk handoff does not reconstruct the exact source risk contract"
            ) from exc

        latched = _utc(self.handoff_latched_at)
        source_until = _utc(source.valid_until)
        if not _utc(self.evaluated_at) <= latched < source_until:
            raise PaperExecutionRiskHandoffIntegrityError(
                "W87 risk handoff must be latched while the source risk contract is fresh"
            )
        if self.seal_fresh_at_handoff is not True:
            raise PaperExecutionRiskHandoffIntegrityError(
                "W87 risk handoff must attest fresh W86 seal at latch time"
            )
        if self.risk_decision_window_retained is not True:
            raise PaperExecutionRiskHandoffIntegrityError(
                "W87 risk handoff must retain only the original RiskDecision window"
            )
        if _utc(self.valid_until) != _utc(self.risk_decision_valid_until):
            raise PaperExecutionRiskHandoffIntegrityError(
                "W87 risk handoff may not outlive or shorten the original RiskDecision"
            )
        if not latched < _utc(self.valid_until):
            raise PaperExecutionRiskHandoffIntegrityError(
                "W87 risk handoff has no positive RiskDecision window"
            )
        if (
            self.oms_handoff_permitted is not False
            or self.capital_reserved is not False
            or self.broker_write_performed is not False
            or self.paper_execution_authorized is not False
            or self.external_execution_authorized is not False
            or self.runtime_execution_authorized is not False
            or self.capital_authority != "NONE"
            or self.live_trading != "BLOCKED"
        ):
            raise PaperExecutionRiskHandoffIntegrityError(
                "W87 risk handoff cannot create OMS, capital, execution or LIVE authority"
            )
        if self.receipt_hash != _hash(_payload(self, include_hash=False)):
            raise PaperExecutionRiskHandoffIntegrityError(
                "W87 risk handoff receipt hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def latch_paper_execution_risk_handoff(
    *,
    handoff_id: str,
    risk_result: PaperExecutionRiskContractResult,
    sealed_result: PaperRuntimeReadinessSealedResult,
) -> PaperExecutionRiskContractResult:
    """Latch a fresh W87-B proof into a RiskDecision-bounded preparation handoff.

    This function creates no new Safety approval and grants no OMS, capital,
    operator, writer, POST or LIVE authority. The W86 seal and exact W87-B
    contract must both be fresh at this instant. After latching, only the
    already-issued RiskDecision validity window remains available to W87-C.
    """

    _id(handoff_id, "handoff_id")
    if not isinstance(risk_result, PaperExecutionRiskContractResult):
        raise TypeError("risk_result must be PaperExecutionRiskContractResult")
    if not isinstance(sealed_result, PaperRuntimeReadinessSealedResult):
        raise TypeError("sealed_result must be PaperRuntimeReadinessSealedResult")

    risk_result.__post_init__()
    sealed_result.pipeline.__post_init__()
    sealed_result.seal.__post_init__()
    sealed_result.__post_init__()

    source = risk_result.receipt
    seal = sealed_result.seal
    now = _utc(_now_utc())

    if source.contract_version != PAPER_EXECUTION_RISK_CONTRACT_VERSION:
        raise PaperExecutionRiskHandoffIntegrityError(
            "W87 risk handoff requires the canonical W87-B source contract"
        )
    if source.readiness_seal_hash != seal.receipt_hash:
        raise PaperExecutionRiskHandoffIntegrityError(
            "W87 risk handoff source is not bound to the exact W86 seal"
        )
    if source.pipeline_receipt_hash != sealed_result.pipeline.receipt.receipt_hash:
        raise PaperExecutionRiskHandoffIntegrityError(
            "W87 risk handoff source is not bound to the exact W86 pipeline"
        )
    if (
        seal.status is not PaperRuntimeReadinessSealStatus.READY
        or seal.paper_runtime_ready is not True
    ):
        raise PaperExecutionRiskHandoffBlocked("W86 readiness seal is not READY")
    if not (_utc(seal.observed_at) <= now < _utc(seal.valid_until)):
        raise PaperExecutionRiskHandoffBlocked(
            "W86 readiness seal is stale before W87 risk handoff"
        )
    if not (_utc(source.evaluated_at) <= now < _utc(source.valid_until)):
        raise PaperExecutionRiskHandoffBlocked(
            "W87-B risk contract is stale before W87 risk handoff"
        )
    if risk_result.decision.status is not RiskDecisionStatus.APPROVED:
        raise PaperExecutionRiskHandoffBlocked(
            "W87 risk handoff requires an APPROVED RiskDecision"
        )
    if _utc(risk_result.decision.valid_until) != _utc(source.risk_decision_valid_until):
        raise PaperExecutionRiskHandoffIntegrityError(
            "W87-B receipt and exact RiskDecision validity differ"
        )
    if not now < _utc(risk_result.decision.valid_until):
        raise PaperExecutionRiskHandoffBlocked(
            "RiskDecision expired before W87 risk handoff"
        )

    values: dict[str, object] = {}
    for field in fields(PaperExecutionRiskContractReceipt):
        if field.name == "receipt_hash":
            continue
        values[field.name] = getattr(source, field.name)
    values["contract_version"] = PAPER_EXECUTION_RISK_HANDOFF_VERSION
    values["valid_until"] = source.risk_decision_valid_until
    values.update(
        {
            "handoff_id": handoff_id,
            "source_risk_contract_hash": source.receipt_hash,
            "handoff_latched_at": now,
            "seal_fresh_at_handoff": True,
            "risk_decision_window_retained": True,
        }
    )
    receipt = PaperExecutionRiskHandoffReceipt(
        **values,
        receipt_hash=_hash(_payload_values(values)),
    )
    result = PaperExecutionRiskContractResult(
        receipt=receipt,
        intent=risk_result.intent,
        decision=risk_result.decision,
        market=risk_result.market,
    )
    result.__post_init__()
    return result


def _payload(
    receipt: PaperExecutionRiskHandoffReceipt,
    *,
    include_hash: bool,
) -> dict[str, object]:
    values = {
        field.name: getattr(receipt, field.name)
        for field in fields(PaperExecutionRiskHandoffReceipt)
        if field.name != "receipt_hash"
    }
    payload = _payload_values(values)
    if include_hash:
        payload["receipt_hash"] = receipt.receipt_hash
    return payload


def _payload_values(values: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            result[key] = _utc(value).isoformat()
        elif isinstance(value, Decimal):
            result[key] = str(value)
        elif isinstance(value, StrEnum):
            result[key] = value.value
        else:
            result[key] = value
    return result


def _hash(payload: Mapping[str, object]) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def _id(value: str, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperExecutionRiskHandoffIntegrityError(
            f"{name} must be canonical identifier"
        )


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperExecutionRiskHandoffIntegrityError(
            f"{name} must be lowercase sha256"
        )


def _aware(value: datetime, name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise PaperExecutionRiskHandoffIntegrityError(
            f"{name} must be timezone-aware datetime"
        )


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "PAPER_EXECUTION_RISK_HANDOFF_VERSION",
    "PaperExecutionRiskHandoffBlocked",
    "PaperExecutionRiskHandoffError",
    "PaperExecutionRiskHandoffIntegrityError",
    "PaperExecutionRiskHandoffReceipt",
    "latch_paper_execution_risk_handoff",
]
