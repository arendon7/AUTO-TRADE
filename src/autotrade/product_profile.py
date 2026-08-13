from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
import re


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AssetClass(StrEnum):
    US_EQUITY = "US_EQUITY"
    CRYPTO = "CRYPTO"


class MarketHoursModel(StrEnum):
    SESSION_CLOCKED = "SESSION_CLOCKED"
    CONTINUOUS_24_7 = "CONTINUOUS_24_7"


class ProtectionModel(StrEnum):
    EQUITY_BRACKET = "EQUITY_BRACKET"
    CRYPTO_STOP_LIMIT = "CRYPTO_STOP_LIMIT"


class BrokerOrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"
    OPG = "opg"
    CLS = "cls"


class ProductCapabilityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProductCapabilities:
    """Broker-observed capability envelope for one asset class/venue.

    `fingerprint` binds the exact observation and its evidence source.
    `contract_fingerprint` binds only the stable product semantics so a later
    fresh observation can prove "same product contract, fresher evidence".
    Neither fingerprint grants capital authority by itself.
    """

    asset_class: AssetClass
    venue: str
    market_hours_model: MarketHoursModel
    allowed_order_types: frozenset[BrokerOrderType]
    allowed_time_in_force: frozenset[TimeInForce]
    fractionable: bool
    marginable: bool
    shortable: bool
    protection_model: ProtectionModel
    source: str
    source_fingerprint: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.venue.strip():
            raise ProductCapabilityError("venue is required")
        if not self.source.strip():
            raise ProductCapabilityError("source is required")
        if not _SHA256_RE.fullmatch(self.source_fingerprint):
            raise ProductCapabilityError("source_fingerprint must be lowercase sha256")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ProductCapabilityError("observed_at must be timezone-aware")
        if not self.allowed_order_types:
            raise ProductCapabilityError("allowed_order_types may not be empty")
        if not self.allowed_time_in_force:
            raise ProductCapabilityError("allowed_time_in_force may not be empty")

        if self.asset_class is AssetClass.CRYPTO:
            crypto_orders = {
                BrokerOrderType.MARKET,
                BrokerOrderType.LIMIT,
                BrokerOrderType.STOP_LIMIT,
            }
            crypto_tif = {TimeInForce.GTC, TimeInForce.IOC}
            if self.market_hours_model is not MarketHoursModel.CONTINUOUS_24_7:
                raise ProductCapabilityError("crypto must use CONTINUOUS_24_7 market-hours model")
            if not self.fractionable:
                raise ProductCapabilityError("crypto profile must be fractionable")
            if self.marginable or self.shortable:
                raise ProductCapabilityError("crypto profile may not claim margin or short authority")
            if not self.allowed_order_types.issubset(crypto_orders):
                raise ProductCapabilityError("crypto profile contains unsupported order type")
            if not self.allowed_time_in_force.issubset(crypto_tif):
                raise ProductCapabilityError("crypto profile contains unsupported time-in-force")
            if self.protection_model is not ProtectionModel.CRYPTO_STOP_LIMIT:
                raise ProductCapabilityError("crypto may not reuse the equity bracket protection model")

        if self.asset_class is AssetClass.US_EQUITY:
            if self.market_hours_model is not MarketHoursModel.SESSION_CLOCKED:
                raise ProductCapabilityError("US equity must use SESSION_CLOCKED market-hours model")
            if self.protection_model is not ProtectionModel.EQUITY_BRACKET:
                raise ProductCapabilityError("R6 US-equity canary requires the certified bracket model")

    def _contract_payload(self) -> dict[str, object]:
        return {
            "asset_class": self.asset_class.value,
            "venue": self.venue,
            "market_hours_model": self.market_hours_model.value,
            "allowed_order_types": sorted(item.value for item in self.allowed_order_types),
            "allowed_time_in_force": sorted(item.value for item in self.allowed_time_in_force),
            "fractionable": self.fractionable,
            "marginable": self.marginable,
            "shortable": self.shortable,
            "protection_model": self.protection_model.value,
            "source": self.source,
        }

    @property
    def contract_fingerprint(self) -> str:
        encoded = json.dumps(
            self._contract_payload(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    @property
    def fingerprint(self) -> str:
        payload = {
            **self._contract_payload(),
            "source_fingerprint": self.source_fingerprint,
            "observed_at": self.observed_at.isoformat(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()

    def require_order(self, *, order_type: BrokerOrderType, time_in_force: TimeInForce) -> None:
        if order_type not in self.allowed_order_types:
            raise ProductCapabilityError(
                f"{self.asset_class.value} does not authorize broker order type {order_type.value}"
            )
        if time_in_force not in self.allowed_time_in_force:
            raise ProductCapabilityError(
                f"{self.asset_class.value} does not authorize time-in-force {time_in_force.value}"
            )

    def require_opening_short(self, *, opening_short: bool) -> None:
        if opening_short and not self.shortable:
            raise ProductCapabilityError(f"{self.asset_class.value} does not authorize opening short exposure")

    def require_margin(self, *, uses_margin: bool) -> None:
        if uses_margin and not self.marginable:
            raise ProductCapabilityError(f"{self.asset_class.value} does not authorize margin exposure")

    @classmethod
    def crypto_alpaca_paper(
        cls,
        *,
        source_fingerprint: str,
        observed_at: datetime,
        fractionable: bool,
        marginable: bool,
        shortable: bool,
    ) -> "ProductCapabilities":
        return cls(
            asset_class=AssetClass.CRYPTO,
            venue="ALPACA_PAPER_CRYPTO",
            market_hours_model=MarketHoursModel.CONTINUOUS_24_7,
            allowed_order_types=frozenset(
                {BrokerOrderType.MARKET, BrokerOrderType.LIMIT, BrokerOrderType.STOP_LIMIT}
            ),
            allowed_time_in_force=frozenset({TimeInForce.GTC, TimeInForce.IOC}),
            fractionable=fractionable,
            marginable=marginable,
            shortable=shortable,
            protection_model=ProtectionModel.CRYPTO_STOP_LIMIT,
            source="ALPACA_PAPER_ASSET_ATTESTATION",
            source_fingerprint=source_fingerprint,
            observed_at=observed_at,
        )

    @classmethod
    def us_equity_alpaca_paper(
        cls,
        *,
        source_fingerprint: str,
        observed_at: datetime,
        fractionable: bool,
        marginable: bool,
        shortable: bool,
    ) -> "ProductCapabilities":
        return cls(
            asset_class=AssetClass.US_EQUITY,
            venue="ALPACA_PAPER_US_EQUITY",
            market_hours_model=MarketHoursModel.SESSION_CLOCKED,
            allowed_order_types=frozenset(
                {
                    BrokerOrderType.MARKET,
                    BrokerOrderType.LIMIT,
                    BrokerOrderType.STOP,
                    BrokerOrderType.STOP_LIMIT,
                    BrokerOrderType.TRAILING_STOP,
                }
            ),
            allowed_time_in_force=frozenset(
                {TimeInForce.DAY, TimeInForce.GTC, TimeInForce.IOC, TimeInForce.FOK, TimeInForce.OPG, TimeInForce.CLS}
            ),
            fractionable=fractionable,
            marginable=marginable,
            shortable=shortable,
            protection_model=ProtectionModel.EQUITY_BRACKET,
            source="ALPACA_PAPER_ASSET_ATTESTATION",
            source_fingerprint=source_fingerprint,
            observed_at=observed_at,
        )
