from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.tournament import (
    RankingDirection,
    TournamentGovernanceError,
    TournamentSpec,
    evaluate_strategy_tournament,
)
from autotrade.research.trials import (
    CampaignSpec,
    SQLiteTrialLedger,
    TrialPhase,
    TrialSpec,
)


def _campaign(ids=("dev-a", "dev-b", "dev-c")):
    return CampaignSpec(
        campaign_id="tournament-campaign",
        family_id="strategy-family",
        expected_trial_ids=ids,
        code_version="r3-test",
        purpose="deterministic development tournament",
    )


def _trial(
    trial_id: str,
    *,
    strategy_id: str | None = None,
    phase: TrialPhase = TrialPhase.DEVELOPMENT,
    split: str = "development",
    authorization: str = "",
):
    return TrialSpec(
        trial_id=trial_id,
        campaign_id="tournament-campaign",
        hypothesis_id=f"hyp-{trial_id}",
        strategy_id=strategy_id or f"strategy-{trial_id}",
        strategy_version="1",
        dataset_hash="d" * 64,
        split_name=split,
        phase=phase,
        parameters={"variant": trial_id},
        code_version="r3-test",
        holdout_authorization_id=authorization,
    )


def _spec(*, direction=RankingDirection.MAXIMIZE, ids=("dev-a", "dev-b", "dev-c")):
    return TournamentSpec(
        tournament_id="tournament-1",
        campaign_id="tournament-campaign",
        metric_name="sharpe",
        direction=direction,
        candidate_trial_ids=ids,
    )


def _complete_development_campaign(tmp_path, now):
    ledger = SQLiteTrialLedger(tmp_path / "tournament.db")
    ledger.create_campaign(_campaign(), now=now)
    for index, trial_id in enumerate(("dev-a", "dev-b", "dev-c"), start=1):
        ledger.preregister(_trial(trial_id), now=now + timedelta(seconds=index))
    ledger.record_completed(
        trial_id="dev-a",
        metrics={"sharpe": 1.2, "return": 0.1},
        p_value=Decimal("0.03"),
        now=now + timedelta(seconds=10),
    )
    ledger.record_completed(
        trial_id="dev-b",
        metrics={"sharpe": 1.8, "return": 0.08},
        p_value=Decimal("0.04"),
        now=now + timedelta(seconds=11),
    )
    ledger.record_failed(
        trial_id="dev-c",
        failure_code="DATA_QUALITY_FAILURE",
        now=now + timedelta(seconds=12),
    )
    return ledger


def test_tournament_ranks_complete_development_universe_and_keeps_failures(tmp_path, now):
    ledger = _complete_development_campaign(tmp_path, now)
    evidence = evaluate_strategy_tournament(ledger, _spec())

    assert [entry.trial_id for entry in evidence.entries] == ["dev-b", "dev-a", "dev-c"]
    assert [entry.rank for entry in evidence.entries] == [1, 2, 3]
    assert evidence.winner_trial_id == "dev-b"
    assert evidence.completed_count == 2
    assert evidence.failed_count == 1
    assert evidence.entries[-1].eligible is False
    assert evidence.entries[-1].failure_code == "DATA_QUALITY_FAILURE"
    assert evidence.fingerprint == evaluate_strategy_tournament(ledger, _spec()).fingerprint


def test_tournament_minimize_direction_is_explicit(tmp_path, now):
    ledger = _complete_development_campaign(tmp_path, now)
    evidence = evaluate_strategy_tournament(
        ledger,
        _spec(direction=RankingDirection.MINIMIZE),
    )
    assert [entry.trial_id for entry in evidence.entries[:2]] == ["dev-a", "dev-b"]
    assert evidence.winner_trial_id == "dev-a"


def test_exact_metric_tie_uses_immutable_identity_not_iteration_order(tmp_path, now):
    ledger = SQLiteTrialLedger(tmp_path / "tie.db")
    ledger.create_campaign(_campaign(ids=("dev-a", "dev-b")), now=now)
    ledger.preregister(_trial("dev-b", strategy_id="strategy-z"), now=now + timedelta(seconds=1))
    ledger.preregister(_trial("dev-a", strategy_id="strategy-a"), now=now + timedelta(seconds=2))
    for offset, trial_id in enumerate(("dev-b", "dev-a"), start=3):
        ledger.record_completed(
            trial_id=trial_id,
            metrics={"sharpe": "1.500"},
            p_value=None,
            now=now + timedelta(seconds=offset),
        )
    evidence = evaluate_strategy_tournament(ledger, _spec(ids=("dev-a", "dev-b")))
    assert [entry.trial_id for entry in evidence.entries] == ["dev-a", "dev-b"]


