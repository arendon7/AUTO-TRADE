from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import re
import sqlite3

from autotrade.persistence import SQLiteRuntime

from .alpaca_paper_crypto_asset import normalize_crypto_pair
from .alpaca_paper_crypto_order import AlpacaPaperCryptoOrderRequest, CryptoOrderRole


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_GENESIS_HASH = "0" * 64
_ENTRY_OPEN = {"accepted", "pending_new", "new", "partially_filled"}
_ENTRY_TERMINAL = {"filled", "canceled", "expired", "rejected"}
_PROTECTION_OPEN = {"accepted", "pending_new", "new", "partially_filled"}
_PROTECTION_TERMINAL = {"filled", "canceled", "expired", "rejected"}


class CryptoLifecycleError(RuntimeError):
    pass


class CryptoLifecycleConflict(CryptoLifecycleError):
    pass


class CryptoLifecycleIntegrityError(CryptoLifecycleError):
    pass


class CryptoLifecycleBlocked(CryptoLifecycleError):
    pass


class CryptoLifecycleStatus(StrEnum):
    ENTRY_PREPARED = "ENTRY_PREPARED"
    ENTRY_SUBMISSION_UNKNOWN = "ENTRY_SUBMISSION_UNKNOWN"
    ENTRY_ACKNOWLEDGED = "ENTRY_ACKNOWLEDGED"
    ENTRY_PARTIALLY_FILLED = "ENTRY_PARTIALLY_FILLED"
    ENTRY_TERMINAL_NO_FILL = "ENTRY_TERMINAL_NO_FILL"
    ENTRY_FILLED_UNPROTECTED = "ENTRY_FILLED_UNPROTECTED"
    PROTECTION_PREPARED = "PROTECTION_PREPARED"
    PROTECTION_SUBMISSION_UNKNOWN = "PROTECTION_SUBMISSION_UNKNOWN"
    PROTECTED_OPEN = "PROTECTED_OPEN"
    PROTECTION_PARTIALLY_FILLED = "PROTECTION_PARTIALLY_FILLED"
    PROTECTION_AT_RISK = "PROTECTION_AT_RISK"
    FLAT_RECONCILED = "FLAT_RECONCILED"
    HALTED_RECONCILIATION_REQUIRED = "HALTED_RECONCILIATION_REQUIRED"


class CryptoLifecycleEventType(StrEnum):
    PREPARED = "PREPARED"
    ENTRY_SUBMISSION_UNKNOWN = "ENTRY_SUBMISSION_UNKNOWN"
    ENTRY_RECONCILED = "ENTRY_RECONCILED"
    PROTECTION_PREPARED = "PROTECTION_PREPARED"
    PROTECTION_SUBMISSION_UNKNOWN = "PROTECTION_SUBMISSION_UNKNOWN"
    PROTECTION_RECONCILED = "PROTECTION_RECONCILED"
    PROTECTION_TRIGGERED_UNFILLED = "PROTECTION_TRIGGERED_UNFILLED"
    FLAT_RECONCILED = "FLAT_RECONCILED"
    HALTED = "HALTED"


@dataclass(frozen=True, slots=True)
class CryptoLifecycleBinding:
    lifecycle_id: str
    account_attestation_fingerprint: str
    asset_attestation_fingerprint: str
    product_profile_fingerprint: str
    symbol: str
    entry_order_fingerprint: str
    entry_client_order_id: str
    entry_quantity: Decimal
    created_at: datetime

    def __post_init__(self) -> None:
        _validate_id(self.lifecycle_id, "lifecycle_id")
        canonical = normalize_crypto_pair(self.symbol)
        if canonical != self.symbol:
            raise ValueError("lifecycle symbol must be canonical BASE/QUOTE")
        for label, value in (
            ("account_attestation_fingerprint", self.account_attestation_fingerprint),
            ("asset_attestation_fingerprint", self.asset_attestation_fingerprint),
            ("product_profile_fingerprint", self.product_profile_fingerprint),
            ("entry_order_fingerprint", self.entry_order_fingerprint),
        ):
            _validate_hash(value, label)
        _validate_id(self.entry_client_order_id, "entry_client_order_id")
        _positive(self.entry_quantity, "entry_quantity")
        _aware(self.created_at, "created_at")

    @property
    def fingerprint(self) -> str:
        return _hash(_binding_payload(self))


@dataclass(frozen=True, slots=True)
class CryptoLifecycleEvent:
    lifecycle_id: str
    sequence: int
    event_type: CryptoLifecycleEventType
    occurred_at: datetime
    payload: dict[str, object]
    previous_event_hash: str
    event_hash: str


