from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
import sqlite3
from typing import Mapping

from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteRuntime

from .alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from .alpaca_paper_crypto_protection_final_guard import (
    CryptoProtectionFinalWriteAttestation,
    CryptoProtectionFinalWritePhase,
)


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")


class CryptoProtectionExecutionAttemptError(RuntimeError):
    pass


class CryptoProtectionExecutionAttemptConflict(CryptoProtectionExecutionAttemptError):
    pass


class CryptoProtectionExecutionAttemptIntegrityError(CryptoProtectionExecutionAttemptError):
    pass


@dataclass(frozen=True, slots=True)
class CryptoProtectionExecutionAttemptCheckpoint:
    """Durable PRE_CONSUME checkpoint for one exact protection attempt.

    This object carries no credentials and grants no broker-write authority. It
    exists only so a process crash after human-authority consumption can resume
    the same immutable protection attempt without fabricating PRE_CONSUME state.
    """

    pre_consume: CryptoProtectionFinalWriteAttestation
    recorded_at: datetime
    record_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.pre_consume, CryptoProtectionFinalWriteAttestation):
            raise ValueError("protection execution attempt requires exact PRE_CONSUME attestation")
        if self.pre_consume.phase is not CryptoProtectionFinalWritePhase.PRE_CONSUME:
            raise ValueError("protection execution checkpoint requires PRE_CONSUME phase")
        if self.pre_consume.lifecycle_status is not CryptoLifecycleStatus.PROTECTION_PREPARED:
            raise ValueError("protection execution checkpoint requires PROTECTION_PREPARED lifecycle")
        if self.pre_consume.protection_attempt_count != 0:
            raise ValueError("protection execution checkpoint requires zero prior protection attempts")
        if self.pre_consume.oms_order_status is not OrderStatus.VALIDATED:
            raise ValueError("protection execution checkpoint requires OMS VALIDATED")
        _require_aware(self.recorded_at, "recorded_at")
        if self.recorded_at.astimezone(timezone.utc) != self.pre_consume.observed_at.astimezone(timezone.utc):
            raise ValueError("protection execution recorded_at must equal PRE_CONSUME observed_at")
        for label, value in (
            ("attempt_id", self.pre_consume.attempt_id),
            ("lifecycle_id", self.pre_consume.lifecycle_id),
            ("order_id", self.pre_consume.order_id),
            ("client_order_id", self.pre_consume.client_order_id),
        ):
            _require_id(value, label)
        for label, value in (
            ("package_hash", self.pre_consume.package_hash),
            ("operator_decision_hash", self.pre_consume.operator_decision_hash),
            ("account_reference", self.pre_consume.account_reference),
            ("credential_reference", self.pre_consume.credential_reference),
            ("fresh_account_fingerprint", self.pre_consume.fresh_account_fingerprint),
            ("lifecycle_control_hash", self.pre_consume.lifecycle_control_hash),
            ("lifecycle_event_head_hash", self.pre_consume.lifecycle_event_head_hash),
            ("position_credential_reference", self.pre_consume.position_credential_reference),
            ("position_response_sha256", self.pre_consume.position_response_sha256),
            ("attestation_hash", self.pre_consume.attestation_hash),
            ("record_hash", self.record_hash),
        ):
            _require_hash(value, label)
        if self.record_hash != _checkpoint_hash(self.pre_consume, self.recorded_at):
            raise ValueError("protection execution checkpoint hash mismatch")

    @property
    def attempt_id(self) -> str:
        return self.pre_consume.attempt_id

    @property
    def package_hash(self) -> str:
        return self.pre_consume.package_hash

    @property
    def operator_decision_hash(self) -> str:
        return self.pre_consume.operator_decision_hash

    @property
    def lifecycle_id(self) -> str:
        return self.pre_consume.lifecycle_id

    @property
    def order_id(self) -> str:
        return self.pre_consume.order_id

    @property
    def client_order_id(self) -> str:
        return self.pre_consume.client_order_id


