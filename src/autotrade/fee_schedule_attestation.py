from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re


FEE_SCHEDULE_ATTESTATION_VERSION = "W82_ALPACA_CRYPTO_FEE_SCHEDULE_V1"
ALPACA_CRYPTO_FEE_SOURCE_URL = "https://docs.alpaca.markets/us/docs/crypto-fees"
ALPACA_CRYPTO_FEE_SOURCE_CHECKED_AT = datetime(
    2026, 8, 24, 1, 55, tzinfo=timezone.utc
)
ALPACA_CRYPTO_TIER1_MAKER_BPS = Decimal("15")
ALPACA_CRYPTO_TIER1_TAKER_BPS = Decimal("25")
ALPACA_CRYPTO_CONSERVATIVE_FLOOR_BPS = ALPACA_CRYPTO_TIER1_TAKER_BPS
ALPACA_CRYPTO_LIQUIDITY_ASSUMPTION = "WORST_CASE_TAKER"
ALPACA_CRYPTO_VOLUME_ASSUMPTION = "UNKNOWN_OR_TIER1"
ALPACA_CRYPTO_FEE_CHARGE_BASIS = "CREDITED_ASSET_OR_FIAT"
ALPACA_CRYPTO_POSTING_SEMANTICS = "END_OF_DAY_MAY_BE_DELAYED"
MAX_FEE_SCHEDULE_ATTESTATION_AGE = timedelta(days=30)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class FeeScheduleAttestationError(RuntimeError):
    pass


class FeeScheduleAttestationIntegrityError(FeeScheduleAttestationError):
    pass


@dataclass(frozen=True, slots=True)
class FeeScheduleAttestation:
    attestation_id: str
    version: str
    source_url: str
    source_checked_at: datetime
    product_id: str
    asset_class: str
    venue: str
    symbol: str
    liquidity_assumption: str
    volume_tier_assumption: str
    maker_fee_bps: Decimal
    taker_fee_bps: Decimal
    required_fee_floor_bps: Decimal
    fee_charge_basis: str
    posting_semantics: str
    broker_authoritative_activity_proven: bool
    external_execution_authorized: bool
    capital_authority: str
    live_trading: str
    attestation_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("attestation_id", self.attestation_id),
            ("product_id", self.product_id),
            ("asset_class", self.asset_class),
            ("venue", self.venue),
        ):
            _require_id(value, label)
        if self.version != FEE_SCHEDULE_ATTESTATION_VERSION:
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule attestation version is not canonical W82"
            )
        if self.source_url != ALPACA_CRYPTO_FEE_SOURCE_URL:
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule attestation source is not canonical Alpaca crypto documentation"
            )
        _require_aware(self.source_checked_at, "source_checked_at")
        if _utc(self.source_checked_at) != _utc(ALPACA_CRYPTO_FEE_SOURCE_CHECKED_AT):
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule source verification timestamp is not canonical W82"
            )
        if self.asset_class != "crypto":
            raise FeeScheduleAttestationIntegrityError(
                "Alpaca crypto fee attestation requires crypto asset class"
            )
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise FeeScheduleAttestationIntegrityError("symbol is required")
        if self.liquidity_assumption != ALPACA_CRYPTO_LIQUIDITY_ASSUMPTION:
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule liquidity assumption is not conservative W82"
            )
        if self.volume_tier_assumption != ALPACA_CRYPTO_VOLUME_ASSUMPTION:
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule volume assumption is not conservative W82"
            )
        for label, value in (
            ("maker_fee_bps", self.maker_fee_bps),
            ("taker_fee_bps", self.taker_fee_bps),
            ("required_fee_floor_bps", self.required_fee_floor_bps),
        ):
            _require_non_negative_decimal(value, label)
        if self.maker_fee_bps != ALPACA_CRYPTO_TIER1_MAKER_BPS:
            raise FeeScheduleAttestationIntegrityError(
                "canonical Alpaca crypto maker fee must be 15 bps"
            )
        if self.taker_fee_bps != ALPACA_CRYPTO_TIER1_TAKER_BPS:
            raise FeeScheduleAttestationIntegrityError(
                "canonical Alpaca crypto taker fee must be 25 bps"
            )
        if self.required_fee_floor_bps != ALPACA_CRYPTO_CONSERVATIVE_FLOOR_BPS:
            raise FeeScheduleAttestationIntegrityError(
                "canonical Alpaca crypto conservative fee floor must be 25 bps"
            )
        if self.required_fee_floor_bps < max(
            self.maker_fee_bps, self.taker_fee_bps
        ):
            raise FeeScheduleAttestationIntegrityError(
                "required fee floor may not undercut documented worst-case tier fee"
            )
        if self.fee_charge_basis != ALPACA_CRYPTO_FEE_CHARGE_BASIS:
            raise FeeScheduleAttestationIntegrityError(
                "fee charge basis must match credited-asset Alpaca semantics"
            )
        if self.posting_semantics != ALPACA_CRYPTO_POSTING_SEMANTICS:
            raise FeeScheduleAttestationIntegrityError(
                "fee posting semantics must preserve delayed/EOD publication"
            )
        if self.broker_authoritative_activity_proven is not False:
            raise FeeScheduleAttestationIntegrityError(
                "documentation attestation is not broker-observed fee activity"
            )
        if self.external_execution_authorized is not False:
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule attestation may not authorize execution"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule attestation may not grant capital or LIVE authority"
            )
        _require_hash(self.attestation_hash, "attestation_hash")
        if self.attestation_hash != _hash(_payload(self, include_hash=False)):
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule attestation hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)

    def validate_for(
        self,
        *,
        product_id: str,
        asset_class: str,
        venue: str,
        symbol: str,
        at: datetime,
    ) -> None:
        _require_aware(at, "at")
        if self.product_id != product_id:
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule attestation product mismatch"
            )
        if self.asset_class != asset_class:
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule attestation asset-class mismatch"
            )
        if self.venue != venue:
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule attestation venue mismatch"
            )
        if self.symbol != symbol:
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule attestation symbol mismatch"
            )
        if _utc(at) < _utc(self.source_checked_at):
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule resolution may not predate source verification"
            )
        if _utc(at) - _utc(self.source_checked_at) > MAX_FEE_SCHEDULE_ATTESTATION_AGE:
            raise FeeScheduleAttestationIntegrityError(
                "fee schedule attestation is stale and must be re-verified"
            )