@dataclass(frozen=True, slots=True)
class CryptoLifecycleState:
    lifecycle_id: str
    binding_hash: str
    status: CryptoLifecycleStatus
    event_sequence: int
    event_head_hash: str
    entry_attempt_count: int
    entry_broker_order_id: str | None
    entry_broker_status: str | None
    entry_filled_quantity: Decimal
    entry_terminal: bool
    confirmed_net_long_quantity: Decimal
    protection_order_fingerprint: str | None
    protection_client_order_id: str | None
    protection_quantity: Decimal
    protection_attempt_count: int
    protection_broker_order_id: str | None
    protection_broker_status: str | None
    protection_filled_quantity: Decimal
    updated_at: datetime
    control_hash: str

    @property
    def restart_action(self) -> str:
        if self.status in {
            CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN,
            CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN,
            CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED,
        }:
            return "RECONCILE_ONLY"
        if self.status in {
            CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED,
            CryptoLifecycleStatus.PROTECTION_AT_RISK,
        }:
            return "REDUCE_RISK_OR_PROTECT"
        if self.status in {
            CryptoLifecycleStatus.PROTECTED_OPEN,
            CryptoLifecycleStatus.PROTECTION_PARTIALLY_FILLED,
        }:
            return "MONITOR_AND_RECONCILE"
        if self.status in {
            CryptoLifecycleStatus.ENTRY_PREPARED,
            CryptoLifecycleStatus.ENTRY_ACKNOWLEDGED,
            CryptoLifecycleStatus.ENTRY_PARTIALLY_FILLED,
            CryptoLifecycleStatus.PROTECTION_PREPARED,
        }:
            return "CONTINUE_CERTIFIED_LIFECYCLE"
        return "IDLE"


@dataclass(frozen=True, slots=True)
class CryptoLifecycleSnapshot:
    binding: CryptoLifecycleBinding
    state: CryptoLifecycleState
    events: tuple[CryptoLifecycleEvent, ...]


