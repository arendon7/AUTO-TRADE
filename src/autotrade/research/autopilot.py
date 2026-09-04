from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from statistics import fmean
from typing import Mapping

from .backtest import BacktestConfig, BacktestEngine, BacktestMetrics, BacktestResult
from .market import MarketDataset
from .multiple_testing import (
    DeflatedSharpeEvidence,
    PBOEvidence,
    campaign_deflated_sharpe,
    campaign_pbo,
)
from .robustness import (
    CandidateRobustnessEvidence,
    RobustnessEvaluator,
    RobustnessPolicy,
    StressScenario,
    WalkForwardConfig,
    robust_rank_key,
)
from .strategy_catalog import LibraryStrategySpec
from .strategy_space import StrategyProgram
from .tournament import (
    RankingDirection,
    TournamentEvidence,
    TournamentSpec,
    evaluate_strategy_tournament,
)
from .trials import (
    CampaignSpec,
    SQLiteTrialLedger,
    TrialGovernanceError,
    TrialPhase,
    TrialSpec,
)


class DevelopmentAutopilotError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DevelopmentSelectionPolicy:
    """Research-only minimum quality gates applied after every trial is recorded."""

    min_net_return: float = 0.0
    min_sharpe: float = 0.0
    max_drawdown: float = 0.25
    min_profit_factor: float = 1.0
    min_fills: int = 10

    def __post_init__(self) -> None:
        for name, value in (
            ("min_net_return", self.min_net_return),
            ("min_sharpe", self.min_sharpe),
            ("max_drawdown", self.max_drawdown),
            ("min_profit_factor", self.min_profit_factor),
        ):
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.max_drawdown < 0 or self.max_drawdown > 1:
            raise ValueError("max_drawdown must be in [0,1]")
        if self.min_profit_factor < 0:
            raise ValueError("min_profit_factor must be >= 0")
        if self.min_fills < 0:
            raise ValueError("min_fills must be >= 0")

    def accepts(self, metrics: BacktestMetrics) -> bool:
        profit_factor = metrics.profit_factor
        return (
            isfinite(metrics.net_return)
            and metrics.net_return >= self.min_net_return
            and isfinite(metrics.sharpe)
            and metrics.sharpe >= self.min_sharpe
            and isfinite(metrics.max_drawdown)
            and metrics.max_drawdown <= self.max_drawdown
            and (profit_factor == float("inf") or profit_factor >= self.min_profit_factor)
            and metrics.fills >= self.min_fills
        )


@dataclass(frozen=True, slots=True)
class CandidateDevelopmentResult:
    trial_id: str
    strategy_id: str
    strategy_version: str
    eligible: bool
    backtest_result: BacktestResult
    period_returns: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class DevelopmentProgramResult:
    campaign_id: str
    program_hash: str
    dataset_hash: str
    candidates: tuple[CandidateDevelopmentResult, ...]
    tournament: TournamentEvidence
    policy_eligible_trial_ids: tuple[str, ...]
    tournament_selected_trial_id: str
    robustness_evidence: tuple[CandidateRobustnessEvidence, ...]
    robustness_eligible_trial_ids: tuple[str, ...]
    selected_trial_id: str
    pbo_evidence: PBOEvidence | None
    pbo_unavailable_reason: str
    deflated_sharpe_evidence: DeflatedSharpeEvidence | None
    deflated_sharpe_unavailable_reason: str


