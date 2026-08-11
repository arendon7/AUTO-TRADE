from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .domain import OrderIntent, OrderRecord, OrderStatus, OrderType, PortfolioSnapshot, Side
from .ledger import DuplicateLedgerEvent, LedgerEvent
from .portfolio_integrity import PortfolioIntegrityError, validate_portfolio_snapshot
from .state import (
    PortfolioNotInitialized,
    ReservationConflict,
    ReservationRace,
    ReservationStatus,
    ReservationView,
    RiskReservation,
    SafetyControlState,
    VersionedPortfolioSnapshot,
    apply_fill_to_portfolio,
)


class LedgerIntegrityError(RuntimeError):
    pass


class SQLiteRuntime:
    """Small SQLite runtime with conservative durability settings.

    Each operation gets its own connection so independent processes coordinate
    through SQLite locking rather than process-local locks.
    """

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5_000) -> None:
        self.path = str(path)
        self.busy_timeout_ms = busy_timeout_ms
        if self.path == ":memory:":
            raise ValueError("use a filesystem path for durable SQLite state")
        Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _initialize_schema(self) -> None:
        conn = self.connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ledger_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS orders (
                    idempotency_key TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL UNIQUE,
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS portfolio_state (
                    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                    version INTEGER NOT NULL CHECK(version > 0),
                    snapshot_json TEXT NOT NULL,
                    snapshot_hash TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS portfolio_applied_orders (
                    order_id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS safety_state (
                    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                    kill_switch_active INTEGER NOT NULL CHECK(kill_switch_active IN (0, 1)),
                    kill_switch_reason TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version >= 0),
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS reservation_meta (
                    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                    generation INTEGER NOT NULL CHECK(generation >= 0)
                );

                CREATE TABLE IF NOT EXISTS risk_reservations (
                    idempotency_key TEXT PRIMARY KEY,
                    reservation_id TEXT NOT NULL UNIQUE,
                    intent_fingerprint TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    signed_notional TEXT NOT NULL,
                    status TEXT NOT NULL,
                    portfolio_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                INSERT OR IGNORE INTO safety_state(
                    singleton_id, kill_switch_active, kill_switch_reason, version, updated_at
                ) VALUES (1, 0, '', 0, NULL);

                INSERT OR IGNORE INTO reservation_meta(singleton_id, generation) VALUES (1, 0);
                """
            )
            _ensure_portfolio_state_integrity_schema(conn)
        finally:
            conn.close()


class SQLiteEventLedger:
    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime

    def append(self, event: LedgerEvent) -> None:
        payload_json = json.dumps(dict(event.payload), sort_keys=True, separators=(",", ":"))
        occurred_at = event.occurred_at.isoformat()
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT event_hash FROM ledger_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = row["event_hash"] if row is not None else "GENESIS"
            event_hash = _ledger_hash(
                prev_hash=prev_hash,
                event_id=event.event_id,
                event_type=event.event_type,
                occurred_at=occurred_at,
                payload_json=payload_json,
            )
            try:
                conn.execute(
                    """
                    INSERT INTO ledger_events(
                        event_id, event_type, occurred_at, payload_json, prev_hash, event_hash
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (event.event_id, event.event_type, occurred_at, payload_json, prev_hash, event_hash),
                )
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise DuplicateLedgerEvent(event.event_id) from exc
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def all_events(self) -> tuple[LedgerEvent, ...]:
        conn = self._runtime.connect()
        try:
            rows = conn.execute(
                "SELECT event_id, event_type, occurred_at, payload_json FROM ledger_events ORDER BY seq"
            ).fetchall()
            return tuple(
                LedgerEvent(
                    event_id=row["event_id"],
                    event_type=row["event_type"],
                    occurred_at=datetime.fromisoformat(row["occurred_at"]),
                    payload=json.loads(row["payload_json"]),
                )
                for row in rows
            )
        finally:
            conn.close()

    def verify_integrity(self) -> bool:
        conn = self._runtime.connect()
        try:
            rows = conn.execute(
                """
                SELECT event_id, event_type, occurred_at, payload_json, prev_hash, event_hash
                FROM ledger_events ORDER BY seq
                """
            ).fetchall()
        finally:
            conn.close()

        expected_prev = "GENESIS"
        for row in rows:
            if row["prev_hash"] != expected_prev:
                raise LedgerIntegrityError(f"broken prev_hash before event {row['event_id']}")
            expected_hash = _ledger_hash(
                prev_hash=expected_prev,
                event_id=row["event_id"],
                event_type=row["event_type"],
                occurred_at=row["occurred_at"],
                payload_json=row["payload_json"],
            )
            if row["event_hash"] != expected_hash:
                raise LedgerIntegrityError(f"event hash mismatch: {row['event_id']}")
            expected_prev = expected_hash
        return True


class SQLiteOrderStore:
    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime

    def get_by_idempotency_key(self, key: str) -> OrderRecord | None:
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT record_json FROM orders WHERE idempotency_key = ?", (key,)
            ).fetchone()
            return _order_from_json(row["record_json"]) if row is not None else None
        finally:
            conn.close()

    def get_by_order_id(self, order_id: str) -> OrderRecord | None:
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT record_json FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            return _order_from_json(row["record_json"]) if row is not None else None
        finally:
            conn.close()

    def create_if_absent(self, order: OrderRecord) -> tuple[bool, OrderRecord]:
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT record_json FROM orders WHERE idempotency_key = ?",
                (order.intent.idempotency_key,),
            ).fetchone()
            if row is not None:
                conn.execute("COMMIT")
                return False, _order_from_json(row["record_json"])
            try:
                conn.execute(
                    """
                    INSERT INTO orders(idempotency_key, order_id, record_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        order.intent.idempotency_key,
                        order.order_id,
                        _order_to_json(order),
                        order.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise ValueError(f"duplicate order identity: {order.order_id}") from exc
            conn.execute("COMMIT")
            return True, order
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def update(self, order: OrderRecord) -> None:
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT order_id FROM orders WHERE idempotency_key = ?",
                (order.intent.idempotency_key,),
            ).fetchone()
            if row is None or row["order_id"] != order.order_id:
                conn.execute("ROLLBACK")
                raise KeyError(order.order_id)
            updated_at = (order.submitted_at or order.created_at).isoformat()
            conn.execute(
                "UPDATE orders SET record_json = ?, updated_at = ? WHERE idempotency_key = ?",
                (_order_to_json(order), updated_at, order.intent.idempotency_key),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def all_orders(self) -> tuple[OrderRecord, ...]:
        conn = self._runtime.connect()
        try:
            rows = conn.execute("SELECT record_json FROM orders ORDER BY rowid").fetchall()
            return tuple(_order_from_json(row["record_json"]) for row in rows)
        finally:
            conn.close()


class SQLitePortfolioStore:
    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime

    def initialize(self, snapshot: PortfolioSnapshot, *, now: datetime) -> VersionedPortfolioSnapshot:
        snapshot_json, snapshot_hash = _portfolio_for_storage(snapshot)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT version, snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO portfolio_state(
                        singleton_id, version, snapshot_json, snapshot_hash, updated_at
                    ) VALUES (1, 1, ?, ?, ?)
                    """,
                    (snapshot_json, snapshot_hash, now.isoformat()),
                )
                conn.execute("COMMIT")
                return VersionedPortfolioSnapshot(version=1, snapshot=snapshot)
            current = _portfolio_from_storage(
                snapshot_json=row["snapshot_json"], snapshot_hash=row["snapshot_hash"]
            )
            conn.execute("COMMIT")
            return VersionedPortfolioSnapshot(version=int(row["version"]), snapshot=current)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self) -> VersionedPortfolioSnapshot:
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT version, snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"
            ).fetchone()
            if row is None:
                raise PortfolioNotInitialized("portfolio state is not initialized")
            return VersionedPortfolioSnapshot(
                version=int(row["version"]),
                snapshot=_portfolio_from_storage(
                    snapshot_json=row["snapshot_json"], snapshot_hash=row["snapshot_hash"]
                ),
            )
        finally:
            conn.close()

    def compare_and_set(
        self,
        *,
        expected_version: int,
        snapshot: PortfolioSnapshot,
        now: datetime,
    ) -> VersionedPortfolioSnapshot | None:
        snapshot_json, snapshot_hash = _portfolio_for_storage(snapshot)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT version, snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"
            ).fetchone()
            if row is None or int(row["version"]) != expected_version:
                conn.execute("ROLLBACK")
                return None
            _portfolio_from_storage(
                snapshot_json=row["snapshot_json"], snapshot_hash=row["snapshot_hash"]
            )
            cursor = conn.execute(
                """
                UPDATE portfolio_state
                SET version = version + 1, snapshot_json = ?, snapshot_hash = ?, updated_at = ?
                WHERE singleton_id = 1 AND version = ?
                """,
                (snapshot_json, snapshot_hash, now.isoformat(), expected_version),
            )
            if cursor.rowcount != 1:
                conn.execute("ROLLBACK")
                return None
            conn.execute("COMMIT")
            return VersionedPortfolioSnapshot(version=expected_version + 1, snapshot=snapshot)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def set_reconciliation_status(
        self,
        *,
        reconciliation_ok: bool,
        broker_state_known: bool,
        now: datetime,
    ) -> VersionedPortfolioSnapshot:
        if not isinstance(reconciliation_ok, bool) or not isinstance(broker_state_known, bool):
            raise PortfolioIntegrityError("reconciliation status flags must be boolean")
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT version, snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise PortfolioNotInitialized("portfolio state is not initialized")
            current = _portfolio_from_storage(
                snapshot_json=row["snapshot_json"], snapshot_hash=row["snapshot_hash"]
            )
            version = int(row["version"])
            if (
                current.reconciliation_ok == reconciliation_ok
                and current.broker_state_known == broker_state_known
            ):
                conn.execute("COMMIT")
                return VersionedPortfolioSnapshot(version=version, snapshot=current)
            updated = replace(
                current,
                reconciliation_ok=reconciliation_ok,
                broker_state_known=broker_state_known,
            )
            snapshot_json, snapshot_hash = _portfolio_for_storage(updated)
            conn.execute(
                """
                UPDATE portfolio_state
                SET version = ?, snapshot_json = ?, snapshot_hash = ?, updated_at = ?
                WHERE singleton_id = 1
                """,
                (version + 1, snapshot_json, snapshot_hash, now.isoformat()),
            )
            conn.execute("COMMIT")
            return VersionedPortfolioSnapshot(version=version + 1, snapshot=updated)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def apply_order_result(self, order: OrderRecord, *, now: datetime) -> VersionedPortfolioSnapshot:
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT version, snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise PortfolioNotInitialized("portfolio state is not initialized")
            version = int(row["version"])
            current = _portfolio_from_storage(
                snapshot_json=row["snapshot_json"], snapshot_hash=row["snapshot_hash"]
            )
            already_applied = conn.execute(
                "SELECT 1 FROM portfolio_applied_orders WHERE order_id = ?", (order.order_id,)
            ).fetchone()
            if already_applied is not None or order.filled_quantity <= 0:
                conn.execute("COMMIT")
                return VersionedPortfolioSnapshot(version=version, snapshot=current)

            updated = apply_fill_to_portfolio(current, order)
            snapshot_json, snapshot_hash = _portfolio_for_storage(updated)
            conn.execute(
                """
                UPDATE portfolio_state
                SET version = ?, snapshot_json = ?, snapshot_hash = ?, updated_at = ?
                WHERE singleton_id = 1
                """,
                (version + 1, snapshot_json, snapshot_hash, now.isoformat()),
            )
            conn.execute(
                "INSERT INTO portfolio_applied_orders(order_id, applied_at) VALUES (?, ?)",
                (order.order_id, now.isoformat()),
            )
            conn.execute("COMMIT")
            return VersionedPortfolioSnapshot(version=version + 1, snapshot=updated)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


