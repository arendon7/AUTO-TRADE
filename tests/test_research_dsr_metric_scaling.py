from datetime import timedelta
from decimal import Decimal
from math import sqrt

import pytest

from autotrade.research.multiple_testing import campaign_deflated_sharpe
from autotrade.research.trials import (
    CampaignSpec,
    SQLiteTrialLedger,
    TrialGovernanceError,
    TrialPhase,
    TrialSpec,
)


def build_ledger(tmp_path, now, *, factors=("365", "365", "365")):
    ledger = SQLiteTrialLedger(tmp_path / "dsr-scale.sqlite")
    ids = ("a", "b", "c")
    ledger.create_campaign(
        CampaignSpec("campaign", "family", ids, "code", "dsr-scale"),
        now=now,
    )
    sharpes = (0.8, 1.2, 1.9)
    for index, (trial_id, factor, sharpe) in enumerate(zip(ids, factors, sharpes)):
        parameters = {}
        if factor is not None:
            parameters["annualization_factor"] = factor
        ledger.preregister(
            TrialSpec(
                trial_id=trial_id,
                campaign_id="campaign",
                hypothesis_id=f"h-{trial_id}",
                strategy_id=f"strategy-{trial_id}",
                strategy_version="1",
                dataset_hash="d" * 64,
                split_name="development",
                phase=TrialPhase.DEVELOPMENT,
                parameters=parameters,
                code_version="code",
            ),
            now=now + timedelta(seconds=index + 1),
        )
        ledger.record_completed(
            trial_id=trial_id,
            metrics={"common_window_sharpe": sharpe, "sharpe": sharpe},
            p_value=Decimal("0.1"),
            now=now + timedelta(seconds=index + 10),
        )
    return ledger


def test_common_window_dsr_is_scaled_to_observation_frequency(tmp_path, now):
    ledger = build_ledger(tmp_path, now)
    evidence = campaign_deflated_sharpe(
        ledger,
        "campaign",
        selected_trial_id="c",
        sample_size=64,
        skewness=0.1,
        kurtosis=3.2,
        metric_name="common_window_sharpe",
    )

    expected_scale = 1.0 / sqrt(365.0)
    assert evidence.metric_name == "common_window_sharpe"
    assert evidence.metric_scale == pytest.approx(expected_scale)
    assert evidence.selected_sharpe == pytest.approx(1.9 * expected_scale)
    assert 0 <= evidence.deflated_sharpe_probability <= 1


def test_legacy_sharpe_remains_unscaled(tmp_path, now):
    ledger = build_ledger(tmp_path, now)
    evidence = campaign_deflated_sharpe(
        ledger,
        "campaign",
        selected_trial_id="c",
        sample_size=64,
        skewness=0.1,
        kurtosis=3.2,
    )
    assert evidence.metric_name == "sharpe"
    assert evidence.metric_scale == 1.0
    assert evidence.selected_sharpe == pytest.approx(1.9)


def test_explicit_metric_scale_is_bound_and_validated(tmp_path, now):
    ledger = build_ledger(tmp_path, now)
    evidence = campaign_deflated_sharpe(
        ledger,
        "campaign",
        selected_trial_id="c",
        sample_size=64,
        skewness=0.1,
        kurtosis=3.2,
        metric_name="common_window_sharpe",
        metric_scale=0.25,
    )
    assert evidence.metric_scale == 0.25
    assert evidence.selected_sharpe == pytest.approx(0.475)

    with pytest.raises(ValueError, match="metric_scale"):
        campaign_deflated_sharpe(
            ledger,
            "campaign",
            selected_trial_id="c",
            sample_size=64,
            skewness=0.1,
            kurtosis=3.2,
            metric_scale=0.0,
        )


def test_common_window_dsr_rejects_mixed_annualization(tmp_path, now):
    ledger = build_ledger(tmp_path, now, factors=("365", "252", "365"))
    with pytest.raises(TrialGovernanceError, match="one frozen annualization_factor"):
        campaign_deflated_sharpe(
            ledger,
            "campaign",
            selected_trial_id="c",
            sample_size=64,
            skewness=0.1,
            kurtosis=3.2,
            metric_name="common_window_sharpe",
        )


@pytest.mark.parametrize("bad_factor", [None, "0", "-1", "nan", "not-a-number"])
def test_common_window_dsr_rejects_missing_or_invalid_annualization(
    tmp_path, now, bad_factor
):
    ledger = build_ledger(tmp_path, now, factors=("365", bad_factor, "365"))
    with pytest.raises(TrialGovernanceError, match="annualization_factor"):
        campaign_deflated_sharpe(
            ledger,
            "campaign",
            selected_trial_id="c",
            sample_size=64,
            skewness=0.1,
            kurtosis=3.2,
            metric_name="common_window_sharpe",
        )