class DevelopmentResearchAutopilot:
    """Run a frozen DEVELOPMENT program without any HOLDOUT/PAPER/LIVE authority.

    This coordinator accepts only an already-designated DEVELOPMENT dataset. It
    cannot fetch market data, consume HOLDOUT permits, submit orders or interact
    with brokers. Every candidate is preregistered before its backtest result is
    written to the trial ledger. Optional robustness evaluation remains entirely
    inside the DEVELOPMENT dataset and therefore cannot contaminate HOLDOUT.
    """

    def __init__(self, *, ledger: SQLiteTrialLedger) -> None:
        self._ledger = ledger
        self._engine = BacktestEngine()

    def run(
        self,
        *,
        campaign_id: str,
        program: StrategyProgram,
        development_dataset: MarketDataset,
        backtest_config: BacktestConfig,
        selection_policy: DevelopmentSelectionPolicy,
        code_version: str,
        started_at: datetime,
        pbo_partitions: int = 8,
        robustness_policy: RobustnessPolicy | None = None,
        walk_forward_config: WalkForwardConfig | None = None,
        stress_scenarios: tuple[StressScenario, ...] = (),
    ) -> DevelopmentProgramResult:
        if not campaign_id.strip():
            raise ValueError("campaign_id is required")
        if not code_version.strip():
            raise ValueError("code_version is required")
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        if pbo_partitions < 4 or pbo_partitions % 2:
            raise ValueError("pbo_partitions must be an even integer >= 4")
        if (robustness_policy is None) != (walk_forward_config is None):
            raise ValueError(
                "robustness_policy and walk_forward_config must be provided together"
            )
        if robustness_policy is None and stress_scenarios:
            raise ValueError("stress_scenarios require robustness_policy")

        candidates = program.candidates()
        trial_ids = program.expected_trial_ids
        if len(candidates) != len(trial_ids):
            raise DevelopmentAutopilotError("program candidate/trial accounting mismatch")

        campaign = CampaignSpec(
            campaign_id=campaign_id,
            family_id=program.program_id,
            expected_trial_ids=trial_ids,
            code_version=code_version,
            purpose="R7 governed automatic DEVELOPMENT strategy search",
        )
        self._ledger.create_campaign(campaign, now=started_at)

        by_trial: dict[str, LibraryStrategySpec] = {
            program.trial_id_for(candidate): candidate for candidate in candidates
        }
        if tuple(sorted(by_trial)) != trial_ids:
            raise DevelopmentAutopilotError("frozen program trial universe mismatch")

        # Freeze every trial identity before observing any result.
        for index, trial_id in enumerate(trial_ids):
            candidate = by_trial[trial_id]
            self._ledger.preregister(
                TrialSpec(
                    trial_id=trial_id,
                    campaign_id=campaign_id,
                    hypothesis_id=f"{program.program_id}:{candidate.strategy_id}",
                    strategy_id=candidate.strategy_id,
                    strategy_version=candidate.strategy_version,
                    dataset_hash=development_dataset.dataset_hash,
                    split_name="development",
                    phase=TrialPhase.DEVELOPMENT,
                    parameters=candidate.parameters,
                    code_version=code_version,
                ),
                now=started_at + timedelta(microseconds=index + 1),
            )

        completed: list[CandidateDevelopmentResult] = []
        result_time_base = started_at + timedelta(seconds=1)
        for index, trial_id in enumerate(trial_ids):
            candidate = by_trial[trial_id]
            try:
                result = self._engine.run(
                    dataset=development_dataset,
                    strategy=candidate.build(),
                    config=backtest_config,
                )
            except (ArithmeticError, ValueError) as exc:
                self._ledger.record_failed(
                    trial_id=trial_id,
                    failure_code=f"BACKTEST_REJECTED_{type(exc).__name__.upper()}",
                    now=result_time_base + timedelta(microseconds=index),
                )
                continue

            period_returns = _equity_returns(result)
            self._ledger.record_completed(
                trial_id=trial_id,
                metrics=_trial_metrics(result),
                p_value=None,
                now=result_time_base + timedelta(microseconds=index),
            )
            completed.append(
                CandidateDevelopmentResult(
                    trial_id=trial_id,
                    strategy_id=candidate.strategy_id,
                    strategy_version=candidate.strategy_version,
                    eligible=selection_policy.accepts(result.metrics),
                    backtest_result=result,
                    period_returns=period_returns,
                )
            )

        accounting = self._ledger.require_complete_campaign(campaign_id)
        if not accounting.completed_trial_ids:
            raise DevelopmentAutopilotError("no candidate produced a completed backtest")

        tournament = evaluate_strategy_tournament(
            self._ledger,
            TournamentSpec(
                tournament_id=f"{campaign_id}:sharpe",
                campaign_id=campaign_id,
                metric_name="sharpe",
                direction=RankingDirection.MAXIMIZE,
                candidate_trial_ids=trial_ids,
            ),
        )

        eligible = tuple(sorted(item.trial_id for item in completed if item.eligible))
        eligible_set = set(eligible)
        tournament_selected_trial_id = next(
            (
                entry.trial_id
                for entry in tournament.entries
                if entry.eligible and entry.trial_id in eligible_set
            ),
            "",
        )

        robustness_evidence: tuple[CandidateRobustnessEvidence, ...] = ()
        robustness_eligible_trial_ids: tuple[str, ...] = ()
        selected_trial_id = tournament_selected_trial_id
        if robustness_policy is not None and walk_forward_config is not None:
            evaluator = RobustnessEvaluator()
            evidence_items: list[CandidateRobustnessEvidence] = []
            strategy_to_trial: dict[str, str] = {}
            for trial_id in eligible:
                candidate = by_trial[trial_id]
                strategy_to_trial[candidate.strategy_id] = trial_id
                evidence_items.append(
                    evaluator.evaluate(
                        candidate=candidate,
                        development_dataset=development_dataset,
                        base_config=backtest_config,
                        walk_forward_config=walk_forward_config,
                        stress_scenarios=stress_scenarios,
                        policy=robustness_policy,
                    )
                )
            robustness_evidence = tuple(
                sorted(evidence_items, key=lambda item: item.strategy_id)
            )
            robustness_eligible_trial_ids = tuple(
                sorted(
                    strategy_to_trial[item.strategy_id]
                    for item in robustness_evidence
                    if item.passed
                )
            )
            passed_evidence = tuple(
                item for item in robustness_evidence if item.passed
            )
            if passed_evidence:
                robust_winner = sorted(passed_evidence, key=robust_rank_key)[0]
                selected_trial_id = strategy_to_trial[robust_winner.strategy_id]
            else:
                selected_trial_id = ""

        pbo, pbo_reason = self._pbo(
            campaign_id=campaign_id,
            completed=tuple(completed),
            failed_trial_ids=accounting.failed_trial_ids,
            partitions=pbo_partitions,
        )
        dsr, dsr_reason = self._deflated_sharpe(
            campaign_id=campaign_id,
            tournament=tournament,
            completed=tuple(completed),
            failed_trial_ids=accounting.failed_trial_ids,
        )

        return DevelopmentProgramResult(
            campaign_id=campaign_id,
            program_hash=program.canonical_hash,
            dataset_hash=development_dataset.dataset_hash,
            candidates=tuple(sorted(completed, key=lambda item: item.trial_id)),
            tournament=tournament,
            policy_eligible_trial_ids=eligible,
            tournament_selected_trial_id=tournament_selected_trial_id,
            robustness_evidence=robustness_evidence,
            robustness_eligible_trial_ids=robustness_eligible_trial_ids,
            selected_trial_id=selected_trial_id,
            pbo_evidence=pbo,
            pbo_unavailable_reason=pbo_reason,
            deflated_sharpe_evidence=dsr,
            deflated_sharpe_unavailable_reason=dsr_reason,
        )

    def _pbo(
        self,
        *,
        campaign_id: str,
        completed: tuple[CandidateDevelopmentResult, ...],
        failed_trial_ids: tuple[str, ...],
        partitions: int,
    ) -> tuple[PBOEvidence | None, str]:
        if failed_trial_ids:
            return None, "PBO unavailable because at least one frozen trial failed"
        return_series = {item.trial_id: item.period_returns for item in completed}
        try:
            evidence = campaign_pbo(
                self._ledger,
                campaign_id,
                return_series,
                partitions=partitions,
            )
        except (TrialGovernanceError, ValueError) as exc:
            return None, str(exc)
        return evidence, ""

    def _deflated_sharpe(
        self,
        *,
        campaign_id: str,
        tournament: TournamentEvidence,
        completed: tuple[CandidateDevelopmentResult, ...],
        failed_trial_ids: tuple[str, ...],
    ) -> tuple[DeflatedSharpeEvidence | None, str]:
        if failed_trial_ids:
            return None, "Deflated Sharpe unavailable because at least one frozen trial failed"
        if not tournament.winner_trial_id:
            return None, "Deflated Sharpe unavailable because tournament has no winner"
        selected = next(
            (item for item in completed if item.trial_id == tournament.winner_trial_id),
            None,
        )
        if selected is None:
            return None, "Deflated Sharpe winner is missing from completed results"
        moments = _distribution_moments(selected.period_returns)
        if moments is None:
            return None, "Deflated Sharpe unavailable for zero-variance return series"
        skewness, kurtosis = moments
        try:
            evidence = campaign_deflated_sharpe(
                self._ledger,
                campaign_id,
                selected_trial_id=tournament.winner_trial_id,
                sample_size=len(selected.period_returns),
                skewness=skewness,
                kurtosis=kurtosis,
            )
        except (TrialGovernanceError, ValueError) as exc:
            return None, str(exc)
        return evidence, ""


