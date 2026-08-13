from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.product_profile import (
    AssetClass,
    BrokerOrderType,
    ProductCapabilities,
    ProtectionModel,
    TimeInForce,
)

from .alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation, normalize_crypto_pair


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CLIENT_ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class CryptoOrderContractError(ValueError):
    pass


class CryptoOrderRole(StrEnum):
    ENTRY = "ENTRY"
    PROTECTION = "PROTECTION"


class CryptoOrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class AlpacaPaperCryptoOrderRequest:
    """Immutable, product-bound Alpaca crypto order payload. No network authority."""

    role: CryptoOrderRole
    symbol: str
    side: CryptoOrderSide
    quantity: Decimal
    order_type: BrokerOrderType
    time_in_force: TimeInForce
    client_order_id: str
    product_profile_fingerprint: str
    asset_attestation_fingerprint: str
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None

    def __post_init__(self) -> None:
        canonical = normalize_crypto_pair(self.symbol)
        if canonical != self.symbol:
            raise CryptoOrderContractError("crypto order symbol must be canonical BASE/QUOTE")
        if not isinstance(self.quantity, Decimal) or not self.quantity.is_finite() or self.quantity <= 0:
            raise CryptoOrderContractError("crypto order quantity must be finite and positive")
        if not _CLIENT_ORDER_ID_RE.fullmatch(self.client_order_id):
            raise CryptoOrderContractError("crypto client_order_id is invalid")
        for label, value in (
            ("product_profile_fingerprint", self.product_profile_fingerprint),
            ("asset_attestation_fingerprint", self.asset_attestation_fingerprint),
        ):
            if not _HASH_RE.fullmatch(value):
                raise CryptoOrderContractError(f"{label} must be lowercase SHA-256")

        if self.role is CryptoOrderRole.ENTRY:
            if self.side is not CryptoOrderSide.BUY:
                raise CryptoOrderContractError("R6 first-canary crypto entry must be BUY")
            if self.order_type not in {BrokerOrderType.MARKET, BrokerOrderType.LIMIT}:
                raise CryptoOrderContractError("R6 crypto entry supports market or limit only")
            if self.stop_price is not None:
                raise CryptoOrderContractError("entry order may not contain stop_price")
        elif self.role is CryptoOrderRole.PROTECTION:
            if self.side is not CryptoOrderSide.SELL:
                raise CryptoOrderContractError("long crypto protection must be SELL")
            if self.order_type is not BrokerOrderType.STOP_LIMIT:
                raise CryptoOrderContractError("crypto protection must be STOP_LIMIT")
            if self.stop_price is None or self.limit_price is None:
                raise CryptoOrderContractError("crypto protective stop-limit needs stop and limit prices")
            if self.limit_price > self.stop_price:
                raise CryptoOrderContractError("long sell protection requires limit_price <= stop_price")

        if self.order_type is BrokerOrderType.MARKET:
            if self.limit_price is not None or self.stop_price is not None:
                raise CryptoOrderContractError("market order may not contain limit/stop price")
        elif self.order_type is BrokerOrderType.LIMIT:
            _positive_price(self.limit_price, "limit_price")
            if self.stop_price is not None:
                raise CryptoOrderContractError("limit order may not contain stop_price")
        elif self.order_type is BrokerOrderType.STOP_LIMIT:
            _positive_price(self.limit_price, "limit_price")
            _positive_price(self.stop_price, "stop_price")
        else:
            raise CryptoOrderContractError("unsupported crypto broker order type")

    @property
    def payload_hash(self) -> str:
        return sha256(_canonical_json(self.to_payload()).encode("utf-8")).hexdigest()

    @property
    def fingerprint(self) -> str:
        payload = {
            "role": self.role.value,
            "broker_payload": self.to_payload(),
            "product_profile_fingerprint": self.product_profile_fingerprint,
            "asset_attestation_fingerprint": self.asset_attestation_fingerprint,
        }
        return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "symbol": self.symbol,
            "qty": _decimal_text(self.quantity),
            "side": self.side.value,
            "type": self.order_type.value,
            "time_in_force": self.time_in_force.value,
            "client_order_id": self.client_order_id,
        }
        if self.limit_price is not None:
            payload["limit_price"] = _decimal_text(self.limit_price)
        if self.stop_price is not None:
            payload["stop_price"] = _decimal_text(self.stop_price)
        return payload


def build_crypto_entry_order(
    *,
    symbol: str,
    quantity: Decimal,
    order_type: BrokerOrderType,
    time_in_force: TimeInForce,
    client_order_id: str,
    product_profile: ProductCapabilities,
    asset_attestation: AlpacaPaperCryptoAssetAttestation,
    limit_price: Decimal | None = None,
) -> AlpacaPaperCryptoOrderRequest:
    canonical = normalize_crypto_pair(symbol)
    _bind_product(profile=product_profile, asset=asset_attestation, symbol=canonical)
    product_profile.require_order(order_type=order_type, time_in_force=time_in_force)
    product_profile.require_margin(uses_margin=False)
    product_profile.require_opening_short(opening_short=False)
    normalized_quantity = _floor_to_increment(quantity, asset_attestation.min_trade_increment)
    if normalized_quantity < asset_attestation.min_order_size:
        raise CryptoOrderContractError("entry quantity is below broker minimum after normalization")
    normalized_limit = None
    if limit_price is not None:
        normalized_limit = _ceil_to_increment(limit_price, asset_attestation.price_increment)
    request = AlpacaPaperCryptoOrderRequest(
        role=CryptoOrderRole.ENTRY,
        symbol=canonical,
        side=CryptoOrderSide.BUY,
        quantity=normalized_quantity,
        order_type=order_type,
        time_in_force=time_in_force,
        client_order_id=client_order_id,
        product_profile_fingerprint=product_profile.fingerprint,
        asset_attestation_fingerprint=asset_attestation.fingerprint,
        limit_price=normalized_limit,
    )
    return request


