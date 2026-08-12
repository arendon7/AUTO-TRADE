from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

from .persistence import SQLiteRuntime, _ledger_hash


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONNECTIVITY_CANARY_STRATEGY_ID = "r6-connectivity-canary"


class ConnectivityCanaryAuthorityError(RuntimeError):
    pass


class ConnectivityCanaryAuthorityConflict(ConnectivityCanaryAuthorityError):
    pass


class ConnectivityCanaryPurpose(StrEnum):
    CONNECTIVITY_CANARY = "CONNECTIVITY_CANARY"


@dataclass(frozen=True, slots=True)
class ConnectivityCanaryAuthority:
    authority_id: str
    purpose: ConnectivityCanaryPurpose
    order_id: str
    strategy_id: str
    intent_fingerprint: str
    risk_decision_id: str
    risk_decision_fingerprint: str
    market_fingerprint: str
    safety_state_version: int
    portfolio_version: int
    portfolio_snapshot_id: str
    portfolio_snapshot_hash: str
    account_attestation_fingerprint: str
    asset_attestation_fingerprint: str
    baseline_flat_account_fingerprint: str
    market_evidence_fingerprint: str
    instrument_rules_fingerprint: str
    max_quantity: Decimal
    max_notional: Decimal
    issued_at: datetime
    expires_at: datetime
    authority_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, ConnectivityCanaryPurpose):
            raise ValueError("purpose must be ConnectivityCanaryPurpose")
        if self.purpose is not ConnectivityCanaryPurpose.CONNECTIVITY_CANARY:
            raise ValueError("unsupported connectivity authority purpose")
        if self.strategy_id != CONNECTIVITY_CANARY_STRATEGY_ID:
            raise ValueError("connectivity authority strategy_id is reserved and exact")
        for name, value in (
            ("authority_id", self.authority_id),
            ("intent_fingerprint", self.intent_fingerprint),
            ("risk_decision_fingerprint", self.risk_decision_fingerprint),
            ("market_fingerprint", self.market_fingerprint),
            ("portfolio_snapshot_hash", self.portfolio_snapshot_hash),
            ("account_attestation_fingerprint", self.account_attestation_fingerprint),
            ("asset_attestation_fingerprint", self.asset_attestation_fingerprint),
            ("baseline_flat_account_fingerprint", self.baseline_flat_account_fingerprint),
            ("market_evidence_fingerprint", self.market_evidence_fingerprint),
            ("instrument_rules_fingerprint", self.instrument_rules_fingerprint),
            ("authority_hash", self.authority_hash),
        ):
            if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
                raise ValueError(f"{name} must be lowercase SHA-256")
        for name, value in (
            ("order_id", self.order_id),
            ("risk_decision_id", self.risk_decision_id),
            ("portfolio_snapshot_id", self.portfolio_snapshot_id),
        ):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be canonical non-empty text")
        for name, value in (
            ("safety_state_version", self.safety_state_version),
            ("portfolio_version", self.portfolio_version),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative integer")
        if self.portfolio_version <= 0:
            raise ValueError("portfolio_version must be > 0")
        if self.max_quantity != Decimal("1"):
            raise ValueError("first connectivity canary authority is exactly one whole share")
        if (
            not isinstance(self.max_notional, Decimal)
            or not self.max_notional.is_finite()
            or not Decimal("0") < self.max_notional <= Decimal("10")
        ):
            raise ValueError("connectivity max_notional must be finite in (0,10]")
        if not _aware(self.issued_at) or not _aware(self.expires_at):
            raise ValueError("connectivity authority timestamps must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("connectivity authority must expire after issue")
        payload = self.payload(include_hash=False)
        expected_id = _hash({k: v for k, v in payload.items() if k != "authority_id"})
        if self.authority_id != expected_id:
            raise ValueError("connectivity authority_id mismatch")
        if self.authority_hash != _hash(payload):
            raise ValueError("connectivity authority hash mismatch")

    def payload(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "authority_id": self.authority_id,
            "purpose": self.purpose.value,
            "order_id": self.order_id,
            "strategy_id": self.strategy_id,
            "intent_fingerprint": self.intent_fingerprint,
            "risk_decision_id": self.risk_decision_id,
            "risk_decision_fingerprint": self.risk_decision_fingerprint,
            "market_fingerprint": self.market_fingerprint,
            "safety_state_version": self.safety_state_version,
            "portfolio_version": self.portfolio_version,
            "portfolio_snapshot_id": self.portfolio_snapshot_id,
            "portfolio_snapshot_hash": self.portfolio_snapshot_hash,
            "account_attestation_fingerprint": self.account_attestation_fingerprint,
            "asset_attestation_fingerprint": self.asset_attestation_fingerprint,
            "baseline_flat_account_fingerprint": self.baseline_flat_account_fingerprint,
            "market_evidence_fingerprint": self.market_evidence_fingerprint,
            "instrument_rules_fingerprint": self.instrument_rules_fingerprint,
            "max_quantity": str(self.max_quantity),
            "max_notional": str(self.max_notional),
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "strategy_health_required": False,
            "strategy_trading_authorized": False,
            "external_post_authorized": False,
            "live_trading": "BLOCKED",
        }
        if include_hash:
            payload["authority_hash"] = self.authority_hash
        return payload

    def is_valid_at(self, now: datetime) -> bool:
        if not _aware(now):
            raise ValueError("now must be timezone-aware")
        return self.issued_at <= now < self.expires_at

    @classmethod
    def issue(
        cls,
        *,
        order_id: str,
        intent_fingerprint: str,
        risk_decision_id: str,
        risk_decision_fingerprint: str,
        market_fingerprint: str,
        safety_state_version: int,
        portfolio_version: int,
        portfolio_snapshot_id: str,
        portfolio_snapshot_hash: str,
        account_attestation_fingerprint: str,
        asset_attestation_fingerprint: str,
        baseline_flat_account_fingerprint: str,
        market_evidence_fingerprint: str,
        instrument_rules_fingerprint: str,
        max_notional: Decimal,
        issued_at: datetime,
        expires_at: datetime,
    ) -> "ConnectivityCanaryAuthority":
        base: dict[str, object] = {
            "purpose": ConnectivityCanaryPurpose.CONNECTIVITY_CANARY.value,
            "order_id": order_id,
            "strategy_id": CONNECTIVITY_CANARY_STRATEGY_ID,
            "intent_fingerprint": intent_fingerprint,
            "risk_decision_id": risk_decision_id,
            "risk_decision_fingerprint": risk_decision_fingerprint,
            "market_fingerprint": market_fingerprint,
            "safety_state_version": safety_state_version,
            "portfolio_version": portfolio_version,
            "portfolio_snapshot_id": portfolio_snapshot_id,
            "portfolio_snapshot_hash": portfolio_snapshot_hash,
            "account_attestation_fingerprint": account_attestation_fingerprint,
            "asset_attestation_fingerprint": asset_attestation_fingerprint,
            "baseline_flat_account_fingerprint": baseline_flat_account_fingerprint,
            "market_evidence_fingerprint": market_evidence_fingerprint,
            "instrument_rules_fingerprint": instrument_rules_fingerprint,
            "max_quantity": "1",
            "max_notional": str(max_notional),
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "strategy_health_required": False,
            "strategy_trading_authorized": False,
            "external_post_authorized": False,
            "live_trading": "BLOCKED",
        }
        authority_id = _hash(base)
        payload_without_hash = {"authority_id": authority_id, **base}
        return cls(
            authority_id=authority_id,
            purpose=ConnectivityCanaryPurpose.CONNECTIVITY_CANARY,
            order_id=order_id,
            strategy_id=CONNECTIVITY_CANARY_STRATEGY_ID,
            intent_fingerprint=intent_fingerprint,
            risk_decision_id=risk_decision_id,
            risk_decision_fingerprint=risk_decision_fingerprint,
            market_fingerprint=market_fingerprint,
            safety_state_version=safety_state_version,
            portfolio_version=portfolio_version,
            portfolio_snapshot_id=portfolio_snapshot_id,
            portfolio_snapshot_hash=portfolio_snapshot_hash,
            account_attestation_fingerprint=account_attestation_fingerprint,
            asset_attestation_fingerprint=asset_attestation_fingerprint,
            baseline_flat_account_fingerprint=baseline_flat_account_fingerprint,
            market_evidence_fingerprint=market_evidence_fingerprint,
            instrument_rules_fingerprint=instrument_rules_fingerprint,
            max_quantity=Decimal("1"),
            max_notional=max_notional,
            issued_at=issued_at,
            expires_at=expires_at,
            authority_hash=_hash(payload_without_hash),
        )


class SQLiteConnectivityCanaryAuthorityStore:
    """Immutable first-canary authority registry inside the core SQLite control plane.

    Issuance creates no broker/write authority. It only records that one exact
    OMS VALIDATED order is a connectivity/protection test rather than strategy
    trading. The exact durable ledger event is committed in the same SQLite
    transaction as the authority row.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        conn = runtime.connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS connectivity_canary_authority (
                    authority_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    authority_hash TEXT NOT NULL UNIQUE,
                    issued_at TEXT NOT NULL
                )
                """
            )
        finally:
            conn.close()

    def issue(self, authority: ConnectivityCanaryAuthority) -> ConnectivityCanaryAuthority:
        if not isinstance(authority, ConnectivityCanaryAuthority):
            raise TypeError("ConnectivityCanaryAuthority is required")
        payload_json = _canonical(authority.payload())
        event_payload = {
            "authority_id": authority.authority_id,
            "authority_hash": authority.authority_hash,
            "order_id": authority.order_id,
            "purpose": authority.purpose.value,
            "risk_decision_id": authority.risk_decision_id,
            "strategy_id": authority.strategy_id,
            "external_post_authorized": "false",
            "strategy_trading_authorized": "false",
            "live_trading": "BLOCKED",
        }
        event_id = f"connectivity-authority:{authority.authority_id}"
        event_payload_json = _canonical(event_payload)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT authority_id, payload_json, authority_hash FROM connectivity_canary_authority"
            ).fetchall()
            if rows:
                if len(rows) != 1:
                    raise ConnectivityCanaryAuthorityConflict(
                        "connectivity authority registry has multiple rows"
                    )
                row = rows[0]
                if (
                    row["authority_id"] != authority.authority_id
                    or row["payload_json"] != payload_json
                    or row["authority_hash"] != authority.authority_hash
                ):
                    raise ConnectivityCanaryAuthorityConflict(
                        "workspace already contains a different connectivity authority"
                    )
                self._verify_event_tx(conn, authority)
                conn.execute("COMMIT")
                return authority

            conn.execute(
                """
                INSERT INTO connectivity_canary_authority(
                    authority_id,order_id,payload_json,authority_hash,issued_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    authority.authority_id,
                    authority.order_id,
                    payload_json,
                    authority.authority_hash,
                    authority.issued_at.isoformat(),
                ),
            )
            previous = conn.execute(
                "SELECT event_hash FROM ledger_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = previous["event_hash"] if previous is not None else "GENESIS"
            event_hash = _ledger_hash(
                prev_hash=prev_hash,
                event_id=event_id,
                event_type="CONNECTIVITY_CANARY_AUTHORITY_ISSUED",
                occurred_at=authority.issued_at.isoformat(),
                payload_json=event_payload_json,
            )
            conn.execute(
                """
                INSERT INTO ledger_events(
                    event_id,event_type,occurred_at,payload_json,prev_hash,event_hash
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    event_id,
                    "CONNECTIVITY_CANARY_AUTHORITY_ISSUED",
                    authority.issued_at.isoformat(),
                    event_payload_json,
                    prev_hash,
                    event_hash,
                ),
            )
            conn.execute("COMMIT")
            return authority
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get_for_order(self, order_id: str) -> ConnectivityCanaryAuthority | None:
        if not isinstance(order_id, str) or not order_id:
            raise ValueError("order_id is required")
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT payload_json, authority_hash FROM connectivity_canary_authority WHERE order_id=?",
                (order_id,),
            ).fetchone()
            if row is None:
                return None
            authority = _from_payload(json.loads(row["payload_json"]))
            if row["authority_hash"] != authority.authority_hash:
                raise ConnectivityCanaryAuthorityConflict("authority row hash mismatch")
            self._verify_event_tx(conn, authority)
            return authority
        finally:
            conn.close()

    @staticmethod
    def _verify_event_tx(conn, authority: ConnectivityCanaryAuthority) -> None:
        event_id = f"connectivity-authority:{authority.authority_id}"
        rows = conn.execute(
            "SELECT event_type, occurred_at, payload_json FROM ledger_events WHERE event_id=?",
            (event_id,),
        ).fetchall()
        if len(rows) != 1:
            raise ConnectivityCanaryAuthorityConflict(
                "connectivity authority ledger event is missing or duplicated"
            )
        row = rows[0]
        if row["event_type"] != "CONNECTIVITY_CANARY_AUTHORITY_ISSUED":
            raise ConnectivityCanaryAuthorityConflict("connectivity authority ledger type mismatch")
        if row["occurred_at"] != authority.issued_at.isoformat():
            raise ConnectivityCanaryAuthorityConflict("connectivity authority ledger time mismatch")
        payload = json.loads(row["payload_json"])
        expected = {
            "authority_id": authority.authority_id,
            "authority_hash": authority.authority_hash,
            "order_id": authority.order_id,
            "purpose": authority.purpose.value,
            "risk_decision_id": authority.risk_decision_id,
            "strategy_id": authority.strategy_id,
            "external_post_authorized": "false",
            "strategy_trading_authorized": "false",
            "live_trading": "BLOCKED",
        }
        if payload != expected:
            raise ConnectivityCanaryAuthorityConflict(
                "connectivity authority ledger binding mismatch"
            )


