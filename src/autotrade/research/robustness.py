from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from math import isfinite
from statistics import median
from typing import Protocol

from .backtest import BacktestConfig, BacktestEngine, BacktestMetrics
from .costs import ExecutionCostModel
from .market import MarketDataset
from .splits import generate_walk_forward_folds
from .strategy import ResearchSignal, ResearchStrategy, StrategyContext
from .strategy_catalog import LibraryStrategySpec


class RobustnessError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    train_bars: int
    evaluation_bars: int
    step_bars: int | None = None
    expanding: bool = False
    min_folds: int = 3

    def __post_init__(self) -> None:
        if self.train_bars <= 0:
            raise ValueError("train_bars must be > 0")
        if self.evaluation_bars <= 1:
            raise ValueError("evaluation_bars must be > 1")
        if self.step_bars is not None and self.step_bars <= 0:
            raise ValueError("step_bars must be > 0 when provided")
        if self.min_folds <= 0:
            raise ValueError("min_folds must be > 0")


@dataclass(frozen=True, slots=True)
class StressScenario:
    scenario_id: str
    fee_multiplier: Decimal = Decimal("1")
    spread_multiplier: Decimal = Decimal("1")
    slippage_multiplier: Decimal = Decimal("1")
    execution_delay_bars: int = 1
    volume_participation_multiplier: Decimal = Decimal("1")
    leverage_multiplier: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ValueError("scenario_id is required")
        for name, value in (
            ("fee_multiplier", self.fee_multiplier),
            ("spread_multiplier", self.spread_multiplier),
            ("slippage_multiplier", self.slippage_multiplier),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 1:
                raise ValueError(f"{name} must be finite and >= 1")
        for name, value in (
            ("volume_participation_multiplier", self.volume_participation_multiplier),
            ("leverage_multiplier", self.leverage_multiplier),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0 or value > 1:
                raise ValueError(f"{name} must be finite and in (0,1]")
        if self.execution_delay_bars < 1:
            raise ValueError("execution_delay_bars must be >= 1")

    @property
    def fingerprint(self) -> str:
        return _hash_payload(
            {
                "scenario_id": self.scenario_id,
                "fee_multiplier": str(self.fee_multiplier),
                "spread_multiplier": str(self.spread_multiplier),
                "slippage_multiplier": str(self.slippage_multiplier),
                "execution_delay_bars": self.execution_delay_bars,
                "volume_participation_multiplier": str(
                    self.volume_participation_multiplier
                ),
                "leverage_multiplier": str(self.leverage_multiplier),
            }
        )

    def apply(self, base: BacktestConfig) -> BacktestConfig:
        stressed_costs = ExecutionCostModel(
            fee_bps=base.cost_model.fee_bps * self.fee_multiplier,
            half_spread_bps=(
                base.cost_model.half_spread_bps * self.spread_multiplier
            ),
            slippage_bps=base.cost_model.slippage_bps * self.slippage_multiplier,
            allow_zero_total_costs=base.cost_model.allow_zero_total_costs,
        )
        return BacktestConfig(
            initial_cash=base.initial_cash,
            cost_model=stressed_costs,
            execution_delay_bars=max(
                base.execution_delay_bars, self.execution_delay_bars
            ),
            annualization_factor=base.annualization_factor,
            max_leverage=base.max_leverage * self.leverage_multiplier,
            max_volume_participation=(
                base.max_volume_participation
                * self.volume_participation_multiplier
            ),
            allow_short=base.allow_short,
        )


@dataclass(frozen=True, slots=True)
class RobustnessPolicy:
    min_positive_fold_ratio: float = 0.50
    min_median_fold_sharpe: float = 0.0
    min_worst_fold_net_return: float = -0.10
    max_worst_fold_drawdown: float = 0.30
    min_stress_pass_ratio: float = 1.0
    min_worst_stress_net_return: float = -0.05
    max_worst_stress_drawdown: float = 0.35

    def __post_init__(self) -> None:
        for name, value in (
            ("min_positive_fold_ratio", self.min_positive_fold_ratio),
            ("min_stress_pass_ratio", self.min_stress_pass_ratio),
        ):
            if not isfinite(value) or value < 0 or value > 1:
                raise ValueError(f"{name} must be finite and in [0,1]")
        for name, value in (
            ("min_median_fold_sharpe", self.min_median_fold_sharpe),
            ("min_worst_fold_net_return", self.min_worst_fold_net_return),
            ("min_worst_stress_net_return", self.min_worst_stress_net_return),
        ):
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")
        for name, value in (
            ("max_worst_fold_drawdown", self.max_worst_fold_drawdown),
            ("max_worst_stress_drawdown", self.max_worst_stress_drawdown),
        ):
            if not isfinite(value) or value < 0 or value > 1:
                raise ValueError(f"{name} must be finite and in [0,1]")


@dataclass(frozen=True, slots=True)
class FoldEvidence:
    fold_index: int
    dataset_hash: str
    net_return: float | None
    sharpe: float | None
    max_drawdown: float | None
    fills: int
    result_hash: str
    failure_code: str

    @property
    def completed(self) -> bool:
        return not self.failure_code


@dataclass(frozen=True, slots=True)
class StressEvidence:
    scenario_id: str
    scenario_fingerprint: str
    config_hash: str
    net_return: float | None
    sharpe: float | None
    max_drawdown: float | None
    fills: int
    result_hash: str
    failure_code: str
    passed: bool


@dataclass(frozen=True, slots=True)
class CandidateRobustnessEvidence:
    strategy_id: str
    strategy_version: str
    walk_forward: tuple[FoldEvidence, ...]
    stress: tuple[StressEvidence, ...]
    positive_fold_ratio: float
    median_fold_sharpe: float | None
    worst_fold_net_return: float | None
    worst_fold_drawdown: float | None
    stress_pass_ratio: float
    worst_stress_net_return: float | None
    worst_stress_drawdown: float | None
    passed: bool

    @property
    def fingerprint(self) -> str:
        return _hash_payload(
            {
                "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version,
                "walk_forward": [
                    {
                        "fold_index": item.fold_index,
                        "dataset_hash": item.dataset_hash,
                        "net_return": item.net_return,
                        "sharpe": item.sharpe,
                        "max_drawdown": item.max_drawdown,
                        "fills": item.fills,
                        "result_hash": item.result_hash,
                        "failure_code": item.failure_code,
                    }
                    for item in self.walk_forward
                ],
                "stress": [
                    {
                        "scenario_id": item.scenario_id,
                        "scenario_fingerprint": item.scenario_fingerprint,
                        "config_hash": item.config_hash,
                        "net_return": item.net_return,
                        "sharpe": item.sharpe,
                        "max_drawdown": item.max_drawdown,
                        "fills": item.fills,
                        "result_hash": item.result_hash,
                        "failure_code": item.failure_code,
                        "passed": item.passed,
                    }
                    for item in self.stress
                ],
                "positive_fold_ratio": self.positive_fold_ratio,
                "median_fold_sharpe": self.median_fold_sharpe,
                "worst_fold_net_return": self.worst_fold_net_return,
                "worst_fold_drawdown": self.worst_fold_drawdown,
                "stress_pass_ratio": self.stress_pass_ratio,
                "worst_stress_net_return": self.worst_stress_net_return,
                "worst_stress_drawdown": self.worst_stress_drawdown,
                "passed": self.passed,
            }
        )


class RobustnessEvaluator:
    """Read-only DEVELOPMENT robustness evaluator.

    It replays a frozen candidate across chronological evaluation folds and
    explicitly adverse execution-cost/liquidity scenarios. It has no HOLDOUT,
    network, broker, OMS, PAPER or LIVE authority.
    """

    def __init__(self) -> None:
        self._engine = BacktestEngine()

    def evaluate(
        self,
        *,
        candidate: LibraryStrategySpec,
        development_dataset: MarketDataset,
        base_config: BacktestConfig,
        walk_forward_config: WalkForwardConfig,
        stress_scenarios: tuple[StressScenario, ...],
        policy: RobustnessPolicy,
    ) -> CandidateRobustnessEvidence:
        if len({item.scenario_id for item in stress_scenarios}) != len(stress_scenarios):
            raise ValueError("stress scenario ids must be unique")

        folds = generate_walk_forward_folds(
            development_dataset,
            train_bars=walk_forward_config.train_bars,
            evaluation_bars=walk_forward_config.evaluation_bars,
            step_bars=walk_forward_config.step_bars,
            expanding=walk_forward_config.expanding,
        )
        fold_evidence = tuple(
            self._evaluate_fold(candidate=candidate, fold=fold, base_config=base_config)
            for fold in folds
        )
        stress_evidence = tuple(
            self._evaluate_stress(
                candidate=candidate,
                dataset=development_dataset,
                base_config=base_config,
                scenario=scenario,
                policy=policy,
            )
            for scenario in sorted(stress_scenarios, key=lambda item: item.scenario_id)
        )

        completed_folds = tuple(item for item in fold_evidence if item.completed)
        positive_fold_ratio = (
            sum(
                1
                for item in fold_evidence
                if item.completed
                and item.net_return is not None
                and item.net_return > 0
            )
            / len(fold_evidence)
            if fold_evidence
            else 0.0
        )
        fold_sharpes = tuple(
            item.sharpe
            for item in completed_folds
            if item.sharpe is not None and isfinite(item.sharpe)
        )
        fold_returns = tuple(
            item.net_return
            for item in completed_folds
            if item.net_return is not None and isfinite(item.net_return)
        )
        fold_drawdowns = tuple(
            item.max_drawdown
            for item in completed_folds
            if item.max_drawdown is not None and isfinite(item.max_drawdown)
        )
        stress_returns = tuple(
            item.net_return
            for item in stress_evidence
            if item.net_return is not None and isfinite(item.net_return)
        )
        stress_drawdowns = tuple(
            item.max_drawdown
            for item in stress_evidence
            if item.max_drawdown is not None and isfinite(item.max_drawdown)
        )
        stress_pass_ratio = (
            sum(1 for item in stress_evidence if item.passed) / len(stress_evidence)
            if stress_evidence
            else 1.0
        )

        median_fold_sharpe = median(fold_sharpes) if fold_sharpes else None
        worst_fold_return = min(fold_returns) if fold_returns else None
        worst_fold_drawdown = max(fold_drawdowns) if fold_drawdowns else None
        worst_stress_return = min(stress_returns) if stress_returns else None
        worst_stress_drawdown = max(stress_drawdowns) if stress_drawdowns else None

        enough_folds = len(fold_evidence) >= walk_forward_config.min_folds
        all_folds_completed = len(completed_folds) == len(fold_evidence)
        fold_pass = (
            enough_folds
            and all_folds_completed
            and median_fold_sharpe is not None
            and worst_fold_return is not None
            and worst_fold_drawdown is not None
            and positive_fold_ratio >= policy.min_positive_fold_ratio
            and median_fold_sharpe >= policy.min_median_fold_sharpe
            and worst_fold_return >= policy.min_worst_fold_net_return
            and worst_fold_drawdown <= policy.max_worst_fold_drawdown
        )
        stress_pass = (
            stress_pass_ratio >= policy.min_stress_pass_ratio
            and (
                not stress_evidence
                or (
                    len(stress_returns) == len(stress_evidence)
                    and len(stress_drawdowns) == len(stress_evidence)
                    and worst_stress_return is not None
                    and worst_stress_drawdown is not None
                    and worst_stress_return >= policy.min_worst_stress_net_return
                    and worst_stress_drawdown <= policy.max_worst_stress_drawdown
                )
            )
        )

        return CandidateRobustnessEvidence(
            strategy_id=candidate.strategy_id,
            strategy_version=candidate.strategy_version,
            walk_forward=fold_evidence,
            stress=stress_evidence,
            positive_fold_ratio=positive_fold_ratio,
            median_fold_sharpe=median_fold_sharpe,
            worst_fold_net_return=worst_fold_return,
            worst_fold_drawdown=worst_fold_drawdown,
            stress_pass_ratio=stress_pass_ratio,
            worst_stress_net_return=worst_stress_return,
            worst_stress_drawdown=worst_stress_drawdown,
            passed=fold_pass and stress_pass,
        )

    def _evaluate_fold(
        self,
        *,
        candidate: LibraryStrategySpec,
        fold,
        base_config: BacktestConfig,
    ) -> FoldEvidence:
        warmup = _required_warmup(candidate)
        tail = fold.train.bars[-min(warmup, len(fold.train.bars)) :]
        combined = MarketDataset(
            instrument=fold.evaluation.instrument,
            bars=tuple(tail) + fold.evaluation.bars,
            source=(
                f"{fold.evaluation.source}#warmup={len(tail)}#wf={fold.fold_index}"
            ),
        )
        strategy = _WarmupStrategy(candidate.build(), warmup_bars=len(tail))
        try:
            result = self._engine.run(
                dataset=combined,
                strategy=strategy,
                config=base_config,
            )
        except (ArithmeticError, ValueError) as exc:
            return FoldEvidence(
                fold_index=fold.fold_index,
                dataset_hash=combined.dataset_hash,
                net_return=None,
                sharpe=None,
                max_drawdown=None,
                fills=0,
                result_hash="",
                failure_code=f"BACKTEST_{type(exc).__name__.upper()}",
            )
        metrics = result.metrics
        return FoldEvidence(
            fold_index=fold.fold_index,
            dataset_hash=combined.dataset_hash,
            net_return=metrics.net_return,
            sharpe=metrics.sharpe,
            max_drawdown=metrics.max_drawdown,
            fills=metrics.fills,
            result_hash=result.result_hash,
            failure_code="",
        )

    def _evaluate_stress(
        self,
        *,
        candidate: LibraryStrategySpec,
        dataset: MarketDataset,
        base_config: BacktestConfig,
        scenario: StressScenario,
        policy: RobustnessPolicy,
    ) -> StressEvidence:
        config = scenario.apply(base_config)
        try:
            result = self._engine.run(
                dataset=dataset,
                strategy=candidate.build(),
                config=config,
            )
        except (ArithmeticError, ValueError) as exc:
            return StressEvidence(
                scenario_id=scenario.scenario_id,
                scenario_fingerprint=scenario.fingerprint,
                config_hash=config.config_hash,
                net_return=None,
                sharpe=None,
                max_drawdown=None,
                fills=0,
                result_hash="",
                failure_code=f"BACKTEST_{type(exc).__name__.upper()}",
                passed=False,
            )
        metrics = result.metrics
        passed = (
            isfinite(metrics.net_return)
            and metrics.net_return >= policy.min_worst_stress_net_return
            and isfinite(metrics.max_drawdown)
            and metrics.max_drawdown <= policy.max_worst_stress_drawdown
        )
        return StressEvidence(
            scenario_id=scenario.scenario_id,
            scenario_fingerprint=scenario.fingerprint,
            config_hash=config.config_hash,
            net_return=metrics.net_return,
            sharpe=metrics.sharpe,
            max_drawdown=metrics.max_drawdown,
            fills=metrics.fills,
            result_hash=result.result_hash,
            failure_code="",
            passed=passed,
        )


def robust_rank_key(evidence: CandidateRobustnessEvidence) -> tuple[object, ...]:
    """Deterministic descending-quality key; use with sorted(..., key=...)."""

    median_sharpe = (
        evidence.median_fold_sharpe
        if evidence.median_fold_sharpe is not None
        else -1e100
    )
    worst_fold_return = (
        evidence.worst_fold_net_return
        if evidence.worst_fold_net_return is not None
        else -1e100
    )
    worst_stress_return = (
        evidence.worst_stress_net_return
        if evidence.worst_stress_net_return is not None
        else -1e100
    )
    return (
        0 if evidence.passed else 1,
        -median_sharpe,
        -worst_fold_return,
        -worst_stress_return,
        evidence.strategy_id,
    )


def default_stress_scenarios() -> tuple[StressScenario, ...]:
    return (
        StressScenario(
            scenario_id="costs-2x",
            fee_multiplier=Decimal("2"),
            spread_multiplier=Decimal("2"),
            slippage_multiplier=Decimal("2"),
        ),
        StressScenario(
            scenario_id="latency-liquidity",
            execution_delay_bars=2,
            volume_participation_multiplier=Decimal("0.5"),
            leverage_multiplier=Decimal("0.75"),
        ),
        StressScenario(
            scenario_id="severe-friction",
            fee_multiplier=Decimal("3"),
            spread_multiplier=Decimal("3"),
            slippage_multiplier=Decimal("4"),
            execution_delay_bars=2,
            volume_participation_multiplier=Decimal("0.5"),
            leverage_multiplier=Decimal("0.5"),
        ),
    )


class _WarmupStrategy:
    def __init__(self, strategy: ResearchStrategy, *, warmup_bars: int) -> None:
        if warmup_bars < 0:
            raise ValueError("warmup_bars must be >= 0")
        self._strategy = strategy
        self._warmup_bars = warmup_bars
        self.strategy_id = strategy.strategy_id
        self.strategy_version = strategy.strategy_version
        self.parameters = dict(strategy.parameters)

    def on_bar(self, context: StrategyContext) -> ResearchSignal | None:
        if context.index < self._warmup_bars:
            return None
        return self._strategy.on_bar(context)


def _required_warmup(candidate: LibraryStrategySpec) -> int:
    windows: list[int] = []
    for name in (
        "lookback_bars",
        "momentum_lookback_bars",
        "volatility_window_bars",
    ):
        raw = candidate.parameters.get(name)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int) and raw > 0:
            windows.append(raw)
        elif isinstance(raw, str) and raw.isdigit() and int(raw) > 0:
            windows.append(int(raw))
    return max(windows, default=1)


def _hash_payload(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "CandidateRobustnessEvidence",
    "FoldEvidence",
    "RobustnessError",
    "RobustnessEvaluator",
    "RobustnessPolicy",
    "StressEvidence",
    "StressScenario",
    "WalkForwardConfig",
    "default_stress_scenarios",
    "robust_rank_key",
]
