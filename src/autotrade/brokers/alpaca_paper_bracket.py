from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Mapping

from autotrade.domain import OrderRecord, OrderStatus, OrderType, Side

from .alpaca_paper_submission import deterministic_client_order_id


_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SUPPORTED_PARENT_STATUSES = frozenset(
    {
        "accepted",
        "new",
        "pending_new",
        "partially_filled",
        "held",
    }
)
_SUPPORTED_LEG_STATUSES = frozenset(
    {
        "accepted",
        "new",
        "pending_new",
        "held",
        "replaced",
    }
)


class PaperBracketError(RuntimeError):
    pass


class PaperBracketRejected(PaperBracketError):
    pass


class PaperBracketResponseIntegrityError(PaperBracketError):
    pass


@dataclass(frozen=True, slots=True)
class PaperEquityVenueRules:
    symbol: str
    asset_class: str
    price_tick: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    instrument_master_fingerprint: str

    def __post_init__(self) -> None:
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise ValueError("symbol must be canonical uppercase equity symbol")
        if self.asset_class != "us_equity":
            raise ValueError("R6 bracket protection supports us_equity only")
        for label, value in (
            ("price_tick", self.price_tick),
            ("quantity_step", self.quantity_step),
            ("minimum_quantity", self.minimum_quantity),
        ):
            if not _finite_positive(value):
                raise ValueError(f"{label} must be finite and > 0")
        if self.minimum_quantity % self.quantity_step != 0:
            raise ValueError("minimum_quantity must align exactly to quantity_step")
        if not _HASH_RE.fullmatch(self.instrument_master_fingerprint):
            raise ValueError("instrument_master_fingerprint must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class AlpacaEquityBracketRequest:
    order_id: str
    client_order_id: str
    instrument_master_fingerprint: str
    canonical_payload: Mapping[str, object]
    payload_json: str
    payload_hash: str


@dataclass(frozen=True, slots=True)
class AlpacaNestedBracketAttestation:
    parent_order_id: str
    client_order_id: str
    take_profit_order_id: str
    stop_loss_order_id: str
    request_id: str
    response_hash: str


class AlpacaEquityBracketBuilder:
    """Builds the exact first-canary bracket request without network I/O."""

    def build(
        self,
        *,
        order: OrderRecord,
        venue_rules: PaperEquityVenueRules,
        take_profit_price: Decimal,
        stop_loss_price: Decimal,
    ) -> AlpacaEquityBracketRequest:
        if order.status is not OrderStatus.VALIDATED:
            raise PaperBracketRejected("bracket parent requires VALIDATED OMS order")
        if order.broker_order_id is not None:
            raise PaperBracketRejected("bracket parent is already broker-bound")
        intent = order.intent
        if intent.symbol != venue_rules.symbol:
            raise PaperBracketRejected("Instrument Master symbol mismatch")
        if intent.side is not Side.BUY:
            raise PaperBracketRejected("R6 first bracket canary is BUY-only")
        if intent.order_type is not OrderType.LIMIT or intent.limit_price is None:
            raise PaperBracketRejected("R6 first bracket parent must be LIMIT")
        if intent.stop_price is not None:
            raise PaperBracketRejected("bracket parent intent cannot carry stop_price")
        if not _finite_positive(intent.quantity):
            raise PaperBracketRejected("parent quantity must be finite and positive")
        if not _finite_positive(intent.limit_price):
            raise PaperBracketRejected("parent limit_price must be finite and positive")
        if not _finite_positive(take_profit_price) or not _finite_positive(stop_loss_price):
            raise PaperBracketRejected("protection prices must be finite and positive")

        if intent.quantity < venue_rules.minimum_quantity:
            raise PaperBracketRejected(
                "quantity below venue minimum; R6 never auto-upsizes to minimum"
            )
        if intent.quantity % venue_rules.quantity_step != 0:
            raise PaperBracketRejected("quantity must align exactly to authoritative quantity_step")
        for label, value in (
            ("parent limit_price", intent.limit_price),
            ("take_profit_price", take_profit_price),
            ("stop_loss_price", stop_loss_price),
        ):
            if value % venue_rules.price_tick != 0:
                raise PaperBracketRejected(
                    f"{label} must align exactly to authoritative price_tick"
                )

        if not take_profit_price > intent.limit_price > stop_loss_price:
            raise PaperBracketRejected(
                "BUY bracket geometry must satisfy take_profit > parent limit > stop_loss"
            )
        if take_profit_price - intent.limit_price < venue_rules.price_tick:
            raise PaperBracketRejected("take-profit must be at least one tick above parent")
        if intent.limit_price - stop_loss_price < venue_rules.price_tick:
            raise PaperBracketRejected("stop-loss must be at least one tick below parent")

        client_order_id = deterministic_client_order_id(order)
        payload: dict[str, object] = {
            "client_order_id": client_order_id,
            "extended_hours": False,
            "limit_price": _decimal_text(intent.limit_price),
            "order_class": "bracket",
            "qty": _decimal_text(intent.quantity),
            "side": "buy",
            "stop_loss": {"stop_price": _decimal_text(stop_loss_price)},
            "symbol": intent.symbol,
            "take_profit": {"limit_price": _decimal_text(take_profit_price)},
            "time_in_force": "day",
            "type": "limit",
        }
        payload_json = _canonical_json(payload)
        return AlpacaEquityBracketRequest(
            order_id=order.order_id,
            client_order_id=client_order_id,
            instrument_master_fingerprint=venue_rules.instrument_master_fingerprint,
            canonical_payload=payload,
            payload_json=payload_json,
            payload_hash=sha256(payload_json.encode("utf-8")).hexdigest(),
        )


class AlpacaNestedBracketResponseValidator:
    """Validates broker nested response before bracket protection is trusted."""

    def validate(
        self,
        *,
        response_body: bytes,
        request_id: str,
        expected: AlpacaEquityBracketRequest,
    ) -> AlpacaNestedBracketAttestation:
        _validate_request_id(request_id)
        payload = _strict_json_object(response_body)
        parent_id = _required_id(payload, "id")
        client_order_id = _required_str(payload, "client_order_id")
        if client_order_id != expected.client_order_id:
            raise PaperBracketResponseIntegrityError("parent client_order_id mismatch")
        if _required_str(payload, "symbol") != expected.canonical_payload["symbol"]:
            raise PaperBracketResponseIntegrityError("parent symbol mismatch")
        if _required_str(payload, "side") != "buy":
            raise PaperBracketResponseIntegrityError("parent side mismatch")
        if _required_str(payload, "type") != "limit":
            raise PaperBracketResponseIntegrityError("parent type mismatch")
        if _required_str(payload, "time_in_force") != "day":
            raise PaperBracketResponseIntegrityError("parent TIF mismatch")
        if _required_str(payload, "order_class") != "bracket":
            raise PaperBracketResponseIntegrityError("parent order_class mismatch")
        if payload.get("extended_hours") is not False:
            raise PaperBracketResponseIntegrityError("bracket extended_hours must be false")
        if _required_decimal_text(payload, "qty") != expected.canonical_payload["qty"]:
            raise PaperBracketResponseIntegrityError("parent quantity mismatch")
        if _required_decimal_text(payload, "limit_price") != expected.canonical_payload["limit_price"]:
            raise PaperBracketResponseIntegrityError("parent limit_price mismatch")
        status = _required_str(payload, "status")
        if status not in _SUPPORTED_PARENT_STATUSES:
            raise PaperBracketResponseIntegrityError("parent broker status is not protection-safe")

        legs = payload.get("legs")
        if not isinstance(legs, list) or len(legs) != 2:
            raise PaperBracketResponseIntegrityError(
                "nested bracket must contain exactly two protection legs"
            )

        expected_qty = str(expected.canonical_payload["qty"])
        expected_tp = str(
            _mapping_value(expected.canonical_payload, "take_profit", "limit_price")
        )
        expected_stop = str(
            _mapping_value(expected.canonical_payload, "stop_loss", "stop_price")
        )

        take_profit_leg: Mapping[str, object] | None = None
        stop_loss_leg: Mapping[str, object] | None = None
        for raw_leg in legs:
            if not isinstance(raw_leg, dict):
                raise PaperBracketResponseIntegrityError("bracket leg must be an object")
            leg = raw_leg
            if _required_str(leg, "side") != "sell":
                raise PaperBracketResponseIntegrityError("protection leg side must be sell")
            if _required_decimal_text(leg, "qty") != expected_qty:
                raise PaperBracketResponseIntegrityError("protection leg quantity mismatch")
            leg_status = _required_str(leg, "status")
            if leg_status not in _SUPPORTED_LEG_STATUSES:
                raise PaperBracketResponseIntegrityError("protection leg status is not safe")
            leg_type = _required_str(leg, "type")
            if leg_type == "limit":
                if take_profit_leg is not None:
                    raise PaperBracketResponseIntegrityError("duplicate take-profit leg")
                if _required_decimal_text(leg, "limit_price") != expected_tp:
                    raise PaperBracketResponseIntegrityError("take-profit price mismatch")
                if leg.get("stop_price") not in (None, ""):
                    raise PaperBracketResponseIntegrityError(
                        "take-profit leg cannot contain stop_price"
                    )
                take_profit_leg = leg
            elif leg_type == "stop":
                if stop_loss_leg is not None:
                    raise PaperBracketResponseIntegrityError("duplicate stop-loss leg")
                if _required_decimal_text(leg, "stop_price") != expected_stop:
                    raise PaperBracketResponseIntegrityError("stop-loss price mismatch")
                if leg.get("limit_price") not in (None, ""):
                    raise PaperBracketResponseIntegrityError(
                        "R6 stop-loss leg must be stop, not stop-limit"
                    )
                stop_loss_leg = leg
            else:
                raise PaperBracketResponseIntegrityError(
                    "protection leg type must be exactly limit or stop"
                )

        if take_profit_leg is None or stop_loss_leg is None:
            raise PaperBracketResponseIntegrityError(
                "nested bracket is missing take-profit or stop-loss leg"
            )
        tp_id = _required_id(take_profit_leg, "id")
        stop_id = _required_id(stop_loss_leg, "id")
        if len({parent_id, tp_id, stop_id}) != 3:
            raise PaperBracketResponseIntegrityError("parent/leg broker IDs must be distinct")

        response_hash = sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        return AlpacaNestedBracketAttestation(
            parent_order_id=parent_id,
            client_order_id=client_order_id,
            take_profit_order_id=tp_id,
            stop_loss_order_id=stop_id,
            request_id=request_id,
            response_hash=response_hash,
        )


def _mapping_value(payload: Mapping[str, object], outer: str, inner: str) -> object:
    value = payload.get(outer)
    if not isinstance(value, Mapping):
        raise PaperBracketResponseIntegrityError(f"expected {outer} request mapping")
    return value[inner]


def _strict_json_object(raw: bytes) -> Mapping[str, object]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, parse_constant=lambda token: _raise_json_constant(token))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PaperBracketResponseIntegrityError("bracket response is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PaperBracketResponseIntegrityError("bracket response root must be object")
    return value


def _raise_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PaperBracketResponseIntegrityError(f"bracket field {key} is required")
    return value


def _required_id(payload: Mapping[str, object], key: str) -> str:
    value = _required_str(payload, key)
    if not _ID_RE.fullmatch(value):
        raise PaperBracketResponseIntegrityError(f"bracket field {key} is malformed")
    return value


def _required_decimal_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise PaperBracketResponseIntegrityError(f"bracket field {key} must be decimal string")
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise PaperBracketResponseIntegrityError(f"bracket field {key} is invalid") from exc
    if not _finite_positive(parsed):
        raise PaperBracketResponseIntegrityError(f"bracket field {key} must be positive")
    return _decimal_text(parsed)


def _validate_request_id(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise PaperBracketResponseIntegrityError("X-Request-ID is missing or too long")
    if any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise PaperBracketResponseIntegrityError("X-Request-ID contains invalid characters")


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("decimal must be finite")
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _finite_positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0