def _trial_metrics(result: BacktestResult) -> Mapping[str, str | int | float]:
    metrics = result.metrics
    raw: dict[str, str | int | float] = {
        "net_return": metrics.net_return,
        "annualized_volatility": metrics.annualized_volatility,
        "sharpe": metrics.sharpe,
        "sortino": metrics.sortino,
        "max_drawdown": metrics.max_drawdown,
        "turnover": metrics.turnover,
        "hit_rate": metrics.hit_rate,
        "profit_factor": metrics.profit_factor,
        "average_gross_exposure": metrics.average_gross_exposure,
        "max_gross_exposure": metrics.max_gross_exposure,
        "max_volume_participation": metrics.max_volume_participation,
        "total_fees": metrics.total_fees,
        "fills": metrics.fills,
        "rejected_signals": metrics.rejected_signals,
        "backtest_result_hash": result.result_hash,
    }
    # The ledger JSON is strict. Preserve non-finite diagnostics as strings rather
    # than serializing non-standard JSON numbers. Ranking metrics remain numeric.
    normalized: dict[str, str | int | float] = {}
    for name, value in raw.items():
        if isinstance(value, float) and not isfinite(value):
            normalized[name] = "Infinity" if value > 0 else "-Infinity"
        else:
            normalized[name] = value
    if not isinstance(normalized["sharpe"], (int, float)):
        raise DevelopmentAutopilotError("Sharpe must be finite for governed ranking")
    return normalized


