from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Mapping

from autotrade.domain import (
    MarketSnapshot,
    RiskDecision,
    RiskDecisionStatus,
    market_fingerprint,
    risk_decision_fingerprint,
)
from autotrade.product_profile import ProductCapabilities
from autotrade.brokers.alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from autotrade.brokers.alpaca_paper_crypto_market_data import AlpacaPaperCryptoMarketAttestation
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation


SCHEMA_VERSION = 1
DOCUMENT_TYPE = "R6_CRYPTO_PAPER_FIRST_CANARY_PREPARED_EVIDENCE"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class FirstCanaryPreparedEvidenceError(RuntimeError):
    pass


class FirstCanaryPreparedEvidenceIntegrityError(FirstCanaryPreparedEvidenceError):
    pass


@dataclass(frozen=True, slots=True)
class FirstCanaryPreparedEvidence:
    account: AlpacaPaperAccountAttestation
    asset: AlpacaPaperCryptoAssetAttestation
    product_profile: ProductCapabilities
    market: AlpacaPaperCryptoMarketAttestation
    risk_decision: RiskDecision

    def __post_init__(self) -> None:
        if self.asset.account_attestation_fingerprint != self.account.fingerprint:
            raise FirstCanaryPreparedEvidenceIntegrityError(
                "prepared asset is not bound to prepared account"
            )
        if self.asset.credential_reference != self.account.credential_reference:
            raise FirstCanaryPreparedEvidenceIntegrityError(
                "prepared asset credential provenance differs from account"
            )
        if self.product_profile.source_fingerprint != self.asset.fingerprint:
            raise FirstCanaryPreparedEvidenceIntegrityError(
                "prepared ProductCapabilities is not bound to prepared asset"
            )
        if self.market.market.symbol != self.asset.symbol:
            raise FirstCanaryPreparedEvidenceIntegrityError(
                "prepared market symbol differs from prepared asset"
            )
        if self.risk_decision.market_fingerprint != market_fingerprint(self.market.market):
            raise FirstCanaryPreparedEvidenceIntegrityError(
                "prepared RiskDecision is not bound to prepared market"
            )

    @property
    def fingerprint(self) -> str:
        return _hash(self.canonical_payload())

    def canonical_payload(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "document_type": DOCUMENT_TYPE,
            "account": _account_payload(self.account),
            "asset": _asset_payload(self.asset),
            "product_profile": _product_profile_payload(self.product_profile),
            "market": _market_payload(self.market),
            "risk_decision": _risk_decision_payload(self.risk_decision),
            "account_fingerprint": self.account.fingerprint,
            "asset_fingerprint": self.asset.fingerprint,
            "product_profile_fingerprint": self.product_profile.fingerprint,
            "market_attestation_fingerprint": self.market.fingerprint,
            "risk_decision_fingerprint": risk_decision_fingerprint(self.risk_decision),
            "credentials_persisted": False,
            "secret_persisted": False,
            "live_trading": "BLOCKED",
        }

    def document(self) -> dict[str, object]:
        payload = self.canonical_payload()
        payload["prepared_evidence_hash"] = _hash(payload)
        return payload

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> "FirstCanaryPreparedEvidence":
        if not isinstance(document, Mapping):
            raise FirstCanaryPreparedEvidenceIntegrityError(
                "prepared evidence root must be an object"
            )
        raw = dict(document)
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise FirstCanaryPreparedEvidenceIntegrityError(
                "prepared evidence schema version is invalid"
            )
        if raw.get("document_type") != DOCUMENT_TYPE:
            raise FirstCanaryPreparedEvidenceIntegrityError(
                "prepared evidence document type is invalid"
            )
        if raw.get("credentials_persisted") is not False:
            raise FirstCanaryPreparedEvidenceIntegrityError(
                "prepared evidence violates credential persistence policy"
            )
        if raw.get("secret_persisted") is not False:
            raise FirstCanaryPreparedEvidenceIntegrityError(
                "prepared evidence violates Secret persistence policy"
            )
        if raw.get("live_trading") != "BLOCKED":
            raise FirstCanaryPreparedEvidenceIntegrityError(
                "prepared evidence does not preserve LIVE deny"
            )
        supplied_hash = _required_hash(raw, "prepared_evidence_hash")
        material = {key: value for key, value in raw.items() if key != "prepared_evidence_hash"}
        if supplied_hash != _hash(material):
            raise FirstCanaryPreparedEvidenceIntegrityError(
                "prepared evidence document hash mismatch"
            )

        account = _account_from_payload(_mapping(raw, "account"))
        asset = _asset_from_payload(_mapping(raw, "asset"))
        market = _market_from_payload(_mapping(raw, "market"))
        product_profile = ProductCapabilities.crypto_alpaca_paper(
            source_fingerprint=asset.fingerprint,
            observed_at=asset.observed_at,
            fractionable=asset.fractionable,
            marginable=asset.marginable,
            shortable=asset.shortable,
        )
        risk_decision = _risk_decision_from_payload(_mapping(raw, "risk_decision"))
        value = cls(
            account=account,
            asset=asset,
            product_profile=product_profile,
            market=market,
            risk_decision=risk_decision,
        )
        expected = (
            ("account_fingerprint", value.account.fingerprint),
            ("asset_fingerprint", value.asset.fingerprint),
            ("product_profile_fingerprint", value.product_profile.fingerprint),
            ("market_attestation_fingerprint", value.market.fingerprint),
            ("risk_decision_fingerprint", risk_decision_fingerprint(value.risk_decision)),
        )
        for key, expected_value in expected:
            if raw.get(key) != expected_value:
                raise FirstCanaryPreparedEvidenceIntegrityError(
                    f"prepared evidence {key} mismatch"
                )
        supplied_profile = _mapping(raw, "product_profile")
        if supplied_profile != _product_profile_payload(value.product_profile):
            raise FirstCanaryPreparedEvidenceIntegrityError(
                "prepared ProductCapabilities payload is non-canonical"
            )
        return value