def test_candidate_cherry_picking_or_noncanonical_order_fails(tmp_path, now):
    ledger = _complete_development_campaign(tmp_path, now)
    with pytest.raises(TournamentGovernanceError, match="complete frozen DEVELOPMENT"):
        evaluate_strategy_tournament(ledger, _spec(ids=("dev-a", "dev-b")))
    with pytest.raises(ValueError, match="canonical sorted"):
        _spec(ids=("dev-b", "dev-a", "dev-c"))


def test_incomplete_campaign_cannot_be_ranked(tmp_path, now):
    ledger = SQLiteTrialLedger(tmp_path / "incomplete.db")
    ledger.create_campaign(_campaign(), now=now)
    ledger.preregister(_trial("dev-a"), now=now + timedelta(seconds=1))
    ledger.record_completed(
        trial_id="dev-a",
        metrics={"sharpe": 1.0},
        p_value=None,
        now=now + timedelta(seconds=2),
    )
    with pytest.raises(Exception, match="incomplete"):
        evaluate_strategy_tournament(ledger, _spec())


def test_tournament_rejects_any_campaign_that_has_seen_final_holdout(tmp_path, now):
    from autotrade.research.registry import HoldoutPermit, SQLiteExperimentRegistry

    db = tmp_path / "holdout.db"
    ledger = SQLiteTrialLedger(db)
    ledger.create_campaign(_campaign(ids=("dev-a", "holdout-a")), now=now)
    ledger.preregister(_trial("dev-a"), now=now + timedelta(seconds=1))

    registry = SQLiteExperimentRegistry(db)
    permit = HoldoutPermit(permit_id="tournament-holdout-permit", issued_by="r3-test")
    registry.consume_holdout_permit(permit=permit, now=now + timedelta(seconds=2))
    ledger.preregister(
        _trial(
            "holdout-a",
            phase=TrialPhase.FINAL_HOLDOUT,
            split="protected_holdout",
            authorization=permit.permit_id,
        ),
        now=now + timedelta(seconds=3),
    )
    ledger.record_completed(
        trial_id="dev-a",
        metrics={"sharpe": 1.1},
        p_value=None,
        now=now + timedelta(seconds=4),
    )
    ledger.record_completed(
        trial_id="holdout-a",
        metrics={"sharpe": 99.0},
        p_value=None,
        now=now + timedelta(seconds=5),
    )
    with pytest.raises(TournamentGovernanceError, match="FINAL_HOLDOUT"):
        evaluate_strategy_tournament(
            ledger,
            TournamentSpec(
                tournament_id="no-peeking",
                campaign_id="tournament-campaign",
                metric_name="sharpe",
                direction=RankingDirection.MAXIMIZE,
                candidate_trial_ids=("dev-a",),
            ),
        )


def test_completed_candidate_requires_finite_numeric_metric(tmp_path, now):
    for index, bad in enumerate((None, True, "not-a-number", float("nan"), float("inf"))):
        ledger = SQLiteTrialLedger(tmp_path / f"bad-{index}.db")
        ledger.create_campaign(_campaign(ids=("dev-a",)), now=now)
        ledger.preregister(_trial("dev-a"), now=now + timedelta(seconds=1))
        metrics = {} if bad is None else {"sharpe": bad}
        ledger.record_completed(
            trial_id="dev-a",
            metrics=metrics,
            p_value=None,
            now=now + timedelta(seconds=2),
        )
        with pytest.raises(TournamentGovernanceError, match="ranking metric|numeric|finite|boolean"):
            evaluate_strategy_tournament(ledger, _spec(ids=("dev-a",)))


def test_all_failed_campaign_produces_evidence_without_false_winner(tmp_path, now):
    ledger = SQLiteTrialLedger(tmp_path / "all-failed.db")
    ledger.create_campaign(_campaign(ids=("dev-a", "dev-b")), now=now)
    for index, trial_id in enumerate(("dev-a", "dev-b"), start=1):
        ledger.preregister(_trial(trial_id), now=now + timedelta(seconds=index))
        ledger.record_failed(
            trial_id=trial_id,
            failure_code=f"FAIL_{trial_id}",
            now=now + timedelta(seconds=index + 3),
        )
    evidence = evaluate_strategy_tournament(ledger, _spec(ids=("dev-a", "dev-b")))
    assert evidence.winner_trial_id == ""
    assert evidence.completed_count == 0
    assert evidence.failed_count == 2
    assert all(not entry.eligible for entry in evidence.entries)


def test_tournament_spec_and_evidence_fingerprints_bind_governance(tmp_path, now):
    ledger = _complete_development_campaign(tmp_path, now)
    maximize = evaluate_strategy_tournament(ledger, _spec())
    minimize = evaluate_strategy_tournament(ledger, _spec(direction=RankingDirection.MINIMIZE))
    assert maximize.spec_fingerprint != minimize.spec_fingerprint
    assert maximize.fingerprint != minimize.fingerprint
    payload = maximize.to_payload()
    assert payload["fingerprint"] == maximize.fingerprint
    assert payload["result_universe_hash"] == maximize.result_universe_hash
