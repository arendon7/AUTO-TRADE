from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.multiple_testing import (
    campaign_deflated_sharpe,
    campaign_holm_evidence,
    campaign_pbo,
    holm_adjust,
)
from autotrade.research.trials import (
    CampaignSpec,
    SQLiteTrialLedger,
    TrialGovernanceError,
    TrialPhase,
    TrialSpec,
)


def setup_campaign(tmp_path, now, *, ids=("a", "b", "c"), failed=()):
    ledger = SQLiteTrialLedger(tmp_path / "stats.db")
    ledger.create_campaign(
        CampaignSpec("campaign", "family", tuple(ids), "code", "stats"), now=now
    )
    for index, trial_id in enumerate(ids):
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
                parameters={"x": index},
                code_version="code",
            ),
            now=now + timedelta(seconds=index + 1),
        )
        if trial_id in failed:
            ledger.record_failed(
                trial_id=trial_id,
                failure_code="NO_VALID_RESULT",
                now=now + timedelta(seconds=20 + index),
            )
        else:
            ledger.record_completed(
                trial_id=trial_id,
                metrics={"sharpe": 0.5 + index * 0.2},
                p_value=Decimal(str(0.01 + index * 0.02)),
                now=now + timedelta(seconds=20 + index),
            )
    return ledger