def _account_payload(value: AlpacaPaperAccountAttestation) -> dict[str, object]:
    return {
        "account_id": value.account_id,
        "account_reference": value.account_reference,
        "credential_reference": value.credential_reference,
        "status": value.status,
        "currency": value.currency,
        "buying_power": _decimal_text(value.buying_power),
        "portfolio_value": _decimal_text(value.portfolio_value),
        "shorting_enabled": value.shorting_enabled,
        "attested_at": _time_text(value.attested_at),
        "request_id": value.request_id,
        "source_host": value.source_host,
        "source_path": value.source_path,
    }


def _account_from_payload(raw: Mapping[str, object]) -> AlpacaPaperAccountAttestation:
    try:
        return AlpacaPaperAccountAttestation(
            account_id=_text(raw, "account_id"),
            account_reference=_required_hash(raw, "account_reference"),
            credential_reference=_required_hash(raw, "credential_reference"),
            status=_text(raw, "status"),
            currency=_text(raw, "currency"),
            buying_power=_decimal(raw, "buying_power"),
            portfolio_value=_decimal(raw, "portfolio_value"),
            shorting_enabled=_bool(raw, "shorting_enabled"),
            attested_at=_datetime(raw, "attested_at"),
            request_id=_text(raw, "request_id"),
            source_host=_text(raw, "source_host"),
            source_path=_text(raw, "source_path"),
        )
    except ValueError as exc:
        raise FirstCanaryPreparedEvidenceIntegrityError(
            "prepared account payload is invalid"
        ) from exc


def _asset_payload(value: AlpacaPaperCryptoAssetAttestation) -> dict[str, object]:
    return {
        "symbol": value.symbol,
        "asset_id": value.asset_id,
        "asset_class": value.asset_class,
        "exchange": value.exchange,
        "status": value.status,
        "tradable": value.tradable,
        "fractionable": value.fractionable,
        "marginable": value.marginable,
        "shortable": value.shortable,
        "min_order_size": _decimal_text(value.min_order_size),
        "min_trade_increment": _decimal_text(value.min_trade_increment),
        "price_increment": _decimal_text(value.price_increment),
        "account_attestation_fingerprint": value.account_attestation_fingerprint,
        "credential_reference": value.credential_reference,
        "observed_at": _time_text(value.observed_at),
        "request_id": value.request_id,
        "response_sha256": value.response_sha256,
        "source_path": value.source_path,
    }


