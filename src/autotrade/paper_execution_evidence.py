from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

from autotrade.domain import (
    MarketSnapshot,
    OrderRecord,
    OrderStatus,
    Side,
    intent_fingerprint,
    market_fingerprint,
)
from autotrade.paper_execution_scenarios import PaperExecutionScenario


BPS_DENOMINATOR = Decimal("10000")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PaperExecutionEvidenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PaperExecutionEvidence:
    """Execution-quality evidence with separate trace and scientific identities.

    `evidence_hash` binds the concrete runtime trace, including OMS order identity
    and capture time. `measurement_hash` deliberately excludes those opaque runtime
    identifiers so the same inputs + execution assumptions + deterministic outcome
    reproduce the same measurement across independent Strategy Lab runs.
    """

    scenario_id: str
    scenario_hash: str
    order_id: str
    intent_fingerprint: str
    market_fingerprint: str
    symbol: str
    side: str
    order_status: str
    requested_quantity: Decimal
    filled_quantity: Decimal
    fill_ratio: Decimal
    reference_touch: Decimal
    average_fill_price: Decimal | None
    adverse_slippage_bps: Decimal | None
    market_observed_at: datetime
    captured_at: datetime
    measurement_hash: str
    evidence_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("scenario_hash", self.scenario_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("market_fingerprint", self.market_fingerprint),
            ("measurement_hash", self.measurement_hash),
            ("evidence_hash", self.evidence_hash),
        ):
            if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                raise PaperExecutionEvidenceError(f"{label} must be lowercase sha256")
        if not isinstance(self.scenario_id, str) or not self.scenario_id:
            raise PaperExecutionEvidenceError("scenario_id is required")
        if not isinstance(self.order_id, str) or not self.order_id:
            raise PaperExecutionEvidenceError("order_id is required")
        if self.side not in {Side.BUY.value, Side.SELL.value}:
            raise PaperExecutionEvidenceError("side is invalid")
        try:
            status = OrderStatus(self.order_status)
        except (TypeError, ValueError) as exc:
            raise PaperExecutionEvidenceError("order_status is invalid") from exc

        for label, value in (
            ("requested_quantity", self.requested_quantity),
            ("filled_quantity", self.filled_quantity),
            ("fill_ratio", self.fill_ratio),
            ("reference_touch", self.reference_touch),
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise PaperExecutionEvidenceError(f"{label} must be finite Decimal")
        if self.requested_quantity <= 0:
            raise PaperExecutionEvidenceError("requested_quantity must be positive")
        if self.filled_quantity < 0 or self.filled_quantity > self.requested_quantity:
            raise PaperExecutionEvidenceError("filled_quantity is outside request bounds")
        if self.fill_ratio != self.filled_quantity / self.requested_quantity:
            raise PaperExecutionEvidenceError("fill_ratio mismatch")
        if self.fill_ratio < 0 or self.fill_ratio > 1:
            raise PaperExecutionEvidenceError("fill_ratio must be within [0,1]")
        _validate_status_fill_consistency(status=status, fill_ratio=self.fill_ratio)

        if self.reference_touch <= 0:
            raise PaperExecutionEvidenceError("reference_touch must be positive")
        if self.filled_quantity == 0:
            if self.average_fill_price is not None or self.adverse_slippage_bps is not None:
                raise PaperExecutionEvidenceError("zero-fill evidence may not claim execution price/slippage")
        else:
            if (
                not isinstance(self.average_fill_price, Decimal)
                or not self.average_fill_price.is_finite()
                or self.average_fill_price <= 0
            ):
                raise PaperExecutionEvidenceError("filled execution requires average_fill_price")
            if (
                not isinstance(self.adverse_slippage_bps, Decimal)
                or not self.adverse_slippage_bps.is_finite()
                or self.adverse_slippage_bps < 0
            ):
                raise PaperExecutionEvidenceError("filled execution requires non-negative adverse slippage evidence")
        _require_aware(self.market_observed_at, "market_observed_at")
        _require_aware(self.captured_at, "captured_at")
        if self.captured_at.astimezone(timezone.utc) < self.market_observed_at.astimezone(timezone.utc):
            raise PaperExecutionEvidenceError("execution evidence cannot predate market observation")
        if self.measurement_hash != _hash(_measurement_payload(self)):
            raise PaperExecutionEvidenceError("execution measurement hash mismatch")
        if self.evidence_hash != _hash(_payload(self, include_hashes=False)):
            raise PaperExecutionEvidenceError("execution evidence hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hashes=True)


def capture_paper_execution_evidence(
    *,
    scenario: PaperExecutionScenario,
    order: OrderRecord,
    market: MarketSnapshot,
    captured_at: datetime,
) -> PaperExecutionEvidence:
    if not isinstance(scenario, PaperExecutionScenario):
        raise TypeError("scenario must be PaperExecutionScenario")
    if not isinstance(order, OrderRecord):
        raise TypeError("order must be OrderRecord")
    if not isinstance(market, MarketSnapshot):
        raise TypeError("market must be MarketSnapshot")
    _require_aware(captured_at, "captured_at")
    _require_aware(market.observed_at, "market.observed_at")
    if order.intent.symbol != market.symbol:
        raise PaperExecutionEvidenceError("order/market symbol mismatch")
    if order.filled_quantity < 0 or order.filled_quantity > order.intent.quantity:
        raise PaperExecutionEvidenceError("order filled quantity is invalid")
    if order.filled_quantity > 0 and order.average_fill_price is None:
        raise PaperExecutionEvidenceError("filled order lacks average fill price")
    if order.filled_quantity == 0 and order.average_fill_price is not None:
        raise PaperExecutionEvidenceError("zero-fill order unexpectedly carries average fill price")

    reference_touch = market.ask if order.intent.side is Side.BUY else market.bid
    fill_ratio = order.filled_quantity / order.intent.quantity
    slippage: Decimal | None = None
    if order.average_fill_price is not None:
        if order.intent.side is Side.BUY:
            slippage = (order.average_fill_price - reference_touch) / reference_touch * BPS_DENOMINATOR
        else:
            slippage = (reference_touch - order.average_fill_price) / reference_touch * BPS_DENOMINATOR
        if slippage < 0:
            raise PaperExecutionEvidenceError("W78 adverse model may not record favorable slippage")

    values = {
        "scenario_id": scenario.scenario_id,
        "scenario_hash": scenario.scenario_hash,
        "order_id": order.order_id,
        "intent_fingerprint": intent_fingerprint(order.intent),
        "market_fingerprint": market_fingerprint(market),
        "symbol": order.intent.symbol,
        "side": order.intent.side.value,
        "order_status": order.status.value,
        "requested_quantity": order.intent.quantity,
        "filled_quantity": order.filled_quantity,
        "fill_ratio": fill_ratio,
        "reference_touch": reference_touch,
        "average_fill_price": order.average_fill_price,
        "adverse_slippage_bps": slippage,
        "market_observed_at": market.observed_at.astimezone(timezone.utc),
        "captured_at": captured_at.astimezone(timezone.utc),
    }
    measurement_hash = _hash(_measurement_payload_from_values(values))
    trace_values = dict(values)
    trace_values["measurement_hash"] = measurement_hash
    return PaperExecutionEvidence(
        **values,
        measurement_hash=measurement_hash,
        evidence_hash=_hash(_payload_from_values(trace_values)),
    )


def _validate_status_fill_consistency(*, status: OrderStatus, fill_ratio: Decimal) -> None:
    if status is OrderStatus.FILLED and fill_ratio != Decimal("1"):
        raise PaperExecutionEvidenceError("FILLED evidence requires fill_ratio=1")
    if status is OrderStatus.PARTIALLY_FILLED and not Decimal("0") < fill_ratio < Decimal("1"):
        raise PaperExecutionEvidenceError("PARTIALLY_FILLED evidence requires 0<fill_ratio<1")
    if status is OrderStatus.SUBMITTED and fill_ratio != Decimal("0"):
        raise PaperExecutionEvidenceError("SUBMITTED evidence requires zero fill")
    if status in {OrderStatus.VALIDATED, OrderStatus.SUBMITTING}:
        raise PaperExecutionEvidenceError("pre-execution OMS state is not execution-quality evidence")
    if status is OrderStatus.UNKNOWN:
        raise PaperExecutionEvidenceError("UNKNOWN execution requires reconciliation, not qualification evidence")


def _measurement_payload(value: PaperExecutionEvidence) -> dict[str, object]:
    return _measurement_payload_from_values(
        {
            "scenario_id": value.scenario_id,
            "scenario_hash": value.scenario_hash,
            "intent_fingerprint": value.intent_fingerprint,
            "market_fingerprint": value.market_fingerprint,
            "symbol": value.symbol,
            "side": value.side,
            "order_status": value.order_status,
            "requested_quantity": value.requested_quantity,
            "filled_quantity": value.filled_quantity,
            "fill_ratio": value.fill_ratio,
            "reference_touch": value.reference_touch,
            "average_fill_price": value.average_fill_price,
            "adverse_slippage_bps": value.adverse_slippage_bps,
            "market_observed_at": value.market_observed_at,
        }
    )


def _measurement_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    for key in (
        "requested_quantity",
        "filled_quantity",
        "fill_ratio",
        "reference_touch",
        "average_fill_price",
        "adverse_slippage_bps",
    ):
        raw = payload[key]
        payload[key] = None if raw is None else _decimal(raw)  # type: ignore[arg-type]
    payload["market_observed_at"] = payload["market_observed_at"].astimezone(timezone.utc).isoformat()  # type: ignore[union-attr]
    return payload


def _payload(value: PaperExecutionEvidence, *, include_hashes: bool) -> dict[str, object]:
    payload = _payload_from_values(
        {
            "scenario_id": value.scenario_id,
            "scenario_hash": value.scenario_hash,
            "order_id": value.order_id,
            "intent_fingerprint": value.intent_fingerprint,
            "market_fingerprint": value.market_fingerprint,
            "symbol": value.symbol,
            "side": value.side,
            "order_status": value.order_status,
            "requested_quantity": value.requested_quantity,
            "filled_quantity": value.filled_quantity,
            "fill_ratio": value.fill_ratio,
            "reference_touch": value.reference_touch,
            "average_fill_price": value.average_fill_price,
            "adverse_slippage_bps": value.adverse_slippage_bps,
            "market_observed_at": value.market_observed_at,
            "captured_at": value.captured_at,
            "measurement_hash": value.measurement_hash,
        }
    )
    if include_hashes:
        payload["evidence_hash"] = value.evidence_hash
    return payload


def _payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    for key in (
        "requested_quantity",
        "filled_quantity",
        "fill_ratio",
        "reference_touch",
        "average_fill_price",
        "adverse_slippage_bps",
    ):
        raw = payload[key]
        payload[key] = None if raw is None else _decimal(raw)  # type: ignore[arg-type]
    for key in ("market_observed_at", "captured_at"):
        payload[key] = payload[key].astimezone(timezone.utc).isoformat()  # type: ignore[union-attr]
    return payload


def _decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperExecutionEvidenceError(f"{label} must be timezone-aware datetime")


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "BPS_DENOMINATOR",
    "PaperExecutionEvidence",
    "PaperExecutionEvidenceError",
    "capture_paper_execution_evidence",
]
