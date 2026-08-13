from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import re
import sqlite3
from typing import Mapping

from autotrade.persistence import SQLiteRuntime

from .alpaca_paper_crypto_final_guard import (
    CryptoFinalWriteAttestation,
    CryptoFinalWritePhase,
)
from .alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from .alpaca_paper_crypto_operator_decision import CryptoOperatorDecisionStatus


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")


class CryptoExecutionAttemptError(RuntimeError):
    pass


class CryptoExecutionAttemptConflict(CryptoExecutionAttemptError):
    pass


class CryptoExecutionAttemptIntegrityError(CryptoExecutionAttemptError):
    pass


@dataclass(frozen=True, slots=True)
class CryptoExecutionAttemptCheckpoint:
    """Immutable durable checkpoint for one exact crypto PAPER execution attempt.

    The checkpoint stores the PRE_CONSUME final-write attestation before the
    human decision is consumed. It carries no credentials and grants no network
    authority. Its only purpose is crash-safe recovery of the same attempt.
    """

    pre_consume: CryptoFinalWriteAttestation
    recorded_at: datetime
    record_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.pre_consume, CryptoFinalWriteAttestation):
            raise ValueError("execution attempt requires CryptoFinalWriteAttestation")
        if self.pre_consume.phase is not CryptoFinalWritePhase.PRE_CONSUME:
            raise ValueError("execution attempt checkpoint requires PRE_CONSUME attestation")
        _require_aware(self.recorded_at, "recorded_at")
        if self.recorded_at.astimezone(timezone.utc) != self.pre_consume.observed_at.astimezone(timezone.utc):
            raise ValueError("execution attempt recorded_at must equal PRE_CONSUME observed_at")
        _validate_id(self.pre_consume.attempt_id, "attempt_id")
        for label, value in (
            ("package_hash", self.pre_consume.package_hash),
            ("preparation_hash", self.pre_consume.preparation_hash),
            ("operator_decision_hash", self.pre_consume.operator_decision_hash),
            ("attestation_hash", self.pre_consume.attestation_hash),
            ("record_hash", self.record_hash),
        ):
            _validate_hash(value, label)
        if self.pre_consume.attestation_hash != _attestation_hash(self.pre_consume):
            raise ValueError("PRE_CONSUME attestation hash mismatch")
        if self.record_hash != _checkpoint_hash(self.pre_consume, self.recorded_at):
            raise ValueError("execution attempt checkpoint hash mismatch")

    @property
    def attempt_id(self) -> str:
        return self.pre_consume.attempt_id

    @property
    def package_hash(self) -> str:
        return self.pre_consume.package_hash

    @property
    def preparation_hash(self) -> str:
        return self.pre_consume.preparation_hash

    @property
    def operator_decision_hash(self) -> str:
        return self.pre_consume.operator_decision_hash

    @property
    def order_id(self) -> str:
        return self.pre_consume.order_id

    @property
    def client_order_id(self) -> str:
        return self.pre_consume.client_order_id