def _asset_from_payload(raw: Mapping[str, object]) -> AlpacaPaperCryptoAssetAttestation:
    try:
        return AlpacaPaperCryptoAssetAttestation(
            symbol=_text(raw, "symbol"),
            asset_id=_text(raw, "asset_id"),
            asset_class=_text(raw, "asset_class"),
            exchange=_text(raw, "exchange"),
            status=_text(raw, "status"),
            tradable=_bool(raw, "tradable"),
            fractionable=_bool(raw, "fractionable"),
            marginable=_bool(raw, "marginable"),
            shortable=_bool(raw, "shortable"),
            min_order_size=_decimal(raw, "min_order_size"),
            min_trade_increment=_decimal(raw, "min_trade_increment"),
            price_increment=_decimal(raw, "price_increment"),
            account_attestation_fingerprint=_required_hash(
                raw, "account_attestation_fingerprint"
            ),
            credential_reference=_required_hash(raw, "credential_reference"),
            observed_at=_datetime(raw, "observed_at"),
            request_id=_text(raw, "request_id"),
            response_sha256=_required_hash(raw, "response_sha256"),
            source_path=_text(raw, "source_path"),
        )
    except ValueError as exc:
        raise FirstCanaryPreparedEvidenceIntegrityError(
            "prepared asset payload is invalid"
        ) from exc


def _product_profile_payload(value: ProductCapabilities) -> dict[str, object]:
    return {
        "asset_class": value.asset_class.value,
        "broker": value.broker,
        "venue": value.venue,
        "supported_order_types": sorted(item.value for item in value.supported_order_types),
        "supported_time_in_force": sorted(item.value for item in value.supported_time_in_force),
        "supports_native_bracket": value.supports_native_bracket,
        "supports_oco": value.supports_oco,
        "supports_trailing_stop": value.supports_trailing_stop,
        "fractionable": value.fractionable,
        "marginable": value.marginable,
        "shortable": value.shortable,
        "source": value.source,
        "source_fingerprint": value.source_fingerprint,
        "observed_at": _time_text(value.observed_at),
        "fingerprint": value.fingerprint,
        "contract_fingerprint": value.contract_fingerprint,
    }


def _market_payload(value: AlpacaPaperCryptoMarketAttestation) -> dict[str, object]:
    market = value.market
    return {
        "market": {
            "symbol": market.symbol,
            "bid": _decimal_text(market.bid),
            "ask": _decimal_text(market.ask),
            "last": _decimal_text(market.last),
            "observed_at": _time_text(market.observed_at),
        },
        "location": value.location,
        "quote_observed_at": _time_text(value.quote_observed_at),
        "trade_observed_at": _time_text(value.trade_observed_at),
        "received_at": _time_text(value.received_at),
        "quote_response_sha256": value.quote_response_sha256,
        "trade_response_sha256": value.trade_response_sha256,
    }


def _market_from_payload(raw: Mapping[str, object]) -> AlpacaPaperCryptoMarketAttestation:
    market_raw = _mapping(raw, "market")
    try:
        market = MarketSnapshot(
            symbol=_text(market_raw, "symbol"),
            bid=_decimal(market_raw, "bid"),
            ask=_decimal(market_raw, "ask"),
            last=_decimal(market_raw, "last"),
            observed_at=_datetime(market_raw, "observed_at"),
        )
        return AlpacaPaperCryptoMarketAttestation(
            market=market,
            location=_text(raw, "location"),
            quote_observed_at=_datetime(raw, "quote_observed_at"),
            trade_observed_at=_datetime(raw, "trade_observed_at"),
            received_at=_datetime(raw, "received_at"),
            quote_response_sha256=_required_hash(raw, "quote_response_sha256"),
            trade_response_sha256=_required_hash(raw, "trade_response_sha256"),
        )
    except ValueError as exc:
        raise FirstCanaryPreparedEvidenceIntegrityError(
            "prepared market payload is invalid"
        ) from exc