class SQLiteSafetyStateStore:
    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime

    def get(self) -> SafetyControlState:
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                """
                SELECT kill_switch_active, kill_switch_reason, version, updated_at
                FROM safety_state WHERE singleton_id = 1
                """
            ).fetchone()
            assert row is not None
            return SafetyControlState(
                kill_switch_active=bool(row["kill_switch_active"]),
                kill_switch_reason=row["kill_switch_reason"],
                version=int(row["version"]),
                updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            )
        finally:
            conn.close()

    def activate(self, *, reason: str, now: datetime) -> SafetyControlState:
        return self._write(active=True, reason=reason, now=now)

    def reset(self, *, now: datetime) -> SafetyControlState:
        return self._write(active=False, reason="", now=now)

    def _write(self, *, active: bool, reason: str, now: datetime) -> SafetyControlState:
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT version FROM safety_state WHERE singleton_id = 1").fetchone()
            assert row is not None
            version = int(row["version"]) + 1
            conn.execute(
                """
                UPDATE safety_state
                SET kill_switch_active = ?, kill_switch_reason = ?, version = ?, updated_at = ?
                WHERE singleton_id = 1
                """,
                (1 if active else 0, reason, version, now.isoformat()),
            )
            conn.execute("COMMIT")
            return SafetyControlState(
                kill_switch_active=active,
                kill_switch_reason=reason,
                version=version,
                updated_at=now,
            )
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