def build_alpaca_crypto_worst_case_fee_attestation(
    *,
    attestation_id: str,
    product_id: str,
    venue: str,
    symbol: str,
) -> FeeScheduleAttestation:
    """Build the fixed conservative Alpaca crypto fee baseline used by W82.

    The baseline deliberately assumes no favorable 30-day volume tier and no
    maker guarantee. A lower fee floor requires a future separately certified
    evidence path; callers cannot lower the canonical values or refresh the
    documentation timestamp without versioning this source snapshot.
    """

    values = {
        "attestation_id": attestation_id,
        "version": FEE_SCHEDULE_ATTESTATION_VERSION,
        "source_url": ALPACA_CRYPTO_FEE_SOURCE_URL,
        "source_checked_at": ALPACA_CRYPTO_FEE_SOURCE_CHECKED_AT,
        "product_id": product_id,
        "asset_class": "crypto",
        "venue": venue,
        "symbol": symbol,
        "liquidity_assumption": ALPACA_CRYPTO_LIQUIDITY_ASSUMPTION,
        "volume_tier_assumption": ALPACA_CRYPTO_VOLUME_ASSUMPTION,
        "maker_fee_bps": ALPACA_CRYPTO_TIER1_MAKER_BPS,
        "taker_fee_bps": ALPACA_CRYPTO_TIER1_TAKER_BPS,
        "required_fee_floor_bps": ALPACA_CRYPTO_CONSERVATIVE_FLOOR_BPS,
        "fee_charge_basis": ALPACA_CRYPTO_FEE_CHARGE_BASIS,
        "posting_semantics": ALPACA_CRYPTO_POSTING_SEMANTICS,
        "broker_authoritative_activity_proven": False,
        "external_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return FeeScheduleAttestation(
        **values,
        attestation_hash=_hash(_payload_from_values(values)),
    )


def _payload(
    value: FeeScheduleAttestation, *, include_hash: bool
) -> dict[str, object]:
    payload = _payload_from_values(
        {
            "attestation_id": value.attestation_id,
            "version": value.version,
            "source_url": value.source_url,
            "source_checked_at": value.source_checked_at,
            "product_id": value.product_id,
            "asset_class": value.asset_class,
            "venue": value.venue,
            "symbol": value.symbol,
            "liquidity_assumption": value.liquidity_assumption,
            "volume_tier_assumption": value.volume_tier_assumption,
            "maker_fee_bps": value.maker_fee_bps,
            "taker_fee_bps": value.taker_fee_bps,
            "required_fee_floor_bps": value.required_fee_floor_bps,
            "fee_charge_basis": value.fee_charge_basis,
            "posting_semantics": value.posting_semantics,
            "broker_authoritative_activity_proven": value.broker_authoritative_activity_proven,
            "external_execution_authorized": value.external_execution_authorized,
            "capital_authority": value.capital_authority,
            "live_trading": value.live_trading,
        }
    )
    if include_hash:
        payload["attestation_hash"] = value.attestation_hash
    return payload


def _payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["source_checked_at"] = _utc_iso(payload["source_checked_at"])
    for key in (
        "maker_fee_bps",
        "taker_fee_bps",
        "required_fee_floor_bps",
    ):
        payload[key] = _decimal(payload[key])
    return payload


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise FeeScheduleAttestationIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise FeeScheduleAttestationIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise FeeScheduleAttestationIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _require_non_negative_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise FeeScheduleAttestationIntegrityError(
            f"{label} must be finite Decimal >= 0"
        )


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


def _hash(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "ALPACA_CRYPTO_CONSERVATIVE_FLOOR_BPS",
    "ALPACA_CRYPTO_FEE_CHARGE_BASIS",
    "ALPACA_CRYPTO_FEE_SOURCE_CHECKED_AT",
    "ALPACA_CRYPTO_FEE_SOURCE_URL",
    "ALPACA_CRYPTO_LIQUIDITY_ASSUMPTION",
    "ALPACA_CRYPTO_POSTING_SEMANTICS",
    "ALPACA_CRYPTO_TIER1_MAKER_BPS",
    "ALPACA_CRYPTO_TIER1_TAKER_BPS",
    "ALPACA_CRYPTO_VOLUME_ASSUMPTION",
    "FEE_SCHEDULE_ATTESTATION_VERSION",
    "FeeScheduleAttestation",
    "FeeScheduleAttestationError",
    "FeeScheduleAttestationIntegrityError",
    "MAX_FEE_SCHEDULE_ATTESTATION_AGE",
    "build_alpaca_crypto_worst_case_fee_attestation",
]
