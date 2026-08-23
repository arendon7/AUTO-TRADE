from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

import pytest

from autotrade.persistence import SQLiteRuntime
from autotrade.research.tournament import RankingDirection, TournamentSpec, evaluate_strategy_tournament
from autotrade.research.trials import CampaignSpec, SQLiteTrialLedger, TrialPhase, TrialSpec
from autotrade.strategy_lab_promotion import (
    SQLiteStrategyPromotionPolicyRegistry,
    build_strategy_promotion_policy,
    build_strategy_promotion_threshold_policy,
)
from autotrade.strategy_promotion_assessment import SQLiteStrategyPromotionAssessmentRegistry
from autotrade.strategy_promotion_assessment_read_model import (
    PromotionAssessmentReadIntegrityError,
    PromotionAssessmentReadModel,
)


NOW = datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc)


def _trial(trial_id: str, strategy_id: str) -> TrialSpec:
    return TrialSpec(
        trial_id=trial_id,
        campaign_id="dev-binding",
        hypothesis_id=f"hyp-{trial_id}",
        strategy_id=strategy_id,
        strategy_version="v1",
        dataset_hash="a" * 64,
        split_name="development",
        phase=TrialPhase.DEVELOPMENT,
        parameters={"strategy": strategy_id},
        code_version="w80-policy-binding-test",
        holdout_authorization_id="",
    )


def _setup(tmp_path):
    db = tmp_path / "core.sqlite3"
    runtime = SQLiteRuntime(db)
    ledger = SQLiteTrialLedger(runtime)
    ledger.create_campaign(
        CampaignSpec(
            campaign_id="dev-binding",
            family_id="family-binding",
            expected_trial_ids=("dev-a", "dev-b"),
            code_version="w80-policy-binding-test",
            purpose="bind durable assessment to exact candidate",
        ),
        now=NOW,
    )
    ledger.create_campaign(
        CampaignSpec(
            campaign_id="holdout-binding",
            family_id="family-binding-holdout",
            expected_trial_ids=("holdout-a",),
            code_version="w80-policy-binding-test",
            purpose="single protected holdout",
        ),
        now=NOW + timedelta(milliseconds=1),
    )
    policies = SQLiteStrategyPromotionPolicyRegistry(runtime)
    thresholds = build_strategy_promotion_threshold_policy(
        threshold_policy_id="thresholds-binding",
        development_campaign_id="dev-binding",
        holdout_campaign_id="holdout-binding",
        holdout_trial_id="holdout-a",
        max_holm_adjusted_p=Decimal("0.05"),
        min_holdout_net_return=Decimal("0.02"),
        max_holdout_drawdown=Decimal("0.10"),
        min_holdout_fills=5,
        min_execution_fill_ratio=Decimal("0.40"),
        max_execution_adverse_slippage_bps=Decimal("10"),
    )
    policies.register_thresholds(
        thresholds,
        trial_ledger=ledger,
        now=NOW + timedelta(milliseconds=10),
    )
    ledger.preregister(_trial("dev-a", "strategy-a"), now=NOW + timedelta(seconds=1))
    ledger.preregister(_trial("dev-b", "strategy-b"), now=NOW + timedelta(seconds=2))
    ledger.record_completed(
        trial_id="dev-a",
        metrics={"sharpe": 1.8},
        p_value=Decimal("0.01"),
        now=NOW + timedelta(seconds=3),
    )
    ledger.record_completed(
        trial_id="dev-b",
        metrics={"sharpe": 1.1},
        p_value=Decimal("0.20"),
        now=NOW + timedelta(seconds=4),
    )
    tournament = evaluate_strategy_tournament(
        ledger,
        TournamentSpec(
            tournament_id="tournament-binding",
            campaign_id="dev-binding",
            metric_name="sharpe",
            direction=RankingDirection.MAXIMIZE,
            candidate_trial_ids=("dev-a", "dev-b"),
        ),
    )
    policy = build_strategy_promotion_policy(
        policy_id="promotion-binding",
        thresholds=thresholds,
        trial_ledger=ledger,
        tournament=tournament,
    )
    policies.register(
        policy,
        trial_ledger=ledger,
        tournament=tournament,
        now=NOW + timedelta(seconds=5),
    )
    assessments = SQLiteStrategyPromotionAssessmentRegistry(runtime)
    assessments.assess_and_record(
        assessment_id="assessment-binding-1",
        policy_registry=policies,
        policy_id=policy.policy_id,
        trial_ledger=ledger,
        tournament=tournament,
        now=NOW + timedelta(seconds=6),
    )
    return db, policy, thresholds


def test_reader_binds_real_writer_receipt_to_frozen_w79_policy(tmp_path):
    db, policy, thresholds = _setup(tmp_path)
    snapshot = PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=10))
    assert snapshot.assessment_evidence_state == "DURABLE_W80_ASSESSMENT"
    item = snapshot.assessments[0]
    assert item.policy_id == policy.policy_id
    assert item.policy_hash == policy.policy_hash
    assert item.threshold_policy_hash == thresholds.threshold_policy_hash
    assert item.selected_strategy_id == policy.selected_strategy_id
    assert item.selected_strategy_version == policy.selected_strategy_version


def test_reader_rejects_self_consistent_journal_after_policy_row_is_deleted(tmp_path):
    db, _, _ = _setup(tmp_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM strategy_promotion_policies")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="lost its frozen W79 policy"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=10))


def test_reader_rejects_policy_hash_side_column_drift(tmp_path):
    db, _, _ = _setup(tmp_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE strategy_promotion_policies SET policy_hash=? WHERE policy_id=?",
            ("f" * 64, "promotion-binding"),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="frozen W79 policy SQLite column mismatch"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=10))


def test_reader_rejects_assessment_identity_rebound_to_another_strategy(tmp_path):
    db, _, _ = _setup(tmp_path)
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT policy_json FROM strategy_promotion_policies WHERE policy_id=?",
            ("promotion-binding",),
        ).fetchone()
        assert row is not None
        import json
        value = json.loads(row[0])
        value["selected_strategy_id"] = "strategy-other"
        conn.execute(
            "UPDATE strategy_promotion_policies SET policy_json=? WHERE policy_id=?",
            (json.dumps(value, sort_keys=True, separators=(",", ":")), "promotion-binding"),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(Exception, match="policy hash mismatch|frozen W79 policy"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=10))


def test_reader_rejects_threshold_binding_loss_even_when_assessment_receipt_is_unchanged(tmp_path):
    db, _, _ = _setup(tmp_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM strategy_promotion_threshold_policies")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="lost its frozen W79 threshold binding"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=10))
