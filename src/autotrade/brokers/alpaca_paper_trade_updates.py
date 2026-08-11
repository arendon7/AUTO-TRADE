from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import re
import sqlite3
from typing import Mapping

from autotrade.persistence import SQLiteRuntime

from .alpaca_paper_bracket import AlpacaNestedBracketAttestation


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")
_MAX_FRAME_BYTES = 1_000_000


class PaperTradeUpdateError(RuntimeError):
    pass


class PaperTradeUpdateIntegrityError(PaperTradeUpdateError):
    pass


class PaperTradeUpdateScopeError(PaperTradeUpdateError):
    pass


class PaperTradeUpdateEventType(StrEnum):
    NEW = "new"
    FILL = "fill"
    PARTIAL_FILL = "partial_fill"
    CANCELED = "canceled"
    EXPIRED = "expired"
    DONE_FOR_DAY = "done_for_day"
    REPLACED = "replaced"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PENDING_NEW = "pending_new"
    STOPPED = "stopped"
    PENDING_CANCEL = "pending_cancel"
    PENDING_REPLACE = "pending_replace"
    CALCULATED = "calculated"
    SUSPENDED = "suspended"
    ORDER_REPLACE_REJECTED = "order_replace_rejected"
    ORDER_CANCEL_REJECTED = "order_cancel_rejected"


_FILL_EVENTS = frozenset(
    {PaperTradeUpdateEventType.FILL, PaperTradeUpdateEventType.PARTIAL_FILL}
)
_TIMESTAMP_EVENTS = frozenset(
    {
        PaperTradeUpdateEventType.FILL,
        PaperTradeUpdateEventType.PARTIAL_FILL,
        PaperTradeUpdateEventType.CANCELED,
        PaperTradeUpdateEventType.EXPIRED,
        PaperTradeUpdateEventType.REPLACED,
        PaperTradeUpdateEventType.REJECTED,
    }
)
_EXPECTED_STATUS = {
    PaperTradeUpdateEventType.NEW: "new",
    PaperTradeUpdateEventType.FILL: "filled",
    PaperTradeUpdateEventType.PARTIAL_FILL: "partially_filled",
    PaperTradeUpdateEventType.CANCELED: "canceled",
    PaperTradeUpdateEventType.EXPIRED: "expired",
    PaperTradeUpdateEventType.DONE_FOR_DAY: "done_for_day",
    PaperTradeUpdateEventType.REPLACED: "replaced",
    PaperTradeUpdateEventType.ACCEPTED: "accepted",
    PaperTradeUpdateEventType.REJECTED: "rejected",
    PaperTradeUpdateEventType.PENDING_NEW: "pending_new",
    PaperTradeUpdateEventType.STOPPED: "stopped",
    PaperTradeUpdateEventType.PENDING_CANCEL: "pending_cancel",
    PaperTradeUpdateEventType.PENDING_REPLACE: "pending_replace",
    PaperTradeUpdateEventType.CALCULATED: "calculated",
    PaperTradeUpdateEventType.SUSPENDED: "suspended",
}


@dataclass(frozen=True, slots=True)
class PaperTradeUpdateScope:
    symbol: str
    parent_order_id: str
    parent_client_order_id: str
    take_profit_order_id: str
    stop_loss_order_id: str

    def __post_init__(self) -> None:
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise ValueError("symbol must be canonical US equity symbol")
        for label, value in (
            ("parent_order_id", self.parent_order_id),
            ("parent_client_order_id", self.parent_client_order_id),
            ("take_profit_order_id", self.take_profit_order_id),
            ("stop_loss_order_id", self.stop_loss_order_id),
        ):
            if not isinstance(value, str) or not _ID_RE.fullmatch(value):
                raise ValueError(f"{label} must be canonical non-empty identifier")
        if len(
            {
                self.parent_order_id,
                self.take_profit_order_id,
                self.stop_loss_order_id,
            }
        ) != 3:
            raise ValueError("parent/take-profit/stop-loss broker IDs must be distinct")

    @classmethod
    def from_bracket(
        cls,
        *,
        symbol: str,
        attestation: AlpacaNestedBracketAttestation,
    ) -> "PaperTradeUpdateScope":
        return cls(
            symbol=symbol,
            parent_order_id=attestation.parent_order_id,
            parent_client_order_id=attestation.client_order_id,
            take_profit_order_id=attestation.take_profit_order_id,
            stop_loss_order_id=attestation.stop_loss_order_id,
        )

    @property
    def broker_order_ids(self) -> frozenset[str]:
        return frozenset(
            {
                self.parent_order_id,
                self.take_profit_order_id,
                self.stop_loss_order_id,
            }
        )


