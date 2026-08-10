from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json
import sqlite3
from uuid import uuid4

from ..broker_state import BrokerAccountState
from ..domain import Fill, MarketSnapshot, OrderRecord, OrderStatus, OrderType, Side
from ..persistence import SQLiteRuntime
from .base import BrokerExecution


class BrokerIdempotencyConflict(RuntimeError):
    pass


class DurablePaperBroker:
    """Deterministic paper broker with durable, idempotent broker-side state.

    It intentionally models only immediate MARKET fills and marketable LIMIT
    fills. Non-marketable LIMIT orders remain open. This is an execution-safety
    simulator, not a realistic backtest fill engine.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_broker_orders (
                    order_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    status TEXT NOT NULL,
                    execution_json TEXT NOT NULL,
                    submitted_at TEXT NOT NULL
                );
                """
            )
        finally:
            conn.close()

    @property
    def submission_count(self) -> int:
        conn = self._runtime.connect()
        try:
            return int(conn.execute("SELECT COUNT(*) AS n FROM paper_broker_orders").fetchone()["n"])
        finally:
            conn.close()

    def submit(self, *, order: OrderRecord, market: MarketSnapshot, now: datetime) -> BrokerExecution:
        if order.status is not OrderStatus.VALIDATED:
            raise ValueError("durable paper broker only accepts VALIDATED orders")
        if market.symbol != order.intent.symbol:
            raise ValueError("market/order symbol mismatch")

        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT execution_json FROM paper_broker_orders WHERE order_id = ?",
                (order.order_id,),
            ).fetchone()
            if row is not None:
                execution = _execution_from_json(row["execution_json"])
                conn.execute("COMMIT")
                return execution

            same_key = conn.execute(
                "SELECT order_id FROM paper_broker_orders WHERE idempotency_key = ?",
                (order.intent.idempotency_key,),
            ).fetchone()
            if same_key is not None:
                conn.execute("ROLLBACK")
                raise BrokerIdempotencyConflict(order.intent.idempotency_key)

            execution = self._execute(order=order, market=market, now=now)
            conn.execute(
                """
                INSERT INTO paper_broker_orders(
                    order_id, idempotency_key, symbol, side, status, execution_json, submitted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.order_id,
                    order.intent.idempotency_key,
                    order.intent.symbol,
                    order.intent.side.value,
                    execution.status.value,
                    _execution_to_json(execution),
                    now.isoformat(),
                ),
            )
            conn.execute("COMMIT")
            return execution
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get_execution(self, order_id: str) -> BrokerExecution | None:
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT execution_json FROM paper_broker_orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            return _execution_from_json(row["execution_json"]) if row is not None else None
        finally:
            conn.close()

    def account_state(self, *, now: datetime) -> BrokerAccountState:
        conn = self._runtime.connect()
        try:
            rows = conn.execute(
                "SELECT order_id, status, execution_json FROM paper_broker_orders ORDER BY rowid"
            ).fetchall()
        finally:
            conn.close()

        zero = Decimal("0")
        positions: dict[str, Decimal] = {}
        open_order_ids: set[str] = set()
        for row in rows:
            status = OrderStatus(row["status"])
            execution = _execution_from_json(row["execution_json"])
            if status in {OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED}:
                open_order_ids.add(row["order_id"])
            for fill in execution.fills:
                signed = fill.side.sign * fill.quantity * fill.price
                positions[fill.symbol] = positions.get(fill.symbol, zero) + signed

        positions = {symbol: value for symbol, value in positions.items() if value != zero}
        return BrokerAccountState(
            observed_at=now,
            state_known=True,
            signed_position_notional_by_symbol=positions,
            open_order_ids=frozenset(open_order_ids),
        )

    @staticmethod
    def _execute(*, order: OrderRecord, market: MarketSnapshot, now: datetime) -> BrokerExecution:
        intent = order.intent
        if intent.order_type is OrderType.MARKET:
            fill_price = market.ask if intent.side is Side.BUY else market.bid
            return BrokerExecution(
                status=OrderStatus.FILLED,
                fills=(DurablePaperBroker._fill(order=order, price=fill_price, now=now),),
            )

        assert intent.limit_price is not None
        marketable = (
            intent.side is Side.BUY and intent.limit_price >= market.ask
        ) or (
            intent.side is Side.SELL and intent.limit_price <= market.bid
        )
        if not marketable:
            return BrokerExecution(status=OrderStatus.SUBMITTED)

        fill_price = market.ask if intent.side is Side.BUY else market.bid
        return BrokerExecution(
            status=OrderStatus.FILLED,
            fills=(DurablePaperBroker._fill(order=order, price=fill_price, now=now),),
        )

    @staticmethod
    def _fill(*, order: OrderRecord, price: Decimal, now: datetime) -> Fill:
        return Fill(
            fill_id=str(uuid4()),
            order_id=order.order_id,
            symbol=order.intent.symbol,
            side=order.intent.side,
            quantity=order.intent.quantity,
            price=price,
            occurred_at=now,
        )


def _execution_to_json(execution: BrokerExecution) -> str:
    payload = {
        "status": execution.status.value,
        "fills": [
            {
                "fill_id": fill.fill_id,
                "order_id": fill.order_id,
                "symbol": fill.symbol,
                "side": fill.side.value,
                "quantity": str(fill.quantity),
                "price": str(fill.price),
                "occurred_at": fill.occurred_at.isoformat(),
            }
            for fill in execution.fills
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _execution_from_json(raw: str) -> BrokerExecution:
    data = json.loads(raw)
    fills = tuple(
        Fill(
            fill_id=item["fill_id"],
            order_id=item["order_id"],
            symbol=item["symbol"],
            side=Side(item["side"]),
            quantity=Decimal(item["quantity"]),
            price=Decimal(item["price"]),
            occurred_at=datetime.fromisoformat(item["occurred_at"]),
        )
        for item in data["fills"]
    )
    return BrokerExecution(status=OrderStatus(data["status"]), fills=fills)
