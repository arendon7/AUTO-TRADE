from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json

from autotrade.brokers.alpaca_paper_portfolio import (
    PaperPortfolioSnapshot,
)


CLOSE_PLAN_TTL = timedelta(seconds=15)
MAX_CLOSE_SLIPPAGE_BPS = Decimal("50")


class PaperClosePlanError(RuntimeError):
    pass


class PaperCloseMode(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"


@dataclass(frozen=True, slots=True)
class PaperCryptoClosePlan:
    account_reference: str
    credential_reference: str
    portfolio_fingerprint: str
    broker_symbol: str
    symbol: str
    asset_class: str
    mode: PaperCloseMode
    side: str
    quantity: Decimal
    observed_position_quantity: Decimal
    observed_available_quantity: Decimal
    reference_price: Decimal
    limit_price: Decimal
    max_slippage_bps: Decimal
    order_type: str
    time_in_force: str
    prepared_at: datetime
    expires_at: datetime
    risk_reducing: bool
    network_write_authorized: bool
    retry_post: bool
    live_trading: str
    plan_hash: str

    def __post_init__(self) -> None:
        if self.asset_class != "crypto" or self.symbol.count("/") != 1:
            raise ValueError("close plan currently supports canonical crypto positions only")
        if self.side != "sell":
            raise ValueError("long crypto close plan must be SELL")
        for label, value in (
            ("quantity", self.quantity),
            ("observed_position_quantity", self.observed_position_quantity),
            ("observed_available_quantity", self.observed_available_quantity),
            ("reference_price", self.reference_price),
            ("limit_price", self.limit_price),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{label} must be finite positive Decimal")
        if self.quantity > self.observed_position_quantity:
            raise ValueError("close quantity exceeds observed position")
        if self.quantity > self.observed_available_quantity:
            raise ValueError("close quantity exceeds broker available quantity")
        if self.max_slippage_bps < 0 or self.max_slippage_bps > MAX_CLOSE_SLIPPAGE_BPS:
            raise ValueError("close slippage bound is invalid")
        minimum_limit = self.reference_price * (Decimal("1") - self.max_slippage_bps / Decimal("10000"))
        if self.limit_price < minimum_limit:
            raise ValueError("close limit exceeds permitted downside slippage")
        if self.order_type != "limit" or self.time_in_force != "ioc":
            raise ValueError("R7 first close contract is SELL LIMIT IOC only")
        if self.risk_reducing is not True or self.network_write_authorized is not False:
            raise ValueError("prepared close plan must be risk-reducing and broker-write inert")
        if self.retry_post is not False or self.live_trading != "BLOCKED":
            raise ValueError("prepared close plan may not authorize retry or LIVE")
        if self.prepared_at.tzinfo is None or self.prepared_at.utcoffset() is None:
            raise ValueError("prepared_at must be timezone-aware")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        if self.expires_at <= self.prepared_at or self.expires_at - self.prepared_at > CLOSE_PLAN_TTL:
            raise ValueError("close plan TTL is invalid")
        if self.plan_hash != _hash(_payload(self, include_hash=False)):
            raise ValueError("close plan hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def prepare_crypto_close_plan(
    *,
    portfolio: PaperPortfolioSnapshot,
    symbol: str,
    now: datetime,
    quantity: Decimal | None = None,
    limit_price: Decimal,
    max_slippage_bps: Decimal = Decimal("25"),
) -> PaperCryptoClosePlan:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PaperClosePlanError("now must be timezone-aware")
    instant = now.astimezone(timezone.utc)
    age = instant - portfolio.observed_at.astimezone(timezone.utc)
    if age < timedelta(0) or age > CLOSE_PLAN_TTL:
        raise PaperClosePlanError("portfolio broker truth is stale for close preparation")
    canonical = symbol.strip().upper()
    matches = [item for item in portfolio.positions if item.symbol == canonical]
    if len(matches) != 1:
        raise PaperClosePlanError("close preparation requires exactly one broker-truth position")
    position = matches[0]
    if position.asset_class != "crypto" or position.side != "long" or position.quantity <= 0:
        raise PaperClosePlanError("R7 first close supports positive long crypto exposure only")
    if position.available_quantity <= 0:
        raise PaperClosePlanError("broker reports no available quantity to close")
    if not isinstance(limit_price, Decimal) or not limit_price.is_finite() or limit_price <= 0:
        raise PaperClosePlanError("explicit close limit_price must be finite positive Decimal")
    if not isinstance(max_slippage_bps, Decimal) or not max_slippage_bps.is_finite():
        raise PaperClosePlanError("max_slippage_bps must be finite Decimal")
    if max_slippage_bps < 0 or max_slippage_bps > MAX_CLOSE_SLIPPAGE_BPS:
        raise PaperClosePlanError("max_slippage_bps exceeds R7 close hard cap")
    reference_price = position.current_price
    minimum_limit = reference_price * (Decimal("1") - max_slippage_bps / Decimal("10000"))
    if limit_price < minimum_limit or limit_price > reference_price * Decimal("1.02"):
        raise PaperClosePlanError("close limit_price is outside the bounded reference-price envelope")

    if quantity is None:
        target = position.available_quantity
        mode = PaperCloseMode.FULL
    else:
        if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity <= 0:
            raise PaperClosePlanError("partial close quantity must be finite positive Decimal")
        if quantity > position.available_quantity:
            raise PaperClosePlanError("close quantity exceeds broker available quantity")
        target = quantity
        mode = PaperCloseMode.FULL if target == position.available_quantity else PaperCloseMode.PARTIAL

    values = {
        "account_reference": portfolio.account.account_reference,
        "credential_reference": portfolio.account.credential_reference,
        "portfolio_fingerprint": portfolio.fingerprint,
        "broker_symbol": position.broker_symbol,
        "symbol": position.symbol,
        "asset_class": position.asset_class,
        "mode": mode,
        "side": "sell",
        "quantity": target,
        "observed_position_quantity": position.quantity,
        "observed_available_quantity": position.available_quantity,
        "reference_price": reference_price,
        "limit_price": limit_price,
        "max_slippage_bps": max_slippage_bps,
        "order_type": "limit",
        "time_in_force": "ioc",
        "prepared_at": instant,
        "expires_at": instant + CLOSE_PLAN_TTL,
        "risk_reducing": True,
        "network_write_authorized": False,
        "retry_post": False,
        "live_trading": "BLOCKED",
    }
    return PaperCryptoClosePlan(**values, plan_hash=_hash(_payload_from_values(values)))


def _payload(value: PaperCryptoClosePlan, *, include_hash: bool) -> dict[str, object]:
    payload = {
        "account_reference": value.account_reference,
        "credential_reference": value.credential_reference,
        "portfolio_fingerprint": value.portfolio_fingerprint,
        "broker_symbol": value.broker_symbol,
        "symbol": value.symbol,
        "asset_class": value.asset_class,
        "mode": value.mode.value,
        "side": value.side,
        "quantity": _decimal(value.quantity),
        "observed_position_quantity": _decimal(value.observed_position_quantity),
        "observed_available_quantity": _decimal(value.observed_available_quantity),
        "reference_price": _decimal(value.reference_price),
        "limit_price": _decimal(value.limit_price),
        "max_slippage_bps": _decimal(value.max_slippage_bps),
        "order_type": value.order_type,
        "time_in_force": value.time_in_force,
        "prepared_at": value.prepared_at.astimezone(timezone.utc).isoformat(),
        "expires_at": value.expires_at.astimezone(timezone.utc).isoformat(),
        "risk_reducing": value.risk_reducing,
        "network_write_authorized": value.network_write_authorized,
        "retry_post": value.retry_post,
        "live_trading": value.live_trading,
    }
    if include_hash:
        payload["plan_hash"] = value.plan_hash
    return payload


def _payload_from_values(values: dict[str, object]) -> dict[str, object]:
    normalized = dict(values)
    normalized["mode"] = values["mode"].value  # type: ignore[union-attr]
    for key in (
        "quantity",
        "observed_position_quantity",
        "observed_available_quantity",
        "reference_price",
        "limit_price",
        "max_slippage_bps",
    ):
        normalized[key] = _decimal(values[key])  # type: ignore[arg-type]
    for key in ("prepared_at", "expires_at"):
        normalized[key] = values[key].astimezone(timezone.utc).isoformat()  # type: ignore[union-attr]
    return normalized


def _decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


__all__ = [
    "CLOSE_PLAN_TTL",
    "MAX_CLOSE_SLIPPAGE_BPS",
    "PaperCloseMode",
    "PaperClosePlanError",
    "PaperCryptoClosePlan",
    "prepare_crypto_close_plan",
]
