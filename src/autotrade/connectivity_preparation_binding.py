from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re

from autotrade.persistence import SQLiteRuntime, _ledger_hash

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class ConnectivityPreparationBindingError(RuntimeError):
    pass


class ConnectivityPreparationBindingConflict(ConnectivityPreparationBindingError):
    pass


@dataclass(frozen=True, slots=True)
class ConnectivityPreparationBinding:
    binding_id: str
    order_id: str
    connectivity_authority_id: str
    connectivity_authority_hash: str
    candidate_hash: str
    standard_package_hash: str
    canary_approval_hash: str
    permit_event_hash: str
    submission_binding_hash: str
    bracket_payload_hash: str
    instrument_master_fingerprint: str
    prepared_at: datetime
    binding_hash: str

    def __post_init__(self) -> None:
        for name, value in (
            ("binding_id", self.binding_id), ("connectivity_authority_id", self.connectivity_authority_id),
            ("connectivity_authority_hash", self.connectivity_authority_hash), ("candidate_hash", self.candidate_hash),
            ("standard_package_hash", self.standard_package_hash), ("canary_approval_hash", self.canary_approval_hash),
            ("permit_event_hash", self.permit_event_hash), ("submission_binding_hash", self.submission_binding_hash),
            ("bracket_payload_hash", self.bracket_payload_hash), ("instrument_master_fingerprint", self.instrument_master_fingerprint),
            ("binding_hash", self.binding_hash),
        ):
            if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                raise ValueError(f"{name} must be lowercase SHA-256")
        if not isinstance(self.order_id, str) or not self.order_id or self.order_id != self.order_id.strip():
            raise ValueError("order_id must be canonical text")
        if self.prepared_at.tzinfo is None or self.prepared_at.utcoffset() is None:
            raise ValueError("prepared_at must be timezone-aware")
        payload = self.payload(include_hash=False)
        expected_id = _hash({k: v for k, v in payload.items() if k != "binding_id"})
        if self.binding_id != expected_id:
            raise ValueError("connectivity preparation binding_id mismatch")
        if self.binding_hash != _hash(payload):
            raise ValueError("connectivity preparation binding hash mismatch")

    def payload(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "binding_id": self.binding_id,
            "purpose": "CONNECTIVITY_CANARY",
            "order_id": self.order_id,
            "connectivity_authority_id": self.connectivity_authority_id,
            "connectivity_authority_hash": self.connectivity_authority_hash,
            "candidate_hash": self.candidate_hash,
            "standard_package_hash": self.standard_package_hash,
            "canary_approval_hash": self.canary_approval_hash,
            "permit_event_hash": self.permit_event_hash,
            "submission_binding_hash": self.submission_binding_hash,
            "bracket_payload_hash": self.bracket_payload_hash,
            "instrument_master_fingerprint": self.instrument_master_fingerprint,
            "prepared_at": self.prepared_at.isoformat(),
            "strategy_health_required": False,
            "strategy_health_created": False,
            "strategy_trading_authorized": False,
            "operator_authority_created": False,
            "external_post_authorized": False,
            "live_trading": "BLOCKED",
        }
        if include_hash:
            payload["binding_hash"] = self.binding_hash
        return payload

    @classmethod
    def create(cls, *, order_id: str, connectivity_authority_id: str, connectivity_authority_hash: str, candidate_hash: str, standard_package_hash: str, canary_approval_hash: str, permit_event_hash: str, submission_binding_hash: str, bracket_payload_hash: str, instrument_master_fingerprint: str, prepared_at: datetime) -> "ConnectivityPreparationBinding":
        base: dict[str, object] = {
            "purpose": "CONNECTIVITY_CANARY", "order_id": order_id,
            "connectivity_authority_id": connectivity_authority_id,
            "connectivity_authority_hash": connectivity_authority_hash,
            "candidate_hash": candidate_hash, "standard_package_hash": standard_package_hash,
            "canary_approval_hash": canary_approval_hash, "permit_event_hash": permit_event_hash,
            "submission_binding_hash": submission_binding_hash, "bracket_payload_hash": bracket_payload_hash,
            "instrument_master_fingerprint": instrument_master_fingerprint,
            "prepared_at": prepared_at.isoformat(), "strategy_health_required": False,
            "strategy_health_created": False, "strategy_trading_authorized": False,
            "operator_authority_created": False, "external_post_authorized": False, "live_trading": "BLOCKED",
        }
        binding_id = _hash(base)
        without_hash = {"binding_id": binding_id, **base}
        return cls(
            binding_id=binding_id, order_id=order_id, connectivity_authority_id=connectivity_authority_id,
            connectivity_authority_hash=connectivity_authority_hash, candidate_hash=candidate_hash,
            standard_package_hash=standard_package_hash, canary_approval_hash=canary_approval_hash,
            permit_event_hash=permit_event_hash, submission_binding_hash=submission_binding_hash,
            bracket_payload_hash=bracket_payload_hash, instrument_master_fingerprint=instrument_master_fingerprint,
            prepared_at=prepared_at, binding_hash=_hash(without_hash),
        )