class SQLiteCryptoPaperLifecycle:
    """Durable crypto entry/protection state machine with no network I/O.

    Network writers must cross UNKNOWN durably before any POST. Unknown states
    never expose a retry permission: restart_action is RECONCILE_ONLY. The first
    R6 crypto canary also requires entry terminality before an opposing sell
    protection can be prepared, avoiding a buy-remnant/sell-protection overlap.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS alpaca_crypto_lifecycle_bindings (
                    lifecycle_id TEXT PRIMARY KEY,
                    entry_client_order_id TEXT NOT NULL UNIQUE,
                    binding_json TEXT NOT NULL,
                    binding_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS alpaca_crypto_lifecycle_events (
                    lifecycle_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    PRIMARY KEY(lifecycle_id, sequence),
                    FOREIGN KEY(lifecycle_id) REFERENCES alpaca_crypto_lifecycle_bindings(lifecycle_id) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS alpaca_crypto_lifecycle_control (
                    lifecycle_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    control_hash TEXT NOT NULL,
                    FOREIGN KEY(lifecycle_id) REFERENCES alpaca_crypto_lifecycle_bindings(lifecycle_id) ON DELETE RESTRICT
                );
                """
            )
        finally:
            conn.close()

    def prepare(self, binding: CryptoLifecycleBinding) -> CryptoLifecycleState:
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM alpaca_crypto_lifecycle_bindings WHERE lifecycle_id = ?",
                (binding.lifecycle_id,),
            ).fetchone()
            by_client = conn.execute(
                "SELECT lifecycle_id FROM alpaca_crypto_lifecycle_bindings WHERE entry_client_order_id = ?",
                (binding.entry_client_order_id,),
            ).fetchone()
            if existing is not None:
                loaded = _binding_from_json(str(existing["binding_json"]))
                if loaded.fingerprint != binding.fingerprint:
                    raise CryptoLifecycleConflict("lifecycle id is already bound to different immutable data")
                if by_client is None or str(by_client["lifecycle_id"]) != binding.lifecycle_id:
                    raise CryptoLifecycleIntegrityError("crypto lifecycle binding indexes disagree")
                _, state, _ = self._verify_locked(conn, binding.lifecycle_id)
                conn.execute("COMMIT")
                return state
            if by_client is not None:
                raise CryptoLifecycleConflict("entry client_order_id already belongs to another lifecycle")

            conn.execute(
                "INSERT INTO alpaca_crypto_lifecycle_bindings VALUES (?, ?, ?, ?)",
                (
                    binding.lifecycle_id,
                    binding.entry_client_order_id,
                    _canonical(_binding_payload(binding)),
                    binding.fingerprint,
                ),
            )
            event = _event(
                lifecycle_id=binding.lifecycle_id,
                sequence=1,
                event_type=CryptoLifecycleEventType.PREPARED,
                occurred_at=binding.created_at,
                payload={"binding_hash": binding.fingerprint},
                previous_event_hash=_GENESIS_HASH,
            )
            _insert_event(conn, event)
            state = CryptoLifecycleState(
                lifecycle_id=binding.lifecycle_id,
                binding_hash=binding.fingerprint,
                status=CryptoLifecycleStatus.ENTRY_PREPARED,
                event_sequence=1,
                event_head_hash=event.event_hash,
                entry_attempt_count=0,
                entry_broker_order_id=None,
                entry_broker_status=None,
                entry_filled_quantity=Decimal("0"),
                entry_terminal=False,
                confirmed_net_long_quantity=Decimal("0"),
                protection_order_fingerprint=None,
                protection_client_order_id=None,
                protection_quantity=Decimal("0"),
                protection_attempt_count=0,
                protection_broker_order_id=None,
                protection_broker_status=None,
                protection_filled_quantity=Decimal("0"),
                updated_at=binding.created_at,
                control_hash="",
            )
            state = _with_control_hash(state)
            _write_state(conn, state)
            conn.execute("COMMIT")
            return state
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def mark_entry_submission_unknown(self, lifecycle_id: str, *, at: datetime) -> CryptoLifecycleState:
        return self._mutate(
            lifecycle_id,
            at=at,
            event_type=CryptoLifecycleEventType.ENTRY_SUBMISSION_UNKNOWN,
            payload={},
            transition=self._entry_unknown_transition,
        )

    @staticmethod
    def _entry_unknown_transition(
        binding: CryptoLifecycleBinding,
        state: CryptoLifecycleState,
        _payload: dict[str, object],
    ) -> CryptoLifecycleState:
        if state.status is not CryptoLifecycleStatus.ENTRY_PREPARED or state.entry_attempt_count != 0:
            raise CryptoLifecycleBlocked("entry submission may cross UNKNOWN exactly once from ENTRY_PREPARED")
        return replace(
            state,
            status=CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN,
            entry_attempt_count=1,
        )

    def reconcile_entry(
        self,
        lifecycle_id: str,
        *,
        broker_order_id: str,
        broker_status: str,
        filled_quantity: Decimal,
        confirmed_net_long_quantity: Decimal,
        at: datetime,
    ) -> CryptoLifecycleState:
        status = broker_status.strip().lower()
        payload = {
            "broker_order_id": broker_order_id,
            "broker_status": status,
            "filled_quantity": _decimal_text(filled_quantity),
            "confirmed_net_long_quantity": _decimal_text(confirmed_net_long_quantity),
        }

        def transition(binding: CryptoLifecycleBinding, state: CryptoLifecycleState, _payload: dict[str, object]) -> CryptoLifecycleState:
            if state.status not in {
                CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN,
                CryptoLifecycleStatus.ENTRY_ACKNOWLEDGED,
                CryptoLifecycleStatus.ENTRY_PARTIALLY_FILLED,
            }:
                raise CryptoLifecycleBlocked("entry reconciliation is not valid from current lifecycle state")
            _validate_id(broker_order_id, "entry broker_order_id")
            _nonnegative(filled_quantity, "filled_quantity")
            _nonnegative(confirmed_net_long_quantity, "confirmed_net_long_quantity")
            if status not in _ENTRY_OPEN | _ENTRY_TERMINAL:
                raise CryptoLifecycleIntegrityError("unsupported entry broker status")
            if state.entry_broker_order_id not in (None, broker_order_id):
                raise CryptoLifecycleIntegrityError("entry broker order id changed")
            if filled_quantity < state.entry_filled_quantity:
                raise CryptoLifecycleIntegrityError("entry cumulative filled quantity regressed")
            if filled_quantity > binding.entry_quantity:
                raise CryptoLifecycleIntegrityError("entry cumulative fill exceeds intended quantity")
            if confirmed_net_long_quantity != filled_quantity:
                raise CryptoLifecycleIntegrityError(
                    "first-canary entry reconciliation requires net long position to equal cumulative entry fills"
                )
            terminal = status in _ENTRY_TERMINAL
            if status == "filled" and filled_quantity != binding.entry_quantity:
                raise CryptoLifecycleIntegrityError("filled entry status requires exact intended quantity")
            if status in {"accepted", "pending_new", "new"} and filled_quantity != 0:
                raise CryptoLifecycleIntegrityError("unfilled entry status may not report cumulative fill")
            if status == "partially_filled" and not (Decimal("0") < filled_quantity < binding.entry_quantity):
                raise CryptoLifecycleIntegrityError("partially_filled entry requires strict partial quantity")

            if terminal and filled_quantity == 0:
                next_status = CryptoLifecycleStatus.ENTRY_TERMINAL_NO_FILL
            elif terminal and filled_quantity > 0:
                next_status = CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED
            elif filled_quantity > 0:
                next_status = CryptoLifecycleStatus.ENTRY_PARTIALLY_FILLED
            else:
                next_status = CryptoLifecycleStatus.ENTRY_ACKNOWLEDGED
            return replace(
                state,
                status=next_status,
                entry_broker_order_id=broker_order_id,
                entry_broker_status=status,
                entry_filled_quantity=filled_quantity,
                entry_terminal=terminal,
                confirmed_net_long_quantity=confirmed_net_long_quantity,
            )

        return self._mutate(
            lifecycle_id,
            at=at,
            event_type=CryptoLifecycleEventType.ENTRY_RECONCILED,
            payload=payload,
            transition=transition,
        )

    def prepare_protection(
        self,
        lifecycle_id: str,
        *,
        order: AlpacaPaperCryptoOrderRequest,
        at: datetime,
    ) -> CryptoLifecycleState:
        if order.role is not CryptoOrderRole.PROTECTION:
            raise CryptoLifecycleBlocked("protection lifecycle requires a PROTECTION order request")
        payload = {
            "protection_order_fingerprint": order.fingerprint,
            "protection_client_order_id": order.client_order_id,
            "protection_quantity": _decimal_text(order.quantity),
        }

        def transition(binding: CryptoLifecycleBinding, state: CryptoLifecycleState, _payload: dict[str, object]) -> CryptoLifecycleState:
            if state.status is not CryptoLifecycleStatus.ENTRY_FILLED_UNPROTECTED:
                raise CryptoLifecycleBlocked("protection can be prepared only after terminal reconciled entry exposure")
            if not state.entry_terminal:
                raise CryptoLifecycleBlocked("remaining entry order must be terminal before opposing protection")
            if state.confirmed_net_long_quantity <= 0:
                raise CryptoLifecycleIntegrityError("cannot protect a non-positive confirmed long position")
            if order.symbol != binding.symbol:
                raise CryptoLifecycleIntegrityError("protection symbol differs from lifecycle")
            if order.asset_attestation_fingerprint != binding.asset_attestation_fingerprint:
                raise CryptoLifecycleIntegrityError("protection asset evidence differs from lifecycle")
            if order.product_profile_fingerprint != binding.product_profile_fingerprint:
                raise CryptoLifecycleIntegrityError("protection product profile differs from lifecycle")
            if order.quantity != state.confirmed_net_long_quantity:
                raise CryptoLifecycleBlocked("first-canary protection must cover exactly the confirmed net long quantity")
            return replace(
                state,
                status=CryptoLifecycleStatus.PROTECTION_PREPARED,
                protection_order_fingerprint=order.fingerprint,
                protection_client_order_id=order.client_order_id,
                protection_quantity=order.quantity,
            )

        return self._mutate(
            lifecycle_id,
            at=at,
            event_type=CryptoLifecycleEventType.PROTECTION_PREPARED,
            payload=payload,
            transition=transition,
        )

    def mark_protection_submission_unknown(self, lifecycle_id: str, *, at: datetime) -> CryptoLifecycleState:
        def transition(_binding: CryptoLifecycleBinding, state: CryptoLifecycleState, _payload: dict[str, object]) -> CryptoLifecycleState:
            if state.status is not CryptoLifecycleStatus.PROTECTION_PREPARED or state.protection_attempt_count != 0:
                raise CryptoLifecycleBlocked(
                    "protection submission may cross UNKNOWN exactly once from PROTECTION_PREPARED"
                )
            return replace(
                state,
                status=CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN,
                protection_attempt_count=1,
            )

        return self._mutate(
            lifecycle_id,
            at=at,
            event_type=CryptoLifecycleEventType.PROTECTION_SUBMISSION_UNKNOWN,
            payload={},
            transition=transition,
        )

    def reconcile_protection(
        self,
        lifecycle_id: str,
        *,
        broker_order_id: str,
        broker_status: str,
        filled_quantity: Decimal,
        confirmed_net_long_quantity: Decimal,
        at: datetime,
    ) -> CryptoLifecycleState:
        status = broker_status.strip().lower()
        payload = {
            "broker_order_id": broker_order_id,
            "broker_status": status,
            "filled_quantity": _decimal_text(filled_quantity),
            "confirmed_net_long_quantity": _decimal_text(confirmed_net_long_quantity),
        }

        def transition(_binding: CryptoLifecycleBinding, state: CryptoLifecycleState, _payload: dict[str, object]) -> CryptoLifecycleState:
            if state.status not in {
                CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN,
                CryptoLifecycleStatus.PROTECTED_OPEN,
                CryptoLifecycleStatus.PROTECTION_PARTIALLY_FILLED,
                CryptoLifecycleStatus.PROTECTION_AT_RISK,
            }:
                raise CryptoLifecycleBlocked("protection reconciliation is not valid from current state")
            _validate_id(broker_order_id, "protection broker_order_id")
            _nonnegative(filled_quantity, "protection filled_quantity")
            _nonnegative(confirmed_net_long_quantity, "confirmed_net_long_quantity")
            if status not in _PROTECTION_OPEN | _PROTECTION_TERMINAL:
                raise CryptoLifecycleIntegrityError("unsupported protection broker status")
            if state.protection_broker_order_id not in (None, broker_order_id):
                raise CryptoLifecycleIntegrityError("protection broker order id changed")
            if filled_quantity < state.protection_filled_quantity:
                raise CryptoLifecycleIntegrityError("protection cumulative fill regressed")
            if filled_quantity > state.protection_quantity:
                raise CryptoLifecycleIntegrityError("protection fill exceeds protected quantity")
            expected_net = state.entry_filled_quantity - filled_quantity
            if expected_net < 0 or confirmed_net_long_quantity != expected_net:
                raise CryptoLifecycleIntegrityError("protection fill and confirmed net position disagree")

            if status == "filled":
                if filled_quantity != state.protection_quantity or confirmed_net_long_quantity != 0:
                    raise CryptoLifecycleIntegrityError("filled protection must reconcile account flat")
                next_status = CryptoLifecycleStatus.FLAT_RECONCILED
            elif status == "partially_filled":
                if not (Decimal("0") < filled_quantity < state.protection_quantity):
                    raise CryptoLifecycleIntegrityError("partial protection requires strict partial quantity")
                next_status = CryptoLifecycleStatus.PROTECTION_PARTIALLY_FILLED
            elif status in {"canceled", "expired", "rejected"}:
                if confirmed_net_long_quantity == 0:
                    next_status = CryptoLifecycleStatus.FLAT_RECONCILED
                else:
                    next_status = CryptoLifecycleStatus.PROTECTION_AT_RISK
            else:
                if filled_quantity != 0:
                    raise CryptoLifecycleIntegrityError("open protection status with fills must be partially_filled")
                next_status = CryptoLifecycleStatus.PROTECTED_OPEN
            return replace(
                state,
                status=next_status,
                protection_broker_order_id=broker_order_id,
                protection_broker_status=status,
                protection_filled_quantity=filled_quantity,
                confirmed_net_long_quantity=confirmed_net_long_quantity,
            )

        return self._mutate(
            lifecycle_id,
            at=at,
            event_type=CryptoLifecycleEventType.PROTECTION_RECONCILED,
            payload=payload,
            transition=transition,
        )

    def mark_protection_triggered_unfilled(self, lifecycle_id: str, *, at: datetime) -> CryptoLifecycleState:
        def transition(_binding: CryptoLifecycleBinding, state: CryptoLifecycleState, _payload: dict[str, object]) -> CryptoLifecycleState:
            if state.status is not CryptoLifecycleStatus.PROTECTED_OPEN:
                raise CryptoLifecycleBlocked("stop-limit trigger risk may be marked only from PROTECTED_OPEN")
            if state.protection_filled_quantity != 0 or state.confirmed_net_long_quantity <= 0:
                raise CryptoLifecycleIntegrityError("triggered-unfilled marker requires remaining long exposure")
            return replace(state, status=CryptoLifecycleStatus.PROTECTION_AT_RISK)

        return self._mutate(
            lifecycle_id,
            at=at,
            event_type=CryptoLifecycleEventType.PROTECTION_TRIGGERED_UNFILLED,
            payload={"reason": "STOP_LIMIT_TRIGGERED_WITH_REMAINING_POSITION"},
            transition=transition,
        )

    def reconcile_flat(self, lifecycle_id: str, *, open_order_count: int, at: datetime) -> CryptoLifecycleState:
        if isinstance(open_order_count, bool) or not isinstance(open_order_count, int) or open_order_count < 0:
            raise ValueError("open_order_count must be integer >= 0")

        def transition(_binding: CryptoLifecycleBinding, state: CryptoLifecycleState, _payload: dict[str, object]) -> CryptoLifecycleState:
            if state.confirmed_net_long_quantity != 0:
                raise CryptoLifecycleBlocked("cannot reconcile flat while confirmed position is non-zero")
            if open_order_count != 0:
                raise CryptoLifecycleBlocked("cannot reconcile flat while broker open orders remain")
            return replace(state, status=CryptoLifecycleStatus.FLAT_RECONCILED)

        return self._mutate(
            lifecycle_id,
            at=at,
            event_type=CryptoLifecycleEventType.FLAT_RECONCILED,
            payload={"open_order_count": open_order_count},
            transition=transition,
        )

    def halt(self, lifecycle_id: str, *, reason: str, at: datetime) -> CryptoLifecycleState:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 512:
            raise ValueError("halt reason is required and bounded")

        def transition(_binding: CryptoLifecycleBinding, state: CryptoLifecycleState, _payload: dict[str, object]) -> CryptoLifecycleState:
            if state.status is CryptoLifecycleStatus.FLAT_RECONCILED:
                raise CryptoLifecycleBlocked("flat reconciled lifecycle does not require halt")
            return replace(state, status=CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED)

        return self._mutate(
            lifecycle_id,
            at=at,
            event_type=CryptoLifecycleEventType.HALTED,
            payload={"reason": reason.strip()},
            transition=transition,
        )

    def snapshot(self, lifecycle_id: str) -> CryptoLifecycleSnapshot:
        conn = self._runtime.connect()
        try:
            binding, state, events = self._verify_locked(conn, lifecycle_id)
            return CryptoLifecycleSnapshot(binding=binding, state=state, events=events)
        finally:
            conn.close()

    def _mutate(
        self,
        lifecycle_id: str,
        *,
        at: datetime,
        event_type: CryptoLifecycleEventType,
        payload: dict[str, object],
        transition,
    ) -> CryptoLifecycleState:
        _validate_id(lifecycle_id, "lifecycle_id")
        _aware(at, "at")
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            binding, state, _ = self._verify_locked(conn, lifecycle_id)
            next_state = transition(binding, state, payload)
            event = _event(
                lifecycle_id=lifecycle_id,
                sequence=state.event_sequence + 1,
                event_type=event_type,
                occurred_at=at,
                payload=payload,
                previous_event_hash=state.event_head_hash,
            )
            _insert_event(conn, event)
            next_state = replace(
                next_state,
                event_sequence=event.sequence,
                event_head_hash=event.event_hash,
                updated_at=at,
                control_hash="",
            )
            next_state = _with_control_hash(next_state)
            _write_state(conn, next_state)
            conn.execute("COMMIT")
            return next_state
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _verify_locked(
        self,
        conn: sqlite3.Connection,
        lifecycle_id: str,
    ) -> tuple[CryptoLifecycleBinding, CryptoLifecycleState, tuple[CryptoLifecycleEvent, ...]]:
        binding_row = conn.execute(
            "SELECT * FROM alpaca_crypto_lifecycle_bindings WHERE lifecycle_id = ?",
            (lifecycle_id,),
        ).fetchone()
        state_row = conn.execute(
            "SELECT * FROM alpaca_crypto_lifecycle_control WHERE lifecycle_id = ?",
            (lifecycle_id,),
        ).fetchone()
        if binding_row is None or state_row is None:
            raise CryptoLifecycleIntegrityError("crypto lifecycle is missing durable binding/control")
        binding = _binding_from_json(str(binding_row["binding_json"]))
        if binding.fingerprint != str(binding_row["binding_hash"]):
            raise CryptoLifecycleIntegrityError("crypto lifecycle binding hash mismatch")
        state = _state_from_json(str(state_row["state_json"]), control_hash=str(state_row["control_hash"]))
        if state.binding_hash != binding.fingerprint or state.lifecycle_id != binding.lifecycle_id:
            raise CryptoLifecycleIntegrityError("crypto lifecycle control is bound to wrong lifecycle")
        if state.control_hash != _state_hash(replace(state, control_hash="")):
            raise CryptoLifecycleIntegrityError("crypto lifecycle control hash mismatch")

        rows = conn.execute(
            "SELECT * FROM alpaca_crypto_lifecycle_events WHERE lifecycle_id = ? ORDER BY sequence",
            (lifecycle_id,),
        ).fetchall()
        if not rows or len(rows) != state.event_sequence:
            raise CryptoLifecycleIntegrityError("crypto lifecycle event sequence/tail mismatch")
        events: list[CryptoLifecycleEvent] = []
        previous = _GENESIS_HASH
        for expected_sequence, row in enumerate(rows, start=1):
            event = _event_from_row(row)
            if event.sequence != expected_sequence or event.previous_event_hash != previous:
                raise CryptoLifecycleIntegrityError("crypto lifecycle event chain sequence mismatch")
            expected_hash = _event_hash(
                lifecycle_id=event.lifecycle_id,
                sequence=event.sequence,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                payload=event.payload,
                previous_event_hash=event.previous_event_hash,
            )
            if event.event_hash != expected_hash:
                raise CryptoLifecycleIntegrityError("crypto lifecycle event hash mismatch")
            previous = event.event_hash
            events.append(event)
        if state.event_head_hash != previous:
            raise CryptoLifecycleIntegrityError("crypto lifecycle control head differs from event chain")
        return binding, state, tuple(events)


