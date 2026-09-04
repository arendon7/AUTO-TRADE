"""OSS-2 finite DEVELOPMENT campaign and common-window evaluation.

This module freezes a small cross-sectional momentum tournament before results
exist and evaluates every candidate over the same observable time window.
It owns no broker, OMS, network, credential, PAPER execution or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from math import isfinite, sqrt
from statistics import fmean, stdev

from .costs import ExecutionCostModel
from .cross_sectional import CrossSectionalMomentumConfig
from .cross_sectional_backtest import (
    CrossSectionalBacktestConfig,
    CrossSectionalBacktestResult,
)
from .tournament import RankingDirection, TournamentSpec
from .trials import CampaignSpec, TrialPhase, TrialSpec
from .universe import AlignedMarketUniverse


_ZERO = Decimal("0")
_ONE = Decimal("1")
_OSS2_HYPOTHESIS_ID = "oss2:cross-sectional-momentum"
_OSS2_METRIC = "common_window_sharpe"


@dataclass(frozen=True, slots=True)
class OSS2CampaignPlan:
    campaign: CampaignSpec
    trials: tuple[TrialSpec, ...]
    tournament: TournamentSpec
    common_window_start_bar_index: int

    def __post_init__(self) -> None:
        trial_ids = tuple(trial.trial_id for trial in self.trials)
        if trial_ids != tuple(sorted(trial_ids)):
            raise ValueError("OSS-2 campaign trials must be in canonical sorted order")
        if self.campaign.expected_trial_ids != trial_ids:
            raise ValueError("campaign expected trial universe mismatch")
        if self.tournament.candidate_trial_ids != trial_ids:
            raise ValueError("tournament candidate universe mismatch")
        if self.tournament.metric_name != _OSS2_METRIC:
            raise ValueError("OSS-2 tournament must rank common_window_sharpe")
        if any(trial.phase is not TrialPhase.DEVELOPMENT for trial in self.trials):
            raise ValueError("OSS-2 campaign may contain DEVELOPMENT trials only")
        if self.common_window_start_bar_index < 3:
            raise ValueError("common_window_start_bar_index must be >= 3")
        for trial in self.trials:
            if trial.parameters.get("common_window_start_bar_index") != self.common_window_start_bar_index:
                raise ValueError("trial common-window binding mismatch")


@dataclass(frozen=True, slots=True)
class CommonWindowMetricsEvidence:
    source_result_hash: str
    universe_hash: str
    start_bar_index: int
    start_at: datetime
    end_at: datetime
    observation_count: int
    net_return: float
    annualized_volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float

    def __post_init__(self) -> None:
        if len(self.source_result_hash) != 64 or len(self.universe_hash) != 64:
            raise ValueError("result and universe hashes must be SHA-256 hex")
        if self.start_bar_index < 0:
            raise ValueError("start_bar_index must be >= 0")
        if self.start_at.tzinfo is None or self.start_at.utcoffset() is None:
            raise ValueError("start_at must be timezone-aware")
        if self.end_at.tzinfo is None or self.end_at.utcoffset() is None:
            raise ValueError("end_at must be timezone-aware")
        if self.end_at < self.start_at:
            raise ValueError("end_at cannot precede start_at")
        if self.observation_count < 2:
            raise ValueError("common window requires at least 2 observations")
        for name, value in (
            ("net_return", self.net_return),
            ("annualized_volatility", self.annualized_volatility),
            ("sharpe", self.sharpe),
            ("sortino", self.sortino),
            ("max_drawdown", self.max_drawdown),
        ):
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.annualized_volatility < 0 or not 0 <= self.max_drawdown <= 1:
            raise ValueError("invalid common-window risk metric")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "source_result_hash": self.source_result_hash,
                "universe_hash": self.universe_hash,
                "start_bar_index": self.start_bar_index,
                "start_at": self.start_at.isoformat(),
                "end_at": self.end_at.isoformat(),
                "observation_count": self.observation_count,
                "net_return": self.net_return,
                "annualized_volatility": self.annualized_volatility,
                "sharpe": self.sharpe,
                "sortino": self.sortino,
                "max_drawdown": self.max_drawdown,
            }
        )

    def ledger_metrics(self) -> dict[str, str | int | float]:
        return {
            _OSS2_METRIC: self.sharpe,
            "common_window_sortino": self.sortino,
            "common_window_net_return": self.net_return,
            "common_window_max_drawdown": self.max_drawdown,
            "common_window_annualized_volatility": self.annualized_volatility,
            "common_window_observations": self.observation_count,
            "common_window_start_at": self.start_at.isoformat(),
            "common_window_end_at": self.end_at.isoformat(),
            "backtest_result_hash": self.source_result_hash,
            "common_window_evidence_hash": self.fingerprint,
        }


# Four momentum horizons x three rebalance frequencies. The finite grid is
# deliberately frozen and is not an adaptive optimizer search space.
_OSS2_CANDIDATES: tuple[tuple[str, int, int], ...] = tuple(
    (f"mom_lb{lookback:03d}_reb{rebalance:02d}", lookback, rebalance)
    for lookback in (12, 24, 48, 96)
    for rebalance in (1, 4, 12)
)

_EXPECTED_TRIAL_PARAMETER_KEYS = {
    "ranking_lookback_bars",
    "ranking_top_n",
    "ranking_min_average_dollar_volume",
    "ranking_max_weight_per_asset",
    "ranking_require_positive_momentum",
    "ranking_fingerprint",
    "initial_cash",
    "annualization_factor",
    "gross_target",
    "max_volume_participation",
    "min_trade_notional",
    "rebalance_every_bars",
    "fee_bps",
    "half_spread_bps",
    "slippage_bps",
    "allow_zero_total_costs",
    "backtest_config_hash",
    "common_window_start_bar_index",
}


def build_oss2_development_campaign(
    *,
    universe_hash: str,
    code_version: str,
    initial_cash: Decimal,
    annualization_factor: Decimal,
    cost_model: ExecutionCostModel,
    top_n: int,
    min_average_dollar_volume: Decimal,
    max_weight_per_asset: Decimal,
    gross_target: Decimal,
    max_volume_participation: Decimal,
    min_trade_notional: Decimal,
    require_positive_momentum: bool = True,
    split_name: str = "development",
) -> OSS2CampaignPlan:
    """Freeze the exact OSS-2 DEVELOPMENT universe before observing results."""
    if not universe_hash.strip():
        raise ValueError("universe_hash is required")
    if not code_version.strip():
        raise ValueError("code_version is required")
    if not split_name.strip() or "holdout" in split_name.strip().lower():
        raise ValueError("OSS-2 DEVELOPMENT split cannot be HOLDOUT")
    if not isinstance(cost_model, ExecutionCostModel):
        raise TypeError("cost_model must be ExecutionCostModel")

    max_lookback = max(lookback for _, lookback, _ in _OSS2_CANDIDATES)
    common_window_start_bar_index = max_lookback + 1

    shared_payload = {
        "universe_hash": universe_hash,
        "code_version": code_version,
        "initial_cash": str(initial_cash),
        "annualization_factor": str(annualization_factor),
        "cost_model": cost_model.fingerprint_payload(),
        "top_n": top_n,
        "min_average_dollar_volume": str(min_average_dollar_volume),
        "max_weight_per_asset": str(max_weight_per_asset),
        "gross_target": str(gross_target),
        "max_volume_participation": str(max_volume_participation),
        "min_trade_notional": str(min_trade_notional),
        "require_positive_momentum": require_positive_momentum,
        "common_window_start_bar_index": common_window_start_bar_index,
    }
    assumptions_hash = _hash(shared_payload)
    campaign_id = f"oss2-development-{universe_hash[:12]}-{assumptions_hash[:12]}"

    trials: list[TrialSpec] = []
    for label, lookback, rebalance in _OSS2_CANDIDATES:
        ranking = CrossSectionalMomentumConfig(
            lookback_bars=lookback,
            top_n=top_n,
            min_average_dollar_volume=min_average_dollar_volume,
            max_weight_per_asset=max_weight_per_asset,
            require_positive_momentum=require_positive_momentum,
        )
        config = CrossSectionalBacktestConfig(
            initial_cash=initial_cash,
            ranking=ranking,
            cost_model=cost_model,
            rebalance_every_bars=rebalance,
            annualization_factor=annualization_factor,
            gross_target=gross_target,
            max_volume_participation=max_volume_participation,
            min_trade_notional=min_trade_notional,
        )
        parameters: dict[str, str | int | float | bool] = {
            "ranking_lookback_bars": lookback,
            "ranking_top_n": top_n,
            "ranking_min_average_dollar_volume": str(min_average_dollar_volume),
            "ranking_max_weight_per_asset": str(max_weight_per_asset),
            "ranking_require_positive_momentum": require_positive_momentum,
            "ranking_fingerprint": ranking.fingerprint,
            "initial_cash": str(initial_cash),
            "annualization_factor": str(annualization_factor),
            "gross_target": str(gross_target),
            "max_volume_participation": str(max_volume_participation),
            "min_trade_notional": str(min_trade_notional),
            "rebalance_every_bars": rebalance,
            "fee_bps": str(cost_model.fee_bps),
            "half_spread_bps": str(cost_model.half_spread_bps),
            "slippage_bps": str(cost_model.slippage_bps),
            "allow_zero_total_costs": cost_model.allow_zero_total_costs,
            "backtest_config_hash": config.config_hash,
            "common_window_start_bar_index": common_window_start_bar_index,
        }
        trial_id = f"{campaign_id}-{label}"
        trials.append(
            TrialSpec(
                trial_id=trial_id,
                campaign_id=campaign_id,
                hypothesis_id=_OSS2_HYPOTHESIS_ID,
                strategy_id=f"oss2-{label}",
                strategy_version="1.0.0",
                dataset_hash=universe_hash,
                split_name=split_name,
                phase=TrialPhase.DEVELOPMENT,
                parameters=parameters,
                code_version=code_version,
            )
        )

    ordered = tuple(sorted(trials, key=lambda trial: trial.trial_id))
    trial_ids = tuple(trial.trial_id for trial in ordered)
    campaign = CampaignSpec(
        campaign_id=campaign_id,
        family_id="oss2-cross-sectional-momentum",
        expected_trial_ids=trial_ids,
        code_version=code_version,
        purpose=(
            "Finite preregistered comparison of cross-sectional momentum horizons "
            "and rebalance frequencies on one common DEVELOPMENT evaluation window"
        ),
    )
    tournament = TournamentSpec(
        tournament_id=f"{campaign_id}-common-window-sharpe-tournament",
        campaign_id=campaign_id,
        metric_name=_OSS2_METRIC,
        direction=RankingDirection.MAXIMIZE,
        candidate_trial_ids=trial_ids,
    )
    return OSS2CampaignPlan(
        campaign=campaign,
        trials=ordered,
        tournament=tournament,
        common_window_start_bar_index=common_window_start_bar_index,
    )


def backtest_config_from_oss2_trial(trial: TrialSpec) -> CrossSectionalBacktestConfig:
    """Reconstruct the exact research backtest configuration bound to a trial."""
    if trial.phase is not TrialPhase.DEVELOPMENT:
        raise ValueError("OSS-2 trial must be DEVELOPMENT")
    if trial.hypothesis_id != _OSS2_HYPOTHESIS_ID:
        raise ValueError("trial is not an OSS-2 cross-sectional momentum trial")
    params = dict(trial.parameters)
    if set(params) != _EXPECTED_TRIAL_PARAMETER_KEYS:
        raise ValueError("OSS-2 trial parameter contract mismatch")

    ranking = CrossSectionalMomentumConfig(
        lookback_bars=_int_param(params, "ranking_lookback_bars"),
        top_n=_int_param(params, "ranking_top_n"),
        min_average_dollar_volume=_decimal_param(params, "ranking_min_average_dollar_volume"),
        max_weight_per_asset=_decimal_param(params, "ranking_max_weight_per_asset"),
        require_positive_momentum=_bool_param(params, "ranking_require_positive_momentum"),
    )
    if ranking.fingerprint != params["ranking_fingerprint"]:
        raise ValueError("ranking fingerprint mismatch")

    cost_model = ExecutionCostModel(
        fee_bps=_decimal_param(params, "fee_bps"),
        half_spread_bps=_decimal_param(params, "half_spread_bps"),
        slippage_bps=_decimal_param(params, "slippage_bps"),
        allow_zero_total_costs=_bool_param(params, "allow_zero_total_costs"),
    )
    config = CrossSectionalBacktestConfig(
        initial_cash=_decimal_param(params, "initial_cash"),
        ranking=ranking,
        cost_model=cost_model,
        rebalance_every_bars=_int_param(params, "rebalance_every_bars"),
        annualization_factor=_decimal_param(params, "annualization_factor"),
        gross_target=_decimal_param(params, "gross_target"),
        max_volume_participation=_decimal_param(params, "max_volume_participation"),
        min_trade_notional=_decimal_param(params, "min_trade_notional"),
    )
    if config.config_hash != params["backtest_config_hash"]:
        raise ValueError("backtest config hash mismatch")
    return config


def evaluate_oss2_common_window(
    *,
    result: CrossSectionalBacktestResult,
    universe: AlignedMarketUniverse,
    trial: TrialSpec,
) -> CommonWindowMetricsEvidence:
    """Recompute risk/return metrics only on the frozen common time window."""
    config = backtest_config_from_oss2_trial(trial)
    if result.universe_hash != universe.universe_hash or trial.dataset_hash != universe.universe_hash:
        raise ValueError("universe identity mismatch")
    if result.config_hash != config.config_hash:
        raise ValueError("backtest result config does not match trial")

    start_bar_index = _int_param(dict(trial.parameters), "common_window_start_bar_index")
    if start_bar_index >= universe.bar_count:
        raise ValueError("common window starts outside universe")
    start_at = universe.datasets[0].bars[start_bar_index].ended_at
    selected = tuple(
        (occurred_at, value)
        for occurred_at, value in result.period_returns
        if occurred_at >= start_at
    )
    if len(selected) < 2:
        raise ValueError("insufficient observations in common evaluation window")
    if selected[0][0] != start_at:
        raise ValueError("backtest does not cover exact common-window start")

    returns = [float(value) for _, value in selected]
    annualization = float(config.annualization_factor)
    period_vol = stdev(returns) if len(returns) >= 2 else 0.0
    mean_return = fmean(returns)
    annualized_volatility = period_vol * sqrt(annualization)
    sharpe = mean_return / period_vol * sqrt(annualization) if period_vol > 0 else 0.0
    downside = [min(value, 0.0) for value in returns]
    downside_deviation = sqrt(fmean([value * value for value in downside]))
    sortino = (
        mean_return / downside_deviation * sqrt(annualization)
        if downside_deviation > 0
        else 0.0
    )

    cumulative = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        cumulative *= 1.0 + value
        peak = max(peak, cumulative)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - cumulative) / peak)
    net_return = cumulative - 1.0

    return CommonWindowMetricsEvidence(
        source_result_hash=result.result_hash,
        universe_hash=universe.universe_hash,
        start_bar_index=start_bar_index,
        start_at=start_at,
        end_at=selected[-1][0],
        observation_count=len(selected),
        net_return=net_return,
        annualized_volatility=annualized_volatility,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
    )


def oss2_candidate_count() -> int:
    return len(_OSS2_CANDIDATES)


def _decimal_param(params: dict[str, object], name: str) -> Decimal:
    try:
        value = Decimal(str(params[name]))
    except Exception as exc:  # pragma: no cover - defensive contract boundary
        raise ValueError(f"invalid Decimal parameter: {name}") from exc
    if not value.is_finite():
        raise ValueError(f"invalid Decimal parameter: {name}")
    return value


def _int_param(params: dict[str, object], name: str) -> int:
    value = params[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"invalid integer parameter: {name}")
    return value


def _bool_param(params: dict[str, object], name: str) -> bool:
    value = params[name]
    if not isinstance(value, bool):
        raise ValueError(f"invalid bool parameter: {name}")
    return value


def _hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()
