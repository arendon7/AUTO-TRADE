from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.domain import Side


PAPER_FEE_ACTIVITY_VERSION = "W82_PAPER_FEE_ACTIVITY_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")


class PaperFeeActivityError(RuntimeError):
    pass


class PaperFeeActivityIntegrityError(PaperFeeActivityError):
    pass


class FeeActivityStatus(StrEnum):
    OBSERVED = "OBSERVED"
    PENDING_PUBLICATION = "PENDING_PUBLICATION"


class FeeNormalizationRule(StrEnum):
    ABS_QTY_TIMES_PRICE = "ABS_QTY_TIMES_PRICE"
    ABS_NET_AMOUNT = "ABS_NET_AMOUNT"


@dataclass(frozen=True, slots=True)
class PaperFeeActivityEvidence:
    evidence_id: str
    version: str
    account_fingerprint: str
    order_id_query: str
    order_query_hash: str
    client_order_id: str
    strategy_id: str
    symbol: str
    side: Side
    activity_id: str
    activity_type: str
    fee_currency: str
    quote_currency: str
    normalization_rule: FeeNormalizationRule
    normalized_fee_amount: Decimal
    activity_price: Decimal | None
    fee_quote_equivalent: Decimal
    activity_created_at: datetime
    captured_at: datetime
    source_payload_sha256: str
    broker_authoritative: bool
    paper_only: bool
    evidence_hash: str

    def __post_init__(self) -> None:
        _require_id(self.evidence_id, "evidence_id")
        if self.version != PAPER_FEE_ACTIVITY_VERSION:
            raise PaperFeeActivityIntegrityError("fee activity version is not canonical W82")
        for label, value in (
            ("account_fingerprint", self.account_fingerprint),
            ("order_query_hash", self.order_query_hash),
            ("source_payload_sha256", self.source_payload_sha256),
            ("evidence_hash", self.evidence_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("order_id_query", self.order_id_query),
            ("client_order_id", self.client_order_id),
            ("strategy_id", self.strategy_id),
            ("activity_id", self.activity_id),
        ):
            _require_id(value, label)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise PaperFeeActivityIntegrityError("symbol is required")
        if not isinstance(self.side, Side):
            raise PaperFeeActivityIntegrityError("side must use canonical Side")
        if self.activity_type not in {"CFEE", "FEE"}:
            raise PaperFeeActivityIntegrityError("activity_type must be CFEE or FEE")
        _require_currency(self.fee_currency, "fee_currency")
        _require_currency(self.quote_currency, "quote_currency")
        if not isinstance(self.normalization_rule, FeeNormalizationRule):
            raise PaperFeeActivityIntegrityError("normalization_rule is invalid")
        _require_positive_decimal(self.normalized_fee_amount, "normalized_fee_amount")
        _require_positive_decimal(self.fee_quote_equivalent, "fee_quote_equivalent")
        if self.normalization_rule is FeeNormalizationRule.ABS_QTY_TIMES_PRICE:
            if self.activity_price is None:
                raise PaperFeeActivityIntegrityError("qty-based fee normalization requires activity price")
            _require_positive_decimal(self.activity_price, "activity_price")
            if self.fee_quote_equivalent != self.normalized_fee_amount * self.activity_price:
                raise PaperFeeActivityIntegrityError("qty-based fee quote equivalent mismatch")
        else:
            if self.activity_price is not None:
                raise PaperFeeActivityIntegrityError("net-amount fee normalization may not carry conversion price")
            if self.fee_currency != self.quote_currency:
                raise PaperFeeActivityIntegrityError("net-amount fee must already be quote-currency denominated")
            if self.fee_quote_equivalent != self.normalized_fee_amount:
                raise PaperFeeActivityIntegrityError("net-amount fee quote equivalent mismatch")
        _require_aware(self.activity_created_at, "activity_created_at")
        _require_aware(self.captured_at, "captured_at")
        if _utc(self.captured_at) < _utc(self.activity_created_at):
            raise PaperFeeActivityIntegrityError("captured fee evidence may not predate broker activity")
        if self.broker_authoritative is not True or self.paper_only is not True:
            raise PaperFeeActivityIntegrityError("fee activity evidence must remain authoritative PAPER evidence")
        if self.evidence_hash != _hash(_observed_payload(self, include_hash=False)):
            raise PaperFeeActivityIntegrityError("fee activity evidence hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _observed_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PendingPaperFeeActivityEvidence:
    evidence_id: str
    version: str
    account_fingerprint: str
    order_id_query: str
    order_query_hash: str
    client_order_id: str
    strategy_id: str
    symbol: str
    side: Side
    trade_observed_at: datetime
    checked_at: datetime
    publication_deadline: datetime
    status: FeeActivityStatus
    fee_amount: None
    reason_code: str
    broker_authoritative_fee_proven: bool
    zero_fee_inferred: bool
    paper_only: bool
    evidence_hash: str

    def __post_init__(self) -> None:
        _require_id(self.evidence_id, "evidence_id")
        if self.version != PAPER_FEE_ACTIVITY_VERSION:
            raise PaperFeeActivityIntegrityError("pending fee activity version is not canonical W82")
        for label, value in (
            ("account_fingerprint", self.account_fingerprint),
            ("order_query_hash", self.order_query_hash),
            ("evidence_hash", self.evidence_hash),
        ):
            _require_hash(value, label)
        for label, value in (
            ("order_id_query", self.order_id_query),
            ("client_order_id", self.client_order_id),
            ("strategy_id", self.strategy_id),
        ):
            _require_id(value, label)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise PaperFeeActivityIntegrityError("symbol is required")
        if not isinstance(self.side, Side):
            raise PaperFeeActivityIntegrityError("side must use canonical Side")
        for label, value in (
            ("trade_observed_at", self.trade_observed_at),
            ("checked_at", self.checked_at),
            ("publication_deadline", self.publication_deadline),
        ):
            _require_aware(value, label)
        if _utc(self.checked_at) < _utc(self.trade_observed_at):
            raise PaperFeeActivityIntegrityError("fee check may not predate trade")
        if _utc(self.publication_deadline) < _utc(self.checked_at):
            raise PaperFeeActivityIntegrityError("pending evidence requires publication window still open")
        if self.status is not FeeActivityStatus.PENDING_PUBLICATION:
            raise PaperFeeActivityIntegrityError("missing fee activity must remain PENDING_PUBLICATION")
        if self.fee_amount is not None:
            raise PaperFeeActivityIntegrityError("pending fee activity may not fabricate fee amount")
        if self.reason_code != "FEE_ACTIVITY_NOT_YET_OBSERVED":
            raise PaperFeeActivityIntegrityError("pending fee reason code is not canonical")
        if self.broker_authoritative_fee_proven is not False:
            raise PaperFeeActivityIntegrityError("pending activity is not broker fee proof")
        if self.zero_fee_inferred is not False:
            raise PaperFeeActivityIntegrityError("absence of fee activity may not infer zero fee")
        if self.paper_only is not True:
            raise PaperFeeActivityIntegrityError("pending fee evidence is PAPER-only")
        if self.evidence_hash != _hash(_pending_payload(self, include_hash=False)):
            raise PaperFeeActivityIntegrityError("pending fee evidence hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _pending_payload(self, include_hash=True)


def build_paper_fee_activity_evidence(
    *,
    evidence_id: str,
    account_fingerprint: str,
    order_id_query: str,
    order_query_hash: str,
    client_order_id: str,
    strategy_id: str,
    symbol: str,
    side: Side,
    activity_id: str,
    activity_type: str,
    fee_currency: str,
    quote_currency: str,
    normalization_rule: FeeNormalizationRule,
    normalized_fee_amount: Decimal,
    activity_price: Decimal | None,
    fee_quote_equivalent: Decimal,
    activity_created_at: datetime,
    captured_at: datetime,
    source_payload_sha256: str,
) -> PaperFeeActivityEvidence:
    values = {
        "evidence_id": evidence_id,
        "version": PAPER_FEE_ACTIVITY_VERSION,
        "account_fingerprint": account_fingerprint,
        "order_id_query": order_id_query,
        "order_query_hash": order_query_hash,
        "client_order_id": client_order_id,
        "strategy_id": strategy_id,
        "symbol": symbol,
        "side": side,
        "activity_id": activity_id,
        "activity_type": activity_type,
        "fee_currency": fee_currency,
        "quote_currency": quote_currency,
        "normalization_rule": normalization_rule,
        "normalized_fee_amount": normalized_fee_amount,
        "activity_price": activity_price,
        "fee_quote_equivalent": fee_quote_equivalent,
        "activity_created_at": activity_created_at,
        "captured_at": captured_at,
        "source_payload_sha256": source_payload_sha256,
        "broker_authoritative": True,
        "paper_only": True,
    }
    return PaperFeeActivityEvidence(
        **values,
        evidence_hash=_hash(_observed_payload_from_values(values)),
    )


def build_pending_paper_fee_activity_evidence(
    *,
    evidence_id: str,
    account_fingerprint: str,
    order_id_query: str,
    order_query_hash: str,
    client_order_id: str,
    strategy_id: str,
    symbol: str,
    side: Side,
    trade_observed_at: datetime,
    checked_at: datetime,
    publication_deadline: datetime,
) -> PendingPaperFeeActivityEvidence:
    values = {
        "evidence_id": evidence_id,
        "version": PAPER_FEE_ACTIVITY_VERSION,
        "account_fingerprint": account_fingerprint,
        "order_id_query": order_id_query,
        "order_query_hash": order_query_hash,
        "client_order_id": client_order_id,
        "strategy_id": strategy_id,
        "symbol": symbol,
        "side": side,
        "trade_observed_at": trade_observed_at,
        "checked_at": checked_at,
        "publication_deadline": publication_deadline,
        "status": FeeActivityStatus.PENDING_PUBLICATION,
        "fee_amount": None,
        "reason_code": "FEE_ACTIVITY_NOT_YET_OBSERVED",
        "broker_authoritative_fee_proven": False,
        "zero_fee_inferred": False,
        "paper_only": True,
    }
    return PendingPaperFeeActivityEvidence(
        **values,
        evidence_hash=_hash(_pending_payload_from_values(values)),
    )


def _observed_payload(value: PaperFeeActivityEvidence, *, include_hash: bool) -> dict[str, object]:
    payload = _observed_payload_from_values({
        "evidence_id": value.evidence_id,
        "version": value.version,
        "account_fingerprint": value.account_fingerprint,
        "order_id_query": value.order_id_query,
        "order_query_hash": value.order_query_hash,
        "client_order_id": value.client_order_id,
        "strategy_id": value.strategy_id,
        "symbol": value.symbol,
        "side": value.side,
        "activity_id": value.activity_id,
        "activity_type": value.activity_type,
        "fee_currency": value.fee_currency,
        "quote_currency": value.quote_currency,
        "normalization_rule": value.normalization_rule,
        "normalized_fee_amount": value.normalized_fee_amount,
        "activity_price": value.activity_price,
        "fee_quote_equivalent": value.fee_quote_equivalent,
        "activity_created_at": value.activity_created_at,
        "captured_at": value.captured_at,
        "source_payload_sha256": value.source_payload_sha256,
        "broker_authoritative": value.broker_authoritative,
        "paper_only": value.paper_only,
    })
    if include_hash:
        payload["evidence_hash"] = value.evidence_hash
    return payload


def _observed_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["side"] = _enum_value(payload["side"])
    payload["normalization_rule"] = _enum_value(payload["normalization_rule"])
    for key in ("normalized_fee_amount", "activity_price", "fee_quote_equivalent"):
        value = payload[key]
        payload[key] = None if value is None else _decimal(value)
    for key in ("activity_created_at", "captured_at"):
        payload[key] = _utc_iso(payload[key])
    return payload


def _pending_payload(value: PendingPaperFeeActivityEvidence, *, include_hash: bool) -> dict[str, object]:
    payload = _pending_payload_from_values({
        "evidence_id": value.evidence_id,
        "version": value.version,
        "account_fingerprint": value.account_fingerprint,
        "order_id_query": value.order_id_query,
        "order_query_hash": value.order_query_hash,
        "client_order_id": value.client_order_id,
        "strategy_id": value.strategy_id,
        "symbol": value.symbol,
        "side": value.side,
        "trade_observed_at": value.trade_observed_at,
        "checked_at": value.checked_at,
        "publication_deadline": value.publication_deadline,
        "status": value.status,
        "fee_amount": value.fee_amount,
        "reason_code": value.reason_code,
        "broker_authoritative_fee_proven": value.broker_authoritative_fee_proven,
        "zero_fee_inferred": value.zero_fee_inferred,
        "paper_only": value.paper_only,
    })
    if include_hash:
        payload["evidence_hash"] = value.evidence_hash
    return payload


def _pending_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["side"] = _enum_value(payload["side"])
    payload["status"] = _enum_value(payload["status"])
    for key in ("trade_observed_at", "checked_at", "publication_deadline"):
        payload[key] = _utc_iso(payload[key])
    return payload


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperFeeActivityIntegrityError(f"{label} must be canonical identifier")


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperFeeActivityIntegrityError(f"{label} must be lowercase sha256")


def _require_currency(value: str, label: str) -> None:
    if not isinstance(value, str) or not _CURRENCY_RE.fullmatch(value):
        raise PaperFeeActivityIntegrityError(f"{label} must be canonical uppercase currency code")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperFeeActivityIntegrityError(f"{label} must be timezone-aware datetime")


def _require_positive_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise PaperFeeActivityIntegrityError(f"{label} must be finite Decimal > 0")


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _enum_value(value: object) -> str:
    return value.value if isinstance(value, StrEnum) else str(value)


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "FeeActivityStatus",
    "FeeNormalizationRule",
    "PAPER_FEE_ACTIVITY_VERSION",
    "PaperFeeActivityError",
    "PaperFeeActivityEvidence",
    "PaperFeeActivityIntegrityError",
    "PendingPaperFeeActivityEvidence",
    "build_paper_fee_activity_evidence",
    "build_pending_paper_fee_activity_evidence",
]