@dataclass(frozen=True, slots=True)
class PaperTradeUpdateEvent:
    event_type: PaperTradeUpdateEventType
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    asset_class: str
    order_class: str
    order_type: str
    order_status: str
    order_qty: Decimal
    filled_qty: Decimal
    occurred_at: datetime
    execution_id: str | None
    fill_price: Decimal | None
    fill_qty: Decimal | None
    position_qty: Decimal | None
    frame_hash: str
    event_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, PaperTradeUpdateEventType):
            raise ValueError("event_type must be PaperTradeUpdateEventType")
        for label, value in (
            ("broker_order_id", self.broker_order_id),
            ("client_order_id", self.client_order_id),
        ):
            if not isinstance(value, str) or not _ID_RE.fullmatch(value):
                raise ValueError(f"{label} must be canonical identifier")
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise ValueError("symbol must be canonical")
        if self.side not in {"buy", "sell"}:
            raise ValueError("side must be buy/sell")
        if self.asset_class != "us_equity":
            raise ValueError("R6 trade_updates supports us_equity only")
        if not _finite_nonnegative(self.order_qty) or self.order_qty <= 0:
            raise ValueError("order_qty must be finite and > 0")
        if not _finite_nonnegative(self.filled_qty):
            raise ValueError("filled_qty must be finite and >= 0")
        if self.filled_qty > self.order_qty:
            raise ValueError("filled_qty cannot exceed order_qty")
        _require_aware(self.occurred_at, "occurred_at")
        if not _HASH_RE.fullmatch(self.frame_hash) or not _HASH_RE.fullmatch(self.event_hash):
            raise ValueError("frame/event hashes must be lowercase SHA-256")
        if self.event_type in _FILL_EVENTS:
            if self.execution_id is None or not _ID_RE.fullmatch(self.execution_id):
                raise ValueError("fill event requires execution_id")
            if self.fill_price is None or not _finite_positive(self.fill_price):
                raise ValueError("fill event requires positive fill_price")
            if self.fill_qty is None or not _finite_positive(self.fill_qty):
                raise ValueError("fill event requires positive fill_qty")
            if self.position_qty is None or not self.position_qty.is_finite():
                raise ValueError("fill event requires finite position_qty")
        elif any(
            value is not None
            for value in (self.execution_id, self.fill_price, self.fill_qty, self.position_qty)
        ):
            raise ValueError("non-fill event cannot carry fill-only fields")

    @property
    def terminal_parent_candidate(self) -> bool:
        return self.event_type in {
            PaperTradeUpdateEventType.FILL,
            PaperTradeUpdateEventType.CANCELED,
            PaperTradeUpdateEventType.EXPIRED,
            PaperTradeUpdateEventType.REJECTED,
        }


