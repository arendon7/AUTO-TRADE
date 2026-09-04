from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from math import isfinite, sqrt
from statistics import fmean, stdev

from .backtest import BacktestConfig, BacktestEngine
from .market import MarketDataset
from .portfolio_dependence import CalibrationPhase
from .regimes import (
    RegimeCalibrationSeries,
    RegimeCalibrationSpec,
    RegimeEvaluationPhase,
    RegimeEvaluationSeries,
    RegimeFeatureObservation,
    RegimeState,
    calibrate_regime_model,
    evaluate_regime_model,
)
from .strategy_catalog import LibraryStrategySpec


@dataclass(frozen=True, slots=True)
class RegimeStabilityConfig:
    volatility_window_bars: int = 24
    low_quantile: Decimal = Decimal("0.33")
    high_quantile: Decimal = Decimal("0.67")
    min_calibration_observations: int = 30

    def __post_init__(self) -> None:
        if self.volatility_window_bars < 2:
            raise ValueError("volatility_window_bars must be >= 2")
        if not isinstance(self.low_quantile, Decimal) or not self.low_quantile.is_finite():
            raise ValueError("low_quantile must be finite Decimal")
        if not isinstance(self.high_quantile, Decimal) or not self.high_quantile.is_finite():
            raise ValueError("high_quantile must be finite Decimal")
        if not Decimal("0") < self.low_quantile < self.high_quantile < Decimal("1"):
            raise ValueError("regime quantiles must satisfy 0 < low < high < 1")
        if self.min_calibration_observations < 3:
            raise ValueError("min_calibration_observations must be >= 3")


@dataclass(frozen=True, slots=True)
class RegimeStabilityPolicy:
    min_observed_states: int = 2
    min_observations_per_state: int = 10
    min_worst_state_compounded_return: float = -0.10
    min_worst_state_sharpe: float = -1.0

    def __post_init__(self) -> None:
        if self.min_observed_states < 1 or self.min_observed_states > 3:
            raise ValueError("min_observed_states must be in [1,3]")
        if self.min_observations_per_state < 1:
            raise ValueError("min_observations_per_state must be >= 1")
        for name, value in (
            ("min_worst_state_compounded_return", self.min_worst_state_compounded_return),
            ("min_worst_state_sharpe", self.min_worst_state_sharpe),
        ):
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")


@dataclass(frozen=True, slots=True)
class RegimeBucketEvidence:
    state: RegimeState
    observations: int
    mean_period_return: float
    compounded_return: float
    annualized_sharpe: float
    positive_return_ratio: float


@dataclass(frozen=True, slots=True)
class RegimeStabilityEvidence:
    strategy_id: str
    strategy_version: str
    model_fingerprint: str
    evaluation_fingerprint: str
    low_threshold: Decimal
    high_threshold: Decimal
    volatility_window_bars: int
    buckets: tuple[RegimeBucketEvidence, ...]
    observed_states: tuple[RegimeState, ...]
    worst_state_compounded_return: float | None
    worst_state_sharpe: float | None
    passed: bool
    reasons: tuple[str, ...]