def _binding_payload(value: CryptoLifecycleBinding) -> dict[str, object]:
    return {
        "lifecycle_id": value.lifecycle_id,
        "account_attestation_fingerprint": value.account_attestation_fingerprint,
        "asset_attestation_fingerprint": value.asset_attestation_fingerprint,
        "product_profile_fingerprint": value.product_profile_fingerprint,
        "symbol": value.symbol,
        "entry_order_fingerprint": value.entry_order_fingerprint,
        "entry_client_order_id": value.entry_client_order_id,
        "entry_quantity": _decimal_text(value.entry_quantity),
        "created_at": value.created_at.astimezone(timezone.utc).isoformat(),
    }


def _state_payload(value: CryptoLifecycleState) -> dict[str, object]:
    return {
        "lifecycle_id": value.lifecycle_id,
        "binding_hash": value.binding_hash,
        "status": value.status.value,
        "event_sequence": value.event_sequence,
        "event_head_hash": value.event_head_hash,
        "entry_attempt_count": value.entry_attempt_count,
        "entry_broker_order_id": value.entry_broker_order_id,
        "entry_broker_status": value.entry_broker_status,
        "entry_filled_quantity": _decimal_text(value.entry_filled_quantity),
        "entry_terminal": value.entry_terminal,
        "confirmed_net_long_quantity": _decimal_text(value.confirmed_net_long_quantity),
        "protection_order_fingerprint": value.protection_order_fingerprint,
        "protection_client_order_id": value.protection_client_order_id,
        "protection_quantity": _decimal_text(value.protection_quantity),
        "protection_attempt_count": value.protection_attempt_count,
        "protection_broker_order_id": value.protection_broker_order_id,
        "protection_broker_status": value.protection_broker_status,
        "protection_filled_quantity": _decimal_text(value.protection_filled_quantity),
        "updated_at": value.updated_at.astimezone(timezone.utc).isoformat(),
    }


