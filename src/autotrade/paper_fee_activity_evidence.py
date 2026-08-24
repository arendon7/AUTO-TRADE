from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.domain import Side


PAPER_FEE_ACTIVITY_VERSION = "W82_PAPER_FEE_ACTIVITY_V2"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class PaperFeeActivityError(RuntimeError):
    pass


class PaperFeeActivityIntegrityError(PaperFeeActivityError):
    pass


class PaperFeeActivitySourceUnavailable(PaperFeeActivityError):
    pass


class FeeActivityStatus(StrEnum):
    PENDING_PUBLICATION = "PENDING_PUBLICATION"


@dataclass(frozen=True, slots=True)
class PendingPaperFeeActivityEvidence:
    """Identity-bound proof that broker fee truth is still unavailable.

    This receipt deliberately contains no fee amount. Absence of a fee activity is
    never interpreted as zero. A future audited read-only adapter may supersede
    this pending state only with explicit broker fee semantics.
    """

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
    credentials_persisted: bool
    broker_network_performed: bool
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
        if self.reason_code != "BROKER_FEE_SOURCE_NOT_CERTIFIED_OR_NOT_YET_OBSERVED":
            raise PaperFeeActivityIntegrityError("pending fee reason code is not canonical")
        if self.broker_authoritative_fee_proven is not False:
            raise PaperFeeActivityIntegrityError("pending activity is not broker fee proof")
        if self.zero_fee_inferred is not False:
            raise PaperFeeActivityIntegrityError("absence of fee activity may not infer zero fee")
        if self.paper_only is not True:
            raise PaperFeeActivityIntegrityError("pending fee evidence is PAPER-only")
        if self.credentials_persisted is not False:
            raise PaperFeeActivityIntegrityError("pending fee evidence may not persist credentials")
        if self.broker_network_performed is not False:
            raise PaperFeeActivityIntegrityError("scientific pending receipt may not perform broker network")
        if self.evidence_hash != _hash(_pending_payload(self, include_hash=False)):
            raise PaperFeeActivityIntegrityError("pending fee evidence hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _pending_payload(self, include_hash=True)


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
        "reason_code": "BROKER_FEE_SOURCE_NOT_CERTIFIED_OR_NOT_YET_OBSERVED",
        "broker_authoritative_fee_proven": False,
        "zero_fee_inferred": False,
        "paper_only": True,
        "credentials_persisted": False,
        "broker_network_performed": False,
    }
    return PendingPaperFeeActivityEvidence(
        **values,
        evidence_hash=_hash(_pending_payload_from_values(values)),
    )


def build_paper_fee_activity_evidence(*_args: object, **_kwargs: object) -> None:
    """Fail closed until an audited read-only broker fee adapter is certified.

    Caller-supplied activity ids, amounts, payload hashes, or gross-vs-net position
    differences are insufficient to assert broker-authoritative fee truth.
    """

    raise PaperFeeActivitySourceUnavailable(
        "BROKER_AUTHORITATIVE fee activity is unsupported until a read-only broker fee source is certified"
    )


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
        "credentials_persisted": value.credentials_persisted,
        "broker_network_performed": value.broker_network_performed,
    })
    if include_hash:
        payload["evidence_hash"] = value.evidence_hash
    return payload


def _pending_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["side"] = _enum_value(payload["side"])
    payload["status"] = _enum_value(payload["status"])
    for key in ("trade_observed_at", "checked_at", "publication_deadline"):
        payload[key] = _utc_iso(payload[key])  # type: ignore[arg-type]
    return payload


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperFeeActivityIntegrityError(f"{label} must be canonical identifier")


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperFeeActivityIntegrityError(f"{label} must be lowercase sha256")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperFeeActivityIntegrityError(f"{label} must be timezone-aware datetime")


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _enum_value(value: object) -> str:
    return value.value if isinstance(value, StrEnum) else str(value)


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "PAPER_FEE_ACTIVITY_VERSION",
    "FeeActivityStatus",
    "PaperFeeActivityError",
    "PaperFeeActivityIntegrityError",
    "PaperFeeActivitySourceUnavailable",
    "PendingPaperFeeActivityEvidence",
    "build_paper_fee_activity_evidence",
    "build_pending_paper_fee_activity_evidence",
]