class PaperTradeUpdateParser:
    """Strict parser for PAPER binary trade_updates frames.

    No network authority. Frames outside one already-reconciled bracket scope
    fail closed rather than being silently ignored.
    """

    def parse(self, frame: bytes, *, scope: PaperTradeUpdateScope) -> PaperTradeUpdateEvent:
        if not isinstance(frame, bytes):
            raise PaperTradeUpdateIntegrityError("PAPER trade_updates frame must be binary")
        if not frame or len(frame) > _MAX_FRAME_BYTES:
            raise PaperTradeUpdateIntegrityError("PAPER trade_updates frame size is invalid")
        frame_hash = sha256(frame).hexdigest()
        payload = _strict_json_object(frame)
        if payload.get("stream") != "trade_updates":
            raise PaperTradeUpdateIntegrityError("frame stream must be trade_updates")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PaperTradeUpdateIntegrityError("trade_updates data must be object")
        try:
            event_type = PaperTradeUpdateEventType(_required_str(data, "event"))
        except ValueError as exc:
            raise PaperTradeUpdateIntegrityError("unsupported trade_updates event type") from exc
        order = data.get("order")
        if not isinstance(order, dict):
            raise PaperTradeUpdateIntegrityError("trade_updates order must be object")

        broker_order_id = _required_id(order, "id")
        client_order_id = _required_id(order, "client_order_id")
        symbol = _required_str(order, "symbol")
        asset_class = _required_str(order, "asset_class")
        side = _required_str(order, "side")
        order_type = _order_type(order)
        order_class = order.get("order_class")
        if not isinstance(order_class, str):
            raise PaperTradeUpdateIntegrityError("order_class must be string")
        status = _required_str(order, "status")
        order_qty = _decimal(order.get("qty"), "order.qty", positive=True)
        filled_qty = _decimal(order.get("filled_qty"), "order.filled_qty", nonnegative=True)
        if filled_qty > order_qty:
            raise PaperTradeUpdateIntegrityError("order.filled_qty exceeds order.qty")

        self._validate_scope(
            scope=scope,
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            asset_class=asset_class,
            side=side,
            event_type=event_type,
            status=status,
        )

        timestamp_source = data.get("timestamp") if event_type in _TIMESTAMP_EVENTS else order.get("updated_at")
        occurred_at = _timestamp(timestamp_source, "event timestamp")

        execution_id: str | None = None
        fill_price: Decimal | None = None
        fill_qty: Decimal | None = None
        position_qty: Decimal | None = None
        if event_type in _FILL_EVENTS:
            execution_id = _required_id(data, "execution_id")
            fill_price = _decimal(data.get("price"), "price", positive=True)
            fill_qty = _decimal(data.get("qty"), "qty", positive=True)
            position_qty = _decimal(data.get("position_qty"), "position_qty", finite=True)
            if fill_qty > order_qty:
                raise PaperTradeUpdateIntegrityError("event fill qty exceeds order qty")
            if event_type is PaperTradeUpdateEventType.FILL and filled_qty != order_qty:
                raise PaperTradeUpdateIntegrityError("fill event requires order filled_qty == order qty")
            if event_type is PaperTradeUpdateEventType.PARTIAL_FILL and not Decimal("0") < filled_qty < order_qty:
                raise PaperTradeUpdateIntegrityError(
                    "partial_fill requires 0 < order filled_qty < order qty"
                )

        canonical = {
            "asset_class": asset_class,
            "broker_order_id": broker_order_id,
            "client_order_id": client_order_id,
            "event_type": event_type.value,
            "execution_id": execution_id,
            "fill_price": str(fill_price) if fill_price is not None else None,
            "fill_qty": str(fill_qty) if fill_qty is not None else None,
            "filled_qty": str(filled_qty),
            "occurred_at": occurred_at.isoformat(),
            "order_class": order_class,
            "order_qty": str(order_qty),
            "order_status": status,
            "order_type": order_type,
            "position_qty": str(position_qty) if position_qty is not None else None,
            "side": side,
            "symbol": symbol,
        }
        event_hash = sha256(_canonical_json(canonical).encode("utf-8")).hexdigest()
        return PaperTradeUpdateEvent(
            event_type=event_type,
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            asset_class=asset_class,
            order_class=order_class,
            order_type=order_type,
            order_status=status,
            order_qty=order_qty,
            filled_qty=filled_qty,
            occurred_at=occurred_at,
            execution_id=execution_id,
            fill_price=fill_price,
            fill_qty=fill_qty,
            position_qty=position_qty,
            frame_hash=frame_hash,
            event_hash=event_hash,
        )

    @staticmethod
    def _validate_scope(
        *,
        scope: PaperTradeUpdateScope,
        broker_order_id: str,
        client_order_id: str,
        symbol: str,
        asset_class: str,
        side: str,
        event_type: PaperTradeUpdateEventType,
        status: str,
    ) -> None:
        if broker_order_id not in scope.broker_order_ids:
            raise PaperTradeUpdateScopeError("trade update broker_order_id is outside bracket scope")
        if symbol != scope.symbol:
            raise PaperTradeUpdateScopeError("trade update symbol mismatch")
        if asset_class != "us_equity":
            raise PaperTradeUpdateScopeError("R6 trade_updates supports us_equity only")
        if broker_order_id == scope.parent_order_id:
            if client_order_id != scope.parent_client_order_id:
                raise PaperTradeUpdateScopeError("parent client_order_id mismatch")
            if side != "buy":
                raise PaperTradeUpdateScopeError("parent trade update side must be buy")
        elif side != "sell":
            raise PaperTradeUpdateScopeError("protection-leg trade update side must be sell")
        expected_status = _EXPECTED_STATUS.get(event_type)
        if expected_status is not None and status != expected_status:
            raise PaperTradeUpdateIntegrityError(
                f"event/status mismatch: {event_type.value} requires {expected_status}"
            )


