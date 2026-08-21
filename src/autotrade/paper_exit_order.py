from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import re

from autotrade.paper_close_plan import PaperCryptoClosePlan


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PaperExitOrderError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PaperExitOrder:
    attempt_id: str
    owner_strategy_id: str
    plan_hash: str
    client_order_id: str
    symbol: str
    broker_symbol: str
    side: str
    quantity: Decimal
    order_type: str
    time_in_force: str
    limit_price: Decimal
    risk_reducing: bool
    retry_post: bool
    live_trading: str
    payload_hash: str
    order_hash: str

    def __post_init__(self) -> None:
        _id(self.attempt_id, "attempt_id")
        _id(self.owner_strategy_id, "owner_strategy_id")
        _id(self.client_order_id, "client_order_id")
        _hash(self.plan_hash, "plan_hash")
        if self.symbol.count("/") != 1 or self.symbol != self.symbol.upper():
            raise PaperExitOrderError("R7 exit symbol must be canonical BASE/QUOTE")
        if not self.broker_symbol or "/" in self.broker_symbol or self.broker_symbol != self.broker_symbol.upper():
            raise PaperExitOrderError("R7 exit broker_symbol must be compact uppercase broker identity")
        if self.side != "sell" or self.order_type != "limit" or self.time_in_force != "ioc":
            raise PaperExitOrderError("R7 first exit is SELL LIMIT IOC only")
        for label, value in (("quantity", self.quantity), ("limit_price", self.limit_price)):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise PaperExitOrderError(f"{label} must be finite positive Decimal")
        if self.risk_reducing is not True:
            raise PaperExitOrderError("R7 exit must be risk reducing")
        if self.retry_post is not False:
            raise PaperExitOrderError("R7 exit forbids POST retry")
        if self.live_trading != "BLOCKED":
            raise PaperExitOrderError("R7 exit cannot authorize LIVE")
        _hash(self.payload_hash, "payload_hash")
        _hash(self.order_hash, "order_hash")
        if self.payload_hash != sha256(_canonical(self.to_broker_payload()).encode()).hexdigest():
            raise PaperExitOrderError("R7 exit broker payload hash mismatch")
        if self.order_hash != sha256(_canonical(_order_payload(self, include_hash=False)).encode()).hexdigest():
            raise PaperExitOrderError("R7 exit order hash mismatch")

    def to_broker_payload(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "qty": _decimal(self.quantity),
            "side": self.side,
            "type": self.order_type,
            "time_in_force": self.time_in_force,
            "limit_price": _decimal(self.limit_price),
            "client_order_id": self.client_order_id,
        }

    def to_dict(self) -> dict[str, object]:
        return _order_payload(self, include_hash=True)


def build_paper_exit_order(
    *,
    plan: PaperCryptoClosePlan,
    attempt_id: str,
    owner_strategy_id: str,
) -> PaperExitOrder:
    if not isinstance(plan, PaperCryptoClosePlan):
        raise PaperExitOrderError("exact PaperCryptoClosePlan is required")
    _id(attempt_id, "attempt_id")
    _id(owner_strategy_id, "owner_strategy_id")
    if plan.asset_class != "crypto" or plan.side != "sell":
        raise PaperExitOrderError("R7 first exit supports long crypto close plans only")
    if plan.network_write_authorized is not False:
        raise PaperExitOrderError("prepared close plan may not itself carry network authority")
    if plan.retry_post is not False or plan.live_trading != "BLOCKED":
        raise PaperExitOrderError("close plan retry/LIVE invariants are invalid")
    client_order_id = deterministic_exit_client_order_id(
        attempt_id=attempt_id,
        plan_hash=plan.plan_hash,
        owner_strategy_id=owner_strategy_id,
    )
    broker_payload = {
        "symbol": plan.symbol,
        "qty": _decimal(plan.quantity),
        "side": "sell",
        "type": "limit",
        "time_in_force": "ioc",
        "limit_price": _decimal(plan.limit_price),
        "client_order_id": client_order_id,
    }
    values = {
        "attempt_id": attempt_id,
        "owner_strategy_id": owner_strategy_id,
        "plan_hash": plan.plan_hash,
        "client_order_id": client_order_id,
        "symbol": plan.symbol,
        "broker_symbol": plan.broker_symbol,
        "side": "sell",
        "quantity": plan.quantity,
        "order_type": "limit",
        "time_in_force": "ioc",
        "limit_price": plan.limit_price,
        "risk_reducing": True,
        "retry_post": False,
        "live_trading": "BLOCKED",
        "payload_hash": sha256(_canonical(broker_payload).encode()).hexdigest(),
    }
    order_hash = sha256(_canonical(_order_payload_from_values(values)).encode()).hexdigest()
    return PaperExitOrder(**values, order_hash=order_hash)


def deterministic_exit_client_order_id(*, attempt_id: str, plan_hash: str, owner_strategy_id: str) -> str:
    _id(attempt_id, "attempt_id")
    _hash(plan_hash, "plan_hash")
    _id(owner_strategy_id, "owner_strategy_id")
    digest = sha256(
        f"AUTO-TRADE:R7:EXIT:{attempt_id}:{owner_strategy_id}:{plan_hash}".encode()
    ).hexdigest()
    value = f"atr7x-{digest[:48]}"
    _id(value, "client_order_id")
    return value


def _order_payload(value: PaperExitOrder, *, include_hash: bool) -> dict[str, object]:
    payload = {
        "attempt_id": value.attempt_id,
        "owner_strategy_id": value.owner_strategy_id,
        "plan_hash": value.plan_hash,
        "client_order_id": value.client_order_id,
        "symbol": value.symbol,
        "broker_symbol": value.broker_symbol,
        "side": value.side,
        "quantity": _decimal(value.quantity),
        "order_type": value.order_type,
        "time_in_force": value.time_in_force,
        "limit_price": _decimal(value.limit_price),
        "risk_reducing": value.risk_reducing,
        "retry_post": value.retry_post,
        "live_trading": value.live_trading,
        "payload_hash": value.payload_hash,
    }
    if include_hash:
        payload["order_hash"] = value.order_hash
    return payload


def _order_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["quantity"] = _decimal(values["quantity"])  # type: ignore[arg-type]
    payload["limit_price"] = _decimal(values["limit_price"])  # type: ignore[arg-type]
    return payload


def _decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperExitOrderError(f"{label} is invalid")


def _hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperExitOrderError(f"{label} must be lowercase SHA-256")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


__all__ = [
    "PaperExitOrder",
    "PaperExitOrderError",
    "build_paper_exit_order",
    "deterministic_exit_client_order_id",
]
