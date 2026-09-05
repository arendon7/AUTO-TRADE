"""OSS-2D DEVELOPMENT-only robustness evidence for the certified OSS-2 campaign.

This module consumes only the frozen OSS-2 DEVELOPMENT campaign, its durable
trial ledger and deterministic research backtest results. It adds no strategy
candidates, cannot observe FINAL_HOLDOUT and owns no broker, network, OMS,
capital, OrderIntent, PAPER execution or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
import json
from math import isfinite, sqrt
from statistics import fmean, median
from typing import Mapping

from .bootstrap import MovingBlockBootstrapConfig, moving_block_bootstrap
from .costs import ExecutionCostModel
from .cross_sectional_backtest import (
    CrossSectionalBacktestConfig,
    CrossSectionalBacktestEngine,
    CrossSectionalBacktestResult,
)
from .multiple_testing import (
    DeflatedSharpeEvidence,
    PBOEvidence,
    campaign_deflated_sharpe,
    campaign_pbo,
)
from .oss2_campaign import (
    CommonWindowMetricsEvidence,
    OSS2CampaignPlan,
    backtest_config_from_oss2_trial,
    evaluate_oss2_common_window,
)
from .tournament import TournamentEvidence, evaluate_strategy_tournament
from .trials import SQLiteTrialLedger, TrialPhase
from .universe import AlignedMarketUniverse


_PBO_PARTITIONS = 8
_BOOTSTRAP_ITERATIONS = 2_000
_BOOTSTRAP_BLOCK_SIZE = 4
_BOOTSTRAP_SEED = 20_260_904
_COST_STRESS_MULTIPLIERS = (Decimal("1.5"), Decimal("2.0"))
_BOUND_METRIC = "common_window_sharpe"


class OSS2RobustnessGovernanceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OSS2RobustnessPolicy:
    pbo_partitions: int = _PBO_PARTITIONS
    pbo_balanced_partitions: bool = True
    bootstrap_iterations: int = _BOOTSTRAP_ITERATIONS
    bootstrap_block_size: int = _BOOTSTRAP_BLOCK_SIZE
    bootstrap_seed: int = _BOOTSTRAP_SEED
    cost_stress_multipliers: tuple[Decimal, ...] = _COST_STRESS_MULTIPLIERS

    def __post_init__(self) -> None:
        if self.pbo_partitions < 4 or self.pbo_partitions % 2:
            raise ValueError("OSS-2D PBO partitions must be an even integer >= 4")
        if self.pbo_balanced_partitions is not True:
            raise ValueError("OSS-2D requires balanced PBO partitions")
        if self.bootstrap_iterations <= 0 or self.bootstrap_block_size <= 0:
            raise ValueError("invalid OSS-2D bootstrap policy")
        if isinstance(self.bootstrap_seed, bool) or not isinstance(self.bootstrap_seed, int):
            raise ValueError("OSS-2D bootstrap seed must be int")
        if not self.cost_stress_multipliers:
            raise ValueError("OSS-2D cost stress multipliers cannot be empty")
        if any(
            not isinstance(value, Decimal)
            or not value.is_finite()
            or value <= Decimal("1")
            for value in self.cost_stress_multipliers
        ):
            raise ValueError("OSS-2D cost stress multipliers must be finite Decimal > 1")
        if tuple(sorted(set(self.cost_stress_multipliers))) != self.cost_stress_multipliers:
            raise ValueError("OSS-2D cost stress multipliers must be unique sorted order")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "pbo_partitions": self.pbo_partitions,
                "pbo_balanced_partitions": self.pbo_balanced_partitions,
                "bootstrap_iterations": self.bootstrap_iterations,
                "bootstrap_block_size": self.bootstrap_block_size,
                "bootstrap_seed": self.bootstrap_seed,
                "cost_stress_multipliers": [
                    str(value) for value in self.cost_stress_multipliers
                ],
                "bound_metric": _BOUND_METRIC,
            }
        )


@dataclass(frozen=True, slots=True)
class OSS2BootstrapEvidence:
    observations: int
    iterations: int
    block_size: int
    seed: int
    mean_compounded_return: float
    median_compounded_return: float
    lower_compounded_return: float
    upper_compounded_return: float
    probability_positive: float
    distribution_hash: str

    def __post_init__(self) -> None:
        if self.observations < 2 or self.iterations <= 0 or self.block_size <= 0:
            raise ValueError("invalid OSS-2D bootstrap evidence counts")
        if not 0 <= self.probability_positive <= 1:
            raise ValueError("bootstrap probability_positive must be in [0,1]")
        for value in (
            self.mean_compounded_return,
            self.median_compounded_return,
            self.lower_compounded_return,
            self.upper_compounded_return,
        ):
            if not isfinite(value):
                raise ValueError("bootstrap summary must be finite")
        _sha(self.distribution_hash, "distribution_hash")


@dataclass(frozen=True, slots=True)
class OSS2CostStressEvidence:
    multiplier: Decimal
    total_cost_bps: Decimal
    config_hash: str
    result_hash: str
    common_window_net_return: float
    common_window_sharpe: float
    common_window_max_drawdown: float
    sharpe_delta_vs_baseline: float
    net_return_delta_vs_baseline: float

    def __post_init__(self) -> None:
        if not self.multiplier.is_finite() or self.multiplier <= Decimal("1"):
            raise ValueError("cost stress multiplier must exceed 1")
        if not self.total_cost_bps.is_finite() or self.total_cost_bps <= Decimal("0"):
            raise ValueError("stressed total cost must be positive")
        _sha(self.config_hash, "config_hash")
        _sha(self.result_hash, "result_hash")
        for value in (
            self.common_window_net_return,
            self.common_window_sharpe,
            self.common_window_max_drawdown,
            self.sharpe_delta_vs_baseline,
            self.net_return_delta_vs_baseline,
        ):
            if not isfinite(value):
                raise ValueError("cost stress metrics must be finite")
        if not 0 <= self.common_window_max_drawdown <= 1:
            raise ValueError("cost stress drawdown must be in [0,1]")


@dataclass(frozen=True, slots=True)
class OSS2LocalNeighbor:
    trial_id: str
    lookback_bars: int
    rebalance_every_bars: int
    common_window_sharpe: float

    def __post_init__(self) -> None:
        if not self.trial_id.strip():
            raise ValueError("neighbor trial_id is required")
        if self.lookback_bars <= 0 or self.rebalance_every_bars <= 0:
            raise ValueError("neighbor grid coordinates must be positive")
        if not isfinite(self.common_window_sharpe):
            raise ValueError("neighbor Sharpe must be finite")


@dataclass(frozen=True, slots=True)
class OSS2LocalSensitivityEvidence:
    selected_lookback_bars: int
    selected_rebalance_every_bars: int
    selected_sharpe: float
    neighbors: tuple[OSS2LocalNeighbor, ...]
    neighbor_median_sharpe: float
    selected_minus_neighbor_median: float
    fraction_selected_at_least_neighbor: float

    def __post_init__(self) -> None:
        if self.selected_lookback_bars <= 0 or self.selected_rebalance_every_bars <= 0:
            raise ValueError("selected grid coordinates must be positive")
        if len(self.neighbors) < 2:
            raise ValueError("OSS-2D local sensitivity requires at least two neighbors")
        if tuple(item.trial_id for item in self.neighbors) != tuple(
            sorted(item.trial_id for item in self.neighbors)
        ):
            raise ValueError("neighbors must be canonical sorted order")
        for value in (
            self.selected_sharpe,
            self.neighbor_median_sharpe,
            self.selected_minus_neighbor_median,
            self.fraction_selected_at_least_neighbor,
        ):
            if not isfinite(value):
                raise ValueError("local sensitivity metrics must be finite")
        if not 0 <= self.fraction_selected_at_least_neighbor <= 1:
            raise ValueError("local sensitivity fraction must be in [0,1]")


@dataclass(frozen=True, slots=True)
class OSS2RobustnessEvidence:
    campaign_id: str
    universe_hash: str
    policy_fingerprint: str
    tournament_fingerprint: str
    selected_trial_id: str
    selected_common_window_evidence_hash: str
    result_universe_hash: str
    pbo: PBOEvidence
    deflated_sharpe: DeflatedSharpeEvidence
    bootstrap: OSS2BootstrapEvidence
    cost_stress: tuple[OSS2CostStressEvidence, ...]
    local_sensitivity: OSS2LocalSensitivityEvidence

    def __post_init__(self) -> None:
        for name, value in (
            ("campaign_id", self.campaign_id),
            ("selected_trial_id", self.selected_trial_id),
        ):
            if not value.strip():
                raise ValueError(f"{name} is required")
        for name, value in (
            ("universe_hash", self.universe_hash),
            ("policy_fingerprint", self.policy_fingerprint),
            ("tournament_fingerprint", self.tournament_fingerprint),
            ("selected_common_window_evidence_hash", self.selected_common_window_evidence_hash),
            ("result_universe_hash", self.result_universe_hash),
        ):
            _sha(value, name)
        if self.pbo.campaign_id != self.campaign_id:
            raise ValueError("PBO campaign identity mismatch")
        if self.deflated_sharpe.campaign_id != self.campaign_id:
            raise ValueError("Deflated Sharpe campaign identity mismatch")
        if self.deflated_sharpe.selected_trial_id != self.selected_trial_id:
            raise ValueError("Deflated Sharpe winner identity mismatch")
        if tuple(item.multiplier for item in self.cost_stress) != _COST_STRESS_MULTIPLIERS:
            raise ValueError("cost stress evidence does not match frozen OSS-2D policy")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "campaign_id": self.campaign_id,
                "universe_hash": self.universe_hash,
                "policy_fingerprint": self.policy_fingerprint,
                "tournament_fingerprint": self.tournament_fingerprint,
                "selected_trial_id": self.selected_trial_id,
                "selected_common_window_evidence_hash": self.selected_common_window_evidence_hash,
                "result_universe_hash": self.result_universe_hash,
                "pbo": {
                    "partitions": self.pbo.partitions,
                    "combinations_evaluated": self.pbo.combinations_evaluated,
                    "pbo": self.pbo.pbo,
                    "partition_sizes": list(self.pbo.partition_sizes),
                    "balanced_partitions": self.pbo.balanced_partitions,
                    "logits_hash": _hash(list(self.pbo.logits)),
                },
                "deflated_sharpe": {
                    "selected_sharpe": self.deflated_sharpe.selected_sharpe,
                    "expected_max_sharpe": self.deflated_sharpe.expected_max_sharpe,
                    "probability": self.deflated_sharpe.deflated_sharpe_probability,
                    "family_size": self.deflated_sharpe.family_size,
                    "sample_size": self.deflated_sharpe.sample_size,
                },
                "bootstrap": {
                    "observations": self.bootstrap.observations,
                    "iterations": self.bootstrap.iterations,
                    "block_size": self.bootstrap.block_size,
                    "seed": self.bootstrap.seed,
                    "mean": self.bootstrap.mean_compounded_return,
                    "median": self.bootstrap.median_compounded_return,
                    "lower": self.bootstrap.lower_compounded_return,
                    "upper": self.bootstrap.upper_compounded_return,
                    "probability_positive": self.bootstrap.probability_positive,
                    "distribution_hash": self.bootstrap.distribution_hash,
                },
                "cost_stress": [
                    {
                        "multiplier": str(item.multiplier),
                        "total_cost_bps": str(item.total_cost_bps),
                        "config_hash": item.config_hash,
                        "result_hash": item.result_hash,
                        "net_return": item.common_window_net_return,
                        "sharpe": item.common_window_sharpe,
                        "max_drawdown": item.common_window_max_drawdown,
                        "sharpe_delta": item.sharpe_delta_vs_baseline,
                        "net_return_delta": item.net_return_delta_vs_baseline,
                    }
                    for item in self.cost_stress
                ],
                "local_sensitivity": {
                    "selected_lookback_bars": self.local_sensitivity.selected_lookback_bars,
                    "selected_rebalance_every_bars": self.local_sensitivity.selected_rebalance_every_bars,
                    "selected_sharpe": self.local_sensitivity.selected_sharpe,
                    "neighbor_median_sharpe": self.local_sensitivity.neighbor_median_sharpe,
                    "selected_minus_neighbor_median": self.local_sensitivity.selected_minus_neighbor_median,
                    "fraction_selected_at_least_neighbor": self.local_sensitivity.fraction_selected_at_least_neighbor,
                    "neighbors": [
                        {
                            "trial_id": item.trial_id,
                            "lookback_bars": item.lookback_bars,
                            "rebalance_every_bars": item.rebalance_every_bars,
                            "common_window_sharpe": item.common_window_sharpe,
                        }
                        for item in self.local_sensitivity.neighbors
                    ],
                },
            }
        )


def canonical_oss2d_policy() -> OSS2RobustnessPolicy:
    """Return the fixed OSS-2D policy; callers cannot adapt it to observed results."""
    return OSS2RobustnessPolicy()


def run_oss2d_robustness(
    *,
    ledger: SQLiteTrialLedger,
    plan: OSS2CampaignPlan,
    universe: AlignedMarketUniverse,
    results_by_trial: Mapping[str, CrossSectionalBacktestResult],
) -> OSS2RobustnessEvidence:
    """Build one reproducible DEVELOPMENT robustness package for OSS-2C."""
    policy = canonical_oss2d_policy()
    if plan.campaign.campaign_id != plan.tournament.campaign_id:
        raise OSS2RobustnessGovernanceError("campaign/tournament identity mismatch")
    if plan.tournament.metric_name != _BOUND_METRIC:
        raise OSS2RobustnessGovernanceError("OSS-2D requires common_window_sharpe tournament")
    if any(trial.phase is not TrialPhase.DEVELOPMENT for trial in plan.trials):
        raise OSS2RobustnessGovernanceError("OSS-2D may consume DEVELOPMENT trials only")
    if plan.campaign.expected_trial_ids != tuple(trial.trial_id for trial in plan.trials):
        raise OSS2RobustnessGovernanceError("plan trial universe mismatch")
    if set(results_by_trial) != set(plan.campaign.expected_trial_ids):
        raise OSS2RobustnessGovernanceError(
            "result universe must exactly match frozen OSS-2 campaign"
        )
    if universe.universe_hash != plan.trials[0].dataset_hash:
        raise OSS2RobustnessGovernanceError("universe does not match frozen campaign dataset")

    ledger.require_complete_campaign(plan.campaign.campaign_id)
    records = {
        record.spec.trial_id: record
        for record in ledger.list_trials(plan.campaign.campaign_id)
    }
    if set(records) != set(plan.campaign.expected_trial_ids):
        raise OSS2RobustnessGovernanceError("ledger trial universe mismatch")

    common_evidence: dict[str, CommonWindowMetricsEvidence] = {}
    returns_by_trial: dict[str, tuple[float, ...]] = {}
    for trial in plan.trials:
        result = results_by_trial[trial.trial_id]
        evidence = evaluate_oss2_common_window(
            result=result,
            universe=universe,
            trial=trial,
        )
        record = records[trial.trial_id]
        _verify_ledger_binding(record.metrics, result, evidence)
        common_evidence[trial.trial_id] = evidence
        returns_by_trial[trial.trial_id] = _exact_common_returns(
            result=result,
            universe=universe,
            start_bar_index=plan.common_window_start_bar_index,
        )

    tournament = evaluate_strategy_tournament(ledger, plan.tournament)
    _verify_tournament(tournament, plan)
    selected_trial_id = tournament.winner_trial_id
    if not selected_trial_id:
        raise OSS2RobustnessGovernanceError("OSS-2 tournament produced no eligible winner")
    selected_trial = next(
        trial for trial in plan.trials if trial.trial_id == selected_trial_id
    )
    selected_evidence = common_evidence[selected_trial_id]
    selected_returns = returns_by_trial[selected_trial_id]

    pbo = campaign_pbo(
        ledger,
        plan.campaign.campaign_id,
        returns_by_trial,
        partitions=policy.pbo_partitions,
        balanced_partitions=policy.pbo_balanced_partitions,
    )

    skewness, kurtosis = _pearson_moments(selected_returns)
    deflated = campaign_deflated_sharpe(
        ledger,
        plan.campaign.campaign_id,
        selected_trial_id=selected_trial_id,
        sample_size=len(selected_returns),
        skewness=skewness,
        kurtosis=kurtosis,
        metric_name=_BOUND_METRIC,
    )

    bootstrap_result = moving_block_bootstrap(
        selected_returns,
        config=MovingBlockBootstrapConfig(
            iterations=policy.bootstrap_iterations,
            block_size=policy.bootstrap_block_size,
            seed=policy.bootstrap_seed,
        ),
    )
    bootstrap = OSS2BootstrapEvidence(
        observations=bootstrap_result.observations,
        iterations=bootstrap_result.iterations,
        block_size=bootstrap_result.block_size,
        seed=bootstrap_result.seed,
        mean_compounded_return=bootstrap_result.mean_compounded_return,
        median_compounded_return=bootstrap_result.median_compounded_return,
        lower_compounded_return=bootstrap_result.lower_compounded_return,
        upper_compounded_return=bootstrap_result.upper_compounded_return,
        probability_positive=bootstrap_result.probability_positive,
        distribution_hash=_hash(list(bootstrap_result.distribution)),
    )

    cost_stress = _cost_stress(
        universe=universe,
        selected_trial=selected_trial,
        baseline_evidence=selected_evidence,
        multipliers=policy.cost_stress_multipliers,
        start_bar_index=plan.common_window_start_bar_index,
    )
    local_sensitivity = _local_sensitivity(
        plan=plan,
        records=records,
        selected_trial_id=selected_trial_id,
    )

    return OSS2RobustnessEvidence(
        campaign_id=plan.campaign.campaign_id,
        universe_hash=universe.universe_hash,
        policy_fingerprint=policy.fingerprint,
        tournament_fingerprint=tournament.fingerprint,
        selected_trial_id=selected_trial_id,
        selected_common_window_evidence_hash=selected_evidence.fingerprint,
        result_universe_hash=tournament.result_universe_hash,
        pbo=pbo,
        deflated_sharpe=deflated,
        bootstrap=bootstrap,
        cost_stress=cost_stress,
        local_sensitivity=local_sensitivity,
    )


def _verify_tournament(tournament: TournamentEvidence, plan: OSS2CampaignPlan) -> None:
    if tournament.campaign_id != plan.campaign.campaign_id:
        raise OSS2RobustnessGovernanceError("tournament campaign mismatch")
    if tournament.spec_fingerprint != plan.tournament.fingerprint:
        raise OSS2RobustnessGovernanceError("tournament spec fingerprint mismatch")
    if tournament.metric_name != _BOUND_METRIC:
        raise OSS2RobustnessGovernanceError("tournament metric mismatch")
    if tournament.completed_count != len(plan.trials) or tournament.failed_count:
        raise OSS2RobustnessGovernanceError(
            "OSS-2D requires every frozen DEVELOPMENT trial completed"
        )


def _verify_ledger_binding(
    metrics: Mapping[str, object],
    result: CrossSectionalBacktestResult,
    evidence: CommonWindowMetricsEvidence,
) -> None:
    expected = {
        "backtest_result_hash": result.result_hash,
        "common_window_evidence_hash": evidence.fingerprint,
        "common_window_sharpe": evidence.sharpe,
        "common_window_observations": evidence.observation_count,
        "common_window_start_at": evidence.start_at.isoformat(),
        "common_window_end_at": evidence.end_at.isoformat(),
    }
    for name, value in expected.items():
        if metrics.get(name) != value:
            raise OSS2RobustnessGovernanceError(
                f"ledger/result common-window binding mismatch: {name}"
            )


def _exact_common_returns(
    *,
    result: CrossSectionalBacktestResult,
    universe: AlignedMarketUniverse,
    start_bar_index: int,
) -> tuple[float, ...]:
    expected_timestamps = tuple(
        universe.datasets[0].bars[index].ended_at
        for index in range(start_bar_index, universe.bar_count)
    )
    selected = tuple(
        (occurred_at, float(value))
        for occurred_at, value in result.period_returns
        if occurred_at >= expected_timestamps[0]
    )
    if tuple(item[0] for item in selected) != expected_timestamps:
        raise OSS2RobustnessGovernanceError("result does not cover exact common window")
    values = tuple(item[1] for item in selected)
    if len(values) < 2 or any(not isfinite(value) or value <= -1 for value in values):
        raise OSS2RobustnessGovernanceError("invalid common-window return series")
    return values


def _pearson_moments(values: tuple[float, ...]) -> tuple[float, float]:
    if len(values) < 3:
        raise OSS2RobustnessGovernanceError("moments require at least three observations")
    mean_value = fmean(values)
    centered = tuple(value - mean_value for value in values)
    second = fmean(tuple(value * value for value in centered))
    if second <= 0 or not isfinite(second):
        raise OSS2RobustnessGovernanceError("winner return variance must be positive")
    third = fmean(tuple(value**3 for value in centered))
    fourth = fmean(tuple(value**4 for value in centered))
    skewness = third / (second ** 1.5)
    kurtosis = fourth / (second * second)
    if not isfinite(skewness) or not isfinite(kurtosis) or kurtosis < 1:
        raise OSS2RobustnessGovernanceError("invalid winner return moments")
    return skewness, kurtosis


def _cost_stress(
    *,
    universe: AlignedMarketUniverse,
    selected_trial,
    baseline_evidence: CommonWindowMetricsEvidence,
    multipliers: tuple[Decimal, ...],
    start_bar_index: int,
) -> tuple[OSS2CostStressEvidence, ...]:
    base = backtest_config_from_oss2_trial(selected_trial)
    if base.cost_model.total_bps <= 0:
        raise OSS2RobustnessGovernanceError("OSS-2D cost stress requires positive base costs")
    engine = CrossSectionalBacktestEngine()
    evidence: list[OSS2CostStressEvidence] = []
    for multiplier in multipliers:
        stressed_costs = ExecutionCostModel(
            fee_bps=base.cost_model.fee_bps * multiplier,
            half_spread_bps=base.cost_model.half_spread_bps * multiplier,
            slippage_bps=base.cost_model.slippage_bps * multiplier,
            allow_zero_total_costs=False,
        )
        stressed_config = replace(base, cost_model=stressed_costs)
        result = engine.run(universe=universe, config=stressed_config)
        summary = _summarize_common_window(
            result=result,
            universe=universe,
            config=stressed_config,
            start_bar_index=start_bar_index,
        )
        evidence.append(
            OSS2CostStressEvidence(
                multiplier=multiplier,
                total_cost_bps=stressed_costs.total_bps,
                config_hash=stressed_config.config_hash,
                result_hash=result.result_hash,
                common_window_net_return=summary[0],
                common_window_sharpe=summary[1],
                common_window_max_drawdown=summary[2],
                sharpe_delta_vs_baseline=summary[1] - baseline_evidence.sharpe,
                net_return_delta_vs_baseline=summary[0] - baseline_evidence.net_return,
            )
        )
    return tuple(evidence)


def _summarize_common_window(
    *,
    result: CrossSectionalBacktestResult,
    universe: AlignedMarketUniverse,
    config: CrossSectionalBacktestConfig,
    start_bar_index: int,
) -> tuple[float, float, float]:
    values = _exact_common_returns(
        result=result,
        universe=universe,
        start_bar_index=start_bar_index,
    )
    compounded = 1.0
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in values:
        compounded *= 1.0 + value
        equity *= 1.0 + value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
    mean_value = fmean(values)
    sample_variance = sum((value - mean_value) ** 2 for value in values) / (len(values) - 1)
    period_stdev = sqrt(sample_variance)
    sharpe = (
        mean_value / period_stdev * sqrt(float(config.annualization_factor))
        if period_stdev > 0
        else 0.0
    )
    result_tuple = (compounded - 1.0, sharpe, max_drawdown)
    if any(not isfinite(value) for value in result_tuple):
        raise OSS2RobustnessGovernanceError("non-finite cost stress common-window metric")
    return result_tuple


def _local_sensitivity(
    *,
    plan: OSS2CampaignPlan,
    records: Mapping[str, object],
    selected_trial_id: str,
) -> OSS2LocalSensitivityEvidence:
    selected = next(trial for trial in plan.trials if trial.trial_id == selected_trial_id)
    selected_lookback = _positive_int(selected.parameters["ranking_lookback_bars"], "lookback")
    selected_rebalance = _positive_int(
        selected.parameters["rebalance_every_bars"], "rebalance"
    )
    lookbacks = tuple(
        sorted({_positive_int(trial.parameters["ranking_lookback_bars"], "lookback") for trial in plan.trials})
    )
    rebalances = tuple(
        sorted({_positive_int(trial.parameters["rebalance_every_bars"], "rebalance") for trial in plan.trials})
    )
    selected_sharpe = _metric(records[selected_trial_id].metrics, _BOUND_METRIC)
    neighbors: list[OSS2LocalNeighbor] = []
    for trial in plan.trials:
        if trial.trial_id == selected_trial_id:
            continue
        lookback = _positive_int(trial.parameters["ranking_lookback_bars"], "lookback")
        rebalance = _positive_int(trial.parameters["rebalance_every_bars"], "rebalance")
        same_rebalance_adjacent_lookback = (
            rebalance == selected_rebalance
            and abs(lookbacks.index(lookback) - lookbacks.index(selected_lookback)) == 1
        )
        same_lookback_adjacent_rebalance = (
            lookback == selected_lookback
            and abs(rebalances.index(rebalance) - rebalances.index(selected_rebalance)) == 1
        )
        if same_rebalance_adjacent_lookback or same_lookback_adjacent_rebalance:
            neighbors.append(
                OSS2LocalNeighbor(
                    trial_id=trial.trial_id,
                    lookback_bars=lookback,
                    rebalance_every_bars=rebalance,
                    common_window_sharpe=_metric(records[trial.trial_id].metrics, _BOUND_METRIC),
                )
            )
    ordered = tuple(sorted(neighbors, key=lambda item: item.trial_id))
    if len(ordered) < 2:
        raise OSS2RobustnessGovernanceError("selected grid point has insufficient local neighbors")
    neighbor_values = tuple(item.common_window_sharpe for item in ordered)
    neighbor_median = float(median(neighbor_values))
    fraction = sum(selected_sharpe >= value for value in neighbor_values) / len(neighbor_values)
    return OSS2LocalSensitivityEvidence(
        selected_lookback_bars=selected_lookback,
        selected_rebalance_every_bars=selected_rebalance,
        selected_sharpe=selected_sharpe,
        neighbors=ordered,
        neighbor_median_sharpe=neighbor_median,
        selected_minus_neighbor_median=selected_sharpe - neighbor_median,
        fraction_selected_at_least_neighbor=fraction,
    )


def _metric(metrics: Mapping[str, object], name: str) -> float:
    value = metrics.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OSS2RobustnessGovernanceError(f"missing numeric ledger metric: {name}")
    result = float(value)
    if not isfinite(result):
        raise OSS2RobustnessGovernanceError(f"non-finite ledger metric: {name}")
    return result


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise OSS2RobustnessGovernanceError(f"invalid {name} grid coordinate")
    return value


def _sha(value: str, name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be SHA-256 hex")


def _hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()
