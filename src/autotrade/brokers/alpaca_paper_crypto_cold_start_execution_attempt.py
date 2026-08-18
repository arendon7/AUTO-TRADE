from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
import sqlite3
from typing import Mapping

from autotrade.persistence import SQLiteRuntime

from .alpaca_paper_crypto_cold_start_final_guard import (
    COLD_START_KILL_REASON,
    COLD_START_SCOPE,
    CryptoColdStartFinalWriteAttestation,
    CryptoColdStartFinalWritePhase,
)
from .alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from .alpaca_paper_crypto_operator_decision import CryptoOperatorDecisionStatus


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")


class CryptoColdStartExecutionAttemptError(RuntimeError):
    pass


class CryptoColdStartExecutionAttemptConflict(CryptoColdStartExecutionAttemptError):
    pass


class CryptoColdStartExecutionAttemptIntegrityError(CryptoColdStartExecutionAttemptError):
    pass


@dataclass(frozen=True, slots=True)
class CryptoColdStartExecutionAttemptCheckpoint:
    pre_consume: CryptoColdStartFinalWriteAttestation
    recorded_at: datetime
    record_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.pre_consume, CryptoColdStartFinalWriteAttestation):
            raise ValueError("cold-start checkpoint requires cold-start PRE_CONSUME attestation")
        if self.pre_consume.phase is not CryptoColdStartFinalWritePhase.PRE_CONSUME:
            raise ValueError("cold-start checkpoint requires PRE_CONSUME")
        if self.pre_consume.bootstrap_scope != COLD_START_SCOPE:
            raise ValueError("cold-start checkpoint scope mismatch")
        if self.pre_consume.bootstrap_kill_reason != COLD_START_KILL_REASON:
            raise ValueError("cold-start checkpoint kill reason mismatch")
        _require_aware(self.recorded_at, "recorded_at")
        if self.recorded_at.astimezone(timezone.utc) != self.pre_consume.observed_at.astimezone(timezone.utc):
            raise ValueError("checkpoint recorded_at must equal PRE_CONSUME observed_at")
        _validate_id(self.pre_consume.attempt_id, "attempt_id")
        for label, value in (
            ("package_hash", self.pre_consume.package_hash),
            ("preparation_hash", self.pre_consume.preparation_hash),
            ("operator_decision_hash", self.pre_consume.operator_decision_hash),
            ("authority_state_fingerprint", self.pre_consume.authority_state_fingerprint),
            ("attestation_hash", self.pre_consume.attestation_hash),
            ("record_hash", self.record_hash),
        ):
            _validate_hash(value, label)
        if self.record_hash != _checkpoint_hash(self.pre_consume, self.recorded_at):
            raise ValueError("cold-start execution checkpoint hash mismatch")

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

    @property
    def authority_state_fingerprint(self) -> str:
        return self.pre_consume.authority_state_fingerprint