def _equity_returns(result: BacktestResult) -> tuple[float, ...]:
    equity = result.equity_curve
    if len(equity) < 2:
        raise DevelopmentAutopilotError("backtest requires at least two equity observations")
    values: list[float] = []
    for previous, current in zip(equity, equity[1:], strict=False):
        prior = float(previous.equity)
        latest = float(current.equity)
        if not isfinite(prior) or not isfinite(latest) or prior <= 0:
            raise DevelopmentAutopilotError("equity curve contains invalid values")
        period_return = latest / prior - 1.0
        if not isfinite(period_return) or period_return <= -1.0:
            raise DevelopmentAutopilotError("equity return is invalid")
        values.append(period_return)
    return tuple(values)


def _distribution_moments(values: tuple[float, ...]) -> tuple[float, float] | None:
    if len(values) < 3 or any(not isfinite(value) for value in values):
        return None
    mean = fmean(values)
    centered = tuple(value - mean for value in values)
    second = fmean(value * value for value in centered)
    if second <= 0 or not isfinite(second):
        return None
    third = fmean(value**3 for value in centered)
    fourth = fmean(value**4 for value in centered)
    skewness = third / (second**1.5)
    kurtosis = fourth / (second**2)
    if not isfinite(skewness) or not isfinite(kurtosis) or kurtosis < 1:
        return None
    return skewness, kurtosis


__all__ = [
    "CandidateDevelopmentResult",
    "DevelopmentAutopilotError",
    "DevelopmentProgramResult",
    "DevelopmentResearchAutopilot",
    "DevelopmentSelectionPolicy",
]
