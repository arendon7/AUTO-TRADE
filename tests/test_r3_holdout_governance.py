from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.registry import HoldoutPermit, SQLiteExperimentRegistry
from autotrade.research.trials import (
    CampaignSpec,
    SQLiteTrialLedger,
    TrialGovernanceError,
    TrialPhase,
    TrialSpec,
)


def final_trial(trial_id: str, permit_id: str) -> TrialSpec:
    return TrialSpec(
        trial_id=trial_id,
        campaign_id="holdout-campaign",
        hypothesis_id=f"h-{trial_id}",
        strategy_id="strategy",
        strategy_version="1",
        dataset_hash="d" * 64,
        split_name="protected_holdout",
        phase=TrialPhase.FINAL_HOLDOUT,
        parameters={"lookback": 20},
        code_version="code",
        holdout_authorization_id=permit_id,
    )


def setup(tmp_path, now):
    db = tmp_path / "holdout.db"
    experiments = SQLiteExperimentRegistry(db)
    trials = SQLiteTrialLedger(db)
    trials.create_campaign(
        CampaignSpec(
            campaign_id="holdout-campaign",
            family_id="family",
            expected_trial_ids=("final-a", "final-b"),
            code_version="code",
            purpose="one-shot final validation",
        ),
        now=now,
    )
    return db, experiments, trials


def test_final_holdout_result_requires_consumed_registry_permit(tmp_path, now):
    _, experiments, trials = setup(tmp_path, now)
    spec = final_trial("final-a", "permit-a")
    trials.preregister(spec, now=now + timedelta(seconds=1))

    with pytest.raises(TrialGovernanceError, match="consumed"):
        trials.record_completed(
            trial_id="final-a",
            metrics={"sharpe": 0.7},
            p_value=Decimal("0.05"),
            now=now + timedelta(seconds=2),
        )

    experiments.consume_holdout_permit(
        permit=HoldoutPermit(permit_id="permit-a", issued_by="human-operator"),
        now=now + timedelta(seconds=3),
    )
    completed = trials.record_completed(
        trial_id="final-a",
        metrics={"sharpe": 0.7},
        p_value=Decimal("0.05"),
        now=now + timedelta(seconds=4),
    )
    assert completed.result_hash


def test_same_holdout_permit_cannot_bind_two_trials(tmp_path, now):
    _, experiments, trials = setup(tmp_path, now)
    trials.preregister(final_trial("final-a", "permit-shared"), now=now)
    with pytest.raises(TrialGovernanceError, match="another trial"):
        trials.preregister(
            final_trial("final-b", "permit-shared"),
            now=now + timedelta(seconds=1),
        )


def test_same_holdout_trial_cannot_change_permit_identity(tmp_path, now):
    _, experiments, trials = setup(tmp_path, now)
    trials.preregister(final_trial("final-a", "permit-a"), now=now)
    with pytest.raises(Exception):
        trials.preregister(
            final_trial("final-a", "permit-b"),
            now=now + timedelta(seconds=1),
        )


def test_final_result_fails_if_holdout_registry_was_never_initialized(tmp_path, now):
    db = tmp_path / "no-registry.db"
    trials = SQLiteTrialLedger(db)
    trials.create_campaign(
        CampaignSpec(
            "holdout-campaign",
            "family",
            ("final-a",),
            "code",
            "final validation",
        ),
        now=now,
    )
    trials.preregister(final_trial("final-a", "permit-a"), now=now + timedelta(seconds=1))
    with pytest.raises(TrialGovernanceError, match="registry"):
        trials.record_failed(
            trial_id="final-a",
            failure_code="NO_EDGE",
            now=now + timedelta(seconds=2),
        )


def test_consumed_permit_remains_one_use_at_r1_registry_boundary(tmp_path, now):
    _, experiments, trials = setup(tmp_path, now)
    permit = HoldoutPermit(permit_id="one-use", issued_by="human")
    experiments.consume_holdout_permit(permit=permit, now=now)
    from autotrade.research.registry import HoldoutPermitConsumed

    with pytest.raises(HoldoutPermitConsumed):
        experiments.consume_holdout_permit(
            permit=permit, now=now + timedelta(seconds=1)
        )
