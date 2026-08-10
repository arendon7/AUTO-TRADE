from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from math import inf, sqrt
from statistics import fmean, stdev
from typing import Iterable

from ..domain import Side
from .costs import ExecutionCostModel
from .market import Bar, MarketDataset
from .strategy import ResearchSignal, ResearchStrategy, StrategyContext


class InvalidBacktestConfig(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_cash: Decimal
    cost_model: ExecutionCostModel
    execution_delay_bars: int
    annualization_factor: Decimal
    max_leverage: Decimal
    max_volume_participation: Decimal
    allow_short: bool = True

    def __post_init__(self) -> None:
        for name, value in (
            ("initial_cash", self.initial_cash),
            ("annualization_factor", self.annualization_factor),
            ("max_leverage", self.max_leverage),
            ("max_volume_participation", self.max_volume_participation),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise InvalidBacktestConfig(f"{name} must be finite and > 0")
        if self.execution_delay_bars < 1:
            raise InvalidBacktestConfig(
                "execution_delay_bars must be >= 1 to prevent same-bar look-ahead"
            )
        if self.max_volume_participation > 1:
            raise InvalidBacktestConfig("max_volume_participation cannot exceed 1")

    @property
    def config_hash(self) -> str:
        payload = {
            "initial_cash": str(self.initial_cash),
            "execution_delay_bars": self.execution_delay_bars,
            "annualization_factor": str(self.annualization_factor),
            "max_leverage": str(self.max_leverage),
            "max_volume_participation": str(self.max_volume_participation),
            "allow_short": self.allow_short,
            "cost_model": self.cost_model.fingerprint_payload(),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class ResearchFill:
    fill_id: str
    signal_id: str
    symbol: str
    side: Side
    quantity: Decimal
    reference_price: Decimal
    execution_price: Decimal
    fee: Decimal
    realized_pnl: Decimal
    occurred_at: datetime
    bar_index: int
    volume_participation: Decimal


@dataclass(frozen=True, slots=True)
class RejectedSignal:
    signal_id: str
    reason_code: str
    detail: str


@dataclass(frozen=True, slots=True)
class EquityPoint:
    occurred_at: datetime
    cash: Decimal
    position_quantity: Decimal
    mark_price: Decimal
    equity: Decimal
    gross_exposure: Decimal


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    net_return: float
    annualized_volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    turnover: float
    hit_rate: float
    profit_factor: float
    average_gross_exposure: float
    max_gross_exposure: float
    max_volume_participation: float
    total_fees: float
    fills: int
    rejected_signals: int


@dataclass(frozen=True, slots=True)
class BacktestResult:
    dataset_hash: str
    strategy_id: str
    strategy_version: str
    strategy_parameters: dict[str, str | int | float | bool]
    config_hash: str
    fills: tuple[ResearchFill, ...]
    rejected_signals: tuple[RejectedSignal, ...]
    equity_curve: tuple[EquityPoint, ...]
    metrics: BacktestMetrics

    @property
    def result_hash(self) -> str:
        payload = {
            "dataset_hash": self.dataset_hash,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_parameters": self.strategy_parameters,
            "config_hash": self.config_hash,
            "fills": [
                {
                    "signal_id": fill.signal_id,
                    "side": fill.side.value,
                    "quantity": str(fill.quantity),
                    "execution_price": str(fill.execution_price),
                    "fee": str(fill.fee),
                    "realized_pnl": str(fill.realized_pnl),
                    "occurred_at": fill.occurred_at.isoformat(),
                    "bar_index": fill.bar_index,
                }
                for fill in self.fills
            ],
            "equity": [str(point.equity) for point in self.equity_curve],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(raw).hexdigest()


@dataclass(slots=True)
class _PositionBook:
    quantity: Decimal = Decimal("0")
    average_effective_entry_price: Decimal = Decimal("0")

    def apply(
        self,
        *,
        signed_quantity: Decimal,
        execution_price: Decimal,
        fee: Decimal,
    ) -> Decimal:
        zero = Decimal("0")
        if signed_quantity == zero:
            return zero
        quantity = abs(signed_quantity)
        fee_per_unit = fee / quantity
        old = self.quantity
        new = old + signed_quantity

        if old == zero or old * signed_quantity > zero:
            effective_entry = (
                execution_price + fee_per_unit
                if signed_quantity > zero
                else execution_price - fee_per_unit
            )
            old_abs = abs(old)
            new_abs = abs(new)
            if old_abs == zero:
                self.average_effective_entry_price = effective_entry
            else:
                self.average_effective_entry_price = (
                    old_abs * self.average_effective_entry_price
                    + quantity * effective_entry
                ) / new_abs
            self.quantity = new
            return zero

        closing_quantity = min(abs(old), quantity)
        if old > zero:
            effective_exit = execution_price - fee_per_unit
            realized = closing_quantity * (
                effective_exit - self.average_effective_entry_price
            )
        else:
            effective_exit = execution_price + fee_per_unit
            realized = closing_quantity * (
                self.average_effective_entry_price - effective_exit
            )

        self.quantity = new
        if new == zero:
            self.average_effective_entry_price = zero
        elif old * new < zero:
            self.average_effective_entry_price = (
                execution_price + fee_per_unit
                if new > zero
                else execution_price - fee_per_unit
            )
        return realized


class BacktestEngine:
    """Single-symbol event-driven backtester with structural anti-look-ahead.

    A strategy sees bars only through the current close. Its signal is scheduled
    at least one full bar into the future and executes against that future bar's
    open plus explicit spread/slippage/fees.
    """

    def run(
        self,
        *,
        dataset: MarketDataset,
        strategy: ResearchStrategy,
        config: BacktestConfig,
    ) -> BacktestResult:
        if not strategy.strategy_id.strip():
            raise ValueError("strategy_id is required")
        if not strategy.strategy_version.strip():
            raise ValueError("strategy_version is required")
        parameters = dict(strategy.parameters)
        json.dumps(parameters, sort_keys=True)

        cash = config.initial_cash
        book = _PositionBook()
        pending: dict[int, list[ResearchSignal]] = {}
        seen_signal_ids: set[str] = set()
        fills: list[ResearchFill] = []
        rejected: list[RejectedSignal] = []
        equity_curve: list[EquityPoint] = []
        turnover_notional = Decimal("0")
        total_fees = Decimal("0")
        max_volume_participation = Decimal("0")

        for index, bar in enumerate(dataset.bars):
            for signal in pending.pop(index, []):
                fill, rejection = self._execute_signal(
                    signal=signal,
                    bar=bar,
                    bar_index=index,
                    cash=cash,
                    book=book,
                    config=config,
                )
                if rejection is not None:
                    rejected.append(rejection)
                    continue
                assert fill is not None
                signed_quantity = fill.side.sign * fill.quantity
                cash = cash - signed_quantity * fill.execution_price - fill.fee
                realized = book.apply(
                    signed_quantity=signed_quantity,
                    execution_price=fill.execution_price,
                    fee=fill.fee,
                )
                fill = ResearchFill(
                    fill_id=fill.fill_id,
                    signal_id=fill.signal_id,
                    symbol=fill.symbol,
                    side=fill.side,
                    quantity=fill.quantity,
                    reference_price=fill.reference_price,
                    execution_price=fill.execution_price,
                    fee=fill.fee,
                    realized_pnl=realized,
                    occurred_at=fill.occurred_at,
                    bar_index=fill.bar_index,
                    volume_participation=fill.volume_participation,
                )
                fills.append(fill)
                turnover_notional += fill.quantity * fill.execution_price
                total_fees += fill.fee
                max_volume_participation = max(
                    max_volume_participation, fill.volume_participation
                )

            equity = cash + book.quantity * bar.close
            gross = abs(book.quantity * bar.close)
            equity_curve.append(
                EquityPoint(
                    occurred_at=bar.ended_at,
                    cash=cash,
                    position_quantity=book.quantity,
                    mark_price=bar.close,
                    equity=equity,
                    gross_exposure=gross,
                )
            )

            context = StrategyContext(
                symbol=dataset.instrument.symbol,
                index=index,
                history=dataset.bars[: index + 1],
                current_position_quantity=book.quantity,
                current_equity=equity,
            )
            signal = strategy.on_bar(context)
            if signal is None:
                continue
            validation_error = self._validate_signal(
                signal=signal,
                current_bar=bar,
                dataset=dataset,
                seen_signal_ids=seen_signal_ids,
            )
            if validation_error is not None:
                rejected.append(validation_error)
                continue
            seen_signal_ids.add(signal.signal_id)
            execution_index = index + config.execution_delay_bars
            if execution_index >= len(dataset.bars):
                rejected.append(
                    RejectedSignal(
                        signal_id=signal.signal_id,
                        reason_code="END_OF_DATA",
                        detail="no future bar available for execution",
                    )
                )
                continue
            pending.setdefault(execution_index, []).append(signal)

        metrics = _metrics(
            initial_cash=config.initial_cash,
            annualization_factor=config.annualization_factor,
            equity_curve=equity_curve,
            fills=fills,
            rejected=rejected,
            turnover_notional=turnover_notional,
            total_fees=total_fees,
            max_volume_participation=max_volume_participation,
        )
        return BacktestResult(
            dataset_hash=dataset.dataset_hash,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.strategy_version,
            strategy_parameters=parameters,
            config_hash=config.config_hash,
            fills=tuple(fills),
            rejected_signals=tuple(rejected),
            equity_curve=tuple(equity_curve),
            metrics=metrics,
        )

    @staticmethod
    def _validate_signal(
        *,
        signal: ResearchSignal,
        current_bar: Bar,
        dataset: MarketDataset,
        seen_signal_ids: set[str],
    ) -> RejectedSignal | None:
        if signal.signal_id in seen_signal_ids:
            return RejectedSignal(signal.signal_id, "DUPLICATE_SIGNAL_ID", signal.signal_id)
        if signal.symbol != dataset.instrument.symbol:
            return RejectedSignal(
                signal.signal_id,
                "SIGNAL_SYMBOL_MISMATCH",
                f"{signal.symbol} != {dataset.instrument.symbol}",
            )
        if signal.generated_at != current_bar.ended_at:
            return RejectedSignal(
                signal.signal_id,
                "SIGNAL_TIMESTAMP_MISMATCH",
                "signals must be generated exactly at current bar close",
            )
        return None

    @staticmethod
    def _execute_signal(
        *,
        signal: ResearchSignal,
        bar: Bar,
        bar_index: int,
        cash: Decimal,
        book: _PositionBook,
        config: BacktestConfig,
    ) -> tuple[ResearchFill | None, RejectedSignal | None]:
        signed_quantity = signal.quantity_delta
        projected_quantity = book.quantity + signed_quantity
        if not config.allow_short and projected_quantity < 0:
            return None, RejectedSignal(
                signal.signal_id, "SHORT_NOT_ALLOWED", str(projected_quantity)
            )

        quantity = abs(signed_quantity)
        if bar.volume <= 0:
            return None, RejectedSignal(
                signal.signal_id, "NO_EXECUTABLE_VOLUME", str(bar.volume)
            )
        participation = quantity / bar.volume
        if participation > config.max_volume_participation:
            return None, RejectedSignal(
                signal.signal_id,
                "MAX_VOLUME_PARTICIPATION",
                str(participation),
            )

        side = Side.BUY if signed_quantity > 0 else Side.SELL
        execution_price = config.cost_model.execution_price(
            side=side, reference_price=bar.open
        )
        fee = config.cost_model.fee(
            quantity=quantity, execution_price=execution_price
        )
        projected_cash = cash - signed_quantity * execution_price - fee
        projected_equity = projected_cash + projected_quantity * execution_price
        if projected_equity <= 0:
            return None, RejectedSignal(
                signal.signal_id, "NON_POSITIVE_EQUITY", str(projected_equity)
            )
        projected_gross = abs(projected_quantity * execution_price)
        leverage = projected_gross / projected_equity
        if leverage > config.max_leverage:
            return None, RejectedSignal(
                signal.signal_id, "MAX_LEVERAGE", str(leverage)
            )

        fill = ResearchFill(
            fill_id=f"research-fill:{signal.signal_id}:{bar_index}",
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            side=side,
            quantity=quantity,
            reference_price=bar.open,
            execution_price=execution_price,
            fee=fee,
            realized_pnl=Decimal("0"),
            occurred_at=bar.started_at,
            bar_index=bar_index,
            volume_participation=participation,
        )
        return fill, None


def _metrics(
    *,
    initial_cash: Decimal,
    annualization_factor: Decimal,
    equity_curve: Iterable[EquityPoint],
    fills: list[ResearchFill],
    rejected: list[RejectedSignal],
    turnover_notional: Decimal,
    total_fees: Decimal,
    max_volume_participation: Decimal,
) -> BacktestMetrics:
    points = tuple(equity_curve)
    equities = [float(point.equity) for point in points]
    initial = float(initial_cash)
    final = equities[-1]
    net_return = final / initial - 1.0

    returns: list[float] = []
    previous = initial
    for equity in equities:
        returns.append(equity / previous - 1.0 if previous != 0 else 0.0)
        previous = equity

    annualization = float(annualization_factor)
    volatility = stdev(returns) * sqrt(annualization) if len(returns) >= 2 else 0.0
    mean_return = fmean(returns) if returns else 0.0
    period_stdev = stdev(returns) if len(returns) >= 2 else 0.0
    sharpe = mean_return / period_stdev * sqrt(annualization) if period_stdev > 0 else 0.0
    downside = [min(value, 0.0) for value in returns]
    downside_deviation = sqrt(fmean([value * value for value in downside])) if downside else 0.0
    sortino = (
        mean_return / downside_deviation * sqrt(annualization)
        if downside_deviation > 0
        else 0.0
    )

    peak = initial
    max_drawdown = 0.0
    exposure_ratios: list[float] = []
    max_gross = 0.0
    for point in points:
        equity = float(point.equity)
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
        gross = float(point.gross_exposure)
        max_gross = max(max_gross, gross)
        exposure_ratios.append(gross / equity if equity > 0 else 0.0)

    realized = [float(fill.realized_pnl) for fill in fills if fill.realized_pnl != 0]
    wins = [value for value in realized if value > 0]
    losses = [value for value in realized if value < 0]
    closed_count = len(wins) + len(losses)
    hit_rate = len(wins) / closed_count if closed_count else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = inf
    else:
        profit_factor = 0.0

    return BacktestMetrics(
        net_return=net_return,
        annualized_volatility=volatility,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        turnover=float(turnover_notional / initial_cash),
        hit_rate=hit_rate,
        profit_factor=profit_factor,
        average_gross_exposure=fmean(exposure_ratios) if exposure_ratios else 0.0,
        max_gross_exposure=max_gross,
        max_volume_participation=float(max_volume_participation),
        total_fees=float(total_fees),
        fills=len(fills),
        rejected_signals=len(rejected),
    )
