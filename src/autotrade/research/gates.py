from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median

from .backtest import BacktestResult


@dataclass(frozen=True, slots=True)
class RobustnessPolicy:
    min_folds: int
    min_total_fills: int
    min_positive_fold_fraction: float
    min_median_net_return: float
    min_worst_fold_return: float
    max_worst_drawdown: float

    def __post_init__(self) -> None:
        if self.min_folds <= 0:
            raise ValueError("min_folds must be > 0")
        if self.min_total_fills < 0:
            raise ValueError("min_total_fills must be >= 0")
        if not 0 <= self.min_positive_fold_fraction <= 1:
            raise ValueError("min_positive_fold_fraction must be between 0 and 1")
        for name, value in (
            ("min_median_net_return", self.min_median_net_return),
            ("min_worst_fold_return", self.min_worst_fold_return),
            ("max_worst_drawdown", self.max_worst_drawdown),
        ):
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0 <= self.max_worst_drawdown <= 1:
            raise ValueError("max_worst_drawdown must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class RobustnessDecision:
    passed: bool
    reason_codes: tuple[str, ...]
    folds: int
    total_fills: int
    positive_fold_fraction: float
    median_net_return: float
    worst_fold_return: float
    worst_drawdown: float


def evaluate_walk_forward_robustness(
    results: tuple[BacktestResult, ...],
    *,
    policy: RobustnessPolicy,
) -> RobustnessDecision:
    if not results:
        raise ValueError("walk-forward results cannot be empty")
    dataset_hashes = [result.dataset_hash for result in results]
    if len(dataset_hashes) != len(set(dataset_hashes)):
        raise ValueError("walk-forward results must use distinct evaluation datasets")

    returns = [result.metrics.net_return for result in results]
    drawdowns = [result.metrics.max_drawdown for result in results]
    for value in (*returns, *drawdowns):
        if not isfinite(value):
            raise ValueError("walk-forward metrics must be finite")

    folds = len(results)
    total_fills = sum(result.metrics.fills for result in results)
    positive_fraction = sum(value > 0 for value in returns) / folds
    median_return = median(returns)
    worst_return = min(returns)
    worst_drawdown = max(drawdowns)

    reasons: list[str] = []
    if folds < policy.min_folds:
        reasons.append("INSUFFICIENT_FOLDS")
    if total_fills < policy.min_total_fills:
        reasons.append("INSUFFICIENT_FILLS")
    if positive_fraction < policy.min_positive_fold_fraction:
        reasons.append("LOW_POSITIVE_FOLD_FRACTION")
    if median_return < policy.min_median_net_return:
        reasons.append("LOW_MEDIAN_NET_RETURN")
    if worst_return < policy.min_worst_fold_return:
        reasons.append("WORST_FOLD_RETURN")
    if worst_drawdown > policy.max_worst_drawdown:
        reasons.append("MAX_DRAWDOWN")

    return RobustnessDecision(
        passed=not reasons,
        reason_codes=tuple(reasons),
        folds=folds,
        total_fills=total_fills,
        positive_fold_fraction=positive_fraction,
        median_net_return=median_return,
        worst_fold_return=worst_return,
        worst_drawdown=worst_drawdown,
    )
