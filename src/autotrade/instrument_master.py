from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import re
import sqlite3

from .persistence import SQLiteRuntime


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class InstrumentMasterError(RuntimeError):
    pass


class InstrumentRuleConflict(InstrumentMasterError):
    pass


class InstrumentRuleNotFound(InstrumentMasterError):
    pass


class InstrumentRuleStale(InstrumentMasterError):
    pass


class InstrumentNotTradable(InstrumentMasterError):
    pass


class InstrumentConstraintViolation(InstrumentMasterError):
    pass


class InstrumentTradingStatus(StrEnum):
    TRADING = "TRADING"
    HALTED = "HALTED"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class AuthoritativeInstrumentRules:
    """Versioned venue rules suitable for capital-sensitive validation.

    This object is intentionally separate from research.market.InstrumentMetadata.
    Research serialization precision is never authority for venue execution rules.
    """

    venue: str
    symbol: str
    base_currency: str
    quote_currency: str
    version: int
    price_tick: Decimal
    quantity_step: Decimal
    min_quantity: Decimal | None
    max_quantity: Decimal | None
    min_notional: Decimal | None
    max_notional: Decimal | None
    trading_status: InstrumentTradingStatus
    source: str
    source_version: str
    source_payload_sha256: str
    observed_at: datetime
    valid_until: datetime | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("venue", self.venue),
            ("symbol", self.symbol),
            ("base_currency", self.base_currency),
            ("quote_currency", self.quote_currency),
            ("source", self.source),
            ("source_version", self.source_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
            if value != value.strip():
                raise ValueError(f"{name} must not contain surrounding whitespace")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version <= 0:
            raise ValueError("version must be integer > 0")
        if not isinstance(self.trading_status, InstrumentTradingStatus):
            raise ValueError("trading_status must be InstrumentTradingStatus")
        if not _aware(self.observed_at):
            raise ValueError("observed_at must be timezone-aware")
        if self.valid_until is not None:
            if not _aware(self.valid_until):
                raise ValueError("valid_until must be timezone-aware")
            if self.valid_until <= self.observed_at:
                raise ValueError("valid_until must be after observed_at")
        if not _SHA256_RE.fullmatch(self.source_payload_sha256):
            raise ValueError("source_payload_sha256 must be lowercase SHA-256 hex")

        for name, value in (
            ("price_tick", self.price_tick),
            ("quantity_step", self.quantity_step),
        ):
            if not _positive_decimal(value):
                raise ValueError(f"{name} must be finite and > 0")
        for name, value in (
            ("min_quantity", self.min_quantity),
            ("max_quantity", self.max_quantity),
            ("min_notional", self.min_notional),
            ("max_notional", self.max_notional),
        ):
            if value is not None and not _positive_decimal(value):
                raise ValueError(f"{name} must be None or finite and > 0")
        for name, value in (
            ("min_quantity", self.min_quantity),
            ("max_quantity", self.max_quantity),
        ):
            if value is not None and value % self.quantity_step != 0:
                raise ValueError(f"{name} must align to quantity_step")
        if (
            self.min_quantity is not None
            and self.max_quantity is not None
            and self.min_quantity > self.max_quantity
        ):
            raise ValueError("min_quantity cannot exceed max_quantity")
        if (
            self.min_notional is not None
            and self.max_notional is not None
            and self.min_notional > self.max_notional
        ):
            raise ValueError("min_notional cannot exceed max_notional")

    @property
    def instrument_key(self) -> str:
        return f"{self.venue}:{self.symbol}"

    @property
    def fingerprint(self) -> str:
        raw = _canonical_json(self.to_payload(include_fingerprint=False)).encode("utf-8")
        return sha256(raw).hexdigest()

    def to_payload(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "venue": self.venue,
            "symbol": self.symbol,
            "base_currency": self.base_currency,
            "quote_currency": self.quote_currency,
            "version": self.version,
            "price_tick": str(self.price_tick),
            "quantity_step": str(self.quantity_step),
            "min_quantity": _decimal_or_none(self.min_quantity),
            "max_quantity": _decimal_or_none(self.max_quantity),
            "min_notional": _decimal_or_none(self.min_notional),
            "max_notional": _decimal_or_none(self.max_notional),
            "trading_status": self.trading_status.value,
            "source": self.source,
            "source_version": self.source_version,
            "source_payload_sha256": self.source_payload_sha256,
            "observed_at": self.observed_at.isoformat(),
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "AuthoritativeInstrumentRules":
        if not isinstance(payload, dict):
            raise ValueError("instrument-rule payload must be an object")
        expected = {
            "venue",
            "symbol",
            "base_currency",
            "quote_currency",
            "version",
            "price_tick",
            "quantity_step",
            "min_quantity",
            "max_quantity",
            "min_notional",
            "max_notional",
            "trading_status",
            "source",
            "source_version",
            "source_payload_sha256",
            "observed_at",
            "valid_until",
            "fingerprint",
        }
        unknown = set(payload) - expected
        required = expected - {"fingerprint"}
        missing = required - set(payload)
        if unknown or missing:
            raise ValueError(
                f"invalid instrument-rule payload fields; missing={sorted(missing)} unknown={sorted(unknown)}"
            )
        supplied_fingerprint = payload.get("fingerprint")
        if supplied_fingerprint is not None:
            if (
                not isinstance(supplied_fingerprint, str)
                or not _SHA256_RE.fullmatch(supplied_fingerprint)
            ):
                raise InstrumentRuleConflict("instrument-rule fingerprint mismatch")
            raw_payload = dict(payload)
            raw_payload.pop("fingerprint", None)
            raw_fingerprint = sha256(
                _canonical_json(raw_payload).encode("utf-8")
            ).hexdigest()
            if supplied_fingerprint != raw_fingerprint:
                raise InstrumentRuleConflict("instrument-rule fingerprint mismatch")

        record = cls(
            venue=_string(payload["venue"]),
            symbol=_string(payload["symbol"]),
            base_currency=_string(payload["base_currency"]),
            quote_currency=_string(payload["quote_currency"]),
            version=_integer(payload["version"]),
            price_tick=_decimal(payload["price_tick"]),
            quantity_step=_decimal(payload["quantity_step"]),
            min_quantity=_nullable_decimal(payload["min_quantity"]),
            max_quantity=_nullable_decimal(payload["max_quantity"]),
            min_notional=_nullable_decimal(payload["min_notional"]),
            max_notional=_nullable_decimal(payload["max_notional"]),
            trading_status=InstrumentTradingStatus(_string(payload["trading_status"])),
            source=_string(payload["source"]),
            source_version=_string(payload["source_version"]),
            source_payload_sha256=_string(payload["source_payload_sha256"]),
            observed_at=_timestamp(payload["observed_at"]),
            valid_until=_nullable_timestamp(payload["valid_until"]),
        )
        supplied_fingerprint = payload.get("fingerprint")
        if supplied_fingerprint is not None and supplied_fingerprint != record.fingerprint:
            raise InstrumentRuleConflict("instrument-rule fingerprint mismatch")
        return record

    def validate_candidate(self, *, quantity: Decimal, price: Decimal) -> Decimal:
        if self.trading_status is not InstrumentTradingStatus.TRADING:
            raise InstrumentNotTradable(
                f"{self.instrument_key} status is {self.trading_status.value}"
            )
        if not _positive_decimal(quantity):
            raise InstrumentConstraintViolation("quantity must be finite and > 0")
        if not _positive_decimal(price):
            raise InstrumentConstraintViolation("price must be finite and > 0")
        if quantity % self.quantity_step != 0:
            raise InstrumentConstraintViolation("quantity does not align to quantity_step")
        if price % self.price_tick != 0:
            raise InstrumentConstraintViolation("price does not align to price_tick")
        if self.min_quantity is not None and quantity < self.min_quantity:
            raise InstrumentConstraintViolation("quantity below min_quantity")
        if self.max_quantity is not None and quantity > self.max_quantity:
            raise InstrumentConstraintViolation("quantity above max_quantity")
        notional = quantity * price
        if self.min_notional is not None and notional < self.min_notional:
            raise InstrumentConstraintViolation("notional below min_notional")
        if self.max_notional is not None and notional > self.max_notional:
            raise InstrumentConstraintViolation("notional above max_notional")
        return notional


class SQLiteInstrumentMaster:
    """Append-only authoritative instrument-rule registry.

    Versions are contiguous per (venue, symbol). Publishing an existing version
    with different content fails closed. Every read cross-checks the canonical
    payload fingerprint against the separately persisted fingerprint column.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS instrument_master (
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    PRIMARY KEY (venue, symbol, version),
                    UNIQUE (fingerprint)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS instrument_master_latest_idx
                ON instrument_master(venue, symbol, version DESC)
                """
            )
        finally:
            conn.close()

    def publish(
        self,
        rules: AuthoritativeInstrumentRules,
        *,
        now: datetime,
    ) -> AuthoritativeInstrumentRules:
        if not isinstance(rules, AuthoritativeInstrumentRules):
            raise TypeError("only AuthoritativeInstrumentRules can be published")
        if not _aware(now):
            raise ValueError("publish time must be timezone-aware")
        if rules.observed_at > now:
            raise InstrumentRuleConflict("cannot publish metadata observed in the future")

        payload_json = _canonical_json(rules.to_payload())
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT fingerprint, payload_json
                FROM instrument_master
                WHERE venue = ? AND symbol = ? AND version = ?
                """,
                (rules.venue, rules.symbol, rules.version),
            ).fetchone()
            if existing is not None:
                if (
                    existing["fingerprint"] != rules.fingerprint
                    or existing["payload_json"] != payload_json
                ):
                    conn.execute("ROLLBACK")
                    raise InstrumentRuleConflict(
                        f"version identity conflict for {rules.instrument_key} v{rules.version}"
                    )
                conn.execute("COMMIT")
                return rules

            latest = conn.execute(
                """
                SELECT version, fingerprint, payload_json
                FROM instrument_master
                WHERE venue = ? AND symbol = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (rules.venue, rules.symbol),
            ).fetchone()
            if latest is None:
                if rules.version != 1:
                    conn.execute("ROLLBACK")
                    raise InstrumentRuleConflict("first instrument-rule version must be 1")
            else:
                latest_version = int(latest["version"])
                if rules.version != latest_version + 1:
                    conn.execute("ROLLBACK")
                    raise InstrumentRuleConflict(
                        f"instrument-rule version must advance exactly by one from {latest_version}"
                    )
                previous = _rules_from_storage(
                    fingerprint=latest["fingerprint"],
                    payload_json=latest["payload_json"],
                )
                if rules.observed_at < previous.observed_at:
                    conn.execute("ROLLBACK")
                    raise InstrumentRuleConflict("observed_at cannot move backwards across versions")

            try:
                conn.execute(
                    """
                    INSERT INTO instrument_master(
                        venue, symbol, version, fingerprint, payload_json, published_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rules.venue,
                        rules.symbol,
                        rules.version,
                        rules.fingerprint,
                        payload_json,
                        now.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise InstrumentRuleConflict("instrument-rule uniqueness conflict") from exc
            conn.execute("COMMIT")
            return rules
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get_version(
        self,
        *,
        venue: str,
        symbol: str,
        version: int,
    ) -> AuthoritativeInstrumentRules:
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                """
                SELECT fingerprint, payload_json
                FROM instrument_master
                WHERE venue = ? AND symbol = ? AND version = ?
                """,
                (venue, symbol, version),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise InstrumentRuleNotFound(f"no instrument rules for {venue}:{symbol} v{version}")
        return _rules_from_storage(
            fingerprint=row["fingerprint"],
            payload_json=row["payload_json"],
        )

    def latest(self, *, venue: str, symbol: str) -> AuthoritativeInstrumentRules:
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                """
                SELECT fingerprint, payload_json
                FROM instrument_master
                WHERE venue = ? AND symbol = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (venue, symbol),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise InstrumentRuleNotFound(f"no instrument rules for {venue}:{symbol}")
        return _rules_from_storage(
            fingerprint=row["fingerprint"],
            payload_json=row["payload_json"],
        )

    def require_current(
        self,
        *,
        venue: str,
        symbol: str,
        now: datetime,
        max_age: timedelta,
    ) -> AuthoritativeInstrumentRules:
        if not _aware(now):
            raise ValueError("now must be timezone-aware")
        if max_age <= timedelta(0):
            raise ValueError("max_age must be > 0")
        rules = self.latest(venue=venue, symbol=symbol)
        if rules.observed_at > now:
            raise InstrumentRuleStale("latest instrument rules are from the future")
        if now - rules.observed_at > max_age:
            raise InstrumentRuleStale("latest instrument rules exceed max_age")
        if rules.valid_until is not None and now > rules.valid_until:
            raise InstrumentRuleStale("latest instrument rules are expired")
        return rules

    def require_tradable(
        self,
        *,
        venue: str,
        symbol: str,
        now: datetime,
        max_age: timedelta,
    ) -> AuthoritativeInstrumentRules:
        rules = self.require_current(
            venue=venue,
            symbol=symbol,
            now=now,
            max_age=max_age,
        )
        if rules.trading_status is not InstrumentTradingStatus.TRADING:
            raise InstrumentNotTradable(
                f"{rules.instrument_key} status is {rules.trading_status.value}"
            )
        return rules

    def history(self, *, venue: str, symbol: str) -> tuple[AuthoritativeInstrumentRules, ...]:
        conn = self._runtime.connect()
        try:
            rows = conn.execute(
                """
                SELECT fingerprint, payload_json
                FROM instrument_master
                WHERE venue = ? AND symbol = ?
                ORDER BY version
                """,
                (venue, symbol),
            ).fetchall()
        finally:
            conn.close()
        return tuple(
            _rules_from_storage(
                fingerprint=row["fingerprint"],
                payload_json=row["payload_json"],
            )
            for row in rows
        )


def _rules_from_storage(*, fingerprint: object, payload_json: object) -> AuthoritativeInstrumentRules:
    if not isinstance(fingerprint, str) or not _SHA256_RE.fullmatch(fingerprint):
        raise InstrumentRuleConflict("stored instrument-rule fingerprint is invalid")
    if not isinstance(payload_json, str):
        raise InstrumentRuleConflict("stored instrument-rule payload is invalid")
    try:
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        record = AuthoritativeInstrumentRules.from_payload(payload)
    except InstrumentRuleConflict:
        raise
    except (json.JSONDecodeError, InvalidOperation, TypeError, ValueError) as exc:
        raise InstrumentRuleConflict("stored instrument-rule payload is invalid") from exc
    if record.fingerprint != fingerprint:
        raise InstrumentRuleConflict("stored instrument-rule fingerprint mismatch")
    return record


def _positive_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _decimal_or_none(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected integer")
    return value


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("decimal must be encoded as string")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal") from exc


def _nullable_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be encoded as string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    if not _aware(parsed):
        raise ValueError("timestamp must be timezone-aware")
    return parsed


def _nullable_timestamp(value: object) -> datetime | None:
    return None if value is None else _timestamp(value)


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
