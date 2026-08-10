from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3
from threading import RLock
from typing import Protocol

from .domain import Fill, OrderRecord, PortfolioSnapshot, Side
from .persistence import SQLitePortfolioStore, SQLiteRuntime, _portfolio_from_json, _portfolio_to_json
from .state import PortfolioNotInitialized, VersionedPortfolioSnapshot


class FillIntegrityConflict(RuntimeError):
    pass


class FillStore(Protocol):
    def record(self, fill: Fill) -> bool: ...

    def fills_for_order(self, order_id: str) -> tuple[Fill, ...]: ...


class InMemoryFillStore:
    def __init__(self) -> None:
        self._fills: dict[str, Fill] = {}
        self._lock = RLock()

    def record(self, fill: Fill) -> bool:
        _validate_fill_shape(fill)
        with self._lock:
            existing = self._fills.get(fill.fill_id)
            if existing is not None:
                if fill_fingerprint(existing) != fill_fingerprint(fill):
                    raise FillIntegrityConflict(fill.fill_id)
                return False
            self._fills[fill.fill_id] = fill
            return True

    def fills_for_order(self, order_id: str) -> tuple[Fill, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (fill for fill in self._fills.values() if fill.order_id == order_id),
                    key=lambda fill: (fill.occurred_at, fill.fill_id),
                )
            )