class RegimeStabilityEvaluator:
    """Diagnostic DEVELOPMENT regime analysis calibrated only from TRAIN data.

    The regime feature is trailing close-to-close realized volatility. Thresholds
    are frozen from TRAIN. DEVELOPMENT labels are causal and each label at bar i
    is paired only with the strategy equity return from bar i to bar i+1. This
    module has no HOLDOUT, broker, PAPER or LIVE authority.
    """

    def __init__(self) -> None:
        self._engine = BacktestEngine()

    def evaluate(
        self,
        *,
        candidate: LibraryStrategySpec,
        train_dataset: MarketDataset,
        development_dataset: MarketDataset,
        backtest_config: BacktestConfig,
        config: RegimeStabilityConfig,
        policy: RegimeStabilityPolicy,
    ) -> RegimeStabilityEvidence:
        if train_dataset.instrument != development_dataset.instrument:
            raise ValueError("TRAIN and DEVELOPMENT instruments must match")
        if train_dataset.ended_at > development_dataset.started_at:
            raise ValueError("TRAIN cannot overlap DEVELOPMENT")

        train_observations = _volatility_observations(
            train_dataset,
            window=config.volatility_window_bars,
        )
        if len(train_observations) < config.min_calibration_observations:
            raise ValueError("TRAIN has insufficient regime calibration observations")

        model = calibrate_regime_model(
            model_id=(
                f"r7-realized-vol:{development_dataset.instrument.symbol}:"
                f"{config.volatility_window_bars}"
            ),
            version=1,
            series=RegimeCalibrationSeries(
                feature_name="trailing-realized-volatility",
                phase=CalibrationPhase.TRAIN,
                source_hash=train_dataset.dataset_hash,
                observations=train_observations,
            ),
            spec=RegimeCalibrationSpec(
                low_quantile=config.low_quantile,
                high_quantile=config.high_quantile,
                min_observations=config.min_calibration_observations,
            ),
            now=train_dataset.bars[-1].ended_at,
        )

        development_observations = _volatility_observations(
            development_dataset,
            window=config.volatility_window_bars,
        )
        evaluation_series = RegimeEvaluationSeries(
            feature_name="trailing-realized-volatility",
            phase=RegimeEvaluationPhase.DEVELOPMENT,
            source_hash=development_dataset.dataset_hash,
            observations=development_observations,
        )
        timeframe = development_dataset.bars[0].timeframe_seconds
        classification = evaluate_regime_model(
            model,
            evaluation_series,
            max_age=timedelta(seconds=max(1, timeframe * 2)),
        )

        backtest = self._engine.run(
            dataset=development_dataset,
            strategy=candidate.build(),
            config=backtest_config,
        )
        equity = backtest.equity_curve
        if len(equity) != len(development_dataset.bars):
            raise RuntimeError("backtest equity/bar accounting mismatch")

        first_feature_index = config.volatility_window_bars
        state_returns: dict[RegimeState, list[float]] = {
            RegimeState.LOW: [],
            RegimeState.NORMAL: [],
            RegimeState.HIGH: [],
        }
        for offset, item in enumerate(classification.classifications):
            bar_index = first_feature_index + offset
            next_index = bar_index + 1
            if next_index >= len(equity):
                break
            if item.state not in state_returns:
                continue
            previous = float(equity[bar_index].equity)
            current = float(equity[next_index].equity)
            if not isfinite(previous) or not isfinite(current) or previous <= 0:
                raise RuntimeError("regime equity return inputs are invalid")
            period_return = current / previous - 1.0
            if not isfinite(period_return) or period_return <= -1.0:
                raise RuntimeError("regime equity return is invalid")
            state_returns[item.state].append(period_return)

        buckets = tuple(
            _bucket(
                state=state,
                returns=tuple(state_returns[state]),
                annualization_factor=float(backtest_config.annualization_factor),
            )
            for state in (RegimeState.LOW, RegimeState.NORMAL, RegimeState.HIGH)
            if state_returns[state]
        )
        observed_states = tuple(item.state for item in buckets)
        worst_return = min((item.compounded_return for item in buckets), default=None)
        worst_sharpe = min((item.annualized_sharpe for item in buckets), default=None)

        reasons: list[str] = []
        if len(observed_states) < policy.min_observed_states:
            reasons.append("INSUFFICIENT_OBSERVED_REGIMES")
        if any(item.observations < policy.min_observations_per_state for item in buckets):
            reasons.append("INSUFFICIENT_REGIME_OBSERVATIONS")
        if worst_return is None:
            reasons.append("NO_REGIME_RETURNS")
        elif worst_return < policy.min_worst_state_compounded_return:
            reasons.append("WORST_REGIME_RETURN_BELOW_MINIMUM")
        if worst_sharpe is None:
            reasons.append("NO_REGIME_SHARPE")
        elif worst_sharpe < policy.min_worst_state_sharpe:
            reasons.append("WORST_REGIME_SHARPE_BELOW_MINIMUM")

        return RegimeStabilityEvidence(
            strategy_id=candidate.strategy_id,
            strategy_version=candidate.strategy_version,
            model_fingerprint=model.fingerprint,
            evaluation_fingerprint=classification.fingerprint,
            low_threshold=model.low_threshold,
            high_threshold=model.high_threshold,
            volatility_window_bars=config.volatility_window_bars,
            buckets=buckets,
            observed_states=observed_states,
            worst_state_compounded_return=worst_return,
            worst_state_sharpe=worst_sharpe,
            passed=not reasons,
            reasons=tuple(reasons),
        )


def _volatility_observations(
    dataset: MarketDataset,
    *,
    window: int,
) -> tuple[RegimeFeatureObservation, ...]:
    if window < 2:
        raise ValueError("window must be >= 2")
    if len(dataset.bars) <= window:
        raise ValueError("dataset is too short for volatility window")
    closes = tuple(float(bar.close) for bar in dataset.bars)
    simple_returns = tuple(
        closes[index] / closes[index - 1] - 1.0
        for index in range(1, len(closes))
    )
    observations: list[RegimeFeatureObservation] = []
    for bar_index in range(window, len(dataset.bars)):
        values = simple_returns[bar_index - window : bar_index]
        volatility = stdev(values)
        if not isfinite(volatility) or volatility < 0:
            raise ValueError("realized volatility is invalid")
        occurred_at = dataset.bars[bar_index].ended_at
        observations.append(
            RegimeFeatureObservation(
                occurred_at=occurred_at,
                available_at=occurred_at,
                value=Decimal(str(volatility)),
            )
        )
    return tuple(observations)


def _bucket(
    *,
    state: RegimeState,
    returns: tuple[float, ...],
    annualization_factor: float,
) -> RegimeBucketEvidence:
    if not returns:
        raise ValueError("bucket returns cannot be empty")
    compounded = 1.0
    for value in returns:
        compounded *= 1.0 + value
    mean_return = fmean(returns)
    sample_stdev = stdev(returns) if len(returns) >= 2 else 0.0
    sharpe = (
        mean_return / sample_stdev * sqrt(annualization_factor)
        if sample_stdev > 0
        else 0.0
    )
    return RegimeBucketEvidence(
        state=state,
        observations=len(returns),
        mean_period_return=mean_return,
        compounded_return=compounded - 1.0,
        annualized_sharpe=sharpe,
        positive_return_ratio=sum(1 for value in returns if value > 0) / len(returns),
    )


__all__ = [
    "RegimeBucketEvidence",
    "RegimeStabilityConfig",
    "RegimeStabilityEvidence",
    "RegimeStabilityEvaluator",
    "RegimeStabilityPolicy",
]
