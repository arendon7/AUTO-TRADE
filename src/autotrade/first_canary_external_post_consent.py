from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Mapping

from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    FirstCanaryAttemptWorkspace,
)
from autotrade.brokers.alpaca_paper_crypto_writer import CRYPTO_ORDERS_PATH
from autotrade.brokers.alpaca_paper_gateway import ALPACA_PAPER_TRADING_HOST


CONSENT_FILENAME = "external_post_consent.json"
CONSENT_TTL = timedelta(seconds=10)
DOCUMENT_TYPE = "R6_CRYPTO_PAPER_FIRST_CANARY_EXTERNAL_POST_CONSENT"
MIN_NOTIONAL = Decimal("1")
MAX_NOTIONAL = Decimal("5")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class FirstCanaryExternalPostConsentError(RuntimeError):
    pass


class FirstCanaryExternalPostConsentBlocked(FirstCanaryExternalPostConsentError):
    pass


@dataclass(frozen=True, slots=True)
class FirstCanaryExternalPostConsent:
    attempt_id: str
    package_hash: str
    preparation_hash: str
    prepared_evidence_hash: str
    client_order_id: str
    symbol: str
    notional: Decimal
    source_host: str
    source_path: str
    consented_at: datetime
    expires_at: datetime
    receipt_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("package_hash", self.package_hash),
            ("preparation_hash", self.preparation_hash),
            ("prepared_evidence_hash", self.prepared_evidence_hash),
            ("receipt_hash", self.receipt_hash),
        ):
            if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                raise ValueError(f"{label} must be lowercase SHA-256")
        if self.symbol != "BTC/USD":
            raise ValueError("first-canary external POST consent is BTC/USD only")
        if not isinstance(self.notional, Decimal) or not self.notional.is_finite():
            raise ValueError("first-canary consent notional must be finite Decimal")
        if not MIN_NOTIONAL <= self.notional <= MAX_NOTIONAL:
            raise ValueError(
                f"first-canary consent notional must remain within USD {MIN_NOTIONAL}-{MAX_NOTIONAL}"
            )
        if self.source_host != ALPACA_PAPER_TRADING_HOST:
            raise ValueError("first-canary consent host must be exact Alpaca PAPER host")
        if self.source_path != CRYPTO_ORDERS_PATH:
            raise ValueError("first-canary consent path must be exact crypto order path")
        _aware(self.consented_at, "consented_at")
        _aware(self.expires_at, "expires_at")
        if self.expires_at <= self.consented_at:
            raise ValueError("first-canary consent expiry must follow consent time")
        if self.expires_at - self.consented_at > CONSENT_TTL:
            raise ValueError("first-canary consent exceeds ten-second one-shot TTL")
        if self.receipt_hash != _hash(_payload(self, include_hash=False)):
            raise ValueError("first-canary external POST consent hash mismatch")

    @property
    def challenge(self) -> str:
        return external_post_challenge(
            attempt_id=self.attempt_id,
            client_order_id=self.client_order_id,
            notional=self.notional,
        )

    def document(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def external_post_challenge(
    *, attempt_id: str,
    client_order_id: str,
    notional: Decimal,
) -> str:
    if not isinstance(notional, Decimal) or not notional.is_finite():
        raise ValueError("notional must be finite Decimal")
    return (
        "EXECUTE ONCE PAPER BTC/USD USD "
        f"{format(notional, 'f')} {attempt_id} {client_order_id}"
    )


def consume_external_post_consent(
    *,
    attempt: FirstCanaryAttemptWorkspace,
    preparation: Mapping[str, object],
    restart_safe: Mapping[str, object],
    confirmation: str,
    now: datetime,
) -> FirstCanaryExternalPostConsent:
    if not isinstance(attempt, FirstCanaryAttemptWorkspace):
        raise TypeError("exact first-canary attempt workspace is required")
    _aware(now, "now")
    instant = now.astimezone(timezone.utc)
    attempt.assert_unexecuted()

    consent_path = attempt.attempt_root / CONSENT_FILENAME
    if consent_path.exists():
        raise FirstCanaryExternalPostConsentBlocked(
            "external PAPER POST consent was already consumed; POST replay is forbidden"
        )

    prep = dict(preparation)
    restart = dict(restart_safe)
    preparation_hash = attempt.require_document_hash(
        prep,
        hash_key="preparation_hash",
        label="first-canary preparation",
    )
    restart_safe_hash = attempt.require_document_hash(
        restart,
        hash_key="restart_safe_hash",
        label="restart-safe preparation",
    )
    if not restart_safe_hash:
        raise AssertionError("restart-safe hash unexpectedly empty")

    if prep.get("status") != "CRYPTO_PAPER_FIRST_CANARY_EXECUTION_PREPARED_NO_POST":
        raise FirstCanaryExternalPostConsentBlocked(
            "only exact no-POST first-canary preparation may receive external POST consent"
        )
    if restart.get("document_type") != "R6_CRYPTO_PAPER_FIRST_CANARY_RESTART_SAFE_PREPARATION":
        raise FirstCanaryExternalPostConsentBlocked(
            "restart-safe first-canary evidence type is invalid"
        )
    for document, label in ((prep, "preparation"), (restart, "restart-safe evidence")):
        if document.get("credentials_persisted") is not False:
            raise FirstCanaryExternalPostConsentBlocked(f"{label} violates credential persistence policy")
        if document.get("live_trading") != "BLOCKED":
            raise FirstCanaryExternalPostConsentBlocked(f"{label} does not preserve LIVE deny")
        if document.get("external_post_authorized") is not False:
            raise FirstCanaryExternalPostConsentBlocked(
                f"{label} must not already authorize external POST"
            )

    package = _mapping(prep, "prepared_package")
    broker = _mapping(prep, "broker_order")
    broker_payload = _mapping(broker, "payload")

    attempt_id = _text(restart, "attempt_id")
    if attempt_id != attempt.attempt_id:
        raise FirstCanaryExternalPostConsentBlocked("restart-safe attempt identity mismatch")
    package_hash = _hash_text(package, "package_hash")
    if restart.get("package_hash") != package_hash:
        raise FirstCanaryExternalPostConsentBlocked("restart-safe package hash mismatch")
    if restart.get("preparation_hash") != preparation_hash:
        raise FirstCanaryExternalPostConsentBlocked("restart-safe preparation hash mismatch")

    prepared_evidence_hash = _hash_text(restart, "prepared_evidence_hash")
    nested_evidence = _mapping(restart, "prepared_evidence")
    if nested_evidence.get("prepared_evidence_hash") != prepared_evidence_hash:
        raise FirstCanaryExternalPostConsentBlocked("prepared evidence hash binding mismatch")

    client_order_id = _text(package, "client_order_id")
    if restart.get("client_order_id") != client_order_id:
        raise FirstCanaryExternalPostConsentBlocked("restart-safe client_order_id mismatch")
    if broker_payload.get("client_order_id") != client_order_id:
        raise FirstCanaryExternalPostConsentBlocked("broker payload client_order_id mismatch")

    symbol = _text(package, "symbol")
    if symbol != "BTC/USD" or broker_payload.get("symbol") != "BTC/USD":
        raise FirstCanaryExternalPostConsentBlocked("real first canary is fixed to BTC/USD")
    if package.get("broker_order_type") != "limit" or broker_payload.get("type") != "limit":
        raise FirstCanaryExternalPostConsentBlocked("real first canary is LIMIT only")
    if package.get("time_in_force") != "ioc" or broker_payload.get("time_in_force") != "ioc":
        raise FirstCanaryExternalPostConsentBlocked("real first canary is IOC only")
    if broker_payload.get("side") != "buy":
        raise FirstCanaryExternalPostConsentBlocked("real first canary is BUY only")
    if package.get("network_write_authorized") is not False:
        raise FirstCanaryExternalPostConsentBlocked("prepared package cannot pre-authorize network write")

    notional = _decimal(package.get("notional"), "notional")
    if not MIN_NOTIONAL <= notional <= MAX_NOTIONAL:
        raise FirstCanaryExternalPostConsentBlocked(
            f"prepared notional is outside USD {MIN_NOTIONAL}-{MAX_NOTIONAL}"
        )
    deadline = _datetime(package.get("execution_deadline"), "execution_deadline")
    if instant >= deadline:
        raise FirstCanaryExternalPostConsentBlocked("prepared first-canary package expired before POST consent")

    expected = external_post_challenge(
        attempt_id=attempt.attempt_id,
        client_order_id=client_order_id,
        notional=notional,
    )
    if not isinstance(confirmation, str) or confirmation != expected:
        raise FirstCanaryExternalPostConsentBlocked(
            "exact external PAPER POST confirmation challenge is required"
        )

    expires_at = min(deadline, instant + CONSENT_TTL)
    values = {
        "attempt_id": attempt.attempt_id,
        "package_hash": package_hash,
        "preparation_hash": preparation_hash,
        "prepared_evidence_hash": prepared_evidence_hash,
        "client_order_id": client_order_id,
        "symbol": symbol,
        "notional": notional,
        "source_host": ALPACA_PAPER_TRADING_HOST,
        "source_path": CRYPTO_ORDERS_PATH,
        "consented_at": instant,
        "expires_at": expires_at,
    }
    receipt = FirstCanaryExternalPostConsent(
        **values,
        receipt_hash=_hash(_payload_from_values(values)),
    )
    attempt.write_once(path=consent_path, document=receipt.document())
    return receipt


def require_fresh_external_post_consent(
    *,
    receipt: FirstCanaryExternalPostConsent,
    now: datetime,
) -> None:
    if not isinstance(receipt, FirstCanaryExternalPostConsent):
        raise TypeError("typed first-canary external POST consent is required")
    _aware(now, "now")
    instant = now.astimezone(timezone.utc)
    if instant < receipt.consented_at - timedelta(seconds=1) or instant >= receipt.expires_at:
        raise FirstCanaryExternalPostConsentBlocked(
            "external PAPER POST consent is stale; create a new attempt rather than retry POST"
        )


def _payload(value: FirstCanaryExternalPostConsent, *, include_hash: bool) -> dict[str, object]:
    payload = _payload_from_values(
        {
            "attempt_id": value.attempt_id,
            "package_hash": value.package_hash,
            "preparation_hash": value.preparation_hash,
            "prepared_evidence_hash": value.prepared_evidence_hash,
            "client_order_id": value.client_order_id,
            "symbol": value.symbol,
            "notional": value.notional,
            "source_host": value.source_host,
            "source_path": value.source_path,
            "consented_at": value.consented_at,
            "expires_at": value.expires_at,
        }
    )
    if include_hash:
        payload["receipt_hash"] = value.receipt_hash
    return payload


def _payload_from_values(values: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "document_type": DOCUMENT_TYPE,
        "attempt_id": str(values["attempt_id"]),
        "package_hash": str(values["package_hash"]),
        "preparation_hash": str(values["preparation_hash"]),
        "prepared_evidence_hash": str(values["prepared_evidence_hash"]),
        "client_order_id": str(values["client_order_id"]),
        "symbol": str(values["symbol"]),
        "notional": format(values["notional"], "f") if isinstance(values["notional"], Decimal) else str(values["notional"]),
        "source_host": str(values["source_host"]),
        "source_path": str(values["source_path"]),
        "consented_at": _time_text(values["consented_at"]),
        "expires_at": _time_text(values["expires_at"]),
        "one_shot": True,
        "retry_authorized": False,
        "credentials_persisted": False,
        "secret_persisted": False,
        "exact_paper_post_authorized": True,
        "capital_authority": "EXACT_PREPARED_PACKAGE_ONLY",
        "live_trading": "BLOCKED",
    }


def _mapping(raw: Mapping[str, object], key: str) -> dict[str, object]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise FirstCanaryExternalPostConsentBlocked(f"{key} must be an object")
    return dict(value)


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise FirstCanaryExternalPostConsentBlocked(f"{key} is missing or invalid")
    return value


def _hash_text(raw: Mapping[str, object], key: str) -> str:
    value = _text(raw, key)
    if not _HASH_RE.fullmatch(value):
        raise FirstCanaryExternalPostConsentBlocked(f"{key} must be lowercase SHA-256")
    return value


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise FirstCanaryExternalPostConsentBlocked(f"{label} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise FirstCanaryExternalPostConsentBlocked(f"{label} is invalid") from exc
    if not parsed.is_finite():
        raise FirstCanaryExternalPostConsentBlocked(f"{label} must be finite")
    return parsed


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise FirstCanaryExternalPostConsentBlocked(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FirstCanaryExternalPostConsentBlocked(f"{label} is invalid datetime") from exc
    _aware(parsed, label)
    return parsed.astimezone(timezone.utc)


def _aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _time_text(value: object) -> str:
    if not isinstance(value, datetime):
        raise TypeError("consent timestamp must be datetime")
    _aware(value, "consent timestamp")
    return value.astimezone(timezone.utc).isoformat()


def _hash(payload: Mapping[str, object]) -> str:
    return sha256(
        json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CONSENT_FILENAME",
    "CONSENT_TTL",
    "FirstCanaryExternalPostConsent",
    "FirstCanaryExternalPostConsentBlocked",
    "FirstCanaryExternalPostConsentError",
    "consume_external_post_consent",
    "external_post_challenge",
    "require_fresh_external_post_consent",
]