def test_holm_adjust_is_monotone_and_bounded():
    adjusted = holm_adjust({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adjusted["a"] == pytest.approx(0.03)
    assert adjusted["c"] == pytest.approx(0.06)
    assert adjusted["b"] == pytest.approx(0.06)
    assert all(0 <= value <= 1 for value in adjusted.values())


def test_campaign_holm_requires_complete_accounting_and_counts_failures(tmp_path, now):
    incomplete = SQLiteTrialLedger(tmp_path / "incomplete.db")
    incomplete.create_campaign(CampaignSpec("c", "f", ("a",), "code", "p"), now=now)
    with pytest.raises(TrialGovernanceError, match="incomplete"):
        campaign_holm_evidence(incomplete, "c")

    ledger = setup_campaign(tmp_path, now, failed=("c",))
    evidence = campaign_holm_evidence(ledger, "campaign")
    assert evidence.family_size == 3
    assert evidence.failed_trial_ids == ("c",)
    assert evidence.raw_p_values["c"] == 1.0
    assert evidence.adjusted_p_values["c"] == 1.0


def test_campaign_holm_rejects_completed_trial_without_pvalue(tmp_path, now):
    ledger = SQLiteTrialLedger(tmp_path / "missing-p.db")
    ledger.create_campaign(CampaignSpec("c", "f", ("a",), "code", "p"), now=now)
    spec = TrialSpec(
        "a",
        "c",
        "h",
        "s",
        "1",
        "d" * 64,
        "development",
        TrialPhase.DEVELOPMENT,
        {},
        "code",
    )
    ledger.preregister(spec, now=now + timedelta(seconds=1))
    ledger.record_completed(
        trial_id="a",
        metrics={"sharpe": 1.0},
        p_value=None,
        now=now + timedelta(seconds=2),
    )
    with pytest.raises(TrialGovernanceError, match="no p_value"):
        campaign_holm_evidence(ledger, "c")


def test_pbo_requires_exact_frozen_universe_and_no_failed_trials(tmp_path, now):
    ledger = setup_campaign(tmp_path, now, ids=("a", "b"))
    with pytest.raises(TrialGovernanceError, match="exactly match"):
        campaign_pbo(ledger, "campaign", {"a": [0.1] * 16}, partitions=4)
    failed = setup_campaign(
        tmp_path / "failed", now, ids=("a", "b"), failed=("b",)
    )
    with pytest.raises(TrialGovernanceError, match="failed trials"):
        campaign_pbo(
            failed,
            "campaign",
            {"a": [0.1] * 16, "b": [0.0] * 16},
            partitions=4,
        )


def test_pbo_stable_strategy_has_low_overfit_probability(tmp_path, now):
    ledger = setup_campaign(tmp_path, now, ids=("a", "b", "c"))
    a = [0.020, 0.018, 0.021, 0.019] * 4
    b = [0.005, -0.002, 0.006, -0.001] * 4
    c = [-0.004, 0.003, -0.005, 0.002] * 4
    evidence = campaign_pbo(
        ledger, "campaign", {"a": a, "b": b, "c": c}, partitions=4
    )
    assert evidence.combinations_evaluated == 6
    assert evidence.pbo == 0.0


def test_pbo_preconditions_fail_closed(tmp_path, now):
    ledger = setup_campaign(tmp_path, now, ids=("a", "b"))
    with pytest.raises(ValueError, match="even"):
        campaign_pbo(
            ledger,
            "campaign",
            {"a": [0.1] * 12, "b": [0.0] * 12},
            partitions=3,
        )
    with pytest.raises(ValueError, match="equal length"):
        campaign_pbo(
            ledger,
            "campaign",
            {"a": [0.1] * 16, "b": [0.0] * 15},
            partitions=4,
        )
    with pytest.raises(ValueError, match="divide evenly"):
        campaign_pbo(
            ledger,
            "campaign",
            {"a": [0.1] * 10, "b": [0.0] * 10},
            partitions=4,
        )


def test_deflated_sharpe_computes_only_with_complete_valid_preconditions(tmp_path, now):
    ledger = setup_campaign(tmp_path, now, ids=("a", "b", "c"))
    evidence = campaign_deflated_sharpe(
        ledger,
        "campaign",
        selected_trial_id="c",
        sample_size=250,
        skewness=0.0,
        kurtosis=3.0,
    )
    assert evidence.family_size == 3
    assert 0 <= evidence.deflated_sharpe_probability <= 1
    assert evidence.selected_sharpe == pytest.approx(0.9)

    with pytest.raises(TrialGovernanceError, match="outside frozen"):
        campaign_deflated_sharpe(
            ledger,
            "campaign",
            selected_trial_id="missing",
            sample_size=250,
            skewness=0,
            kurtosis=3,
        )
    with pytest.raises(ValueError, match="sample_size"):
        campaign_deflated_sharpe(
            ledger,
            "campaign",
            selected_trial_id="c",
            sample_size=2,
            skewness=0,
            kurtosis=3,
        )


def test_deflated_sharpe_rejects_failed_missing_or_zero_variance_family(tmp_path, now):
    failed = setup_campaign(
        tmp_path / "failed", now, ids=("a", "b"), failed=("b",)
    )
    with pytest.raises(TrialGovernanceError, match="every trial"):
        campaign_deflated_sharpe(
            failed,
            "campaign",
            selected_trial_id="a",
            sample_size=100,
            skewness=0,
            kurtosis=3,
        )

    ledger = SQLiteTrialLedger(tmp_path / "zero.db")
    ledger.create_campaign(
        CampaignSpec("c", "f", ("a", "b"), "code", "p"), now=now
    )
    for i, trial_id in enumerate(("a", "b")):
        ledger.preregister(
            TrialSpec(
                trial_id,
                "c",
                "h",
                trial_id,
                "1",
                "d" * 64,
                "development",
                TrialPhase.DEVELOPMENT,
                {},
                "code",
            ),
            now=now + timedelta(seconds=i + 1),
        )
        ledger.record_completed(
            trial_id=trial_id,
            metrics={"sharpe": 1.0},
            p_value=Decimal("0.1"),
            now=now + timedelta(seconds=i + 10),
        )
    with pytest.raises(TrialGovernanceError, match="non-zero"):
        campaign_deflated_sharpe(
            ledger,
            "c",
            selected_trial_id="a",
            sample_size=100,
            skewness=0,
            kurtosis=3,
        )


def test_pbo_counts_all_cscv_orientations(tmp_path, now):
    ledger = setup_campaign(tmp_path, now, ids=("a", "b"))
    a = [0.02, 0.01, 0.03, 0.015] * 4
    b = [0.001, -0.002, 0.003, -0.001] * 4
    evidence = campaign_pbo(
        ledger, "campaign", {"a": a, "b": b}, partitions=4
    )
    # C(4,2)=6; complement-swapped orientations are distinct CSCV splits.
    assert evidence.combinations_evaluated == 6
    assert len(evidence.logits) == 6


def test_pbo_zero_variance_segment_is_not_assigned_infinite_sharpe(tmp_path, now):
    ledger = setup_campaign(tmp_path, now, ids=("a", "b"))
    with pytest.raises(ValueError, match="zero-variance"):
        campaign_pbo(
            ledger,
            "campaign",
            {"a": [0.01] * 16, "b": [0.0, 0.01] * 8},
            partitions=4,
        )


def test_deflated_sharpe_requires_selected_trial_to_be_family_best(tmp_path, now):
    ledger = setup_campaign(tmp_path, now, ids=("a", "b", "c"))
    with pytest.raises(TrialGovernanceError, match="maximum-Sharpe"):
        campaign_deflated_sharpe(
            ledger,
            "campaign",
            selected_trial_id="a",
            sample_size=250,
            skewness=0.0,
            kurtosis=3.0,
        )
