"""Frozen OSS-1 research campaign construction.

This module turns the OSS strategy foundation into a finite preregisterable
DEVELOPMENT universe. It deliberately avoids adaptive hyperparameter search:
all candidate identities and parameter sets are fixed before results exist.

No function in this module owns broker, OMS, Safety writer, credential,
network, PAPER execution or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

from .dsl import StrategySpec
from .tournament import RankingDirection, TournamentSpec
from .trials import CampaignSpec, TrialPhase, TrialSpec


@dataclass(frozen=True, slots=True)
class OSSCampaignPlan:
    campaign: CampaignSpec
    trials: tuple[TrialSpec, ...]
    tournament: TournamentSpec

    def __post_init__(self) -> None:
        trial_ids = tuple(trial.trial_id for trial in self.trials)
        if trial_ids != tuple(sorted(trial_ids)):
            raise ValueError("OSS campaign trials must be in canonical sorted order")
        if self.campaign.expected_trial_ids != trial_ids:
            raise ValueError("campaign expected trial universe mismatch")
        if self.tournament.candidate_trial_ids != trial_ids:
            raise ValueError("tournament candidate universe mismatch")
        if any(trial.phase is not TrialPhase.DEVELOPMENT for trial in self.trials):
            raise ValueError("OSS-1 campaign may contain DEVELOPMENT trials only")


# This is intentionally a small, finite preregistered set. It is not an
# optimizer search space and should not grow casually after observing results.
_OSS1_CANDIDATES: tuple[tuple[str, str, dict[str, object], str], ...] = (
    (
        "baseline_ma_05_20",
        "moving_average_cross",
        {
            "short_window": 5,
            "long_window": 20,
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.03",
    ),
    (
        "baseline_ma_10_30",
        "moving_average_cross",
        {
            "short_window": 10,
            "long_window": 30,
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.03",
    ),
    (
        "baseline_ma_20_50",
        "moving_average_cross",
        {
            "short_window": 20,
            "long_window": 50,
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.04",
    ),
    (
        "trend_ema_atr_08_21",
        "trend_ema_atr",
        {
            "fast_span": 8,
            "slow_span": 21,
            "atr_window": 14,
            "min_atr_pct": "0.001",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.03",
    ),
    (
        "trend_ema_atr_12_36",
        "trend_ema_atr",
        {
            "fast_span": 12,
            "slow_span": 36,
            "atr_window": 14,
            "min_atr_pct": "0.002",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.04",
    ),
    (
        "trend_ema_atr_20_55",
        "trend_ema_atr",
        {
            "fast_span": 20,
            "slow_span": 55,
            "atr_window": 20,
            "min_atr_pct": "0.003",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.05",
    ),
    (
        "ts_momentum_06_24",
        "time_series_momentum",
        {
            "fast_horizon": 6,
            "slow_horizon": 24,
            "threshold": "0.005",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.03",
    ),
    (
        "ts_momentum_12_48",
        "time_series_momentum",
        {
            "fast_horizon": 12,
            "slow_horizon": 48,
            "threshold": "0.01",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.04",
    ),
    (
        "ts_momentum_24_96",
        "time_series_momentum",
        {
            "fast_horizon": 24,
            "slow_horizon": 96,
            "threshold": "0.015",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.05",
    ),
    (
        "mean_reversion_z_20",
        "mean_reversion_zscore",
        {
            "lookback": 20,
            "entry_z": "1.5",
            "exit_z": "0.25",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.02",
    ),
    (
        "mean_reversion_z_48",
        "mean_reversion_zscore",
        {
            "lookback": 48,
            "entry_z": "2.0",
            "exit_z": "0.50",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.03",
    ),
    (
        "mean_reversion_z_96",
        "mean_reversion_zscore",
        {
            "lookback": 96,
            "entry_z": "2.5",
            "exit_z": "0.75",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.04",
    ),
    (
        "donchian_20",
        "donchian_breakout",
        {
            "lookback": 20,
            "atr_window": 14,
            "min_atr_pct": "0.001",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.03",
    ),
    (
        "donchian_55",
        "donchian_breakout",
        {
            "lookback": 55,
            "atr_window": 20,
            "min_atr_pct": "0.002",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.04",
    ),
    (
        "donchian_100",
        "donchian_breakout",
        {
            "lookback": 100,
            "atr_window": 20,
            "min_atr_pct": "0.003",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.05",
    ),
    (
        "vol_regime_06_24",
        "volatility_regime",
        {
            "short_vol_window": 6,
            "long_vol_window": 24,
            "trend_window": 12,
            "vol_ratio_threshold": "1.05",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.03",
    ),
    (
        "vol_regime_12_48",
        "volatility_regime",
        {
            "short_vol_window": 12,
            "long_vol_window": 48,
            "trend_window": 24,
            "vol_ratio_threshold": "1.10",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.04",
    ),
    (
        "vol_regime_24_96",
        "volatility_regime",
        {
            "short_vol_window": 24,
            "long_vol_window": 96,
            "trend_window": 48,
            "vol_ratio_threshold": "1.15",
            "order_quantity": "1",
            "position_mode": "long_flat",
        },
        "0.05",
    ),
)


def build_oss1_development_campaign(
    *,
    dataset_hash: str,
    code_version: str,
    split_name: str = "development",
) -> OSSCampaignPlan:
    """Build the exact finite OSS-1 DEVELOPMENT tournament universe.

    The caller supplies immutable data/code identity. No market result, metric,
    p-value or HOLDOUT observation is accepted as an input to this constructor.
    """
    if not dataset_hash.strip():
        raise ValueError("dataset_hash is required")
    if not code_version.strip():
        raise ValueError("code_version is required")
    if not split_name.strip() or "holdout" in split_name.strip().lower():
        raise ValueError("OSS-1 DEVELOPMENT split cannot be HOLDOUT")

    campaign_id = f"oss1-development-{dataset_hash[:12]}-{code_version[:12]}"
    trials: list[TrialSpec] = []
    for label, kind, parameters, initial_stop_pct in _OSS1_CANDIDATES:
        strategy_id = f"oss1-{label}"
        strategy = StrategySpec.from_json(
            json.dumps(
                {
                    "strategy_id": strategy_id,
                    "strategy_version": "1.0.0",
                    "kind": kind,
                    "parameters": parameters,
                    "initial_stop_pct": initial_stop_pct,
                },
                sort_keys=True,
            )
        )
        flattened = dict(strategy.parameters)
        flattened["dsl_kind"] = kind
        flattened["initial_stop_pct"] = initial_stop_pct
        flattened["strategy_spec_hash"] = strategy.canonical_hash
        trial_id = f"{campaign_id}-{label}"
        trials.append(
            TrialSpec(
                trial_id=trial_id,
                campaign_id=campaign_id,
                hypothesis_id=f"oss1:{kind}",
                strategy_id=strategy.strategy_id,
                strategy_version=strategy.strategy_version,
                dataset_hash=dataset_hash,
                split_name=split_name,
                phase=TrialPhase.DEVELOPMENT,
                parameters=flattened,
                code_version=code_version,
            )
        )

    ordered = tuple(sorted(trials, key=lambda trial: trial.trial_id))
    trial_ids = tuple(trial.trial_id for trial in ordered)
    campaign = CampaignSpec(
        campaign_id=campaign_id,
        family_id="oss1-safe-dsl",
        expected_trial_ids=trial_ids,
        code_version=code_version,
        purpose=(
            "Finite preregistered comparison of safe deterministic OSS-inspired "
            "single-symbol strategy families before any HOLDOUT or PAPER promotion"
        ),
    )
    tournament = TournamentSpec(
        tournament_id=f"{campaign_id}-sharpe-tournament",
        campaign_id=campaign_id,
        metric_name="sharpe",
        direction=RankingDirection.MAXIMIZE,
        candidate_trial_ids=trial_ids,
    )
    return OSSCampaignPlan(
        campaign=campaign,
        trials=ordered,
        tournament=tournament,
    )


def oss1_candidate_count() -> int:
    return len(_OSS1_CANDIDATES)