class SQLiteFillStore:
    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        conn = runtime.connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_fills (
                    fill_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    fill_json TEXT NOT NULL,
                    fill_hash TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_order_fills_order_id ON order_fills(order_id, occurred_at)"
            )
        finally:
            conn.close()

    def record(self, fill: Fill) -> bool:
        _validate_fill_shape(fill)
        payload = _fill_to_json(fill)
        fingerprint = fill_fingerprint(fill)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT fill_hash FROM order_fills WHERE fill_id = ?", (fill.fill_id,)
            ).fetchone()
            if row is not None:
                if row["fill_hash"] != fingerprint:
                    conn.execute("ROLLBACK")
                    raise FillIntegrityConflict(fill.fill_id)
                conn.execute("COMMIT")
                return False
            conn.execute(
                """
                INSERT INTO order_fills(fill_id, order_id, fill_json, fill_hash, occurred_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (fill.fill_id, fill.order_id, payload, fingerprint, fill.occurred_at.isoformat()),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def fills_for_order(self, order_id: str) -> tuple[Fill, ...]:
        conn = self._runtime.connect()
        try:
            rows = conn.execute(
                """
                SELECT fill_json FROM order_fills
                WHERE order_id = ? ORDER BY occurred_at, fill_id
                """,
                (order_id,),
            ).fetchall()
            return tuple(_fill_from_json(row["fill_json"]) for row in rows)
        finally:
            conn.close()


class SQLiteFillAwarePortfolioStore(SQLitePortfolioStore):
    """Portfolio projection with exactly-once fill application.

    `portfolio_applied_orders` from Foundation is retained as a legacy guard.
    New R2 accounting is keyed by immutable fill_id so one order can receive
    multiple partial fills safely across restarts.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        super().__init__(runtime)
        self._runtime = runtime
        conn = runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS portfolio_applied_fills (
                    fill_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_portfolio_applied_fills_order_id
                ON portfolio_applied_fills(order_id);
                """
            )
        finally:
            conn.close()

    def apply_fills(
        self,
        order: OrderRecord,
        fills: tuple[Fill, ...],
        *,
        now: datetime,
    ) -> VersionedPortfolioSnapshot:
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT version, snapshot_json FROM portfolio_state WHERE singleton_id = 1"
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise PortfolioNotInitialized("portfolio state is not initialized")

            version = int(row["version"])
            snapshot = _portfolio_from_json(row["snapshot_json"])

            # Foundation could only mark whole orders applied. Since its durable
            # paper broker never generated partial fills, treating such legacy
            # orders as fully accounted is the only non-duplicating migration.
            legacy_applied = conn.execute(
                "SELECT 1 FROM portfolio_applied_orders WHERE order_id = ?",
                (order.order_id,),
            ).fetchone()
            if legacy_applied is not None:
                conn.execute("COMMIT")
                return VersionedPortfolioSnapshot(version=version, snapshot=snapshot)

            changed = False
            seen_batch: set[str] = set()
            for fill in sorted(fills, key=lambda value: (value.occurred_at, value.fill_id)):
                _validate_fill_for_order(fill=fill, order=order)
                if fill.fill_id in seen_batch:
                    continue
                seen_batch.add(fill.fill_id)
                already = conn.execute(
                    "SELECT 1 FROM portfolio_applied_fills WHERE fill_id = ?",
                    (fill.fill_id,),
                ).fetchone()
                if already is not None:
                    continue
                snapshot = apply_single_fill_to_portfolio(snapshot=snapshot, order=order, fill=fill)
                conn.execute(
                    """
                    INSERT INTO portfolio_applied_fills(fill_id, order_id, applied_at)
                    VALUES (?, ?, ?)
                    """,
                    (fill.fill_id, order.order_id, now.isoformat()),
                )
                changed = True

            if not changed:
                conn.execute("COMMIT")
                return VersionedPortfolioSnapshot(version=version, snapshot=snapshot)

            conn.execute(
                """
                UPDATE portfolio_state
                SET version = ?, snapshot_json = ?, updated_at = ?
                WHERE singleton_id = 1
                """,
                (version + 1, _portfolio_to_json(snapshot), now.isoformat()),
            )
            conn.execute("COMMIT")
            return VersionedPortfolioSnapshot(version=version + 1, snapshot=snapshot)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def apply_single_fill_to_portfolio(
    *, snapshot: PortfolioSnapshot, order: OrderRecord, fill: Fill
) -> PortfolioSnapshot:
    _validate_fill_for_order(fill=fill, order=order)
    zero = Decimal("0")
    signed_fill = fill.side.sign * fill.quantity * fill.price

    positions = dict(snapshot.signed_position_notional_by_symbol)
    positions[fill.symbol] = positions.get(fill.symbol, zero) + signed_fill
    positions = {symbol: value for symbol, value in positions.items() if value != zero}

    strategy_positions = {
        strategy: dict(values)
        for strategy, values in snapshot.strategy_signed_position_notional_by_symbol.items()
    }
    own = strategy_positions.setdefault(order.intent.strategy_id, {})
    own[fill.symbol] = own.get(fill.symbol, zero) + signed_fill
    own = {symbol: value for symbol, value in own.items() if value != zero}
    if own:
        strategy_positions[order.intent.strategy_id] = own
    else:
        strategy_positions.pop(order.intent.strategy_id, None)

    strategy_gross = {
        strategy: sum((abs(value) for value in values.values()), start=zero)
        for strategy, values in strategy_positions.items()
    }
    gross = sum((abs(value) for value in positions.values()), start=zero)
    net = sum(positions.values(), start=zero)

    return replace(
        snapshot,
        signed_position_notional_by_symbol=positions,
        strategy_signed_position_notional_by_symbol=strategy_positions,
        strategy_gross_exposure=strategy_gross,
        gross_exposure=gross,
        net_exposure=net,
    )


def fill_fingerprint(fill: Fill) -> str:
    return sha256(_fill_to_json(fill).encode("utf-8")).hexdigest()


def _validate_fill_shape(fill: Fill) -> None:
    if not fill.fill_id.strip() or not fill.order_id.strip() or not fill.symbol.strip():
        raise ValueError("fill identity is required")
    if not fill.quantity.is_finite() or fill.quantity <= 0:
        raise ValueError("fill quantity must be finite and > 0")
    if not fill.price.is_finite() or fill.price <= 0:
        raise ValueError("fill price must be finite and > 0")
    if fill.occurred_at.tzinfo is None or fill.occurred_at.utcoffset() is None:
        raise ValueError("fill occurred_at must be timezone-aware")


def _validate_fill_for_order(*, fill: Fill, order: OrderRecord) -> None:
    _validate_fill_shape(fill)
    if fill.order_id != order.order_id:
        raise FillIntegrityConflict("fill order_id mismatch")
    if fill.symbol != order.intent.symbol or fill.side is not order.intent.side:
        raise FillIntegrityConflict("fill instrument/side mismatch")


def _fill_to_json(fill: Fill) -> str:
    payload = {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "symbol": fill.symbol,
        "side": fill.side.value,
        "quantity": str(fill.quantity),
        "price": str(fill.price),
        "occurred_at": fill.occurred_at.isoformat(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _fill_from_json(raw: str) -> Fill:
    data = json.loads(raw)
    return Fill(
        fill_id=data["fill_id"],
        order_id=data["order_id"],
        symbol=data["symbol"],
        side=Side(data["side"]),
        quantity=Decimal(data["quantity"]),
        price=Decimal(data["price"]),
        occurred_at=datetime.fromisoformat(data["occurred_at"]),
    )
