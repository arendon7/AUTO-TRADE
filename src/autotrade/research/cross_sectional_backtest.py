"""Future-bar-only multi-asset backtest for OSS-2 cross-sectional research.

The engine computes ranking evidence at closed bar t and may rebalance only at
bar t+1 open. It is deterministic, long-only, volume-bounded and cost-aware.
It owns no broker/network/OMS/Safety writer or execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from math import sqrt
from statistics import fmean, stdev

from ..domain import Side
from .costs import ExecutionCostModel
from .cross_sectional import (
    CrossSectionalMomentumConfig,
    CrossSectionalRankingEvidence,
    rank_cross_sectional_momentum,
)
from .portfolio_dependence import (
    CalibrationPhase,
    ReturnObservation,
    StrategyReturnSeries,
)
from .universe import AlignedMarketUniverse


_ZERO = Decimal("0")
_ONE = Decimal("1")


class InvalidCrossSectionalBacktestConfig(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CrossSectionalBacktestConfig:
    initial_cash: Decimal
    ranking: CrossSectionalMomentumConfig
    cost_model: ExecutionCostModel
    rebalance_every_bars: int
    annualization_factor: Decimal
    gross_target: Decimal
    max_volume_participation: Decimal
    min_trade_notional: Decimal

    def __post_init__(self) -> None:
        if (
            not isinstance(self.initial_cash, Decimal)
            or not self.initial_cash.is_finite()
            or self.initial_cash <= _ZERO
        ):
            raise InvalidCrossSectionalBacktestConfig(
                "initial_cash must be finite Decimal > 0"
            )
        if not isinstance(self.ranking, CrossSectionalMomentumConfig):
            raise InvalidCrossSectionalBacktestConfig(
                "ranking must be CrossSectionalMomentumConfig"
            )
        if not isinstance(self.cost_model, ExecutionCostModel):
            raise InvalidCrossSectionalBacktestConfig(
                "cost_model must be ExecutionCostModel"
            )
        if (
            isinstance(self.rebalance_every_bars, bool)
            or not isinstance(self.rebalance_every_bars, int)
            or self.rebalance_every_bars < 1
        ):
            raise InvalidCrossSectionalBacktestConfig(
                "rebalance_every_bars must be integer >= 1"
            )
        for name, value in (
            ("annualization_factor", self.annualization_factor),
            ("gross_target", self.gross_target),
            ("max_volume_participation", self.max_volume_participation),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= _ZERO:
                raise InvalidCrossSectionalBacktestConfig(
                    f"{name} must be finite Decimal > 0"
                )
        if self.gross_target > _ONE:
            raise InvalidCrossSectionalBacktestConfig("gross_target cannot exceed 1")
        if self.max_volume_participation > _ONE:
            raise InvalidCrossSectionalBacktestConfig(
                "max_volume_participation cannot exceed 1"
            )
        if (
            not isinstance(self.min_trade_notional, Decimal)
            or not self.min_trade_notional.is_finite()
            or self.min_trade_notional < _ZERO
        ):
            raise InvalidCrossSectionalBacktestConfig(
                "min_trade_notional must be finite Decimal >= 0"
            )

    @property
    def config_hash(self) -> str:
        return _hash(
            {
                "initial_cash": str(self.initial_cash),
                "ranking_fingerprint": self.ranking.fingerprint,
                "cost_model": self.cost_model.fingerprint_payload(),
                "rebalance_every_bars": self.rebalance_every_bars,
                "annualization_factor": str(self.annualization_factor),
                "gross_target": str(self.gross_target),
                "max_volume_participation": str(self.max_volume_participation),
                "min_trade_notional": str(self.min_trade_notional),
            }
        )


@dataclass(frozen=True, slots=True)
class CrossSectionalResearchFill:
    fill_id: str
    ranking_fingerprint: str
    symbol: str
    side: Side
    quantity: Decimal
    reference_price: Decimal
    execution_price: Decimal
    fee: Decimal
    signal_bar_index: int
    execution_bar_index: int
    occurred_at: datetime
    volume_participation: Decimal


@dataclass(frozen=True, slots=True)
class CrossSectionalEquityPoint:
    occurred_at: datetime
    cash: Decimal
    positions: tuple[tuple[str, Decimal], ...]
    equity: Decimal
    gross_exposure: Decimal


@dataclass(frozen=True, slots=True)
class CrossSectionalBacktestMetrics:
    net_return: float
    annualized_volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    turnover: float
    average_gross_exposure_ratio: float
    max_gross_exposure_ratio: float
    max_volume_participation: float
    total_fees: float
    average_target_tracking_error: float
    max_target_tracking_error: float
    fills: int
    rebalances: int


@dataclass(frozen=True, slots=True)
class CrossSectionalBacktestResult:
    universe_hash: str
    config_hash: str
    fills: tuple[CrossSectionalResearchFill, ...]
    ranking_evidence: tuple[CrossSectionalRankingEvidence, ...]
    equity_curve: tuple[CrossSectionalEquityPoint, ...]
    period_returns: tuple[tuple[datetime, Decimal], ...]
    metrics: CrossSectionalBacktestMetrics

    @property
    def result_hash(self) -> str:
        return _hash(
            {
                "universe_hash": self.universe_hash,
                "config_hash": self.config_hash,
                "ranking_evidence": [item.fingerprint for item in self.ranking_evidence],
                "fills": [
                    {
                        "fill_id": fill.fill_id,
                        "ranking_fingerprint": fill.ranking_fingerprint,
                        "symbol": fill.symbol,
                        "side": fill.side.value,
                        "quantity": str(fill.quantity),
                        "execution_price": str(fill.execution_price),
                        "fee": str(fill.fee),
                        "signal_bar_index": fill.signal_bar_index,
                        "execution_bar_index": fill.execution_bar_index,
                        "occurred_at": fill.occurred_at.isoformat(),
                    }
                    for fill in self.fills
                ],
                "equity": [str(point.equity) for point in self.equity_curve],
                "period_returns": [
                    [occurred_at.isoformat(), str(value)]
                    for occurred_at, value in self.period_returns
                ],
            }
        )

    def to_strategy_return_series(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        phase: CalibrationPhase,
    ) -> StrategyReturnSeries:
        observations = tuple(
            ReturnObservation(occurred_at=occurred_at, value=value)
            for occurred_at, value in self.period_returns
        )
        return StrategyReturnSeries(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            phase=phase,
            source_hash=self.result_hash,
            observations=observations,
        )


class CrossSectionalBacktestEngine:
    """Long-only deterministic research simulator with one-bar execution delay."""

    def run(
        self,
        *,
        universe: AlignedMarketUniverse,
        config: CrossSectionalBacktestConfig,
    ) -> CrossSectionalBacktestResult:
        if universe.bar_count <= config.ranking.lookback_bars + 1:
            raise InvalidCrossSectionalBacktestConfig(
                "universe does not contain enough bars for ranking plus future execution"
            )

        cash = config.initial_cash
        positions = {symbol: _ZERO for symbol in universe.symbols}
        fills: list[CrossSectionalResearchFill] = []
        rankings: list[CrossSectionalRankingEvidence] = []
        equity_curve: list[CrossSectionalEquityPoint] = []
        turnover_notional = _ZERO
        total_fees = _ZERO
        max_participation = _ZERO
        tracking_errors: list[Decimal] = []
        started = False

        for execution_index in range(config.ranking.lookback_bars + 1, universe.bar_count):
            signal_index = execution_index - 1
            should_rebalance = (
                (signal_index - config.ranking.lookback_bars)
                % config.rebalance_every_bars
                == 0
            )
            if should_rebalance:
                ranking = rank_cross_sectional_momentum(
                    universe,
                    config.ranking,
                    as_of_bar_index=signal_index,
                )
                rankings.append(ranking)
                (
                    cash,
                    rebalance_fills,
                    rebalance_turnover,
                    rebalance_fees,
                    rebalance_max_participation,
                    tracking_error,
                ) = self._rebalance(
                    universe=universe,
                    config=config,
                    ranking=ranking,
                    execution_index=execution_index,
                    cash=cash,
                    positions=positions,
                )
                fills.extend(rebalance_fills)
                turnover_notional += rebalance_turnover
                total_fees += rebalance_fees
                max_participation = max(
                    max_participation, rebalance_max_participation
                )
                tracking_errors.append(tracking_error)
                started = True

            if started:
                point = _mark_equity(
                    universe=universe,
                    index=execution_index,
                    cash=cash,
                    positions=positions,
                )
                if point.equity <= _ZERO:
                    raise RuntimeError("cross-sectional backtest produced non-positive equity")
                equity_curve.append(point)

        if not equity_curve:
            raise InvalidCrossSectionalBacktestConfig("backtest produced no evaluation points")
        period_returns = _period_returns(config.initial_cash, tuple(equity_curve))
        metrics = _portfolio_metrics(
            initial_cash=config.initial_cash,
            annualization_factor=config.annualization_factor,
            equity_curve=tuple(equity_curve),
            period_returns=period_returns,
            turnover_notional=turnover_notional,
            total_fees=total_fees,
            max_volume_participation=max_participation,
            tracking_errors=tuple(tracking_errors),
            fills=len(fills),
            rebalances=len(rankings),
        )
        return CrossSectionalBacktestResult(
            universe_hash=universe.universe_hash,
            config_hash=config.config_hash,
            fills=tuple(fills),
            ranking_evidence=tuple(rankings),
            equity_curve=tuple(equity_curve),
            period_returns=period_returns,
            metrics=metrics,
        )

    @staticmethod
    def _rebalance(
        *,
        universe: AlignedMarketUniverse,
        config: CrossSectionalBacktestConfig,
        ranking: CrossSectionalRankingEvidence,
        execution_index: int,
        cash: Decimal,
        positions: dict[str, Decimal],
    ) -> tuple[
        Decimal,
        list[CrossSectionalResearchFill],
        Decimal,
        Decimal,
        Decimal,
        Decimal,
    ]:
        bars = {
            dataset.instrument.symbol: dataset.bars[execution_index]
            for dataset in universe.datasets
        }
        equity_open = cash + sum(
            (positions[symbol] * bars[symbol].open for symbol in universe.symbols),
            _ZERO,
        )
        if equity_open <= _ZERO:
            raise RuntimeError("non-positive equity before rebalance")

        ranking_weights = dict(ranking.target_weights)
        target_quantities: dict[str, Decimal] = {}
        desired_weights: dict[str, Decimal] = {}
        for dataset in universe.datasets:
            symbol = dataset.instrument.symbol
            target_weight = ranking_weights[symbol] * config.gross_target
            desired_weights[symbol] = target_weight
            raw_quantity = target_weight * equity_open / bars[symbol].open
            target_quantities[symbol] = _floor_step(
                raw_quantity, dataset.instrument.quantity_step
            )

        fills: list[CrossSectionalResearchFill] = []
        turnover = _ZERO
        fees = _ZERO
        max_participation = _ZERO

        # Risk reduction first: sales free cash before any buys are considered.
        for dataset in universe.datasets:
            symbol = dataset.instrument.symbol
            desired_sell = positions[symbol] - target_quantities[symbol]
            if desired_sell <= _ZERO:
                continue
            (
                cash,
                fill,
            ) = _execute_trade(
                dataset=dataset,
                bar=bars[symbol],
                signal_bar_index=ranking.as_of_bar_index,
                execution_bar_index=execution_index,
                ranking_fingerprint=ranking.fingerprint,
                side=Side.SELL,
                desired_quantity=desired_sell,
                cash=cash,
                current_quantity=positions[symbol],
                config=config,
            )
            if fill is None:
                continue
            positions[symbol] -= fill.quantity
            fills.append(fill)
            turnover += fill.quantity * fill.execution_price
            fees += fill.fee
            max_participation = max(max_participation, fill.volume_participation)

        # Buys are bounded by remaining cash and current-bar volume.
        for dataset in universe.datasets:
            symbol = dataset.instrument.symbol
            desired_buy = target_quantities[symbol] - positions[symbol]
            if desired_buy <= _ZERO:
                continue
            (
                cash,
                fill,
            ) = _execute_trade(
                dataset=dataset,
                bar=bars[symbol],
                signal_bar_index=ranking.as_of_bar_index,
                execution_bar_index=execution_index,
                ranking_fingerprint=ranking.fingerprint,
                side=Side.BUY,
                desired_quantity=desired_buy,
                cash=cash,
                current_quantity=positions[symbol],
                config=config,
            )
            if fill is None:
                continue
            positions[symbol] += fill.quantity
            fills.append(fill)
            turnover += fill.quantity * fill.execution_price
            fees += fill.fee
            max_participation = max(max_participation, fill.volume_participation)

        equity_after = cash + sum(
            (positions[symbol] * bars[symbol].open for symbol in universe.symbols),
            _ZERO,
        )
        if equity_after <= _ZERO:
            raise RuntimeError("non-positive equity after rebalance")
        tracking_error = sum(
            (
                abs(
                    positions[symbol] * bars[symbol].open / equity_after
                    - desired_weights[symbol]
                )
                for symbol in universe.symbols
            ),
            _ZERO,
        )
        return (
            cash,
            fills,
            turnover,
            fees,
            max_participation,
            tracking_error,
        )


def _execute_trade(
    *,
    dataset,
    bar,
    signal_bar_index: int,
    execution_bar_index: int,
    ranking_fingerprint: str,
    side: Side,
    desired_quantity: Decimal,
    cash: Decimal,
    current_quantity: Decimal,
    config: CrossSectionalBacktestConfig,
) -> tuple[Decimal, CrossSectionalResearchFill | None]:
    step = dataset.instrument.quantity_step
    desired = _floor_step(desired_quantity, step)
    if desired <= _ZERO or bar.volume <= _ZERO:
        return cash, None

    volume_cap = _floor_step(bar.volume * config.max_volume_participation, step)
    quantity = min(desired, volume_cap)
    if side is Side.SELL:
        quantity = min(quantity, current_quantity)
    quantity = _floor_step(quantity, step)
    if quantity <= _ZERO:
        return cash, None

    execution_price = config.cost_model.execution_price(
        side=side, reference_price=bar.open
    )
    if side is Side.BUY:
        unit_fee = config.cost_model.fee(
            quantity=_ONE,
            execution_price=execution_price,
        )
        affordable = _floor_step(cash / (execution_price + unit_fee), step)
        quantity = min(quantity, affordable)
        quantity = _floor_step(quantity, step)
        if quantity <= _ZERO:
            return cash, None

    reference_notional = quantity * bar.open
    if reference_notional < config.min_trade_notional:
        return cash, None

    fee = config.cost_model.fee(quantity=quantity, execution_price=execution_price)
    if side is Side.BUY:
        cash_after = cash - quantity * execution_price - fee
        if cash_after < _ZERO:
            return cash, None
    else:
        cash_after = cash + quantity * execution_price - fee

    participation = quantity / bar.volume
    fill = CrossSectionalResearchFill(
        fill_id=(
            f"oss2-fill:{ranking_fingerprint[:16]}:{dataset.instrument.symbol}:"
            f"{side.value}:{execution_bar_index}"
        ),
        ranking_fingerprint=ranking_fingerprint,
        symbol=dataset.instrument.symbol,
        side=side,
        quantity=quantity,
        reference_price=bar.open,
        execution_price=execution_price,
        fee=fee,
        signal_bar_index=signal_bar_index,
        execution_bar_index=execution_bar_index,
        occurred_at=bar.started_at,
        volume_participation=participation,
    )
    return cash_after, fill


def _floor_step(quantity: Decimal, step: Decimal) -> Decimal:
    if quantity <= _ZERO:
        return _ZERO
    return (quantity // step) * step


def _mark_equity(
    *,
    universe: AlignedMarketUniverse,
    index: int,
    cash: Decimal,
    positions: dict[str, Decimal],
) -> CrossSectionalEquityPoint:
    gross = _ZERO
    equity = cash
    for dataset in universe.datasets:
        symbol = dataset.instrument.symbol
        value = positions[symbol] * dataset.bars[index].close
        gross += abs(value)
        equity += value
    return CrossSectionalEquityPoint(
        occurred_at=universe.datasets[0].bars[index].ended_at,
        cash=cash,
        positions=tuple((symbol, positions[symbol]) for symbol in universe.symbols),
        equity=equity,
        gross_exposure=gross,
    )


def _period_returns(
    initial_cash: Decimal,
    equity_curve: tuple[CrossSectionalEquityPoint, ...],
) -> tuple[tuple[datetime, Decimal], ...]:
    previous = initial_cash
    result: list[tuple[datetime, Decimal]] = []
    for point in equity_curve:
        value = point.equity / previous - _ONE if previous != _ZERO else _ZERO
        result.append((point.occurred_at, value))
        previous = point.equity
    return tuple(result)


def _portfolio_metrics(
    *,
    initial_cash: Decimal,
    annualization_factor: Decimal,
    equity_curve: tuple[CrossSectionalEquityPoint, ...],
    period_returns: tuple[tuple[datetime, Decimal], ...],
    turnover_notional: Decimal,
    total_fees: Decimal,
    max_volume_participation: Decimal,
    tracking_errors: tuple[Decimal, ...],
    fills: int,
    rebalances: int,
) -> CrossSectionalBacktestMetrics:
    final = float(equity_curve[-1].equity)
    initial = float(initial_cash)
    returns = [float(value) for _, value in period_returns]
    annualization = float(annualization_factor)
    period_vol = stdev(returns) if len(returns) >= 2 else 0.0
    mean_return = fmean(returns) if returns else 0.0
    annualized_vol = period_vol * sqrt(annualization)
    sharpe = mean_return / period_vol * sqrt(annualization) if period_vol > 0 else 0.0
    downside = [min(value, 0.0) for value in returns]
    downside_deviation = (
        sqrt(fmean([value * value for value in downside])) if downside else 0.0
    )
    sortino = (
        mean_return / downside_deviation * sqrt(annualization)
        if downside_deviation > 0
        else 0.0
    )

    peak = initial
    max_drawdown = 0.0
    exposure_ratios: list[float] = []
    for point in equity_curve:
        equity = float(point.equity)
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
        exposure_ratios.append(
            float(point.gross_exposure / point.equity)
            if point.equity > _ZERO
            else 0.0
        )

    tracking = [float(value) for value in tracking_errors]
    return CrossSectionalBacktestMetrics(
        net_return=final / initial - 1.0,
        annualized_volatility=annualized_vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        turnover=float(turnover_notional / initial_cash),
        average_gross_exposure_ratio=(
            fmean(exposure_ratios) if exposure_ratios else 0.0
        ),
        max_gross_exposure_ratio=max(exposure_ratios, default=0.0),
        max_volume_participation=float(max_volume_participation),
        total_fees=float(total_fees),
        average_target_tracking_error=fmean(tracking) if tracking else 0.0,
        max_target_tracking_error=max(tracking, default=0.0),
        fills=fills,
        rebalances=rebalances,
    )


def _hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()