class SQLiteCryptoExecutionAttemptRegistry:
    """Immutable tamper-evident PRE_CONSUME checkpoint registry.

    One package may cross PRE_CONSUME under only one attempt. Identical replay is
    idempotent; any attempt/package/preparation rebinding fails closed.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS alpaca_crypto_execution_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    preparation_hash TEXT NOT NULL UNIQUE,
                    package_hash TEXT NOT NULL UNIQUE,
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
        attestation: CryptoFinalWriteAttestation,
    ) -> CryptoExecutionAttemptCheckpoint:
        if not isinstance(attestation, CryptoFinalWriteAttestation):
            raise TypeError("PRE_CONSUME attestation is required")
        if attestation.phase is not CryptoFinalWritePhase.PRE_CONSUME:
            raise CryptoExecutionAttemptConflict("only PRE_CONSUME may be checkpointed")
        checkpoint = _build_checkpoint(attestation)
        record_json = _canonical_json(_checkpoint_payload(checkpoint))
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT attempt_id, preparation_hash, package_hash, recorded_at, record_json, record_hash
                FROM alpaca_crypto_execution_attempts
                WHERE attempt_id = ? OR preparation_hash = ? OR package_hash = ?
                """,
                (checkpoint.attempt_id, checkpoint.preparation_hash, checkpoint.package_hash),
            ).fetchall()
            if len(rows) > 1:
                raise CryptoExecutionAttemptIntegrityError(
                    "execution attempt uniqueness indexes disagree"
                )
            if rows:
                existing = _checkpoint_from_row(rows[0])
                if existing == checkpoint:
                    conn.execute("COMMIT")
                    return existing
                raise CryptoExecutionAttemptConflict(
                    "crypto package/preparation is already checkpointed by another attempt or evidence set"
                )
            conn.execute(
                """
                INSERT INTO alpaca_crypto_execution_attempts(
                    attempt_id, preparation_hash, package_hash, recorded_at, record_json, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.attempt_id,
                    checkpoint.preparation_hash,
                    checkpoint.package_hash,
                    _iso(checkpoint.recorded_at),
                    record_json,
                    checkpoint.record_hash,
                ),
            )
            row = conn.execute(
                """
                SELECT attempt_id, preparation_hash, package_hash, recorded_at, record_json, record_hash
                FROM alpaca_crypto_execution_attempts WHERE attempt_id = ?
                """,
                (checkpoint.attempt_id,),
            ).fetchone()
            if row is None:
                raise CryptoExecutionAttemptIntegrityError("execution attempt insert was not durable")
            durable = _checkpoint_from_row(row)
            if durable != checkpoint:
                raise CryptoExecutionAttemptIntegrityError("durable execution attempt differs after insert")
            conn.execute("COMMIT")
            return durable
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, attempt_id: str) -> CryptoExecutionAttemptCheckpoint:
        _validate_id(attempt_id, "attempt_id")
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                """
                SELECT attempt_id, preparation_hash, package_hash, recorded_at, record_json, record_hash
                FROM alpaca_crypto_execution_attempts WHERE attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            return _checkpoint_from_row(row)
        finally:
            conn.close()

    def get_for_package(self, package_hash: str) -> CryptoExecutionAttemptCheckpoint:
        _validate_hash(package_hash, "package_hash")
        conn = self._runtime.connect()
        try:
            rows = conn.execute(
                """
                SELECT attempt_id, preparation_hash, package_hash, recorded_at, record_json, record_hash
                FROM alpaca_crypto_execution_attempts WHERE package_hash = ?
                """,
                (package_hash,),
            ).fetchall()
            if not rows:
                raise KeyError(package_hash)
            if len(rows) != 1:
                raise CryptoExecutionAttemptIntegrityError("package has multiple execution attempt checkpoints")
            return _checkpoint_from_row(rows[0])
        finally:
            conn.close()


def _build_checkpoint(attestation: CryptoFinalWriteAttestation) -> CryptoExecutionAttemptCheckpoint:
    observed = attestation.observed_at.astimezone(timezone.utc)
    return CryptoExecutionAttemptCheckpoint(
        pre_consume=attestation,
        recorded_at=observed,
        record_hash=_checkpoint_hash(attestation, observed),
    )


def _checkpoint_hash(attestation: CryptoFinalWriteAttestation, recorded_at: datetime) -> str:
    return sha256(
        _canonical_json(
            {
                "attempt_id": attestation.attempt_id,
                "preparation_hash": attestation.preparation_hash,
                "package_hash": attestation.package_hash,
                "operator_decision_hash": attestation.operator_decision_hash,
                "order_id": attestation.order_id,
                "client_order_id": attestation.client_order_id,
                "pre_consume": _attestation_dict(attestation),
                "recorded_at": _iso(recorded_at),
            }
        ).encode("utf-8")
    ).hexdigest()


def _checkpoint_payload(checkpoint: CryptoExecutionAttemptCheckpoint) -> dict[str, object]:
    return {
        "attempt_id": checkpoint.attempt_id,
        "preparation_hash": checkpoint.preparation_hash,
        "package_hash": checkpoint.package_hash,
        "operator_decision_hash": checkpoint.operator_decision_hash,
        "order_id": checkpoint.order_id,
        "client_order_id": checkpoint.client_order_id,
        "pre_consume": _attestation_dict(checkpoint.pre_consume),
        "recorded_at": _iso(checkpoint.recorded_at),
    }


def _checkpoint_from_row(row: sqlite3.Row) -> CryptoExecutionAttemptCheckpoint:
    try:
        raw = _strict_json_object(str(row["record_json"]))
        expected_keys = {
            "attempt_id",
            "preparation_hash",
            "package_hash",
            "operator_decision_hash",
            "order_id",
            "client_order_id",
            "pre_consume",
            "recorded_at",
        }
        if set(raw) != expected_keys:
            raise ValueError("execution attempt payload is non-canonical")
        pre_raw = raw.get("pre_consume")
        if not isinstance(pre_raw, dict):
            raise ValueError("execution attempt PRE_CONSUME payload must be object")
        attestation = _attestation_from_dict(pre_raw)
        recorded_at = _datetime(raw.get("recorded_at"), "recorded_at")
        checkpoint = CryptoExecutionAttemptCheckpoint(
            pre_consume=attestation,
            recorded_at=recorded_at,
            record_hash=str(row["record_hash"]),
        )
        if str(row["attempt_id"]) != checkpoint.attempt_id:
            raise ValueError("execution attempt primary key mismatch")
        if str(row["preparation_hash"]) != checkpoint.preparation_hash:
            raise ValueError("execution attempt preparation index mismatch")
        if str(row["package_hash"]) != checkpoint.package_hash:
            raise ValueError("execution attempt package index mismatch")
        if str(row["recorded_at"]) != _iso(checkpoint.recorded_at):
            raise ValueError("execution attempt timestamp is non-canonical")
        if str(row["record_json"]) != _canonical_json(_checkpoint_payload(checkpoint)):
            raise ValueError("execution attempt record JSON is non-canonical")
        return checkpoint
    except CryptoExecutionAttemptIntegrityError:
        raise
    except Exception as exc:
        raise CryptoExecutionAttemptIntegrityError("invalid durable crypto execution attempt checkpoint") from exc


def _attestation_dict(attestation: CryptoFinalWriteAttestation) -> dict[str, object]:
    payload = _attestation_payload(attestation)
    payload["attestation_hash"] = attestation.attestation_hash
    return payload


def _attestation_payload(attestation: CryptoFinalWriteAttestation) -> dict[str, object]:
    return {
        "phase": attestation.phase.value,
        "package_hash": attestation.package_hash,
        "preparation_hash": attestation.preparation_hash,
        "operator_decision_hash": attestation.operator_decision_hash,
        "operator_status": attestation.operator_status.value,
        "attempt_id": attestation.attempt_id,
        "order_id": attestation.order_id,
        "client_order_id": attestation.client_order_id,
        "intent_fingerprint": attestation.intent_fingerprint,
        "risk_decision_id": attestation.risk_decision_id,
        "safety_state_version": attestation.safety_state_version,
        "portfolio_version": attestation.portfolio_version,
        "portfolio_snapshot_id": attestation.portfolio_snapshot_id,
        "strategy_health_fingerprint": attestation.strategy_health_fingerprint,
        "portfolio_health_fingerprint": attestation.portfolio_health_fingerprint,
        "account_reference": attestation.account_reference,
        "credential_reference": attestation.credential_reference,
        "fresh_account_fingerprint": attestation.fresh_account_fingerprint,
        "prepared_asset_fingerprint": attestation.prepared_asset_fingerprint,
        "fresh_asset_fingerprint": attestation.fresh_asset_fingerprint,
        "asset_contract_fingerprint": attestation.asset_contract_fingerprint,
        "prepared_product_profile_fingerprint": attestation.prepared_product_profile_fingerprint,
        "fresh_product_profile_fingerprint": attestation.fresh_product_profile_fingerprint,
        "product_contract_fingerprint": attestation.product_contract_fingerprint,
        "fresh_market_attestation_fingerprint": attestation.fresh_market_attestation_fingerprint,
        "flat_account_fingerprint": attestation.flat_account_fingerprint,
        "lifecycle_binding_hash": attestation.lifecycle_binding_hash,
        "lifecycle_control_hash": attestation.lifecycle_control_hash,
        "lifecycle_event_head_hash": attestation.lifecycle_event_head_hash,
        "lifecycle_status": attestation.lifecycle_status.value,
        "entry_attempt_count": attestation.entry_attempt_count,
        "previous_attestation_hash": attestation.previous_attestation_hash,
        "observed_at": _iso(attestation.observed_at),
    }


def _attestation_hash(attestation: CryptoFinalWriteAttestation) -> str:
    return sha256(_canonical_json(_attestation_payload(attestation)).encode("utf-8")).hexdigest()


def _attestation_from_dict(payload: Mapping[str, object]) -> CryptoFinalWriteAttestation:
    expected = {
        "phase",
        "package_hash",
        "preparation_hash",
        "operator_decision_hash",
        "operator_status",
        "attempt_id",
        "order_id",
        "client_order_id",
        "intent_fingerprint",
        "risk_decision_id",
        "safety_state_version",
        "portfolio_version",
        "portfolio_snapshot_id",
        "strategy_health_fingerprint",
        "portfolio_health_fingerprint",
        "account_reference",
        "credential_reference",
        "fresh_account_fingerprint",
        "prepared_asset_fingerprint",
        "fresh_asset_fingerprint",
        "asset_contract_fingerprint",
        "prepared_product_profile_fingerprint",
        "fresh_product_profile_fingerprint",
        "product_contract_fingerprint",
        "fresh_market_attestation_fingerprint",
        "flat_account_fingerprint",
        "lifecycle_binding_hash",
        "lifecycle_control_hash",
        "lifecycle_event_head_hash",
        "lifecycle_status",
        "entry_attempt_count",
        "previous_attestation_hash",
        "observed_at",
        "attestation_hash",
    }
    if set(payload) != expected:
        raise ValueError("persisted PRE_CONSUME attestation is non-canonical")
    attestation = CryptoFinalWriteAttestation(
        phase=CryptoFinalWritePhase(_required_str(payload, "phase")),
        package_hash=_required_str(payload, "package_hash"),
        preparation_hash=_required_str(payload, "preparation_hash"),
        operator_decision_hash=_required_str(payload, "operator_decision_hash"),
        operator_status=CryptoOperatorDecisionStatus(_required_str(payload, "operator_status")),
        attempt_id=_required_str(payload, "attempt_id"),
        order_id=_required_str(payload, "order_id"),
        client_order_id=_required_str(payload, "client_order_id"),
        intent_fingerprint=_required_str(payload, "intent_fingerprint"),
        risk_decision_id=_required_str(payload, "risk_decision_id"),
        safety_state_version=_strict_int(payload.get("safety_state_version"), "safety_state_version"),
        portfolio_version=_strict_int(payload.get("portfolio_version"), "portfolio_version"),
        portfolio_snapshot_id=_required_str(payload, "portfolio_snapshot_id"),
        strategy_health_fingerprint=_required_str(payload, "strategy_health_fingerprint"),
        portfolio_health_fingerprint=_required_str(payload, "portfolio_health_fingerprint"),
        account_reference=_required_str(payload, "account_reference"),
        credential_reference=_required_str(payload, "credential_reference"),
        fresh_account_fingerprint=_required_str(payload, "fresh_account_fingerprint"),
        prepared_asset_fingerprint=_required_str(payload, "prepared_asset_fingerprint"),
        fresh_asset_fingerprint=_required_str(payload, "fresh_asset_fingerprint"),
        asset_contract_fingerprint=_required_str(payload, "asset_contract_fingerprint"),
        prepared_product_profile_fingerprint=_required_str(payload, "prepared_product_profile_fingerprint"),
        fresh_product_profile_fingerprint=_required_str(payload, "fresh_product_profile_fingerprint"),
        product_contract_fingerprint=_required_str(payload, "product_contract_fingerprint"),
        fresh_market_attestation_fingerprint=_required_str(payload, "fresh_market_attestation_fingerprint"),
        flat_account_fingerprint=_required_str(payload, "flat_account_fingerprint"),
        lifecycle_binding_hash=_required_str(payload, "lifecycle_binding_hash"),
        lifecycle_control_hash=_required_str(payload, "lifecycle_control_hash"),
        lifecycle_event_head_hash=_required_str(payload, "lifecycle_event_head_hash"),
        lifecycle_status=CryptoLifecycleStatus(_required_str(payload, "lifecycle_status")),
        entry_attempt_count=_strict_int(payload.get("entry_attempt_count"), "entry_attempt_count"),
        previous_attestation_hash=_optional_str(payload.get("previous_attestation_hash")),
        observed_at=_datetime(payload.get("observed_at"), "observed_at"),
        attestation_hash=_required_str(payload, "attestation_hash"),
    )
    if attestation.attestation_hash != _attestation_hash(attestation):
        raise ValueError("persisted PRE_CONSUME attestation hash mismatch")
    return attestation


def _strict_json_object(raw: str) -> dict[str, object]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("execution attempt JSON root must be object")
    return parsed


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _iso(value: datetime) -> str:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be ISO datetime text")
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, label)
    if value != _iso(parsed):
        raise ValueError(f"{label} must be canonical UTC ISO datetime")
    return parsed.astimezone(timezone.utc)


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be integer")
    return value


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be non-empty text")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("optional hash must be non-empty text or null")
    return value


def _validate_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