class SQLiteConnectivityPreparationBindingStore:
    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        conn = runtime.connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS connectivity_preparation_binding (
                    binding_id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL, binding_hash TEXT NOT NULL UNIQUE,
                    prepared_at TEXT NOT NULL
                )
            """)
        finally:
            conn.close()

    def record(self, binding: ConnectivityPreparationBinding) -> ConnectivityPreparationBinding:
        if not isinstance(binding, ConnectivityPreparationBinding):
            raise TypeError("ConnectivityPreparationBinding is required")
        payload_json = _canonical(binding.payload())
        event_id = f"connectivity-prepared:{binding.binding_id}"
        event_payload = {
            "binding_id": binding.binding_id, "binding_hash": binding.binding_hash,
            "order_id": binding.order_id, "connectivity_authority_id": binding.connectivity_authority_id,
            "standard_package_hash": binding.standard_package_hash, "canary_approval_hash": binding.canary_approval_hash,
            "permit_event_hash": binding.permit_event_hash, "purpose": "CONNECTIVITY_CANARY",
            "strategy_health_required": "false", "external_post_authorized": "false", "live_trading": "BLOCKED",
        }
        event_payload_json = _canonical(event_payload)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT binding_id,payload_json,binding_hash FROM connectivity_preparation_binding").fetchall()
            if rows:
                if len(rows) != 1:
                    raise ConnectivityPreparationBindingConflict("connectivity preparation registry has multiple rows")
                row = rows[0]
                if row["binding_id"] != binding.binding_id or row["payload_json"] != payload_json or row["binding_hash"] != binding.binding_hash:
                    raise ConnectivityPreparationBindingConflict("workspace already contains a different connectivity preparation binding")
                self._verify_event_tx(conn, binding, event_payload)
                conn.execute("COMMIT")
                return binding
            conn.execute(
                "INSERT INTO connectivity_preparation_binding(binding_id,order_id,payload_json,binding_hash,prepared_at) VALUES(?,?,?,?,?)",
                (binding.binding_id, binding.order_id, payload_json, binding.binding_hash, binding.prepared_at.isoformat()),
            )
            previous = conn.execute("SELECT event_hash FROM ledger_events ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = previous["event_hash"] if previous is not None else "GENESIS"
            event_hash = _ledger_hash(prev_hash=prev_hash, event_id=event_id, event_type="CONNECTIVITY_CANARY_PREPARED", occurred_at=binding.prepared_at.isoformat(), payload_json=event_payload_json)
            conn.execute(
                "INSERT INTO ledger_events(event_id,event_type,occurred_at,payload_json,prev_hash,event_hash) VALUES(?,?,?,?,?,?)",
                (event_id, "CONNECTIVITY_CANARY_PREPARED", binding.prepared_at.isoformat(), event_payload_json, prev_hash, event_hash),
            )
            conn.execute("COMMIT")
            return binding
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get_for_order(self, order_id: str) -> ConnectivityPreparationBinding | None:
        conn = self._runtime.connect()
        try:
            row = conn.execute("SELECT payload_json,binding_hash FROM connectivity_preparation_binding WHERE order_id=?", (order_id,)).fetchone()
            if row is None:
                return None
            binding = _from_payload(json.loads(row["payload_json"]))
            if row["binding_hash"] != binding.binding_hash:
                raise ConnectivityPreparationBindingConflict("connectivity preparation row hash mismatch")
            event_payload = {
                "binding_id": binding.binding_id, "binding_hash": binding.binding_hash,
                "order_id": binding.order_id, "connectivity_authority_id": binding.connectivity_authority_id,
                "standard_package_hash": binding.standard_package_hash, "canary_approval_hash": binding.canary_approval_hash,
                "permit_event_hash": binding.permit_event_hash, "purpose": "CONNECTIVITY_CANARY",
                "strategy_health_required": "false", "external_post_authorized": "false", "live_trading": "BLOCKED",
            }
            self._verify_event_tx(conn, binding, event_payload)
            return binding
        finally:
            conn.close()

    @staticmethod
    def _verify_event_tx(conn, binding: ConnectivityPreparationBinding, expected_payload: dict[str, object]) -> None:
        rows = conn.execute("SELECT event_type,occurred_at,payload_json FROM ledger_events WHERE event_id=?", (f"connectivity-prepared:{binding.binding_id}",)).fetchall()
        if len(rows) != 1:
            raise ConnectivityPreparationBindingConflict("connectivity preparation ledger event is missing or duplicated")
        row = rows[0]
        if row["event_type"] != "CONNECTIVITY_CANARY_PREPARED" or row["occurred_at"] != binding.prepared_at.isoformat() or json.loads(row["payload_json"]) != expected_payload:
            raise ConnectivityPreparationBindingConflict("connectivity preparation ledger binding mismatch")


def _from_payload(payload: object) -> ConnectivityPreparationBinding:
    if not isinstance(payload, dict):
        raise ConnectivityPreparationBindingConflict("connectivity preparation payload must be object")
    for key, expected in (
        ("purpose", "CONNECTIVITY_CANARY"), ("strategy_health_required", False),
        ("strategy_health_created", False), ("strategy_trading_authorized", False),
        ("operator_authority_created", False), ("external_post_authorized", False), ("live_trading", "BLOCKED"),
    ):
        if payload.get(key) != expected:
            raise ConnectivityPreparationBindingConflict(f"unsafe connectivity preparation field: {key}")
    try:
        binding = ConnectivityPreparationBinding(
            binding_id=str(payload["binding_id"]), order_id=str(payload["order_id"]),
            connectivity_authority_id=str(payload["connectivity_authority_id"]), connectivity_authority_hash=str(payload["connectivity_authority_hash"]),
            candidate_hash=str(payload["candidate_hash"]), standard_package_hash=str(payload["standard_package_hash"]),
            canary_approval_hash=str(payload["canary_approval_hash"]), permit_event_hash=str(payload["permit_event_hash"]),
            submission_binding_hash=str(payload["submission_binding_hash"]), bracket_payload_hash=str(payload["bracket_payload_hash"]),
            instrument_master_fingerprint=str(payload["instrument_master_fingerprint"]), prepared_at=datetime.fromisoformat(str(payload["prepared_at"])),
            binding_hash=str(payload["binding_hash"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConnectivityPreparationBindingConflict("connectivity preparation payload is invalid") from exc
    if binding.payload() != payload:
        raise ConnectivityPreparationBindingConflict("connectivity preparation payload is non-canonical")
    return binding


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: dict[str, object]) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


__all__ = ["ConnectivityPreparationBinding", "ConnectivityPreparationBindingConflict", "SQLiteConnectivityPreparationBindingStore"]
