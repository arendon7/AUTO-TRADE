import pytest

from autotrade.research.oss_campaign import (
    build_oss1_development_campaign,
    oss1_candidate_count,
)
from autotrade.research.tournament import RankingDirection
from autotrade.research.trials import TrialPhase


def test_oss1_campaign_freezes_complete_development_universe():
    plan = build_oss1_development_campaign(
        dataset_hash="d" * 64,
        code_version="c" * 40,
    )

    assert oss1_candidate_count() == 18
    assert len(plan.trials) == 18
    assert plan.campaign.expected_trial_ids == tuple(
        trial.trial_id for trial in plan.trials
    )
    assert plan.tournament.candidate_trial_ids == plan.campaign.expected_trial_ids
    assert plan.tournament.metric_name == "sharpe"
    assert plan.tournament.direction is RankingDirection.MAXIMIZE
    assert all(trial.phase is TrialPhase.DEVELOPMENT for trial in plan.trials)
    assert all(not trial.holdout_authorization_id for trial in plan.trials)
    assert all(trial.dataset_hash == "d" * 64 for trial in plan.trials)
    assert all(trial.code_version == "c" * 40 for trial in plan.trials)


def test_oss1_campaign_contains_diverse_strategy_families_and_exact_spec_hashes():
    plan = build_oss1_development_campaign(
        dataset_hash="e" * 64,
        code_version="code-v1",
    )
    kinds = {trial.parameters["dsl_kind"] for trial in plan.trials}

    assert kinds == {
        "moving_average_cross",
        "trend_ema_atr",
        "time_series_momentum",
        "mean_reversion_zscore",
        "donchian_breakout",
        "volatility_regime",
    }
    assert all(len(str(trial.parameters["strategy_spec_hash"])) == 64 for trial in plan.trials)
    assert len({trial.parameters["strategy_spec_hash"] for trial in plan.trials}) == 18


def test_oss1_campaign_identity_is_deterministic():
    first = build_oss1_development_campaign(
        dataset_hash="a" * 64,
        code_version="version-a",
    )
    second = build_oss1_development_campaign(
        dataset_hash="a" * 64,
        code_version="version-a",
    )

    assert first == second
    assert first.campaign.fingerprint == second.campaign.fingerprint
    assert first.tournament.fingerprint == second.tournament.fingerprint
    assert tuple(trial.fingerprint for trial in first.trials) == tuple(
        trial.fingerprint for trial in second.trials
    )


def test_oss1_campaign_rejects_holdout_and_missing_identity():
    with pytest.raises(ValueError, match="dataset_hash"):
        build_oss1_development_campaign(dataset_hash="", code_version="v1")
    with pytest.raises(ValueError, match="code_version"):
        build_oss1_development_campaign(dataset_hash="d", code_version="")
    with pytest.raises(ValueError, match="cannot be HOLDOUT"):
        build_oss1_development_campaign(
            dataset_hash="d",
            code_version="v1",
            split_name="final_holdout",
        )


def test_oss1_campaign_does_not_smuggle_authority_fields():
    plan = build_oss1_development_campaign(
        dataset_hash="f" * 64,
        code_version="v-safe",
    )
    forbidden = {
        "broker",
        "credentials",
        "oms",
        "order_intent",
        "paper_execution_authorized",
        "live_authority",
        "capital_authority",
    }

    for trial in plan.trials:
        assert forbidden.isdisjoint(set(trial.parameters))