class SQLiteCryptoColdStartExecutionAttemptRegistry:
    """Tamper-evident PRE_CONSUME registry dedicated to bootstrap execution.

    It intentionally uses a separate table and type from normal crypto execution
    checkpoints. Identical replay is idempotent; package, preparation or attempt
    rebinding fails closed.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        if not isinstance(runtime, SQLiteRuntime):
            raise TypeError("cold-start checkpoint registry requires SQLiteRuntime")
        self._runtime = runtime
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alpaca_crypto_cold_start_execution_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    preparation_hash TEXT NOT NULL UNIQUE,
                    package_hash TEXT NOT NULL UNIQUE,
                    authority_state_fingerprint TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    record_hash TEXT NOT NULL UNIQUE
                )
                """
            )
        finally:
            conn.close()

    def record_pre_consume(
        self,
        attestation: CryptoColdStartFinalWriteAttestation,
    ) -> CryptoColdStartExecutionAttemptCheckpoint:
        if not isinstance(attestation, CryptoColdStartFinalWriteAttestation):
            raise TypeError("cold-start PRE_CONSUME attestation is required")
        if attestation.phase is not CryptoColdStartFinalWritePhase.PRE_CONSUME:
            raise CryptoColdStartExecutionAttemptConflict("only cold-start PRE_CONSUME may be checkpointed")
        checkpoint = _build_checkpoint(attestation)
        record_json = _canonical_json(_checkpoint_payload(checkpoint))
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT attempt_id, preparation_hash, package_hash,
                       authority_state_fingerprint, recorded_at, record_json, record_hash
                FROM alpaca_crypto_cold_start_execution_attempts
                WHERE attempt_id = ? OR preparation_hash = ? OR package_hash = ?
                """,
                (checkpoint.attempt_id, checkpoint.preparation_hash, checkpoint.package_hash),
            ).fetchall()
            if len(rows) > 1:
                raise CryptoColdStartExecutionAttemptIntegrityError(
                    "cold-start checkpoint uniqueness indexes disagree"
                )
            if rows:
                existing = _checkpoint_from_row(rows[0])
                if existing == checkpoint:
                    conn.execute("COMMIT")
                    return existing
                raise CryptoColdStartExecutionAttemptConflict(
                    "cold-start package/preparation already belongs to different evidence"
                )
            conn.execute(
                """
                INSERT INTO alpaca_crypto_cold_start_execution_attempts(
                    attempt_id, preparation_hash, package_hash,
                    authority_state_fingerprint, recorded_at, record_json, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.attempt_id,
                    checkpoint.preparation_hash,
                    checkpoint.package_hash,
                    checkpoint.authority_state_fingerprint,
                    _iso(checkpoint.recorded_at),
                    record_json,
                    checkpoint.record_hash,
                ),
            )
            row = conn.execute(
                """
                SELECT attempt_id, preparation_hash, package_hash,
                       authority_state_fingerprint, recorded_at, record_json, record_hash
                FROM alpaca_crypto_cold_start_execution_attempts WHERE attempt_id = ?
                """,
                (checkpoint.attempt_id,),
            ).fetchone()
            if row is None:
                raise CryptoColdStartExecutionAttemptIntegrityError("cold-start checkpoint insert was not durable")
            durable = _checkpoint_from_row(row)
            if durable != checkpoint:
                raise CryptoColdStartExecutionAttemptIntegrityError("durable cold-start checkpoint differs after insert")
            conn.execute("COMMIT")
            return durable
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, attempt_id: str) -> CryptoColdStartExecutionAttemptCheckpoint:
        _validate_id(attempt_id, "attempt_id")
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                """
                SELECT attempt_id, preparation_hash, package_hash,
                       authority_state_fingerprint, recorded_at, record_json, record_hash
                FROM alpaca_crypto_cold_start_execution_attempts WHERE attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            return _checkpoint_from_row(row)
        finally:
            conn.close()


def _build_checkpoint(attestation: CryptoColdStartFinalWriteAttestation) -> CryptoColdStartExecutionAttemptCheckpoint:
    observed = attestation.observed_at.astimezone(timezone.utc)
    return CryptoColdStartExecutionAttemptCheckpoint(
        pre_consume=attestation,
        recorded_at=observed,
        record_hash=_checkpoint_hash(attestation, observed),
    )


def _checkpoint_hash(attestation: CryptoColdStartFinalWriteAttestation, recorded_at: datetime) -> str:
    return sha256(
        _canonical_json(
            {
                "attempt_id": attestation.attempt_id,
                "package_hash": attestation.package_hash,
                "preparation_hash": attestation.preparation_hash,
                "operator_decision_hash": attestation.operator_decision_hash,
                "authority_state_fingerprint": attestation.authority_state_fingerprint,
                "order_id": attestation.order_id,
                "client_order_id": attestation.client_order_id,
                "pre_consume": _attestation_dict(attestation),
                "recorded_at": _iso(recorded_at),
            }
        ).encode("utf-8")
    ).hexdigest()


def _checkpoint_payload(checkpoint: CryptoColdStartExecutionAttemptCheckpoint) -> dict[str, object]:
    return {
        "attempt_id": checkpoint.attempt_id,
        "package_hash": checkpoint.package_hash,
        "preparation_hash": checkpoint.preparation_hash,
        "operator_decision_hash": checkpoint.operator_decision_hash,
        "authority_state_fingerprint": checkpoint.authority_state_fingerprint,
        "order_id": checkpoint.order_id,
        "client_order_id": checkpoint.client_order_id,
        "pre_consume": _attestation_dict(checkpoint.pre_consume),
        "recorded_at": _iso(checkpoint.recorded_at),
    }


def _checkpoint_from_row(row: sqlite3.Row) -> CryptoColdStartExecutionAttemptCheckpoint:
    try:
        raw = json.loads(str(row["record_json"]))
        if not isinstance(raw, dict):
            raise ValueError("record JSON must be object")
        expected = {
            "attempt_id",
            "package_hash",
            "preparation_hash",
            "operator_decision_hash",
            "authority_state_fingerprint",
            "order_id",
            "client_order_id",
            "pre_consume",
            "recorded_at",
        }
        if set(raw) != expected:
            raise ValueError("cold-start checkpoint payload is non-canonical")
        pre_raw = raw.get("pre_consume")
        if not isinstance(pre_raw, dict):
            raise ValueError("cold-start PRE_CONSUME payload must be object")
        attestation = _attestation_from_dict(pre_raw)
        checkpoint = CryptoColdStartExecutionAttemptCheckpoint(
            pre_consume=attestation,
            recorded_at=datetime.fromisoformat(str(raw["recorded_at"])),
            record_hash=str(row["record_hash"]),
        )
        if str(row["attempt_id"]) != checkpoint.attempt_id:
            raise ValueError("cold-start checkpoint attempt primary key mismatch")
        if str(row["preparation_hash"]) != checkpoint.preparation_hash:
            raise ValueError("cold-start checkpoint preparation index mismatch")
        if str(row["package_hash"]) != checkpoint.package_hash:
            raise ValueError("cold-start checkpoint package index mismatch")
        if str(row["authority_state_fingerprint"]) != checkpoint.authority_state_fingerprint:
            raise ValueError("cold-start checkpoint authority index mismatch")
        if str(row["recorded_at"]) != _iso(checkpoint.recorded_at):
            raise ValueError("cold-start checkpoint timestamp is non-canonical")
        if str(row["record_json"]) != _canonical_json(_checkpoint_payload(checkpoint)):
            raise ValueError("cold-start checkpoint record JSON is non-canonical")
        return checkpoint
    except CryptoColdStartExecutionAttemptIntegrityError:
        raise
    except Exception as exc:
        raise CryptoColdStartExecutionAttemptIntegrityError(
            "invalid durable cold-start execution checkpoint"
        ) from exc


def _attestation_dict(attestation: CryptoColdStartFinalWriteAttestation) -> dict[str, object]:
    payload = {
        "phase": attestation.phase.value,
        "bootstrap_scope": attestation.bootstrap_scope,
        "bootstrap_kill_reason": attestation.bootstrap_kill_reason,
        "package_hash": attestation.package_hash,
        "preparation_hash": attestation.preparation_hash,
        "operator_decision_hash": attestation.operator_decision_hash,
        "operator_status": attestation.operator_status.value,
        "attempt_id": attestation.attempt_id,
        "order_id": attestation.order_id,
        "client_order_id": attestation.client_order_id,
        "intent_fingerprint": attestation.intent_fingerprint,
        "risk_decision_id": attestation.risk_decision_id,
        "authority_state_fingerprint": attestation.authority_state_fingerprint,
        "authoritative_safety_state_version": attestation.authoritative_safety_state_version,
        "portfolio_version": attestation.portfolio_version,
        "portfolio_snapshot_id": attestation.portfolio_snapshot_id,
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
        "attestation_hash": attestation.attestation_hash,
    }
    return payload


def _attestation_from_dict(raw: Mapping[str, object]) -> CryptoColdStartFinalWriteAttestation:
    expected = {
        "phase", "bootstrap_scope", "bootstrap_kill_reason", "package_hash",
        "preparation_hash", "operator_decision_hash", "operator_status", "attempt_id",
        "order_id", "client_order_id", "intent_fingerprint", "risk_decision_id",
        "authority_state_fingerprint", "authoritative_safety_state_version",
        "portfolio_version", "portfolio_snapshot_id", "account_reference",
        "credential_reference", "fresh_account_fingerprint", "prepared_asset_fingerprint",
        "fresh_asset_fingerprint", "asset_contract_fingerprint",
        "prepared_product_profile_fingerprint", "fresh_product_profile_fingerprint",
        "product_contract_fingerprint", "fresh_market_attestation_fingerprint",
        "flat_account_fingerprint", "lifecycle_binding_hash", "lifecycle_control_hash",
        "lifecycle_event_head_hash", "lifecycle_status", "entry_attempt_count",
        "previous_attestation_hash", "observed_at", "attestation_hash",
    }
    if set(raw) != expected:
        raise ValueError("persisted cold-start attestation is non-canonical")
    return CryptoColdStartFinalWriteAttestation(
        phase=CryptoColdStartFinalWritePhase(str(raw["phase"])),
        bootstrap_scope=str(raw["bootstrap_scope"]),
        bootstrap_kill_reason=str(raw["bootstrap_kill_reason"]),
        package_hash=str(raw["package_hash"]),
        preparation_hash=str(raw["preparation_hash"]),
        operator_decision_hash=str(raw["operator_decision_hash"]),
        operator_status=CryptoOperatorDecisionStatus(str(raw["operator_status"])),
        attempt_id=str(raw["attempt_id"]),
        order_id=str(raw["order_id"]),
        client_order_id=str(raw["client_order_id"]),
        intent_fingerprint=str(raw["intent_fingerprint"]),
        risk_decision_id=str(raw["risk_decision_id"]),
        authority_state_fingerprint=str(raw["authority_state_fingerprint"]),
        authoritative_safety_state_version=int(raw["authoritative_safety_state_version"]),
        portfolio_version=int(raw["portfolio_version"]),
        portfolio_snapshot_id=str(raw["portfolio_snapshot_id"]),
        account_reference=str(raw["account_reference"]),
        credential_reference=str(raw["credential_reference"]),
        fresh_account_fingerprint=str(raw["fresh_account_fingerprint"]),
        prepared_asset_fingerprint=str(raw["prepared_asset_fingerprint"]),
        fresh_asset_fingerprint=str(raw["fresh_asset_fingerprint"]),
        asset_contract_fingerprint=str(raw["asset_contract_fingerprint"]),
        prepared_product_profile_fingerprint=str(raw["prepared_product_profile_fingerprint"]),
        fresh_product_profile_fingerprint=str(raw["fresh_product_profile_fingerprint"]),
        product_contract_fingerprint=str(raw["product_contract_fingerprint"]),
        fresh_market_attestation_fingerprint=str(raw["fresh_market_attestation_fingerprint"]),
        flat_account_fingerprint=str(raw["flat_account_fingerprint"]),
        lifecycle_binding_hash=str(raw["lifecycle_binding_hash"]),
        lifecycle_control_hash=str(raw["lifecycle_control_hash"]),
        lifecycle_event_head_hash=str(raw["lifecycle_event_head_hash"]),
        lifecycle_status=CryptoLifecycleStatus(str(raw["lifecycle_status"])),
        entry_attempt_count=int(raw["entry_attempt_count"]),
        previous_attestation_hash=(str(raw["previous_attestation_hash"]) if raw["previous_attestation_hash"] is not None else None),
        observed_at=datetime.fromisoformat(str(raw["observed_at"])),
        attestation_hash=str(raw["attestation_hash"]),
    )


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _iso(value: datetime) -> str:
    _require_aware(value, "timestamp")
    return value.astimezone(timezone.utc).isoformat()


def _validate_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = [
    "CryptoColdStartExecutionAttemptCheckpoint",
    "CryptoColdStartExecutionAttemptConflict",
    "CryptoColdStartExecutionAttemptError",
    "CryptoColdStartExecutionAttemptIntegrityError",
    "SQLiteCryptoColdStartExecutionAttemptRegistry",
]
