from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
import sqlite3

from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    ATTEMPT_ID_RE,
    FirstCanaryAttemptWorkspace,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleIntegrityError,
    CryptoLifecycleSnapshot,
    CryptoLifecycleStatus,
    _GENESIS_HASH,
    _binding_from_json,
    _event_from_row,
    _event_hash,
    _state_from_json,
    _state_hash,
)
from autotrade.domain import OrderRecord, OrderStatus, Side, intent_fingerprint
from autotrade.first_canary_fee_aware_recovery import (
    _validate_fee_adjusted_net_position,
)
from autotrade.first_canary_real_paper_execution import _package_from_payload
from autotrade.persistence import _order_from_json, _order_to_json


class PaperCloseSourceProvenanceError(RuntimeError):
    pass


class PaperCloseSourceProvenanceMissing(PaperCloseSourceProvenanceError):
    pass


class PaperCloseSourceProvenanceConflict(PaperCloseSourceProvenanceError):
    pass


@dataclass(frozen=True, slots=True)
class PaperCloseSourceProvenance:
    attempt_id: str
    source_order: OrderRecord
    source_lifecycle: CryptoLifecycleSnapshot
    preparation_hash: str
    execution_started_hash: str
    execution_result_hash: str
    resolution_hash: str
    resolution_kind: str
    broker_order_id: str
    broker_order_status: str
    gross_filled_quantity: Decimal
    confirmed_net_long_quantity: Decimal
    attempt_db_sha256: str
    verified_at: datetime
    provenance_hash: str

    def __post_init__(self) -> None:
        if not ATTEMPT_ID_RE.fullmatch(self.attempt_id):
            raise ValueError("source attempt_id is invalid")
        if self.source_order.status is not OrderStatus.SUBMITTING:
            raise ValueError("source OMS order must remain durable SUBMITTING after external handoff")
        if self.source_lifecycle.state.status is not CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED:
            raise ValueError("source lifecycle must be ENTRY_FILLED_UNPROTECTED")
        for label, value in (
            ("preparation_hash", self.preparation_hash),
            ("execution_started_hash", self.execution_started_hash),
            ("execution_result_hash", self.execution_result_hash),
            ("resolution_hash", self.resolution_hash),
            ("attempt_db_sha256", self.attempt_db_sha256),
            ("provenance_hash", self.provenance_hash),
        ):
            _require_hash(value, label)
        if self.resolution_kind not in {
            "INITIAL_RECONCILIATION",
            "GET_ONLY_RECOVERY_RESOLUTION",
        }:
            raise ValueError("source resolution kind is invalid")
        if self.broker_order_status != "filled":
            raise ValueError("source broker order must be terminal filled")
        if not self.broker_order_id.strip():
            raise ValueError("source broker order id is required")
        for label, value in (
            ("gross_filled_quantity", self.gross_filled_quantity),
            ("confirmed_net_long_quantity", self.confirmed_net_long_quantity),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{label} must be finite and positive")
        if self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None:
            raise ValueError("source provenance verified_at must be timezone-aware")
        if self.provenance_hash != _provenance_hash(self._payload_without_hash()):
            raise ValueError("source provenance hash mismatch")

    @property
    def strategy_id(self) -> str:
        return self.source_order.intent.strategy_id

    @property
    def lifecycle_id(self) -> str:
        return self.source_lifecycle.binding.lifecycle_id

    def _payload_without_hash(self) -> dict[str, object]:
        state = self.source_lifecycle.state
        return {
            "attempt_db_sha256": self.attempt_db_sha256,
            "attempt_id": self.attempt_id,
            "broker_order_id": self.broker_order_id,
            "broker_order_status": self.broker_order_status,
            "confirmed_net_long_quantity": _decimal_text(self.confirmed_net_long_quantity),
            "execution_result_hash": self.execution_result_hash,
            "execution_started_hash": self.execution_started_hash,
            "gross_filled_quantity": _decimal_text(self.gross_filled_quantity),
            "lifecycle_binding_hash": self.source_lifecycle.binding.fingerprint,
            "lifecycle_control_hash": state.control_hash,
            "lifecycle_event_head_hash": state.event_head_hash,
            "lifecycle_id": self.lifecycle_id,
            "order_id": self.source_order.order_id,
            "order_intent_fingerprint": intent_fingerprint(self.source_order.intent),
            "preparation_hash": self.preparation_hash,
            "resolution_hash": self.resolution_hash,
            "resolution_kind": self.resolution_kind,
            "strategy_id": self.strategy_id,
            "verified_at": self.verified_at.isoformat(),
        }


class FirstCanaryCloseSourceReader:
    """Reconstruct the executed first-canary source chain without mutating it.

    The attempt database is accepted only as a stable checkpointed SQLite file:
    no WAL/SHM sidecars, no symlinks, URI ``mode=ro&immutable=1`` and
    ``PRAGMA query_only=ON``. No durable store constructor is instantiated.
    The reader has no credentials, network, writer, retry or LIVE authority.
    """

    def __init__(self, *, workspace_path: Path, attempt_id: str) -> None:
        if not isinstance(workspace_path, Path):
            raise TypeError("workspace_path must be pathlib.Path")
        root = workspace_path.expanduser()
        if root.is_symlink() or not root.is_dir():
            raise PaperCloseSourceProvenanceMissing(
                "existing non-symlink PAPER workspace is required"
            )
        if not isinstance(attempt_id, str) or not ATTEMPT_ID_RE.fullmatch(attempt_id):
            raise PaperCloseSourceProvenanceMissing("valid first-canary attempt_id is required")
        self._attempt = FirstCanaryAttemptWorkspace(
            workspace_root=root.resolve(),
            attempt_id=attempt_id,
        )
        if self._attempt.attempt_root.is_symlink() or not self._attempt.attempt_root.is_dir():
            raise PaperCloseSourceProvenanceMissing("first-canary attempt directory is missing or unsafe")

    def verify(self, *, now: datetime) -> PaperCloseSourceProvenance:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("source provenance verification time must be timezone-aware")

        preparation = self._read_hashed(
            self._attempt.preparation_path,
            hash_key="preparation_hash",
            label="first-canary preparation",
        )
        self._require_preparation(preparation)
        try:
            package = _package_from_payload(_mapping(preparation, "prepared_package"))
        except Exception as exc:
            raise PaperCloseSourceProvenanceConflict("prepared package is invalid") from exc
        if package.order_id == "" or package.lifecycle_id == "":
            raise PaperCloseSourceProvenanceConflict("prepared package source identities are missing")

        started = self._read_hashed(
            self._attempt.execution_started_path,
            hash_key="execution_started_hash",
            label="first-canary execution-start latch",
        )
        result = self._read_hashed(
            self._attempt.execution_result_path,
            hash_key="execution_result_hash",
            label="first-canary execution result",
        )
        self._require_burned_execution(started=started, result=result, package=package)
        resolution_kind, resolution, resolution_hash = self._terminal_resolution(package=package)

        db_path = self._attempt.database_path
        self._require_stable_database(db_path)
        before_hash = _file_sha256(db_path)
        uri = f"{db_path.resolve().as_uri()}?mode=ro&immutable=1"
        try:
            conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        except sqlite3.Error as exc:
            raise PaperCloseSourceProvenanceMissing(
                "cannot open first-canary attempt database read-only"
            ) from exc
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            source_order = self._read_source_order(conn, package=package)
            source_lifecycle = self._read_source_lifecycle(conn, package=package)
        except PaperCloseSourceProvenanceError:
            raise
        except (sqlite3.Error, KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise PaperCloseSourceProvenanceConflict(
                "first-canary attempt SQLite provenance is invalid"
            ) from exc
        finally:
            conn.close()

        after_hash = _file_sha256(db_path)
        if after_hash != before_hash:
            raise PaperCloseSourceProvenanceConflict(
                "first-canary attempt database bytes changed during read-only verification"
            )
        self._require_stable_database(db_path)

        gross, net, broker_order_id, broker_status = self._bind_terminal_truth(
            package=package,
            resolution=resolution,
            lifecycle=source_lifecycle,
        )
        values = {
            "attempt_db_sha256": before_hash,
            "attempt_id": self._attempt.attempt_id,
            "broker_order_id": broker_order_id,
            "broker_order_status": broker_status,
            "confirmed_net_long_quantity": _decimal_text(net),
            "execution_result_hash": str(result["execution_result_hash"]),
            "execution_started_hash": str(started["execution_started_hash"]),
            "gross_filled_quantity": _decimal_text(gross),
            "lifecycle_binding_hash": source_lifecycle.binding.fingerprint,
            "lifecycle_control_hash": source_lifecycle.state.control_hash,
            "lifecycle_event_head_hash": source_lifecycle.state.event_head_hash,
            "lifecycle_id": source_lifecycle.binding.lifecycle_id,
            "order_id": source_order.order_id,
            "order_intent_fingerprint": intent_fingerprint(source_order.intent),
            "preparation_hash": str(preparation["preparation_hash"]),
            "resolution_hash": resolution_hash,
            "resolution_kind": resolution_kind,
            "strategy_id": source_order.intent.strategy_id,
            "verified_at": now.isoformat(),
        }
        return PaperCloseSourceProvenance(
            attempt_id=self._attempt.attempt_id,
            source_order=source_order,
            source_lifecycle=source_lifecycle,
            preparation_hash=str(preparation["preparation_hash"]),
            execution_started_hash=str(started["execution_started_hash"]),
            execution_result_hash=str(result["execution_result_hash"]),
            resolution_hash=resolution_hash,
            resolution_kind=resolution_kind,
            broker_order_id=broker_order_id,
            broker_order_status=broker_status,
            gross_filled_quantity=gross,
            confirmed_net_long_quantity=net,
            attempt_db_sha256=before_hash,
            verified_at=now,
            provenance_hash=_provenance_hash(values),
        )

    def _read_hashed(self, path: Path, *, hash_key: str, label: str) -> dict[str, object]:
        try:
            document = self._attempt.read(path=path)
            self._attempt.require_document_hash(document, hash_key=hash_key, label=label)
            return document
        except Exception as exc:
            raise PaperCloseSourceProvenanceMissing(f"{label} is missing or invalid") from exc

    def _require_preparation(self, document: dict[str, object]) -> None:
        expected = (
            ("attempt_id", self._attempt.attempt_id),
            ("environment", "PAPER"),
            ("external_post_authorized", False),
            ("broker_write_performed", False),
            ("credentials_persisted", False),
            ("live_trading", "BLOCKED"),
        )
        for key, value in expected:
            if document.get(key) != value:
                raise PaperCloseSourceProvenanceConflict(
                    f"first-canary preparation source binding mismatch: {key}"
                )

    def _require_burned_execution(self, *, started, result, package) -> None:
        for key, value in (
            ("attempt_id", self._attempt.attempt_id),
            ("client_order_id", package.client_order_id),
            ("package_hash", package.package_hash),
            ("retry_forbidden", True),
            ("writer_invocation_permitted_once", True),
            ("live_trading", "BLOCKED"),
        ):
            if started.get(key) != value:
                raise PaperCloseSourceProvenanceConflict(
                    f"execution-start source binding mismatch: {key}"
                )
        if result.get("attempt_id") != self._attempt.attempt_id:
            raise PaperCloseSourceProvenanceConflict("execution result attempt mismatch")
        if result.get("client_order_id") != package.client_order_id:
            raise PaperCloseSourceProvenanceConflict("execution result client_order_id mismatch")
        if result.get("package_hash") != package.package_hash:
            raise PaperCloseSourceProvenanceConflict("execution result package mismatch")
        if result.get("execution_started_hash") != started.get("execution_started_hash"):
            raise PaperCloseSourceProvenanceConflict("execution result is not bound to start latch")
        if result.get("entry_attempt_count") != 1 or result.get("retry_forbidden") is not True:
            raise PaperCloseSourceProvenanceConflict("execution result does not prove one burned POST attempt")
        if result.get("broker_post_outcome") not in {
            "BROKER_RESPONSE_RECEIVED",
            "UNKNOWN_RECONCILIATION_REQUIRED",
        }:
            raise PaperCloseSourceProvenanceConflict("execution result never crossed a recognized broker POST outcome")
        if result.get("live_trading") != "BLOCKED":
            raise PaperCloseSourceProvenanceConflict("execution result violates LIVE deny")

    def _terminal_resolution(self, *, package) -> tuple[str, dict[str, object], str]:
        recovery = self._attempt.recovery_resolution_path
        initial = self._attempt.reconciliation_path
        if recovery.exists():
            document = self._read_hashed(
                recovery,
                hash_key="recovery_resolution_hash",
                label="first-canary GET-only recovery resolution",
            )
            for key, value in (
                ("status", "CRYPTO_PAPER_FIRST_CANARY_GET_ONLY_RECONCILIATION_FINAL_NO_RETRY"),
                ("attempt_id", self._attempt.attempt_id),
                ("client_order_id", package.client_order_id),
                ("reconciliation_type", "ORDER_PLUS_POSITION"),
                ("resulting_lifecycle_status", CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED.value),
                ("entry_attempt_count", 1),
                ("retry_post", False),
                ("recovery_get_only", True),
                ("credentials_persisted", False),
                ("live_trading", "BLOCKED"),
            ):
                if document.get(key) != value:
                    raise PaperCloseSourceProvenanceConflict(
                        f"GET-only source resolution mismatch: {key}"
                    )
            return (
                "GET_ONLY_RECOVERY_RESOLUTION",
                document,
                str(document["recovery_resolution_hash"]),
            )
        if initial.exists():
            document = self._read_hashed(
                initial,
                hash_key="reconciliation_hash",
                label="first-canary initial reconciliation",
            )
            for key, value in (
                ("status", "CRYPTO_PAPER_FIRST_CANARY_RECONCILED_FINAL_NO_RETRY"),
                ("attempt_id", self._attempt.attempt_id),
                ("client_order_id", package.client_order_id),
                ("evidence_type", "ORDER_PLUS_POSITION"),
                ("lifecycle_status", CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED.value),
                ("retry_post", False),
                ("persisted_final_resolution", True),
                ("live_trading", "BLOCKED"),
            ):
                if document.get(key) != value:
                    raise PaperCloseSourceProvenanceConflict(
                        f"initial source reconciliation mismatch: {key}"
                    )
            return "INITIAL_RECONCILIATION", document, str(document["reconciliation_hash"])
        raise PaperCloseSourceProvenanceMissing(
            "first-canary has no terminal broker reconciliation suitable for R7 close attribution"
        )

    @staticmethod
    def _require_stable_database(path: Path) -> None:
        if path.is_symlink() or not path.is_file():
            raise PaperCloseSourceProvenanceMissing("attempt.sqlite3 is missing or unsafe")
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                raise PaperCloseSourceProvenanceConflict(
                    "attempt SQLite has active WAL/SHM sidecars; close the writer and checkpoint before provenance read"
                )

    @staticmethod
    def _read_source_order(conn: sqlite3.Connection, *, package) -> OrderRecord:
        row = conn.execute(
            "SELECT idempotency_key, order_id, record_json FROM orders WHERE order_id=?",
            (package.order_id,),
        ).fetchone()
        if row is None:
            raise PaperCloseSourceProvenanceMissing("source OMS order is missing from attempt database")
        raw = row["record_json"]
        if not isinstance(raw, str):
            raise PaperCloseSourceProvenanceConflict("source OMS order payload is not text")
        order = _order_from_json(raw)
        if _order_to_json(order) != raw:
            raise PaperCloseSourceProvenanceConflict("source OMS order payload is not canonical")
        if row["order_id"] != order.order_id or row["idempotency_key"] != order.intent.idempotency_key:
            raise PaperCloseSourceProvenanceConflict("source OMS row identity mismatch")
        if order.order_id != package.order_id:
            raise PaperCloseSourceProvenanceConflict("source OMS order differs from prepared package")
        if order.risk_decision_id != package.risk_decision_id:
            raise PaperCloseSourceProvenanceConflict("source OMS RiskDecision differs from prepared package")
        if intent_fingerprint(order.intent) != package.intent_fingerprint:
            raise PaperCloseSourceProvenanceConflict("source OMS intent fingerprint differs from prepared package")
        if order.status is not OrderStatus.SUBMITTING:
            raise PaperCloseSourceProvenanceConflict("source OMS order never reached durable external SUBMITTING")
        if order.intent.side is not Side.BUY or order.intent.symbol != package.symbol:
            raise PaperCloseSourceProvenanceConflict("source OMS order is not the prepared long entry")
        if order.intent.quantity != package.quantity:
            raise PaperCloseSourceProvenanceConflict("source OMS quantity differs from prepared package")
        return order

    @staticmethod
    def _read_source_lifecycle(conn: sqlite3.Connection, *, package) -> CryptoLifecycleSnapshot:
        binding_row = conn.execute(
            "SELECT * FROM alpaca_crypto_lifecycle_bindings WHERE lifecycle_id=?",
            (package.lifecycle_id,),
        ).fetchone()
        state_row = conn.execute(
            "SELECT * FROM alpaca_crypto_lifecycle_control WHERE lifecycle_id=?",
            (package.lifecycle_id,),
        ).fetchone()
        if binding_row is None or state_row is None:
            raise PaperCloseSourceProvenanceMissing("source crypto lifecycle binding/control is missing")
        try:
            binding = _binding_from_json(str(binding_row["binding_json"]))
            if binding.fingerprint != str(binding_row["binding_hash"]):
                raise CryptoLifecycleIntegrityError("binding hash mismatch")
            state = _state_from_json(
                str(state_row["state_json"]),
                control_hash=str(state_row["control_hash"]),
            )
            if state.lifecycle_id != binding.lifecycle_id or state.binding_hash != binding.fingerprint:
                raise CryptoLifecycleIntegrityError("control is bound to another lifecycle")
            if state.control_hash != _state_hash(replace(state, control_hash="")):
                raise CryptoLifecycleIntegrityError("control hash mismatch")
            rows = conn.execute(
                "SELECT * FROM alpaca_crypto_lifecycle_events WHERE lifecycle_id=? ORDER BY sequence",
                (package.lifecycle_id,),
            ).fetchall()
            if not rows or len(rows) != state.event_sequence:
                raise CryptoLifecycleIntegrityError("event sequence/tail mismatch")
            previous = _GENESIS_HASH
            events = []
            for expected_sequence, row in enumerate(rows, start=1):
                event = _event_from_row(row)
                if event.sequence != expected_sequence or event.previous_event_hash != previous:
                    raise CryptoLifecycleIntegrityError("event chain sequence mismatch")
                if event.event_hash != _event_hash(
                    lifecycle_id=event.lifecycle_id,
                    sequence=event.sequence,
                    event_type=event.event_type,
                    occurred_at=event.occurred_at,
                    payload=event.payload,
                    previous_event_hash=event.previous_event_hash,
                ):
                    raise CryptoLifecycleIntegrityError("event hash mismatch")
                previous = event.event_hash
                events.append(event)
            if state.event_head_hash != previous:
                raise CryptoLifecycleIntegrityError("control head differs from event chain")
        except CryptoLifecycleIntegrityError as exc:
            raise PaperCloseSourceProvenanceConflict("source crypto lifecycle integrity failed") from exc

        if binding.lifecycle_id != package.lifecycle_id:
            raise PaperCloseSourceProvenanceConflict("source lifecycle id differs from prepared package")
        if binding.fingerprint != package.lifecycle_binding_hash:
            raise PaperCloseSourceProvenanceConflict("source lifecycle binding differs from prepared package")
        if binding.entry_client_order_id != package.client_order_id:
            raise PaperCloseSourceProvenanceConflict("source lifecycle client_order_id differs from package")
        if binding.entry_order_fingerprint != package.crypto_order_fingerprint:
            raise PaperCloseSourceProvenanceConflict("source lifecycle entry order differs from package")
        if binding.entry_quantity != package.quantity or binding.symbol != package.symbol:
            raise PaperCloseSourceProvenanceConflict("source lifecycle quantity/symbol differs from package")
        return CryptoLifecycleSnapshot(binding=binding, state=state, events=tuple(events))

    @staticmethod
    def _bind_terminal_truth(*, package, resolution, lifecycle: CryptoLifecycleSnapshot):
        state = lifecycle.state
        if state.status is not CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED:
            raise PaperCloseSourceProvenanceConflict("source lifecycle is not terminal unprotected long exposure")
        if state.entry_attempt_count != 1 or not state.entry_terminal:
            raise PaperCloseSourceProvenanceConflict("source lifecycle does not prove one terminal entry attempt")
        broker_order_id = _text(resolution, "broker_order_id")
        broker_status = _text(resolution, "broker_order_status").strip().lower()
        gross = _decimal(resolution.get("broker_filled_quantity"), "broker_filled_quantity")
        net = _decimal(resolution.get("position_quantity"), "position_quantity")
        if broker_status != "filled":
            raise PaperCloseSourceProvenanceConflict("source broker order is not terminal filled")
        if gross != package.quantity:
            raise PaperCloseSourceProvenanceConflict("terminal gross fill differs from prepared entry quantity")
        try:
            _validate_fee_adjusted_net_position(
                filled_quantity=gross,
                confirmed_net_long_quantity=net,
            )
        except Exception as exc:
            raise PaperCloseSourceProvenanceConflict("terminal net position is not fee-consistent with gross fill") from exc
        if (
            state.entry_broker_order_id != broker_order_id
            or (state.entry_broker_status or "").lower() != broker_status
            or state.entry_filled_quantity != gross
            or state.confirmed_net_long_quantity != net
        ):
            raise PaperCloseSourceProvenanceConflict(
                "terminal reconciliation differs from hash-verified lifecycle truth"
            )
        return gross, net, broker_order_id, broker_status


def _mapping(document: dict[str, object], key: str) -> dict[str, object]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise PaperCloseSourceProvenanceConflict(f"{key} must be an object")
    return value


def _text(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PaperCloseSourceProvenanceConflict(f"{key} must be non-empty text")
    return value


def _decimal(value: object, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PaperCloseSourceProvenanceConflict(f"{label} must be Decimal-compatible") from exc
    if not number.is_finite() or number < 0:
        raise PaperCloseSourceProvenanceConflict(f"{label} must be finite and non-negative")
    return number


def _file_sha256(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PaperCloseSourceProvenanceMissing("cannot hash attempt SQLite database") from exc
    return digest.hexdigest()


def _provenance_hash(payload: dict[str, object]) -> str:
    import json

    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


__all__ = [
    "FirstCanaryCloseSourceReader",
    "PaperCloseSourceProvenance",
    "PaperCloseSourceProvenanceConflict",
    "PaperCloseSourceProvenanceError",
    "PaperCloseSourceProvenanceMissing",
]