def _risk_decision_payload(value: RiskDecision) -> dict[str, object]:
    return {
        "decision_id": value.decision_id,
        "intent_id": value.intent_id,
        "status": value.status.value,
        "reason_code": value.reason_code,
        "reason_detail": value.reason_detail,
        "evaluated_at": _time_text(value.evaluated_at),
        "valid_until": _time_text(value.valid_until),
        "intent_fingerprint": value.intent_fingerprint,
        "market_fingerprint": value.market_fingerprint,
        "approved_notional": (
            None if value.approved_notional is None else _decimal_text(value.approved_notional)
        ),
        "safety_state_version": value.safety_state_version,
        "limits_version": value.limits_version,
    }


def _risk_decision_from_payload(raw: Mapping[str, object]) -> RiskDecision:
    approved_raw = raw.get("approved_notional")
    approved = None if approved_raw is None else _decimal_value(approved_raw, "approved_notional")
    try:
        return RiskDecision(
            decision_id=_text(raw, "decision_id"),
            intent_id=_text(raw, "intent_id"),
            status=RiskDecisionStatus(_text(raw, "status")),
            reason_code=_text(raw, "reason_code"),
            reason_detail=_text(raw, "reason_detail"),
            evaluated_at=_datetime(raw, "evaluated_at"),
            valid_until=_datetime(raw, "valid_until"),
            intent_fingerprint=_required_hash(raw, "intent_fingerprint"),
            market_fingerprint=_required_hash(raw, "market_fingerprint"),
            approved_notional=approved,
            safety_state_version=_integer(raw, "safety_state_version"),
            limits_version=_text(raw, "limits_version"),
        )
    except ValueError as exc:
        raise FirstCanaryPreparedEvidenceIntegrityError(
            "prepared RiskDecision payload is invalid"
        ) from exc


def _mapping(raw: Mapping[str, object], key: str) -> dict[str, object]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise FirstCanaryPreparedEvidenceIntegrityError(f"{key} must be an object")
    return dict(value)


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise FirstCanaryPreparedEvidenceIntegrityError(f"{key} is missing or invalid")
    return value


def _required_hash(raw: Mapping[str, object], key: str) -> str:
    value = _text(raw, key)
    if not _HASH_RE.fullmatch(value):
        raise FirstCanaryPreparedEvidenceIntegrityError(f"{key} must be lowercase SHA-256")
    return value


def _bool(raw: Mapping[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise FirstCanaryPreparedEvidenceIntegrityError(f"{key} must be boolean")
    return value


def _integer(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise FirstCanaryPreparedEvidenceIntegrityError(f"{key} must be integer")
    return value


def _decimal(raw: Mapping[str, object], key: str) -> Decimal:
    return _decimal_value(raw.get(key), key)


def _decimal_value(value: object, key: str) -> Decimal:
    if not isinstance(value, str):
        raise FirstCanaryPreparedEvidenceIntegrityError(f"{key} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise FirstCanaryPreparedEvidenceIntegrityError(f"{key} is invalid") from exc
    if not parsed.is_finite():
        raise FirstCanaryPreparedEvidenceIntegrityError(f"{key} must be finite")
    return parsed


def _datetime(raw: Mapping[str, object], key: str) -> datetime:
    value = _text(raw, key)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FirstCanaryPreparedEvidenceIntegrityError(f"{key} is invalid datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FirstCanaryPreparedEvidenceIntegrityError(f"{key} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _time_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FirstCanaryPreparedEvidenceIntegrityError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _decimal_text(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise FirstCanaryPreparedEvidenceIntegrityError("decimal must be finite")
    return format(value, "f")


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


__all__ = [
    "DOCUMENT_TYPE",
    "FirstCanaryPreparedEvidence",
    "FirstCanaryPreparedEvidenceError",
    "FirstCanaryPreparedEvidenceIntegrityError",
    "SCHEMA_VERSION",
]