def _with_control_hash(value: CryptoLifecycleState) -> CryptoLifecycleState:
    return replace(value, control_hash=_state_hash(replace(value, control_hash="")))


def _state_hash(value: CryptoLifecycleState) -> str:
    return _hash(_state_payload(value))


def _event(
    *,
    lifecycle_id: str,
    sequence: int,
    event_type: CryptoLifecycleEventType,
    occurred_at: datetime,
    payload: dict[str, object],
    previous_event_hash: str,
) -> CryptoLifecycleEvent:
    return CryptoLifecycleEvent(
        lifecycle_id=lifecycle_id,
        sequence=sequence,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=payload,
        previous_event_hash=previous_event_hash,
        event_hash=_event_hash(
            lifecycle_id=lifecycle_id,
            sequence=sequence,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
            previous_event_hash=previous_event_hash,
        ),
    )


def _event_hash(
    *,
    lifecycle_id: str,
    sequence: int,
    event_type: CryptoLifecycleEventType,
    occurred_at: datetime,
    payload: dict[str, object],
    previous_event_hash: str,
) -> str:
    return _hash(
        {
            "lifecycle_id": lifecycle_id,
            "sequence": sequence,
            "event_type": event_type.value,
            "occurred_at": occurred_at.astimezone(timezone.utc).isoformat(),
            "payload": payload,
            "previous_event_hash": previous_event_hash,
        }
    )