class SQLiteCryptoProtectionExecutionAttemptRegistry:
    """Immutable tamper-evident registry for protection PRE_CONSUME evidence.

    A package may be checkpointed under exactly one attempt. Exact replay is
    idempotent; package, attempt or operator-authority rebinding fails closed.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        if not isinstance(runtime, SQLiteRuntime):
            raise TypeError("protection execution attempt registry requires SQLiteRuntime")
        self._runtime = runtime
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS alpaca_crypto_protection_execution_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    package_hash TEXT NOT NULL UNIQUE,
                    operator_decision_hash TEXT NOT NULL UNIQUE,
                    lifecycle_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    record_hash TEXT NOT NULL UNIQUE
                );
                """
            )
        finally:
            conn.close()

    def record_pre_consume(
        self,
        attestation: CryptoProtectionFinalWriteAttestation,
    ) -> CryptoProtectionExecutionAttemptCheckpoint:
        if not isinstance(attestation, CryptoProtectionFinalWriteAttestation):
            raise TypeError("protection PRE_CONSUME attestation is required")
        if attestation.phase is not CryptoProtectionFinalWritePhase.PRE_CONSUME:
            raise CryptoProtectionExecutionAttemptConflict("only protection PRE_CONSUME may be checkpointed")
        checkpoint = _build_checkpoint(attestation)
        record_json = _canonical_json(_checkpoint_payload(checkpoint))
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT attempt_id, package_hash, operator_decision_hash,
                       lifecycle_id, order_id, client_order_id,
                       recorded_at, record_json, record_hash
                FROM alpaca_crypto_protection_execution_attempts
                WHERE attempt_id = ? OR package_hash = ? OR operator_decision_hash = ?
                """,
                (
                    checkpoint.attempt_id,
                    checkpoint.package_hash,
                    checkpoint.operator_decision_hash,
                ),
            ).fetchall()
            if len(rows) > 1:
                raise CryptoProtectionExecutionAttemptIntegrityError(
                    "protection execution attempt uniqueness indexes disagree"
                )
            if rows:
                existing = _checkpoint_from_row(rows[0])
                if existing == checkpoint:
                    conn.execute("COMMIT")
                    return existing
                raise CryptoProtectionExecutionAttemptConflict(
                    "protection package/operator authority is already checkpointed by another attempt or evidence set"
                )
            conn.execute(
                """
                INSERT INTO alpaca_crypto_protection_execution_attempts(
                    attempt_id, package_hash, operator_decision_hash,
                    lifecycle_id, order_id, client_order_id,
                    recorded_at, record_json, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.attempt_id,
                    checkpoint.package_hash,
                    checkpoint.operator_decision_hash,
                    checkpoint.lifecycle_id,
                    checkpoint.order_id,
                    checkpoint.client_order_id,
                    _iso(checkpoint.recorded_at),
                    record_json,
                    checkpoint.record_hash,
                ),
            )
            row = conn.execute(
                """
                SELECT attempt_id, package_hash, operator_decision_hash,
                       lifecycle_id, order_id, client_order_id,
                       recorded_at, record_json, record_hash
                FROM alpaca_crypto_protection_execution_attempts
                WHERE attempt_id = ?
                """,
                (checkpoint.attempt_id,),
            ).fetchone()
            if row is None:
                raise CryptoProtectionExecutionAttemptIntegrityError(
                    "protection execution attempt insert was not durable"
                )
            durable = _checkpoint_from_row(row)
            if durable != checkpoint:
                raise CryptoProtectionExecutionAttemptIntegrityError(
                    "durable protection execution attempt differs after insert"
                )
            conn.execute("COMMIT")
            return durable
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, attempt_id: str) -> CryptoProtectionExecutionAttemptCheckpoint:
        _require_id(attempt_id, "attempt_id")
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                """
                SELECT attempt_id, package_hash, operator_decision_hash,
                       lifecycle_id, order_id, client_order_id,
                       recorded_at, record_json, record_hash
                FROM alpaca_crypto_protection_execution_attempts
                WHERE attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            return _checkpoint_from_row(row)
        finally:
            conn.close()

    def get_for_package(self, package_hash: str) -> CryptoProtectionExecutionAttemptCheckpoint:
        _require_hash(package_hash, "package_hash")
        conn = self._runtime.connect()
        try:
            rows = conn.execute(
                """
                SELECT attempt_id, package_hash, operator_decision_hash,
                       lifecycle_id, order_id, client_order_id,
                       recorded_at, record_json, record_hash
                FROM alpaca_crypto_protection_execution_attempts
                WHERE package_hash = ?
                """,
                (package_hash,),
            ).fetchall()
            if not rows:
                raise KeyError(package_hash)
            if len(rows) != 1:
                raise CryptoProtectionExecutionAttemptIntegrityError(
                    "protection package has multiple execution attempt checkpoints"
                )
            return _checkpoint_from_row(rows[0])
        finally:
            conn.close()


def _build_checkpoint(
    attestation: CryptoProtectionFinalWriteAttestation,
) -> CryptoProtectionExecutionAttemptCheckpoint:
    observed = attestation.observed_at.astimezone(timezone.utc)
    return CryptoProtectionExecutionAttemptCheckpoint(
        pre_consume=attestation,
        recorded_at=observed,
        record_hash=_checkpoint_hash(attestation, observed),
    )


def _checkpoint_hash(
    attestation: CryptoProtectionFinalWriteAttestation,
    recorded_at: datetime,
) -> str:
    return sha256(
        _canonical_json(
            {
                "kind": "R6_CRYPTO_PROTECTION_EXECUTION_ATTEMPT",
                "attempt_id": attestation.attempt_id,
                "package_hash": attestation.package_hash,
                "operator_decision_hash": attestation.operator_decision_hash,
                "lifecycle_id": attestation.lifecycle_id,
                "order_id": attestation.order_id,
                "client_order_id": attestation.client_order_id,
                "pre_consume": _attestation_dict(attestation),
                "recorded_at": _iso(recorded_at),
            }
        ).encode("utf-8")
    ).hexdigest()


def _checkpoint_payload(
    checkpoint: CryptoProtectionExecutionAttemptCheckpoint,
) -> dict[str, object]:
    return {
        "attempt_id": checkpoint.attempt_id,
        "package_hash": checkpoint.package_hash,
        "operator_decision_hash": checkpoint.operator_decision_hash,
        "lifecycle_id": checkpoint.lifecycle_id,
        "order_id": checkpoint.order_id,
        "client_order_id": checkpoint.client_order_id,
        "pre_consume": _attestation_dict(checkpoint.pre_consume),
        "recorded_at": _iso(checkpoint.recorded_at),
    }


def _checkpoint_from_row(row: sqlite3.Row) -> CryptoProtectionExecutionAttemptCheckpoint:
    try:
        raw = _strict_json_object(str(row["record_json"]))
        expected_keys = {
            "attempt_id",
            "package_hash",
            "operator_decision_hash",
            "lifecycle_id",
            "order_id",
            "client_order_id",
            "pre_consume",
            "recorded_at",
        }
        if set(raw) != expected_keys:
            raise ValueError("protection execution attempt payload is non-canonical")
        pre_raw = raw.get("pre_consume")
        if not isinstance(pre_raw, dict):
            raise ValueError("protection execution PRE_CONSUME payload must be object")
        attestation = _attestation_from_dict(pre_raw)
        recorded_at = _datetime(raw.get("recorded_at"), "recorded_at")
        checkpoint = CryptoProtectionExecutionAttemptCheckpoint(
            pre_consume=attestation,
            recorded_at=recorded_at,
            record_hash=str(row["record_hash"]),
        )
        for column, expected in (
            ("attempt_id", checkpoint.attempt_id),
            ("package_hash", checkpoint.package_hash),
            ("operator_decision_hash", checkpoint.operator_decision_hash),
            ("lifecycle_id", checkpoint.lifecycle_id),
            ("order_id", checkpoint.order_id),
            ("client_order_id", checkpoint.client_order_id),
            ("recorded_at", _iso(checkpoint.recorded_at)),
        ):
            if str(row[column]) != expected:
                raise ValueError(f"protection execution attempt {column} index mismatch")
        if str(row["record_json"]) != _canonical_json(_checkpoint_payload(checkpoint)):
            raise ValueError("protection execution attempt record JSON is non-canonical")
        return checkpoint
    except CryptoProtectionExecutionAttemptIntegrityError:
        raise
    except Exception as exc:
        raise CryptoProtectionExecutionAttemptIntegrityError(
            "invalid durable crypto protection execution attempt checkpoint"
        ) from exc


def _attestation_dict(
    attestation: CryptoProtectionFinalWriteAttestation,
) -> dict[str, object]:
    return {
        "phase": attestation.phase.value,
        "package_hash": attestation.package_hash,
        "operator_decision_hash": attestation.operator_decision_hash,
        "attempt_id": attestation.attempt_id,
        "lifecycle_id": attestation.lifecycle_id,
        "order_id": attestation.order_id,
        "client_order_id": attestation.client_order_id,
        "account_reference": attestation.account_reference,
        "credential_reference": attestation.credential_reference,
        "fresh_account_fingerprint": attestation.fresh_account_fingerprint,
        "lifecycle_status": attestation.lifecycle_status.value,
        "lifecycle_control_hash": attestation.lifecycle_control_hash,
        "lifecycle_event_head_hash": attestation.lifecycle_event_head_hash,
        "protection_attempt_count": attestation.protection_attempt_count,
        "oms_order_status": attestation.oms_order_status.value,
        "position_quantity": format(attestation.position_quantity, "f"),
        "position_credential_reference": attestation.position_credential_reference,
        "position_request_id": attestation.position_request_id,
        "position_response_sha256": attestation.position_response_sha256,
        "position_observed_at": _iso(attestation.position_observed_at),
        "observed_at": _iso(attestation.observed_at),
        "previous_attestation_hash": attestation.previous_attestation_hash,
        "attestation_hash": attestation.attestation_hash,
    }


def _attestation_from_dict(payload: Mapping[str, object]) -> CryptoProtectionFinalWriteAttestation:
    expected = {
        "phase",
        "package_hash",
        "operator_decision_hash",
        "attempt_id",
        "lifecycle_id",
        "order_id",
        "client_order_id",
        "account_reference",
        "credential_reference",
        "fresh_account_fingerprint",
        "lifecycle_status",
        "lifecycle_control_hash",
        "lifecycle_event_head_hash",
        "protection_attempt_count",
        "oms_order_status",
        "position_quantity",
        "position_credential_reference",
        "position_request_id",
        "position_response_sha256",
        "position_observed_at",
        "observed_at",
        "previous_attestation_hash",
        "attestation_hash",
    }
    if set(payload) != expected:
        raise ValueError("persisted protection PRE_CONSUME attestation is non-canonical")
    return CryptoProtectionFinalWriteAttestation(
        phase=CryptoProtectionFinalWritePhase(_required_str(payload, "phase")),
        package_hash=_required_str(payload, "package_hash"),
        operator_decision_hash=_required_str(payload, "operator_decision_hash"),
        attempt_id=_required_str(payload, "attempt_id"),
        lifecycle_id=_required_str(payload, "lifecycle_id"),
        order_id=_required_str(payload, "order_id"),
        client_order_id=_required_str(payload, "client_order_id"),
        account_reference=_required_str(payload, "account_reference"),
        credential_reference=_required_str(payload, "credential_reference"),
        fresh_account_fingerprint=_required_str(payload, "fresh_account_fingerprint"),
        lifecycle_status=CryptoLifecycleStatus(_required_str(payload, "lifecycle_status")),
        lifecycle_control_hash=_required_str(payload, "lifecycle_control_hash"),
        lifecycle_event_head_hash=_required_str(payload, "lifecycle_event_head_hash"),
        protection_attempt_count=_strict_int(payload.get("protection_attempt_count"), "protection_attempt_count"),
        oms_order_status=OrderStatus(_required_str(payload, "oms_order_status")),
        position_quantity=_decimal(payload.get("position_quantity"), "position_quantity"),
        position_credential_reference=_required_str(payload, "position_credential_reference"),
        position_request_id=_required_str(payload, "position_request_id"),
        position_response_sha256=_required_str(payload, "position_response_sha256"),
        position_observed_at=_datetime(payload.get("position_observed_at"), "position_observed_at"),
        observed_at=_datetime(payload.get("observed_at"), "observed_at"),
        previous_attestation_hash=_optional_str(payload.get("previous_attestation_hash"), "previous_attestation_hash"),
        attestation_hash=_required_str(payload, "attestation_hash"),
    )


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _strict_json_object(raw: str) -> dict[str, object]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("protection execution attempt JSON root must be object")
    if _canonical_json(parsed) != raw:
        raise ValueError("protection execution attempt JSON is not canonical")
    return parsed


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be non-empty text")
    return value


def _optional_str(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be non-empty text when present")
    return value


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be integer")
    return value


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be canonical decimal text")
    parsed = Decimal(value)
    if not parsed.is_finite() or parsed <= 0 or format(parsed, "f") != value:
        raise ValueError(f"{label} must be canonical positive decimal text")
    return parsed


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be ISO datetime")
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, label)
    if _iso(parsed) != value:
        raise ValueError(f"{label} must be canonical UTC ISO datetime")
    return parsed


def _iso(value: datetime) -> str:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _require_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _require_hash(value: object, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


__all__ = [
    "CryptoProtectionExecutionAttemptCheckpoint",
    "CryptoProtectionExecutionAttemptConflict",
    "CryptoProtectionExecutionAttemptError",
    "CryptoProtectionExecutionAttemptIntegrityError",
    "SQLiteCryptoProtectionExecutionAttemptRegistry",
]
