from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.trials import (
    CampaignSpec,
    SQLiteTrialLedger,
    TrialConflict,
    TrialGovernanceError,
    TrialPhase,
    TrialSpec,
    TrialStatus,
)


def campaign():
    return CampaignSpec(
        campaign_id="campaign-1",
        family_id="trend-family",
        expected_trial_ids=("trial-1", "trial-2", "trial-3"),
        code_version="abc123",
        purpose="bounded R3 research",
    )


def trial(trial_id, *, phase=TrialPhase.TRAIN, split="train", authorization=""):
    return TrialSpec(
        trial_id=trial_id,
        campaign_id="campaign-1",
        hypothesis_id="hypothesis-1",
        strategy_id="strategy-1",
        strategy_version="1",
        dataset_hash="d" * 64,
        split_name=split,
        phase=phase,
        parameters={"lookback": 20, "enabled": True},
        code_version="abc123",
        holdout_authorization_id=authorization,
    )


def test_campaign_universe_is_frozen_and_idempotent(tmp_path, now):
    ledger = SQLiteTrialLedger(tmp_path / "trials.db")
    spec = campaign()
    assert ledger.create_campaign(spec, now=now) == spec
    assert ledger.create_campaign(spec, now=now + timedelta(seconds=1)) == spec
    with pytest.raises(TrialConflict):
        ledger.create_campaign(
            replace(spec, expected_trial_ids=("trial-1",)),
            now=now + timedelta(seconds=2),
        )


def test_trial_must_belong_to_frozen_campaign_and_be_preregistered_before_result(tmp_path, now):
    ledger = SQLiteTrialLedger(tmp_path / "trials.db")
    ledger.create_campaign(campaign(), now=now)
    with pytest.raises(TrialGovernanceError, match="frozen campaign universe"):
        ledger.preregister(trial("not-expected"), now=now)
    with pytest.raises(TrialGovernanceError, match="before preregistration"):
        ledger.record_completed(
            trial_id="trial-1",
            metrics={"return": 0.1},
            p_value=Decimal("0.05"),
            now=now,
        )


def test_preregistration_is_immutable_and_result_is_terminal_idempotent(tmp_path, now):
    ledger = SQLiteTrialLedger(tmp_path / "trials.db")
    ledger.create_campaign(campaign(), now=now)
    spec = trial("trial-1")
    first = ledger.preregister(spec, now=now + timedelta(seconds=1))
    replay = ledger.preregister(spec, now=now + timedelta(seconds=2))
    assert replay == first
    with pytest.raises(TrialConflict, match="trial identity conflict"):
        ledger.preregister(
            replace(spec, parameters={"lookback": 21}),
            now=now + timedelta(seconds=2),
        )

    completed = ledger.record_completed(
        trial_id="trial-1",
        metrics={"return": 0.1, "trades": 12},
        p_value=Decimal("0.04"),
        now=now + timedelta(seconds=3),
    )
    assert completed.status is TrialStatus.COMPLETED
    assert completed.result_hash
    identical = ledger.record_completed(
        trial_id="trial-1",
        metrics={"return": 0.1, "trades": 12},
        p_value=Decimal("0.04"),
        now=now + timedelta(seconds=4),
    )
    assert identical == completed
    with pytest.raises(TrialConflict, match="terminal trial result conflict"):
        ledger.record_failed(
            trial_id="trial-1",
            failure_code="pretend-failure",
            now=now + timedelta(seconds=4),
        )


def test_failed_trials_are_counted_and_campaign_cannot_hide_missing_trials(tmp_path, now):
    ledger = SQLiteTrialLedger(tmp_path / "trials.db")
    ledger.create_campaign(campaign(), now=now)
    for idx in (1, 2):
        ledger.preregister(trial(f"trial-{idx}"), now=now + timedelta(seconds=idx))
    ledger.record_completed(
        trial_id="trial-1",
        metrics={"return": 0.1},
        p_value=Decimal("0.03"),
        now=now + timedelta(seconds=4),
    )
    ledger.record_failed(
        trial_id="trial-2",
        failure_code="DATA_QUALITY_FAILURE",
        now=now + timedelta(seconds=5),
    )
    accounting = ledger.campaign_accounting("campaign-1")
    assert accounting.failed_trial_ids == ("trial-2",)
    assert accounting.missing_preregistration_ids == ("trial-3",)
    assert accounting.complete is False
    with pytest.raises(TrialGovernanceError, match="incomplete"):
        ledger.require_complete_campaign("campaign-1")

    ledger.preregister(trial("trial-3"), now=now + timedelta(seconds=6))
    assert ledger.campaign_accounting("campaign-1").unterminated_trial_ids == ("trial-3",)
    ledger.record_failed(
        trial_id="trial-3", failure_code="NO_EDGE", now=now + timedelta(seconds=7)
    )
    complete = ledger.require_complete_campaign("campaign-1")
    assert complete.complete is True
    assert complete.failed_trial_ids == ("trial-2", "trial-3")


def test_holdout_phase_requires_explicit_authorization_and_iterative_phases_cannot_use_it():
    with pytest.raises(ValueError, match="authorization"):
        trial("trial-1", phase=TrialPhase.FINAL_HOLDOUT, split="protected_holdout")
    final = trial(
        "trial-1",
        phase=TrialPhase.FINAL_HOLDOUT,
        split="protected_holdout",
        authorization="permit-1",
    )
    assert final.holdout_authorization_id == "permit-1"
    with pytest.raises(ValueError, match="iterative"):
        trial(
            "trial-1", phase=TrialPhase.DEVELOPMENT, split="holdout", authorization="x"
        )


def test_invalid_pvalues_failure_codes_and_time_order_fail_closed(tmp_path, now):
    ledger = SQLiteTrialLedger(tmp_path / "trials.db")
    ledger.create_campaign(campaign(), now=now)
    ledger.preregister(trial("trial-1"), now=now + timedelta(seconds=2))
    for bad in (Decimal("-0.1"), Decimal("1.1"), Decimal("NaN")):
        with pytest.raises(ValueError, match="p_value"):
            ledger.record_completed(
                trial_id="trial-1",
                metrics={},
                p_value=bad,
                now=now + timedelta(seconds=3),
            )
    with pytest.raises(ValueError, match="failure_code"):
        ledger.record_failed(
            trial_id="trial-1", failure_code="", now=now + timedelta(seconds=3)
        )
    with pytest.raises(TrialGovernanceError, match="predate"):
        ledger.record_completed(
            trial_id="trial-1",
            metrics={},
            p_value=None,
            now=now + timedelta(seconds=1),
        )