def _insert_event(conn: sqlite3.Connection, event: CryptoLifecycleEvent) -> None:
    conn.execute(
        "INSERT INTO alpaca_crypto_lifecycle_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event.lifecycle_id,
            event.sequence,
            event.event_type.value,
            event.occurred_at.astimezone(timezone.utc).isoformat(),
            _canonical(event.payload),
            event.previous_event_hash,
            event.event_hash,
        ),
    )


def _write_state(conn: sqlite3.Connection, state: CryptoLifecycleState) -> None:
    conn.execute(
        """
        INSERT INTO alpaca_crypto_lifecycle_control(lifecycle_id, state_json, control_hash)
        VALUES (?, ?, ?)
        ON CONFLICT(lifecycle_id) DO UPDATE SET
            state_json=excluded.state_json,
            control_hash=excluded.control_hash
        """,
        (state.lifecycle_id, _canonical(_state_payload(state)), state.control_hash),
    )


def _binding_from_json(text: str) -> CryptoLifecycleBinding:
    try:
        value = json.loads(text)
        return CryptoLifecycleBinding(
            lifecycle_id=str(value["lifecycle_id"]),
            account_attestation_fingerprint=str(value["account_attestation_fingerprint"]),
            asset_attestation_fingerprint=str(value["asset_attestation_fingerprint"]),
            product_profile_fingerprint=str(value["product_profile_fingerprint"]),
            symbol=str(value["symbol"]),
            entry_order_fingerprint=str(value["entry_order_fingerprint"]),
            entry_client_order_id=str(value["entry_client_order_id"]),
            entry_quantity=Decimal(str(value["entry_quantity"])),
            created_at=datetime.fromisoformat(str(value["created_at"])),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError) as exc:
        raise CryptoLifecycleIntegrityError("crypto lifecycle binding JSON is invalid") from exc


