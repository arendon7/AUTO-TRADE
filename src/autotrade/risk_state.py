from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import sqlite3
from typing import Protocol
from uuid import uuid4

from .persistence import SQLiteRuntime, _ledger_hash
from .state import SafetyControlState


@dataclass(frozen=True, slots=True)
class RiskTelemetryState:
    session_date: str
    day_start_equity: Decimal
    peak_equity: Decimal
    current_equity: Decimal
    daily_pnl: Decimal
    drawdown: Decimal
    version: int
    updated_at: datetime


class RiskTelemetryStore(Protocol):
    def get(self) -> RiskTelemetryState: ...
    def record_equity(self, *, equity: Decimal, now: datetime) -> RiskTelemetryState: ...


class RiskTelemetryNotInitialized(RuntimeError):
    pass


class SQLiteR2SafetyStateStore:
    """R2 safety-state persistence with independent kill and circuit controls."""

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        conn = self._runtime.connect()
        try:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(safety_state)").fetchall()
            }
            if "circuit_active" not in columns:
                conn.execute(
                    "ALTER TABLE safety_state ADD COLUMN circuit_active INTEGER NOT NULL DEFAULT 0 CHECK(circuit_active IN (0, 1))"
                )
            if "circuit_reason" not in columns:
                conn.execute(
                    "ALTER TABLE safety_state ADD COLUMN circuit_reason TEXT NOT NULL DEFAULT ''"
                )
        finally:
            conn.close()

    def get(self) -> SafetyControlState:
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                """
                SELECT kill_switch_active, kill_switch_reason,
                       circuit_active, circuit_reason, version, updated_at
                FROM safety_state WHERE singleton_id = 1
                """
            ).fetchone()
            assert row is not None
            return SafetyControlState(
                kill_switch_active=bool(row["kill_switch_active"]),
                kill_switch_reason=row["kill_switch_reason"],
                circuit_active=bool(row["circuit_active"]),
                circuit_reason=row["circuit_reason"],
                version=int(row["version"]),
                updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            )
        finally:
            conn.close()

    def activate(self, *, reason: str, now: datetime) -> SafetyControlState:
        if not reason.strip():
            raise ValueError("kill switch reason is required")
        return self._write_flag(
            flag="kill_switch_active",
            reason_column="kill_switch_reason",
            active=True,
            reason=reason,
            now=now,
        )

    def reset(self, *, now: datetime) -> SafetyControlState:
        return self._write_flag(
            flag="kill_switch_active",
            reason_column="kill_switch_reason",
            active=False,
            reason="",
            now=now,
        )

    def activate_circuit(self, *, reason: str, now: datetime) -> SafetyControlState:
        if not reason.strip():
            raise ValueError("circuit reason is required")
        current = self.get()
        if current.circuit_active:
            return current
        return self._write_flag(
            flag="circuit_active",
            reason_column="circuit_reason",
            active=True,
            reason=reason,
            now=now,
        )

    def acknowledge_circuit(self, *, reason: str, now: datetime) -> SafetyControlState:
        if not reason.strip():
            raise ValueError("circuit acknowledgement reason is required")
        current = self.get()
        if not current.circuit_active:
            return current
        return self._write_flag(
            flag="circuit_active",
            reason_column="circuit_reason",
            active=False,
            reason="",
            now=now,
        )

    def _write_flag(
        self,
        *,
        flag: str,
        reason_column: str,
        active: bool,
        reason: str,
        now: datetime,
    ) -> SafetyControlState:
        if not _aware(now):
            raise ValueError("safety-state timestamp must be timezone-aware")
        if flag not in {"kill_switch_active", "circuit_active"}:
            raise ValueError("invalid safety flag")
        if reason_column not in {"kill_switch_reason", "circuit_reason"}:
            raise ValueError("invalid safety reason column")
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT {flag}, {reason_column}, version FROM safety_state WHERE singleton_id = 1"
            ).fetchone()
            assert row is not None
            if bool(row[flag]) is active and row[reason_column] == reason:
                conn.execute("COMMIT")
                return self.get()
            version = int(row["version"]) + 1
            conn.execute(
                f"""
                UPDATE safety_state
                SET {flag} = ?, {reason_column} = ?, version = ?, updated_at = ?
                WHERE singleton_id = 1
                """,
                (1 if active else 0, reason, version, now.isoformat()),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return self.get()


class SQLiteRiskTelemetryStore:
    """Durable UTC-session equity telemetry with fail-closed circuit activation.

    Automatic session rollover resets daily metrics but NEVER clears circuit.
    Circuit activation and its ledger event are committed atomically with the
    telemetry update so an approval cannot survive a breach via a crash window.
    """

    def __init__(
        self,
        runtime: SQLiteRuntime,
        *,
        max_daily_loss: Decimal,
        max_drawdown: Decimal,
    ) -> None:
        if not max_daily_loss.is_finite() or max_daily_loss <= 0:
            raise ValueError("max_daily_loss must be finite and > 0")
        if not max_drawdown.is_finite() or not Decimal("0") < max_drawdown < Decimal("1"):
            raise ValueError("max_drawdown must be finite and between 0 and 1")
        self._runtime = runtime
        self._max_daily_loss = max_daily_loss
        self._max_drawdown = max_drawdown
        SQLiteR2SafetyStateStore(runtime)  # ensure circuit columns before atomic writes
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS risk_telemetry (
                    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                    session_date TEXT NOT NULL,
                    day_start_equity TEXT NOT NULL,
                    peak_equity TEXT NOT NULL,
                    current_equity TEXT NOT NULL,
                    daily_pnl TEXT NOT NULL,
                    drawdown TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    updated_at TEXT NOT NULL
                )
                """
            )
        finally:
            conn.close()

    def initialize(self, *, equity: Decimal, now: datetime) -> RiskTelemetryState:
        _validate_equity(equity)
        _validate_time(now)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM risk_telemetry WHERE singleton_id = 1"
            ).fetchone()
            if row is None:
                session_date = _session_date(now)
                conn.execute(
                    """
                    INSERT INTO risk_telemetry(
                        singleton_id, session_date, day_start_equity, peak_equity,
                        current_equity, daily_pnl, drawdown, version, updated_at
                    ) VALUES (1, ?, ?, ?, ?, '0', '0', 1, ?)
                    """,
                    (
                        session_date,
                        str(equity),
                        str(equity),
                        str(equity),
                        now.isoformat(),
                    ),
                )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return self.get()

    def get(self) -> RiskTelemetryState:
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT * FROM risk_telemetry WHERE singleton_id = 1"
            ).fetchone()
            if row is None:
                raise RiskTelemetryNotInitialized("risk telemetry is not initialized")
            return _risk_state_from_row(row)
        finally:
            conn.close()

    def record_equity(self, *, equity: Decimal, now: datetime) -> RiskTelemetryState:
        _validate_equity(equity)
        _validate_time(now)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM risk_telemetry WHERE singleton_id = 1"
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise RiskTelemetryNotInitialized("risk telemetry is not initialized")

            previous = _risk_state_from_row(row)
            session_date = _session_date(now)
            if now < previous.updated_at:
                conn.execute("ROLLBACK")
                raise ValueError("risk telemetry update cannot move backward in time")

            if session_date != previous.session_date:
                day_start = equity
                peak = equity
                daily_pnl = Decimal("0")
                drawdown = Decimal("0")
            else:
                day_start = previous.day_start_equity
                peak = max(previous.peak_equity, equity)
                daily_pnl = equity - day_start
                drawdown = (peak - equity) / peak if peak > 0 else Decimal("0")

            version = previous.version + 1
            conn.execute(
                """
                UPDATE risk_telemetry
                SET session_date = ?, day_start_equity = ?, peak_equity = ?,
                    current_equity = ?, daily_pnl = ?, drawdown = ?,
                    version = ?, updated_at = ?
                WHERE singleton_id = 1
                """,
                (
                    session_date,
                    str(day_start),
                    str(peak),
                    str(equity),
                    str(daily_pnl),
                    str(drawdown),
                    version,
                    now.isoformat(),
                ),
            )

            breach_reason = ""
            if daily_pnl <= -self._max_daily_loss:
                breach_reason = f"MAX_DAILY_LOSS:{daily_pnl}"
            elif drawdown >= self._max_drawdown:
                breach_reason = f"MAX_DRAWDOWN:{drawdown}"

            if breach_reason:
                safety = conn.execute(
                    """
                    SELECT circuit_active, circuit_reason, version
                    FROM safety_state WHERE singleton_id = 1
                    """
                ).fetchone()
                assert safety is not None
                if not bool(safety["circuit_active"]):
                    safety_version = int(safety["version"]) + 1
                    conn.execute(
                        """
                        UPDATE safety_state
                        SET circuit_active = 1, circuit_reason = ?,
                            version = ?, updated_at = ?
                        WHERE singleton_id = 1
                        """,
                        (breach_reason, safety_version, now.isoformat()),
                    )
                    _append_ledger_event_tx(
                        conn,
                        event_id=f"circuit-auto:{uuid4()}",
                        event_type="CIRCUIT_ACTIVATED",
                        occurred_at=now,
                        payload={
                            "reason": breach_reason,
                            "source": "RISK_TELEMETRY",
                            "safety_state_version": str(safety_version),
                            "risk_telemetry_version": str(version),
                        },
                    )

            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return self.get()


def _risk_state_from_row(row: sqlite3.Row) -> RiskTelemetryState:
    return RiskTelemetryState(
        session_date=row["session_date"],
        day_start_equity=Decimal(row["day_start_equity"]),
        peak_equity=Decimal(row["peak_equity"]),
        current_equity=Decimal(row["current_equity"]),
        daily_pnl=Decimal(row["daily_pnl"]),
        drawdown=Decimal(row["drawdown"]),
        version=int(row["version"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _append_ledger_event_tx(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    payload: dict[str, str],
) -> None:
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    occurred_raw = occurred_at.isoformat()
    row = conn.execute(
        "SELECT event_hash FROM ledger_events ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prev_hash = row["event_hash"] if row is not None else "GENESIS"
    event_hash = _ledger_hash(
        prev_hash=prev_hash,
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_raw,
        payload_json=payload_json,
    )
    conn.execute(
        """
        INSERT INTO ledger_events(
            event_id, event_type, occurred_at, payload_json, prev_hash, event_hash
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (event_id, event_type, occurred_raw, payload_json, prev_hash, event_hash),
    )


def _session_date(now: datetime) -> str:
    return now.astimezone(timezone.utc).date().isoformat()


def _validate_equity(equity: Decimal) -> None:
    if not isinstance(equity, Decimal) or not equity.is_finite() or equity <= 0:
        raise ValueError("equity must be finite and > 0")


def _validate_time(now: datetime) -> None:
    if not _aware(now):
        raise ValueError("risk telemetry timestamp must be timezone-aware")


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