class SQLiteReservationStore:
    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime

    def active_view(self) -> ReservationView:
        conn = self._runtime.connect()
        try:
            generation = int(
                conn.execute(
                    "SELECT generation FROM reservation_meta WHERE singleton_id = 1"
                ).fetchone()["generation"]
            )
            rows = conn.execute(
                """
                SELECT * FROM risk_reservations
                WHERE status != ?
                ORDER BY created_at, reservation_id
                """,
                (ReservationStatus.RELEASED.value,),
            ).fetchall()
            return ReservationView(
                generation=generation,
                reservations=tuple(_reservation_from_row(row) for row in rows),
            )
        finally:
            conn.close()

    def reserve(
        self,
        reservation: RiskReservation,
        *,
        expected_generation: int,
        expected_portfolio_version: int,
    ) -> RiskReservation:
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_row = conn.execute(
                "SELECT * FROM risk_reservations WHERE idempotency_key = ?",
                (reservation.idempotency_key,),
            ).fetchone()
            if existing_row is not None:
                existing = _reservation_from_row(existing_row)
                if existing.intent_fingerprint != reservation.intent_fingerprint:
                    conn.execute("ROLLBACK")
                    raise ReservationConflict(reservation.idempotency_key)
                conn.execute("COMMIT")
                return existing

            generation = int(
                conn.execute(
                    "SELECT generation FROM reservation_meta WHERE singleton_id = 1"
                ).fetchone()["generation"]
            )
            portfolio_row = conn.execute(
                """
                SELECT version, snapshot_json, snapshot_hash
                FROM portfolio_state WHERE singleton_id = 1
                """
            ).fetchone()
            if portfolio_row is None:
                conn.execute("ROLLBACK")
                raise PortfolioNotInitialized("portfolio state is not initialized")
            _portfolio_from_storage(
                snapshot_json=portfolio_row["snapshot_json"],
                snapshot_hash=portfolio_row["snapshot_hash"],
            )
            portfolio_version = int(portfolio_row["version"])
            if generation != expected_generation or portfolio_version != expected_portfolio_version:
                conn.execute("ROLLBACK")
                raise ReservationRace(
                    f"state changed: reservations={generation}/{expected_generation}, "
                    f"portfolio={portfolio_version}/{expected_portfolio_version}"
                )

            conn.execute(
                """
                INSERT INTO risk_reservations(
                    idempotency_key, reservation_id, intent_fingerprint, strategy_id, symbol,
                    signed_notional, status, portfolio_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reservation.idempotency_key,
                    reservation.reservation_id,
                    reservation.intent_fingerprint,
                    reservation.strategy_id,
                    reservation.symbol,
                    reservation.signed_notional,
                    reservation.status.value,
                    reservation.portfolio_version,
                    reservation.created_at.isoformat(),
                    reservation.updated_at.isoformat(),
                ),
            )
            conn.execute(
                "UPDATE reservation_meta SET generation = generation + 1 WHERE singleton_id = 1"
            )
            conn.execute("COMMIT")
            return reservation
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def set_status(
        self,
        *,
        idempotency_key: str,
        status: ReservationStatus,
        now: datetime,
    ) -> RiskReservation:
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM risk_reservations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(idempotency_key)
            current = _reservation_from_row(row)
            if current.status is status:
                conn.execute("COMMIT")
                return current
            conn.execute(
                "UPDATE risk_reservations SET status = ?, updated_at = ? WHERE idempotency_key = ?",
                (status.value, now.isoformat(), idempotency_key),
            )
            conn.execute(
                "UPDATE reservation_meta SET generation = generation + 1 WHERE singleton_id = 1"
            )
            conn.execute("COMMIT")
            return replace(current, status=status, updated_at=now)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, idempotency_key: str) -> RiskReservation | None:
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT * FROM risk_reservations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            return _reservation_from_row(row) if row is not None else None
        finally:
            conn.close()


def _ledger_hash(
    *, prev_hash: str, event_id: str, event_type: str, occurred_at: str, payload_json: str
) -> str:
    raw = "\x1f".join((prev_hash, event_id, event_type, occurred_at, payload_json)).encode("utf-8")
    return sha256(raw).hexdigest()


def _order_to_json(order: OrderRecord) -> str:
    intent = order.intent
    payload = {
        "order_id": order.order_id,
        "risk_decision_id": order.risk_decision_id,
        "status": order.status.value,
        "created_at": order.created_at.isoformat(),
        "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
        "filled_quantity": str(order.filled_quantity),
        "average_fill_price": str(order.average_fill_price) if order.average_fill_price is not None else None,
        "intent": {
            "intent_id": intent.intent_id,
            "idempotency_key": intent.idempotency_key,
            "strategy_id": intent.strategy_id,
            "symbol": intent.symbol,
            "side": intent.side.value,
            "quantity": str(intent.quantity),
            "order_type": intent.order_type.value,
            "created_at": intent.created_at.isoformat(),
            "limit_price": str(intent.limit_price) if intent.limit_price is not None else None,
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _order_from_json(raw: str) -> OrderRecord:
    data = json.loads(raw)
    intent_data = data["intent"]
    intent = OrderIntent(
        intent_id=intent_data["intent_id"],
        idempotency_key=intent_data["idempotency_key"],
        strategy_id=intent_data["strategy_id"],
        symbol=intent_data["symbol"],
        side=Side(intent_data["side"]),
        quantity=Decimal(intent_data["quantity"]),
        order_type=OrderType(intent_data["order_type"]),
        created_at=datetime.fromisoformat(intent_data["created_at"]),
        limit_price=Decimal(intent_data["limit_price"]) if intent_data["limit_price"] is not None else None,
    )
    return OrderRecord(
        order_id=data["order_id"],
        intent=intent,
        risk_decision_id=data["risk_decision_id"],
        status=OrderStatus(data["status"]),
        created_at=datetime.fromisoformat(data["created_at"]),
        submitted_at=datetime.fromisoformat(data["submitted_at"]) if data["submitted_at"] else None,
        filled_quantity=Decimal(data["filled_quantity"]),
        average_fill_price=(
            Decimal(data["average_fill_price"]) if data["average_fill_price"] is not None else None
        ),
    )


def _portfolio_to_json(snapshot: PortfolioSnapshot) -> str:
    payload = {
        "snapshot_id": snapshot.snapshot_id,
        "equity": str(snapshot.equity),
        "gross_exposure": str(snapshot.gross_exposure),
        "net_exposure": str(snapshot.net_exposure),
        "daily_pnl": str(snapshot.daily_pnl),
        "drawdown": str(snapshot.drawdown),
        "open_orders": snapshot.open_orders,
        "signed_position_notional_by_symbol": _decimal_map(snapshot.signed_position_notional_by_symbol),
        "strategy_gross_exposure": _decimal_map(snapshot.strategy_gross_exposure),
        "strategy_signed_position_notional_by_symbol": {
            strategy: _decimal_map(values)
            for strategy, values in snapshot.strategy_signed_position_notional_by_symbol.items()
        },
        "reconciliation_ok": snapshot.reconciliation_ok,
        "broker_state_known": snapshot.broker_state_known,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _portfolio_from_json(raw: str) -> PortfolioSnapshot:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("portfolio payload must be object")
    expected = {
        "snapshot_id",
        "equity",
        "gross_exposure",
        "net_exposure",
        "daily_pnl",
        "drawdown",
        "open_orders",
        "signed_position_notional_by_symbol",
        "strategy_gross_exposure",
        "strategy_signed_position_notional_by_symbol",
        "reconciliation_ok",
        "broker_state_known",
    }
    if set(data) != expected:
        raise ValueError("portfolio payload fields mismatch")
    if isinstance(data["open_orders"], bool) or not isinstance(data["open_orders"], int):
        raise ValueError("portfolio open_orders must be integer")
    if not isinstance(data["reconciliation_ok"], bool):
        raise ValueError("portfolio reconciliation_ok must be boolean")
    if not isinstance(data["broker_state_known"], bool):
        raise ValueError("portfolio broker_state_known must be boolean")
    if not isinstance(data["strategy_signed_position_notional_by_symbol"], dict):
        raise ValueError("portfolio strategy position maps must be object")
    snapshot = PortfolioSnapshot(
        snapshot_id=data["snapshot_id"],
        equity=Decimal(data["equity"]),
        gross_exposure=Decimal(data["gross_exposure"]),
        net_exposure=Decimal(data["net_exposure"]),
        daily_pnl=Decimal(data["daily_pnl"]),
        drawdown=Decimal(data["drawdown"]),
        open_orders=data["open_orders"],
        signed_position_notional_by_symbol=_parse_decimal_map(data["signed_position_notional_by_symbol"]),
        strategy_gross_exposure=_parse_decimal_map(data["strategy_gross_exposure"]),
        strategy_signed_position_notional_by_symbol={
            strategy: _parse_decimal_map(values)
            for strategy, values in data["strategy_signed_position_notional_by_symbol"].items()
        },
        reconciliation_ok=data["reconciliation_ok"],
        broker_state_known=data["broker_state_known"],
    )
    validate_portfolio_snapshot(snapshot)
    return snapshot


def _portfolio_hash(snapshot_json: str) -> str:
    return sha256(snapshot_json.encode("utf-8")).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _portfolio_for_storage(snapshot: PortfolioSnapshot) -> tuple[str, str]:
    validate_portfolio_snapshot(snapshot)
    snapshot_json = _portfolio_to_json(snapshot)
    return snapshot_json, _portfolio_hash(snapshot_json)


def _portfolio_from_storage(*, snapshot_json: object, snapshot_hash: object) -> PortfolioSnapshot:
    if not isinstance(snapshot_json, str):
        raise PortfolioIntegrityError("stored portfolio payload is invalid")
    if not _valid_sha256(snapshot_hash):
        raise PortfolioIntegrityError("stored portfolio hash is invalid")
    if _portfolio_hash(snapshot_json) != snapshot_hash:
        raise PortfolioIntegrityError("stored portfolio hash mismatch")
    try:
        return _portfolio_from_json(snapshot_json)
    except PortfolioIntegrityError:
        raise
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise PortfolioIntegrityError("stored portfolio payload is invalid") from exc


def _ensure_portfolio_state_integrity_schema(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(portfolio_state)").fetchall()}
    if "snapshot_hash" not in columns:
        conn.execute("ALTER TABLE portfolio_state ADD COLUMN snapshot_hash TEXT")
    row = conn.execute(
        "SELECT snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id = 1"
    ).fetchone()
    if row is None:
        return
    if row["snapshot_hash"] is None:
        # Legacy migration is conservative: semantically parse/validate before
        # blessing the existing bytes with their first independent commitment.
        try:
            snapshot = _portfolio_from_json(row["snapshot_json"])
            validate_portfolio_snapshot(snapshot)
        except Exception as exc:
            raise PortfolioIntegrityError(
                "legacy portfolio state cannot be integrity-migrated"
            ) from exc
        snapshot_hash = _portfolio_hash(row["snapshot_json"])
        conn.execute(
            "UPDATE portfolio_state SET snapshot_hash = ? WHERE singleton_id = 1",
            (snapshot_hash,),
        )
        return
    _portfolio_from_storage(
        snapshot_json=row["snapshot_json"], snapshot_hash=row["snapshot_hash"]
    )


def _decimal_map(values: Iterable[tuple[str, Decimal]] | dict[str, Decimal]) -> dict[str, str]:
    items = values.items() if hasattr(values, "items") else values
    return {key: str(value) for key, value in items}


def _parse_decimal_map(values: object) -> dict[str, Decimal]:
    if not isinstance(values, dict):
        raise ValueError("portfolio decimal map must be object")
    parsed: dict[str, Decimal] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("portfolio decimal map entries must be string:string")
        parsed[key] = Decimal(value)
    return parsed


def _reservation_from_row(row: sqlite3.Row) -> RiskReservation:
    return RiskReservation(
        reservation_id=row["reservation_id"],
        idempotency_key=row["idempotency_key"],
        intent_fingerprint=row["intent_fingerprint"],
        strategy_id=row["strategy_id"],
        symbol=row["symbol"],
        signed_notional=row["signed_notional"],
        status=ReservationStatus(row["status"]),
        portfolio_version=int(row["portfolio_version"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