@dataclass(frozen=True, slots=True)
class PaperTradeUpdateLedgerState:
    scope_hash: str
    event_count: int
    head_hash: str
    parent_filled_qty: Decimal
    take_profit_filled_qty: Decimal
    stop_loss_filled_qty: Decimal
    last_event_at: datetime
    control_hash: str

    def __post_init__(self) -> None:
        for value in (self.scope_hash, self.head_hash, self.control_hash):
            if not _HASH_RE.fullmatch(value):
                raise ValueError("ledger hashes must be lowercase SHA-256")
        if self.event_count <= 0:
            raise ValueError("event_count must be > 0")
        for value in (
            self.parent_filled_qty,
            self.take_profit_filled_qty,
            self.stop_loss_filled_qty,
        ):
            if not _finite_nonnegative(value):
                raise ValueError("filled quantities must be finite and >= 0")
        _require_aware(self.last_event_at, "last_event_at")


class SQLitePaperTradeUpdateLedger:
    """Append-only, tamper-evident event evidence for one bracket scope."""

    def __init__(self, runtime: SQLiteRuntime, *, scope: PaperTradeUpdateScope) -> None:
        self._runtime = runtime
        self.scope = scope
        self._scope_hash = _scope_hash(scope)
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS alpaca_paper_trade_update_events (
                    scope_hash TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    identity_key TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    chain_hash TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    PRIMARY KEY(scope_hash, sequence),
                    UNIQUE(scope_hash, identity_key),
                    UNIQUE(scope_hash, event_hash),
                    UNIQUE(scope_hash, chain_hash)
                );
                CREATE TABLE IF NOT EXISTS alpaca_paper_trade_update_control (
                    scope_hash TEXT PRIMARY KEY,
                    event_count INTEGER NOT NULL CHECK(event_count > 0),
                    head_hash TEXT NOT NULL,
                    parent_filled_qty TEXT NOT NULL,
                    take_profit_filled_qty TEXT NOT NULL,
                    stop_loss_filled_qty TEXT NOT NULL,
                    last_event_at TEXT NOT NULL,
                    control_hash TEXT NOT NULL
                );
                """
            )
        finally:
            conn.close()

    def append(self, event: PaperTradeUpdateEvent) -> bool:
        self._validate_event_scope(event)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            prior_state = self._verify_locked(conn, allow_empty=True)
            identity_key = _identity_key(event)
            existing = conn.execute(
                """
                SELECT event_hash FROM alpaca_paper_trade_update_events
                WHERE scope_hash=? AND identity_key=?
                """,
                (self._scope_hash, identity_key),
            ).fetchone()
            if existing is not None:
                if str(existing["event_hash"]) != event.event_hash:
                    raise PaperTradeUpdateIntegrityError(
                        "trade update identity replayed with conflicting content"
                    )
                conn.execute("COMMIT")
                return False

            if prior_state is not None and event.occurred_at < prior_state.last_event_at:
                raise PaperTradeUpdateIntegrityError("trade update time moved backwards")
            self._validate_fill_progression(event, prior_state)

            sequence = 1 if prior_state is None else prior_state.event_count + 1
            previous_hash = "0" * 64 if prior_state is None else prior_state.head_hash
            event_json = _event_json(event)
            chain_hash = sha256(
                _canonical_json(
                    {
                        "event_hash": event.event_hash,
                        "event_json": event_json,
                        "previous_hash": previous_hash,
                        "scope_hash": self._scope_hash,
                        "sequence": sequence,
                    }
                ).encode("utf-8")
            ).hexdigest()
            conn.execute(
                """
                INSERT INTO alpaca_paper_trade_update_events(
                    scope_hash, sequence, identity_key, event_hash, event_json,
                    previous_hash, chain_hash, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._scope_hash,
                    sequence,
                    identity_key,
                    event.event_hash,
                    event_json,
                    previous_hash,
                    chain_hash,
                    event.occurred_at.isoformat(),
                ),
            )
            parent, take_profit, stop_loss = _next_filled_quantities(
                scope=self.scope,
                event=event,
                prior=prior_state,
            )
            control_payload = {
                "event_count": sequence,
                "head_hash": chain_hash,
                "last_event_at": event.occurred_at.isoformat(),
                "parent_filled_qty": str(parent),
                "scope_hash": self._scope_hash,
                "stop_loss_filled_qty": str(stop_loss),
                "take_profit_filled_qty": str(take_profit),
            }
            control_hash = sha256(_canonical_json(control_payload).encode("utf-8")).hexdigest()
            conn.execute(
                """
                INSERT INTO alpaca_paper_trade_update_control(
                    scope_hash, event_count, head_hash, parent_filled_qty,
                    take_profit_filled_qty, stop_loss_filled_qty, last_event_at,
                    control_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_hash) DO UPDATE SET
                    event_count=excluded.event_count,
                    head_hash=excluded.head_hash,
                    parent_filled_qty=excluded.parent_filled_qty,
                    take_profit_filled_qty=excluded.take_profit_filled_qty,
                    stop_loss_filled_qty=excluded.stop_loss_filled_qty,
                    last_event_at=excluded.last_event_at,
                    control_hash=excluded.control_hash
                """,
                (
                    self._scope_hash,
                    sequence,
                    chain_hash,
                    str(parent),
                    str(take_profit),
                    str(stop_loss),
                    event.occurred_at.isoformat(),
                    control_hash,
                ),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def verify(self) -> PaperTradeUpdateLedgerState:
        conn = self._runtime.connect()
        try:
            state = self._verify_locked(conn, allow_empty=False)
            assert state is not None
            return state
        finally:
            conn.close()

    def events(self) -> tuple[PaperTradeUpdateEvent, ...]:
        conn = self._runtime.connect()
        try:
            self._verify_locked(conn, allow_empty=False)
            rows = conn.execute(
                """
                SELECT event_json FROM alpaca_paper_trade_update_events
                WHERE scope_hash=? ORDER BY sequence
                """,
                (self._scope_hash,),
            ).fetchall()
            return tuple(_event_from_json(str(row["event_json"])) for row in rows)
        finally:
            conn.close()

    def _validate_event_scope(self, event: PaperTradeUpdateEvent) -> None:
        if event.broker_order_id not in self.scope.broker_order_ids:
            raise PaperTradeUpdateScopeError("event broker_order_id outside ledger scope")
        if event.symbol != self.scope.symbol:
            raise PaperTradeUpdateScopeError("event symbol outside ledger scope")
        if event.broker_order_id == self.scope.parent_order_id:
            if event.client_order_id != self.scope.parent_client_order_id:
                raise PaperTradeUpdateScopeError("event parent client_order_id mismatch")
            if event.side != "buy":
                raise PaperTradeUpdateScopeError("event parent side mismatch")
        elif event.side != "sell":
            raise PaperTradeUpdateScopeError("event protection side mismatch")

    def _validate_fill_progression(
        self,
        event: PaperTradeUpdateEvent,
        prior: PaperTradeUpdateLedgerState | None,
    ) -> None:
        previous = Decimal("0")
        if prior is not None:
            if event.broker_order_id == self.scope.parent_order_id:
                previous = prior.parent_filled_qty
            elif event.broker_order_id == self.scope.take_profit_order_id:
                previous = prior.take_profit_filled_qty
            else:
                previous = prior.stop_loss_filled_qty
        if event.filled_qty < previous:
            raise PaperTradeUpdateIntegrityError("broker cumulative filled_qty regressed")
        if event.event_type is PaperTradeUpdateEventType.PARTIAL_FILL and event.filled_qty <= previous:
            raise PaperTradeUpdateIntegrityError("partial_fill did not advance cumulative filled_qty")
        if event.event_type is PaperTradeUpdateEventType.FILL and event.filled_qty <= previous:
            raise PaperTradeUpdateIntegrityError("fill did not advance cumulative filled_qty")
        if event.broker_order_id != self.scope.parent_order_id and event.filled_qty > 0:
            if prior is None or prior.parent_filled_qty <= 0:
                raise PaperTradeUpdateIntegrityError(
                    "protection leg cannot fill before parent has fill evidence"
                )
        if (
            prior is not None
            and prior.take_profit_filled_qty > 0
            and event.broker_order_id == self.scope.stop_loss_order_id
            and event.filled_qty > 0
        ):
            raise PaperTradeUpdateIntegrityError("both bracket protection legs cannot fill")
        if (
            prior is not None
            and prior.stop_loss_filled_qty > 0
            and event.broker_order_id == self.scope.take_profit_order_id
            and event.filled_qty > 0
        ):
            raise PaperTradeUpdateIntegrityError("both bracket protection legs cannot fill")

    def _verify_locked(
        self,
        conn: sqlite3.Connection,
        *,
        allow_empty: bool,
    ) -> PaperTradeUpdateLedgerState | None:
        rows = conn.execute(
            """
            SELECT sequence, identity_key, event_hash, event_json, previous_hash,
                   chain_hash, occurred_at
            FROM alpaca_paper_trade_update_events
            WHERE scope_hash=? ORDER BY sequence
            """,
            (self._scope_hash,),
        ).fetchall()
        control = conn.execute(
            "SELECT * FROM alpaca_paper_trade_update_control WHERE scope_hash=?",
            (self._scope_hash,),
        ).fetchone()
        if not rows:
            if control is not None:
                raise PaperTradeUpdateIntegrityError("trade update control exists without events")
            if allow_empty:
                return None
            raise PaperTradeUpdateIntegrityError("trade update evidence is empty")
        if control is None:
            raise PaperTradeUpdateIntegrityError("trade update control is missing")

        expected_previous = "0" * 64
        prior_state: PaperTradeUpdateLedgerState | None = None
        for index, row in enumerate(rows, start=1):
            if int(row["sequence"]) != index:
                raise PaperTradeUpdateIntegrityError("trade update sequence gap detected")
            event = _event_from_json(str(row["event_json"]))
            self._validate_event_scope(event)
            if str(row["event_hash"]) != event.event_hash:
                raise PaperTradeUpdateIntegrityError("stored trade update event hash mismatch")
            if str(row["identity_key"]) != _identity_key(event):
                raise PaperTradeUpdateIntegrityError("stored trade update identity mismatch")
            if str(row["previous_hash"]) != expected_previous:
                raise PaperTradeUpdateIntegrityError("trade update previous hash mismatch")
            calculated = sha256(
                _canonical_json(
                    {
                        "event_hash": event.event_hash,
                        "event_json": str(row["event_json"]),
                        "previous_hash": expected_previous,
                        "scope_hash": self._scope_hash,
                        "sequence": index,
                    }
                ).encode("utf-8")
            ).hexdigest()
            if str(row["chain_hash"]) != calculated:
                raise PaperTradeUpdateIntegrityError("trade update chain hash mismatch")
            if str(row["occurred_at"]) != event.occurred_at.isoformat():
                raise PaperTradeUpdateIntegrityError("stored trade update timestamp mismatch")
            if prior_state is not None and event.occurred_at < prior_state.last_event_at:
                raise PaperTradeUpdateIntegrityError("trade update time regression detected")
            self._validate_fill_progression(event, prior_state)
            parent, take_profit, stop_loss = _next_filled_quantities(
                scope=self.scope,
                event=event,
                prior=prior_state,
            )
            control_payload = {
                "event_count": index,
                "head_hash": calculated,
                "last_event_at": event.occurred_at.isoformat(),
                "parent_filled_qty": str(parent),
                "scope_hash": self._scope_hash,
                "stop_loss_filled_qty": str(stop_loss),
                "take_profit_filled_qty": str(take_profit),
            }
            prior_state = PaperTradeUpdateLedgerState(
                scope_hash=self._scope_hash,
                event_count=index,
                head_hash=calculated,
                parent_filled_qty=parent,
                take_profit_filled_qty=take_profit,
                stop_loss_filled_qty=stop_loss,
                last_event_at=event.occurred_at,
                control_hash=sha256(_canonical_json(control_payload).encode("utf-8")).hexdigest(),
            )
            expected_previous = calculated

        assert prior_state is not None
        stored = _state_from_control(control)
        if stored != prior_state:
            raise PaperTradeUpdateIntegrityError("trade update anchored control mismatch")
        return prior_state


def _next_filled_quantities(
    *,
    scope: PaperTradeUpdateScope,
    event: PaperTradeUpdateEvent,
    prior: PaperTradeUpdateLedgerState | None,
) -> tuple[Decimal, Decimal, Decimal]:
    parent = Decimal("0") if prior is None else prior.parent_filled_qty
    take_profit = Decimal("0") if prior is None else prior.take_profit_filled_qty
    stop_loss = Decimal("0") if prior is None else prior.stop_loss_filled_qty
    if event.broker_order_id == scope.parent_order_id:
        parent = event.filled_qty
    elif event.broker_order_id == scope.take_profit_order_id:
        take_profit = event.filled_qty
    else:
        stop_loss = event.filled_qty
    return parent, take_profit, stop_loss


def _scope_hash(scope: PaperTradeUpdateScope) -> str:
    return sha256(
        _canonical_json(
            {
                "parent_client_order_id": scope.parent_client_order_id,
                "parent_order_id": scope.parent_order_id,
                "stop_loss_order_id": scope.stop_loss_order_id,
                "symbol": scope.symbol,
                "take_profit_order_id": scope.take_profit_order_id,
            }
        ).encode("utf-8")
    ).hexdigest()


def _identity_key(event: PaperTradeUpdateEvent) -> str:
    if event.execution_id is not None:
        return f"execution:{event.execution_id}"
    return "event:" + sha256(
        _canonical_json(
            {
                "broker_order_id": event.broker_order_id,
                "event_type": event.event_type.value,
                "filled_qty": str(event.filled_qty),
                "occurred_at": event.occurred_at.isoformat(),
                "order_status": event.order_status,
            }
        ).encode("utf-8")
    ).hexdigest()


def _event_json(event: PaperTradeUpdateEvent) -> str:
    return _canonical_json(
        {
            "asset_class": event.asset_class,
            "broker_order_id": event.broker_order_id,
            "client_order_id": event.client_order_id,
            "event_hash": event.event_hash,
            "event_type": event.event_type.value,
            "execution_id": event.execution_id,
            "fill_price": str(event.fill_price) if event.fill_price is not None else None,
            "fill_qty": str(event.fill_qty) if event.fill_qty is not None else None,
            "filled_qty": str(event.filled_qty),
            "frame_hash": event.frame_hash,
            "occurred_at": event.occurred_at.isoformat(),
            "order_class": event.order_class,
            "order_qty": str(event.order_qty),
            "order_status": event.order_status,
            "order_type": event.order_type,
            "position_qty": str(event.position_qty) if event.position_qty is not None else None,
            "side": event.side,
            "symbol": event.symbol,
        }
    )


def _event_from_json(raw: str) -> PaperTradeUpdateEvent:
    try:
        data = json.loads(raw, parse_constant=lambda token: _reject_constant(token))
        if not isinstance(data, dict):
            raise ValueError("event JSON root")
        return PaperTradeUpdateEvent(
            event_type=PaperTradeUpdateEventType(str(data["event_type"])),
            broker_order_id=str(data["broker_order_id"]),
            client_order_id=str(data["client_order_id"]),
            symbol=str(data["symbol"]),
            side=str(data["side"]),
            asset_class=str(data["asset_class"]),
            order_class=str(data["order_class"]),
            order_type=str(data["order_type"]),
            order_status=str(data["order_status"]),
            order_qty=Decimal(str(data["order_qty"])),
            filled_qty=Decimal(str(data["filled_qty"])),
            occurred_at=_timestamp(data["occurred_at"], "occurred_at"),
            execution_id=str(data["execution_id"]) if data["execution_id"] is not None else None,
            fill_price=Decimal(str(data["fill_price"])) if data["fill_price"] is not None else None,
            fill_qty=Decimal(str(data["fill_qty"])) if data["fill_qty"] is not None else None,
            position_qty=Decimal(str(data["position_qty"])) if data["position_qty"] is not None else None,
            frame_hash=str(data["frame_hash"]),
            event_hash=str(data["event_hash"]),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise PaperTradeUpdateIntegrityError("stored trade update event is malformed") from exc


def _state_from_control(row: sqlite3.Row) -> PaperTradeUpdateLedgerState:
    try:
        payload = {
            "event_count": int(row["event_count"]),
            "head_hash": str(row["head_hash"]),
            "last_event_at": str(row["last_event_at"]),
            "parent_filled_qty": str(row["parent_filled_qty"]),
            "scope_hash": str(row["scope_hash"]),
            "stop_loss_filled_qty": str(row["stop_loss_filled_qty"]),
            "take_profit_filled_qty": str(row["take_profit_filled_qty"]),
        }
        calculated = sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        if str(row["control_hash"]) != calculated:
            raise PaperTradeUpdateIntegrityError("trade update control hash mismatch")
        return PaperTradeUpdateLedgerState(
            scope_hash=payload["scope_hash"],
            event_count=payload["event_count"],
            head_hash=payload["head_hash"],
            parent_filled_qty=Decimal(payload["parent_filled_qty"]),
            take_profit_filled_qty=Decimal(payload["take_profit_filled_qty"]),
            stop_loss_filled_qty=Decimal(payload["stop_loss_filled_qty"]),
            last_event_at=_timestamp(payload["last_event_at"], "last_event_at"),
            control_hash=calculated,
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        if isinstance(exc, PaperTradeUpdateIntegrityError):
            raise
        raise PaperTradeUpdateIntegrityError("trade update control is malformed") from exc


def _strict_json_object(raw: bytes) -> Mapping[str, object]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, parse_constant=lambda token: _reject_constant(token))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PaperTradeUpdateIntegrityError("trade_updates frame is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PaperTradeUpdateIntegrityError("trade_updates frame root must be object")
    return value


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PaperTradeUpdateIntegrityError(f"field {key} must be non-empty string")
    return value


def _required_id(payload: Mapping[str, object], key: str) -> str:
    value = _required_str(payload, key)
    if not _ID_RE.fullmatch(value):
        raise PaperTradeUpdateIntegrityError(f"field {key} is not canonical identifier")
    return value


def _order_type(order: Mapping[str, object]) -> str:
    raw_type = order.get("type")
    raw_order_type = order.get("order_type")
    values = [value for value in (raw_type, raw_order_type) if isinstance(value, str) and value]
    if not values:
        raise PaperTradeUpdateIntegrityError("order type is missing")
    if len(set(values)) != 1:
        raise PaperTradeUpdateIntegrityError("order type aliases conflict")
    return values[0]


def _decimal(
    value: object,
    label: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
    finite: bool = False,
) -> Decimal:
    if not isinstance(value, str):
        raise PaperTradeUpdateIntegrityError(f"{label} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PaperTradeUpdateIntegrityError(f"{label} is invalid decimal") from exc
    if not parsed.is_finite():
        raise PaperTradeUpdateIntegrityError(f"{label} must be finite")
    if positive and parsed <= 0:
        raise PaperTradeUpdateIntegrityError(f"{label} must be > 0")
    if nonnegative and parsed < 0:
        raise PaperTradeUpdateIntegrityError(f"{label} must be >= 0")
    if not (positive or nonnegative or finite):
        raise AssertionError("decimal validation mode is required")
    return parsed


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise PaperTradeUpdateIntegrityError(f"{label} is missing")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PaperTradeUpdateIntegrityError(f"{label} is invalid ISO timestamp") from exc
    _require_aware(parsed, label)
    return parsed.astimezone(timezone.utc)


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _finite_positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _finite_nonnegative(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