def _from_payload(payload: dict[str, object]) -> ConnectivityCanaryAuthority:
    if not isinstance(payload, dict):
        raise ConnectivityCanaryAuthorityConflict("authority payload must be object")
    if payload.get("strategy_health_required") is not False:
        raise ConnectivityCanaryAuthorityConflict("connectivity authority semantic marker changed")
    if payload.get("strategy_trading_authorized") is not False:
        raise ConnectivityCanaryAuthorityConflict("connectivity authority cannot authorize strategy trading")
    if payload.get("external_post_authorized") is not False:
        raise ConnectivityCanaryAuthorityConflict("connectivity authority cannot directly authorize POST")
    if payload.get("live_trading") != "BLOCKED":
        raise ConnectivityCanaryAuthorityConflict("connectivity authority cannot authorize LIVE")
    try:
        authority = ConnectivityCanaryAuthority(
            authority_id=str(payload["authority_id"]),
            purpose=ConnectivityCanaryPurpose(str(payload["purpose"])),
            order_id=str(payload["order_id"]),
            strategy_id=str(payload["strategy_id"]),
            intent_fingerprint=str(payload["intent_fingerprint"]),
            risk_decision_id=str(payload["risk_decision_id"]),
            risk_decision_fingerprint=str(payload["risk_decision_fingerprint"]),
            market_fingerprint=str(payload["market_fingerprint"]),
            safety_state_version=int(payload["safety_state_version"]),
            portfolio_version=int(payload["portfolio_version"]),
            portfolio_snapshot_id=str(payload["portfolio_snapshot_id"]),
            portfolio_snapshot_hash=str(payload["portfolio_snapshot_hash"]),
            account_attestation_fingerprint=str(payload["account_attestation_fingerprint"]),
            asset_attestation_fingerprint=str(payload["asset_attestation_fingerprint"]),
            baseline_flat_account_fingerprint=str(payload["baseline_flat_account_fingerprint"]),
            market_evidence_fingerprint=str(payload["market_evidence_fingerprint"]),
            instrument_rules_fingerprint=str(payload["instrument_rules_fingerprint"]),
            max_quantity=Decimal(str(payload["max_quantity"])),
            max_notional=Decimal(str(payload["max_notional"])),
            issued_at=datetime.fromisoformat(str(payload["issued_at"])),
            expires_at=datetime.fromisoformat(str(payload["expires_at"])),
            authority_hash=str(payload["authority_hash"]),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ConnectivityCanaryAuthorityConflict("connectivity authority payload is invalid") from exc
    if authority.payload() != payload:
        raise ConnectivityCanaryAuthorityConflict("connectivity authority payload is non-canonical")
    return authority


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: dict[str, object]) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


__all__ = [
    "CONNECTIVITY_CANARY_STRATEGY_ID",
    "ConnectivityCanaryAuthority",
    "ConnectivityCanaryAuthorityConflict",
    "ConnectivityCanaryAuthorityError",
    "ConnectivityCanaryPurpose",
    "SQLiteConnectivityCanaryAuthorityStore",
]
