from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Mapping

from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionContext,
    crypto_operator_confirmation_challenge,
)
from autotrade.paper_execution_canary_preparation import (
    PaperExecutionCanaryPreparationResult,
)
from autotrade.paper_execution_risk_contract import (
    PaperExecutionRiskContractResult,
)
from autotrade.paper_execution_risk_handoff import (
    PAPER_EXECUTION_RISK_HANDOFF_VERSION,
    PaperExecutionRiskHandoffReceipt,
)


PAPER_EXECUTION_HUMAN_REVIEW_VERSION = "W87_PAPER_EXECUTION_HUMAN_REVIEW_V1"
MIN_HUMAN_APPROVAL_REMAINING = timedelta(seconds=5)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")


class PaperExecutionHumanReviewError(RuntimeError):
    pass


class PaperExecutionHumanReviewIntegrityError(PaperExecutionHumanReviewError):
    pass


class PaperExecutionHumanReviewBlocked(PaperExecutionHumanReviewError):
    pass


class PaperExecutionHumanReviewStatus(StrEnum):
    REVIEW_PREPARED = "REVIEW_PREPARED"


@dataclass(frozen=True, slots=True)
class PaperExecutionHumanReviewReceipt:
    review_id: str
    contract_version: str
    canary_preparation_hash: str
    risk_handoff_hash: str
    source_risk_contract_hash: str
    package_hash: str
    attempt_id: str
    operator_preparation_hash: str
    account_id: str
    symbol: str
    quantity: Decimal
    limit_price: Decimal
    notional_usd: Decimal
    review_prepared_at: datetime
    package_execution_deadline: datetime
    approval_challenge: str
    status: PaperExecutionHumanReviewStatus
    exact_canary_binding_verified: bool
    exact_risk_handoff_binding_verified: bool
    sufficient_human_window_verified: bool
    human_operator_approval_required: bool
    operator_decision_status: str
    operator_decision_issued: bool
    operator_decision_consumed: bool
    oms_handoff_permitted: bool
    capital_reserved: bool
    broker_write_performed: bool
    external_post_authorized: bool
    paper_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    next_action: str
    receipt_hash: str

    def __post_init__(self) -> None:
        for name in ("review_id", "attempt_id"):
            _id(getattr(self, name), name)
        if self.contract_version != PAPER_EXECUTION_HUMAN_REVIEW_VERSION:
            raise PaperExecutionHumanReviewIntegrityError(
                "W87 human-review version is not canonical"
            )
        for name in (
            "canary_preparation_hash",
            "risk_handoff_hash",
            "source_risk_contract_hash",
            "package_hash",
            "operator_preparation_hash",
            "receipt_hash",
        ):
            _sha(getattr(self, name), name)
        if not isinstance(self.account_id, str) or not self.account_id.strip():
            raise PaperExecutionHumanReviewIntegrityError("account_id is required")
        if (
            not isinstance(self.symbol, str)
            or self.symbol.count("/") != 1
            or self.symbol != self.symbol.upper()
        ):
            raise PaperExecutionHumanReviewIntegrityError(
                "review symbol must be canonical BASE/QUOTE"
            )
        for name in ("quantity", "limit_price", "notional_usd"):
            _positive(getattr(self, name), name)
        if self.notional_usd != self.quantity * self.limit_price:
            raise PaperExecutionHumanReviewIntegrityError(
                "review notional must equal exact quantity * limit_price"
            )
        _aware(self.review_prepared_at, "review_prepared_at")
        _aware(self.package_execution_deadline, "package_execution_deadline")
        remaining = _utc(self.package_execution_deadline) - _utc(self.review_prepared_at)
        if remaining < MIN_HUMAN_APPROVAL_REMAINING:
            raise PaperExecutionHumanReviewIntegrityError(
                "review receipt does not retain the minimum human approval window"
            )
        expected_challenge = (
            f"APPROVE CRYPTO PAPER {self.symbol} "
            f"{self.operator_preparation_hash[:12]}"
        )
        if self.approval_challenge != expected_challenge:
            raise PaperExecutionHumanReviewIntegrityError(
                "human-review challenge does not match exact operator context"
            )
        if self.status is not PaperExecutionHumanReviewStatus.REVIEW_PREPARED:
            raise PaperExecutionHumanReviewIntegrityError(
                "W87 human review must remain REVIEW_PREPARED"
            )
        if (
            self.exact_canary_binding_verified is not True
            or self.exact_risk_handoff_binding_verified is not True
            or self.sufficient_human_window_verified is not True
            or self.human_operator_approval_required is not True
            or self.operator_decision_status != "NOT_ISSUED"
            or self.operator_decision_issued is not False
            or self.operator_decision_consumed is not False
            or self.oms_handoff_permitted is not False
            or self.capital_reserved is not False
            or self.broker_write_performed is not False
            or self.external_post_authorized is not False
            or self.paper_execution_authorized is not False
            or self.runtime_execution_authorized is not False
            or self.capital_authority != "NONE"
            or self.live_trading != "BLOCKED"
            or self.next_action != "HUMAN_OPERATOR_APPROVAL_REQUIRED"
        ):
            raise PaperExecutionHumanReviewIntegrityError(
                "W87 human review may prepare a challenge only; approval, OMS, "
                "capital, POST and LIVE authority remain unavailable"
            )
        if self.receipt_hash != _hash(_payload(self, include_hash=False)):
            raise PaperExecutionHumanReviewIntegrityError(
                "W87 human-review receipt hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PaperExecutionHumanReviewResult:
    receipt: PaperExecutionHumanReviewReceipt
    operator_context: CryptoOperatorDecisionContext

    def __post_init__(self) -> None:
        self.receipt.__post_init__()
        self.operator_context.__post_init__()
        if self.operator_context.prepared_package_hash != self.receipt.package_hash:
            raise PaperExecutionHumanReviewIntegrityError(
                "operator context is bound to another prepared package"
            )
        if self.operator_context.attempt_id != self.receipt.attempt_id:
            raise PaperExecutionHumanReviewIntegrityError(
                "operator context attempt differs from review receipt"
            )
        if (
            self.operator_context.preparation_hash
            != self.receipt.operator_preparation_hash
        ):
            raise PaperExecutionHumanReviewIntegrityError(
                "operator context preparation hash differs from review receipt"
            )
        if (
            self.operator_context.symbol != self.receipt.symbol
            or self.operator_context.quantity != self.receipt.quantity
            or self.operator_context.limit_price != self.receipt.limit_price
            or self.operator_context.notional != self.receipt.notional_usd
        ):
            raise PaperExecutionHumanReviewIntegrityError(
                "operator context economics differ from exact review"
            )
        if (
            crypto_operator_confirmation_challenge(self.operator_context)
            != self.receipt.approval_challenge
        ):
            raise PaperExecutionHumanReviewIntegrityError(
                "operator confirmation challenge differs from review receipt"
            )


def prepare_paper_execution_human_review(
    *,
    review_id: str,
    preparation: PaperExecutionCanaryPreparationResult,
    risk_handoff: PaperExecutionRiskContractResult,
) -> PaperExecutionHumanReviewResult:
    """Prepare the exact R6 human-approval challenge without issuing approval.

    This surface is credential-free, network-free and persistence-free. It
    cannot record or consume an operator decision and cannot reach OMS staging
    or a writer. If fewer than five seconds remain in the exact prepared package,
    it fails closed and requires fresh W86/W87 preparation.
    """

    _id(review_id, "review_id")
    if not isinstance(preparation, PaperExecutionCanaryPreparationResult):
        raise TypeError(
            "preparation must be PaperExecutionCanaryPreparationResult"
        )
    if not isinstance(risk_handoff, PaperExecutionRiskContractResult):
        raise TypeError("risk_handoff must be PaperExecutionRiskContractResult")

    preparation.__post_init__()
    risk_handoff.__post_init__()
    handoff_receipt = risk_handoff.receipt
    if not isinstance(handoff_receipt, PaperExecutionRiskHandoffReceipt):
        raise PaperExecutionHumanReviewIntegrityError(
            "human review requires the explicit W87 risk handoff"
        )
    if handoff_receipt.contract_version != PAPER_EXECUTION_RISK_HANDOFF_VERSION:
        raise PaperExecutionHumanReviewIntegrityError(
            "human review risk handoff version is not canonical"
        )
    if preparation.receipt.risk_contract_hash != handoff_receipt.receipt_hash:
        raise PaperExecutionHumanReviewIntegrityError(
            "canary preparation is not bound to the exact W87 risk handoff"
        )
    if (
        preparation.package.risk_decision_fingerprint
        != handoff_receipt.risk_decision_fingerprint
    ):
        raise PaperExecutionHumanReviewIntegrityError(
            "prepared package and risk handoff RiskDecision differ"
        )
    if (
        preparation.package.account_attestation_fingerprint
        != preparation.receipt.account_attestation_fingerprint
    ):
        raise PaperExecutionHumanReviewIntegrityError(
            "prepared package and W87 receipt account evidence differ"
        )

    now = _utc(_now_utc())
    if not (
        _utc(handoff_receipt.handoff_latched_at)
        <= now
        < _utc(handoff_receipt.valid_until)
    ):
        raise PaperExecutionHumanReviewBlocked(
            "W87 risk handoff expired before human review"
        )
    if now >= _utc(preparation.package.execution_deadline):
        raise PaperExecutionHumanReviewBlocked(
            "prepared R6 package expired before human review"
        )
    remaining = _utc(preparation.package.execution_deadline) - now
    if remaining < MIN_HUMAN_APPROVAL_REMAINING:
        raise PaperExecutionHumanReviewBlocked(
            "prepared R6 package is too close to expiry for human approval"
        )

    attempt_id = f"w87-human:{preparation.receipt.receipt_hash[:24]}"
    context = CryptoOperatorDecisionContext.from_prepared_package(
        preparation.package,
        attempt_id=attempt_id,
    )
    challenge = crypto_operator_confirmation_challenge(context)
    values = {
        "review_id": review_id,
        "contract_version": PAPER_EXECUTION_HUMAN_REVIEW_VERSION,
        "canary_preparation_hash": preparation.receipt.receipt_hash,
        "risk_handoff_hash": handoff_receipt.receipt_hash,
        "source_risk_contract_hash": handoff_receipt.source_risk_contract_hash,
        "package_hash": preparation.package.package_hash,
        "attempt_id": attempt_id,
        "operator_preparation_hash": context.preparation_hash,
        "account_id": preparation.receipt.account_id,
        "symbol": preparation.package.symbol,
        "quantity": preparation.package.quantity,
        "limit_price": preparation.package.limit_price,
        "notional_usd": preparation.package.notional,
        "review_prepared_at": now,
        "package_execution_deadline": preparation.package.execution_deadline,
        "approval_challenge": challenge,
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
        receipt_hash=_hash(_payload_values(values)),
    )
    result = PaperExecutionHumanReviewResult(
        receipt=receipt,
        operator_context=context,
    )
    result.__post_init__()
    return result


def _payload(
    receipt: PaperExecutionHumanReviewReceipt,
    *,
    include_hash: bool,
) -> dict[str, object]:
    values = {
        name: getattr(receipt, name)
        for name in receipt.__dataclass_fields__
        if name != "receipt_hash"
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
        raise PaperExecutionHumanReviewIntegrityError(
            f"{name} must be canonical identifier"
        )


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperExecutionHumanReviewIntegrityError(
            f"{name} must be lowercase sha256"
        )


def _positive(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise PaperExecutionHumanReviewIntegrityError(
            f"{name} must be finite positive Decimal"
        )


def _aware(value: datetime, name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise PaperExecutionHumanReviewIntegrityError(
            f"{name} must be timezone-aware datetime"
        )


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "MIN_HUMAN_APPROVAL_REMAINING",
    "PAPER_EXECUTION_HUMAN_REVIEW_VERSION",
    "PaperExecutionHumanReviewBlocked",
    "PaperExecutionHumanReviewError",
    "PaperExecutionHumanReviewIntegrityError",
    "PaperExecutionHumanReviewReceipt",
    "PaperExecutionHumanReviewResult",
    "PaperExecutionHumanReviewStatus",
    "prepare_paper_execution_human_review",
]
