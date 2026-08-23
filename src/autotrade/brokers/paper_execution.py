from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

from autotrade.broker_state import BrokerAccountState
from autotrade.domain import (
    Fill,
    MarketSnapshot,
    OrderRecord,
    OrderStatus,
    OrderType,
    Side,
    intent_fingerprint,
)

from .base import BrokerExecution


BPS_DENOMINATOR = Decimal("10000")


class PaperExecutionError(RuntimeError):
    pass


class PaperExecutionMarketError(PaperExecutionError):
    """The supplied market snapshot is unsafe for deterministic PAPER execution."""


class PaperExecutionConflict(PaperExecutionError):
    """A local paper order identity was reused with different semantics."""


@dataclass(frozen=True, slots=True)
class PaperExecutionConfig:
    """Conservative deterministic execution assumptions for offline/PAPER qualification.

    This model intentionally has no network surface and does not predict real fills.
    It provides reproducible adverse execution assumptions while reusing the normal
    OMS, Safety, fill store, portfolio projection and reconciliation machinery.
    """

    slippage_bps: Decimal = Decimal("2")
    max_fill_fraction: Decimal = Decimal("1")
    max_market_age: timedelta = timedelta(seconds=2)
    max_spread_bps: Decimal = Decimal("250")

    def __post_init__(self) -> None:
        _bounded_decimal(self.slippage_bps, "slippage_bps", low=Decimal("0"), high=Decimal("500"))
        _bounded_decimal(
            self.max_fill_fraction,
            "max_fill_fraction",
            low=Decimal("0"),
            high=Decimal("1"),
            low_inclusive=False,
        )
        _bounded_decimal(
            self.max_spread_bps,
            "max_spread_bps",
            low=Decimal("0"),
            high=BPS_DENOMINATOR,
            low_inclusive=False,
        )
        if not isinstance(self.max_market_age, timedelta):
            raise TypeError("max_market_age must be timedelta")
        if self.max_market_age <= timedelta(0) or self.max_market_age > timedelta(minutes=5):
            raise ValueError("max_market_age must be >0 and <=5 minutes")


class DeterministicPaperExecutionBroker:
    """Fail-closed, no-network PAPER execution broker for Strategy Lab qualification.

    Key properties:
    - current-touch execution only; no look-ahead data;
    - adverse deterministic slippage;
    - bounded deterministic partial fills;
    - stale/future/crossed/over-wide market rejection before a simulated submit;
    - duplicate local order ids are idempotent only when the intent fingerprint is exact;
    - cancellation never creates fills;
    - exposes deterministic simulated broker truth through the existing InspectableBroker shape;
    - no external broker, credential, HTTP, socket or LIVE authority.

    This is an execution stress model, not a claim that a real venue would fill at
    these prices or quantities.
    """

    def __init__(self, *, config: PaperExecutionConfig | None = None) -> None:
        self._config = config or PaperExecutionConfig()
        self._submission_count = 0
        self._cancel_count = 0
        self._intent_fingerprints: dict[str, str] = {}
        self._executions: dict[str, BrokerExecution] = {}
        self._accounted_fill_ids: set[str] = set()
        self._signed_position_notional_by_symbol: dict[str, Decimal] = {}

    @property
    def submission_count(self) -> int:
        return self._submission_count

    @property
    def cancel_count(self) -> int:
        return self._cancel_count

    def execution_for_order(self, order_id: str) -> BrokerExecution | None:
        return self._executions.get(order_id)

    def get_execution(self, order_id: str) -> BrokerExecution | None:
        """InspectableBroker-compatible cumulative execution lookup."""
        return self._executions.get(order_id)

    def account_state(self, *, now: datetime) -> BrokerAccountState:
        """Return deterministic simulated broker truth; performs no external I/O."""
        _require_aware(now, "now")
        open_order_ids = frozenset(
            order_id
            for order_id, execution in self._executions.items()
            if execution.status.broker_open
        )
        positions = {
            symbol: value
            for symbol, value in self._signed_position_notional_by_symbol.items()
            if value != 0
        }
        return BrokerAccountState(
            observed_at=now.astimezone(timezone.utc),
            state_known=True,
            signed_position_notional_by_symbol=positions,
            open_order_ids=open_order_ids,
        )

    def submit(
        self,
        *,
        order: OrderRecord,
        market: MarketSnapshot,
        now: datetime,
    ) -> BrokerExecution:
        _require_aware(now, "now")
        if not isinstance(order, OrderRecord):
            raise TypeError("paper execution requires OrderRecord")
        if order.status is not OrderStatus.VALIDATED:
            raise PaperExecutionConflict("paper execution accepts only OMS VALIDATED orders")
        _validate_order(order)
        _validate_market(
            market=market,
            now=now,
            expected_symbol=order.intent.symbol,
            config=self._config,
        )

        fingerprint = intent_fingerprint(order.intent)
        existing_fingerprint = self._intent_fingerprints.get(order.order_id)
        if existing_fingerprint is not None:
            if existing_fingerprint != fingerprint:
                raise PaperExecutionConflict("paper order_id was reused with a different intent")
            return self._executions[order.order_id]

        execution = self._simulate(order=order, market=market, now=now)
        self._intent_fingerprints[order.order_id] = fingerprint
        self._executions[order.order_id] = execution
        self._apply_account_fills(execution.fills)
        self._submission_count += 1
        return execution

    def cancel(self, *, order_id: str, now: datetime) -> BrokerExecution:
        _require_aware(now, "now")
        execution = self._executions.get(order_id)
        if execution is None:
            raise KeyError(order_id)
        if execution.status.terminal:
            return execution
        cancelled = BrokerExecution(status=OrderStatus.CANCELLED, fills=execution.fills)
        self._executions[order_id] = cancelled
        self._cancel_count += 1
        return cancelled

    def _apply_account_fills(self, fills: tuple[Fill, ...]) -> None:
        for fill in fills:
            if fill.fill_id in self._accounted_fill_ids:
                continue
            signed_notional = fill.side.sign * fill.quantity * fill.price
            self._signed_position_notional_by_symbol[fill.symbol] = (
                self._signed_position_notional_by_symbol.get(fill.symbol, Decimal("0"))
                + signed_notional
            )
            self._accounted_fill_ids.add(fill.fill_id)

    def _simulate(
        self,
        *,
        order: OrderRecord,
        market: MarketSnapshot,
        now: datetime,
    ) -> BrokerExecution:
        intent = order.intent
        touch = market.ask if intent.side is Side.BUY else market.bid
        slippage = touch * self._config.slippage_bps / BPS_DENOMINATOR
        adverse_price = touch + slippage if intent.side is Side.BUY else touch - slippage
        if adverse_price <= 0:
            raise PaperExecutionMarketError("adverse execution price is non-positive")

        if intent.order_type is OrderType.LIMIT:
            assert intent.limit_price is not None
            marketable_after_slippage = (
                intent.side is Side.BUY and intent.limit_price >= adverse_price
            ) or (
                intent.side is Side.SELL and intent.limit_price <= adverse_price
            )
            if not marketable_after_slippage:
                return BrokerExecution(status=OrderStatus.SUBMITTED)

        fill_quantity = intent.quantity * self._config.max_fill_fraction
        if fill_quantity <= 0 or fill_quantity > intent.quantity:
            raise PaperExecutionConflict("configured paper fill quantity is invalid")

        fill = Fill(
            fill_id=_fill_id(order),
            order_id=order.order_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=fill_quantity,
            price=adverse_price,
            occurred_at=now.astimezone(timezone.utc),
        )
        status = (
            OrderStatus.FILLED
            if fill_quantity == intent.quantity
            else OrderStatus.PARTIALLY_FILLED
        )
        return BrokerExecution(status=status, fills=(fill,))