def build_crypto_long_protection_order(
    *,
    symbol: str,
    confirmed_entry_filled_quantity: Decimal,
    confirmed_net_long_quantity: Decimal,
    requested_protection_quantity: Decimal,
    stop_price: Decimal,
    limit_price: Decimal,
    client_order_id: str,
    product_profile: ProductCapabilities,
    asset_attestation: AlpacaPaperCryptoAssetAttestation,
) -> AlpacaPaperCryptoOrderRequest:
    canonical = normalize_crypto_pair(symbol)
    _bind_product(profile=product_profile, asset=asset_attestation, symbol=canonical)
    if product_profile.protection_model is not ProtectionModel.CRYPTO_STOP_LIMIT:
        raise CryptoOrderContractError("crypto protection model mismatch")
    product_profile.require_order(order_type=BrokerOrderType.STOP_LIMIT, time_in_force=TimeInForce.GTC)
    if not _finite_positive(confirmed_entry_filled_quantity):
        raise CryptoOrderContractError("confirmed entry filled quantity must be positive")
    if not _finite_positive(confirmed_net_long_quantity):
        raise CryptoOrderContractError("confirmed net long quantity must be positive")
    if requested_protection_quantity > confirmed_entry_filled_quantity:
        raise CryptoOrderContractError("protection may not exceed confirmed entry filled quantity")
    if requested_protection_quantity > confirmed_net_long_quantity:
        raise CryptoOrderContractError("protection may not exceed confirmed net long position")
    normalized_quantity = _floor_to_increment(
        requested_protection_quantity,
        asset_attestation.min_trade_increment,
    )
    if normalized_quantity <= 0:
        raise CryptoOrderContractError("protective quantity rounds to zero")
    if normalized_quantity > confirmed_net_long_quantity:
        raise CryptoOrderContractError("normalized protection exceeds confirmed position")

    normalized_stop = _floor_to_increment(stop_price, asset_attestation.price_increment)
    normalized_limit = _floor_to_increment(limit_price, asset_attestation.price_increment)
    if normalized_stop <= 0 or normalized_limit <= 0:
        raise CryptoOrderContractError("protective prices must remain positive after normalization")
    if normalized_limit > normalized_stop:
        raise CryptoOrderContractError("normalized long protection requires limit <= stop")

    return AlpacaPaperCryptoOrderRequest(
        role=CryptoOrderRole.PROTECTION,
        symbol=canonical,
        side=CryptoOrderSide.SELL,
        quantity=normalized_quantity,
        order_type=BrokerOrderType.STOP_LIMIT,
        time_in_force=TimeInForce.GTC,
        client_order_id=client_order_id,
        product_profile_fingerprint=product_profile.fingerprint,
        asset_attestation_fingerprint=asset_attestation.fingerprint,
        limit_price=normalized_limit,
        stop_price=normalized_stop,
    )


def deterministic_crypto_client_order_id(*, lifecycle_id: str, role: CryptoOrderRole) -> str:
    if not isinstance(lifecycle_id, str) or not lifecycle_id.strip():
        raise CryptoOrderContractError("lifecycle_id is required")
    digest = sha256(f"AUTO-TRADE:R6:CRYPTO:{lifecycle_id.strip()}:{role.value}".encode("utf-8")).hexdigest()
    value = f"atr6c-{role.value.lower()}-{digest[:40]}"
    if not _CLIENT_ORDER_ID_RE.fullmatch(value):
        raise AssertionError("deterministic crypto client_order_id construction is invalid")
    return value


def _bind_product(
    *,
    profile: ProductCapabilities,
    asset: AlpacaPaperCryptoAssetAttestation,
    symbol: str,
) -> None:
    if profile.asset_class is not AssetClass.CRYPTO:
        raise CryptoOrderContractError("crypto order requires CRYPTO ProductCapabilities")
    if profile.source_fingerprint != asset.fingerprint:
        raise CryptoOrderContractError("ProductCapabilities is not bound to exact asset attestation")
    if asset.symbol != symbol:
        raise CryptoOrderContractError("crypto asset attestation symbol mismatch")
    if profile.fractionable != asset.fractionable:
        raise CryptoOrderContractError("product/asset fractional capability mismatch")
    if profile.marginable != asset.marginable or profile.shortable != asset.shortable:
        raise CryptoOrderContractError("product/asset leverage capability mismatch")


def _floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    if not _finite_positive(value) or not _finite_positive(increment):
        raise CryptoOrderContractError("value and increment must be finite positive decimals")
    units = (value / increment).to_integral_value(rounding=ROUND_FLOOR)
    return units * increment


def _ceil_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    if not _finite_positive(value) or not _finite_positive(increment):
        raise CryptoOrderContractError("value and increment must be finite positive decimals")
    units = (value / increment).to_integral_value(rounding=ROUND_CEILING)
    return units * increment


def _finite_positive(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _positive_price(value: Decimal | None, label: str) -> None:
    if value is None or not value.is_finite() or value <= 0:
        raise CryptoOrderContractError(f"{label} must be finite and positive")


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


__all__ = [
    "AlpacaPaperCryptoOrderRequest",
    "CryptoOrderContractError",
    "CryptoOrderRole",
    "CryptoOrderSide",
    "build_crypto_entry_order",
    "build_crypto_long_protection_order",
    "deterministic_crypto_client_order_id",
]