def _state_from_json(text: str, *, control_hash: str) -> CryptoLifecycleState:
    try:
        value = json.loads(text)
        return CryptoLifecycleState(
            lifecycle_id=str(value["lifecycle_id"]),
            binding_hash=str(value["binding_hash"]),
            status=CryptoLifecycleStatus(str(value["status"])),
            event_sequence=int(value["event_sequence"]),
            event_head_hash=str(value["event_head_hash"]),
            entry_attempt_count=int(value["entry_attempt_count"]),
            entry_broker_order_id=value.get("entry_broker_order_id"),
            entry_broker_status=value.get("entry_broker_status"),
            entry_filled_quantity=Decimal(str(value["entry_filled_quantity"])),
            entry_terminal=bool(value["entry_terminal"]),
            confirmed_net_long_quantity=Decimal(str(value["confirmed_net_long_quantity"])),
            protection_order_fingerprint=value.get("protection_order_fingerprint"),
            protection_client_order_id=value.get("protection_client_order_id"),
            protection_quantity=Decimal(str(value["protection_quantity"])),
            protection_attempt_count=int(value["protection_attempt_count"]),
            protection_broker_order_id=value.get("protection_broker_order_id"),
            protection_broker_status=value.get("protection_broker_status"),
            protection_filled_quantity=Decimal(str(value["protection_filled_quantity"])),
            updated_at=datetime.fromisoformat(str(value["updated_at"])),
            control_hash=control_hash,
        )
    except (KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError) as exc:
        raise CryptoLifecycleIntegrityError("crypto lifecycle state JSON is invalid") from exc


def _event_from_row(row: sqlite3.Row) -> CryptoLifecycleEvent:
    try:
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            raise TypeError("event payload must be object")
        return CryptoLifecycleEvent(
            lifecycle_id=str(row["lifecycle_id"]),
            sequence=int(row["sequence"]),
            event_type=CryptoLifecycleEventType(str(row["event_type"])),
            occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
            payload=payload,
            previous_event_hash=str(row["previous_event_hash"]),
            event_hash=str(row["event_hash"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CryptoLifecycleIntegrityError("crypto lifecycle event row is invalid") from exc


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _validate_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _positive(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{label} must be finite and positive")


def _nonnegative(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{label} must be finite and non-negative")


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


__all__ = [
    "CryptoLifecycleBinding",
    "CryptoLifecycleBlocked",
    "CryptoLifecycleConflict",
    "CryptoLifecycleError",
    "CryptoLifecycleIntegrityError",
    "CryptoLifecycleSnapshot",
    "CryptoLifecycleState",
    "CryptoLifecycleStatus",
    "SQLiteCryptoPaperLifecycle",
]