def _validate_order(order: OrderRecord) -> None:
    intent = order.intent
    if not isinstance(intent.quantity, Decimal) or not intent.quantity.is_finite() or intent.quantity <= 0:
        raise PaperExecutionConflict("paper order quantity must be finite positive Decimal")
    if intent.order_type not in {OrderType.MARKET, OrderType.LIMIT}:
        raise PaperExecutionConflict("unsupported paper order type")
    if intent.order_type is OrderType.MARKET:
        if intent.limit_price is not None:
            raise PaperExecutionConflict("market paper order may not carry limit_price")
    else:
        if (
            not isinstance(intent.limit_price, Decimal)
            or not intent.limit_price.is_finite()
            or intent.limit_price <= 0
        ):
            raise PaperExecutionConflict("limit paper order requires finite positive limit_price")


def _validate_market(
    *,
    market: MarketSnapshot,
    now: datetime,
    expected_symbol: str,
    config: PaperExecutionConfig,
) -> None:
    if not isinstance(market, MarketSnapshot):
        raise TypeError("paper execution requires MarketSnapshot")
    _require_aware(market.observed_at, "market.observed_at")
    if market.symbol != expected_symbol:
        raise PaperExecutionMarketError("market/order symbol mismatch")
    for label, value in (("bid", market.bid), ("ask", market.ask), ("last", market.last)):
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            raise PaperExecutionMarketError(f"market {label} must be finite positive Decimal")
    if market.bid > market.ask:
        raise PaperExecutionMarketError("crossed market snapshot is unsafe")

    instant = now.astimezone(timezone.utc)
    observed = market.observed_at.astimezone(timezone.utc)
    age = instant - observed
    if age < timedelta(0):
        raise PaperExecutionMarketError("future market snapshot is unsafe")
    if age > config.max_market_age:
        raise PaperExecutionMarketError("stale market snapshot is unsafe")

    midpoint = (market.bid + market.ask) / Decimal("2")
    spread_bps = (market.ask - market.bid) / midpoint * BPS_DENOMINATOR
    if spread_bps > config.max_spread_bps:
        raise PaperExecutionMarketError("market spread exceeds configured PAPER execution bound")


def _fill_id(order: OrderRecord) -> str:
    payload = f"{order.order_id}|{intent_fingerprint(order.intent)}|paper-execution-fill-1"
    return "paper-fill-" + sha256(payload.encode("utf-8")).hexdigest()[:32]


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware datetime")


def _bounded_decimal(
    value: Decimal,
    label: str,
    *,
    low: Decimal,
    high: Decimal,
    low_inclusive: bool = True,
) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError(f"{label} must be finite Decimal")
    below = value < low if low_inclusive else value <= low
    if below or value > high:
        comparator = ">=" if low_inclusive else ">"
        raise ValueError(f"{label} must be {comparator}{low} and <={high}")


__all__ = [
    "BPS_DENOMINATOR",
    "DeterministicPaperExecutionBroker",
    "PaperExecutionConfig",
    "PaperExecutionConflict",
    "PaperExecutionError",
    "PaperExecutionMarketError",
]
