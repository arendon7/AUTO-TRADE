from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
import sqlite3
import sys

import pytest

from autotrade.persistence import SQLiteRuntime
from autotrade.research.tournament import RankingDirection, TournamentSpec, evaluate_strategy_tournament
from autotrade.research.trials import CampaignSpec, SQLiteTrialLedger, TrialPhase, TrialSpec
from autotrade.strategy_lab_promotion import (
    SQLiteStrategyPromotionPolicyRegistry,
    build_strategy_promotion_policy,
    build_strategy_promotion_threshold_policy,
)
from autotrade.strategy_lab_read_model import (
    StrategyLabPromotionReadModel,
    StrategyLabReadModelIntegrityError,
)
from autotrade.strategy_promotion_assessment import SQLiteStrategyPromotionAssessmentRegistry


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "web/mac_strategy_lab.html"
DASHBOARD_PATH = ROOT / "scripts/mac_dashboard.py"
SPEC = importlib.util.spec_from_file_location("mac_dashboard_w80_under_test", DASHBOARD_PATH)
assert SPEC is not None and SPEC.loader is not None
dashboard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dashboard
SPEC.loader.exec_module(dashboard)
NOW = datetime(2026, 8, 23, 18, 30, tzinfo=timezone.utc)


def _trial(trial_id: str, strategy_id: str) -> TrialSpec:
    return TrialSpec(
        trial_id=trial_id,
        campaign_id="dev-ui-w80",
        hypothesis_id=f"hyp-{trial_id}",
        strategy_id=strategy_id,
        strategy_version="v1",
        dataset_hash="a" * 64,
        split_name="development",
        phase=TrialPhase.DEVELOPMENT,
        parameters={"strategy": strategy_id},
        code_version="w80-strategy-lab-test",
        holdout_authorization_id="",
    )


def _workspace_with_assessment(tmp_path: Path) -> tuple[Path, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = workspace / "core.sqlite3"
    runtime = SQLiteRuntime(db)
    ledger = SQLiteTrialLedger(runtime)
    ledger.create_campaign(
        CampaignSpec(
            campaign_id="dev-ui-w80",
            family_id="family-ui-w80",
            expected_trial_ids=("dev-a", "dev-b"),
            code_version="w80-strategy-lab-test",
            purpose="Strategy Lab durable assessment projection",
        ),
        now=NOW,
    )
    ledger.create_campaign(
        CampaignSpec(
            campaign_id="holdout-ui-w80",
            family_id="family-ui-w80-holdout",
            expected_trial_ids=("holdout-a",),
            code_version="w80-strategy-lab-test",
            purpose="Strategy Lab protected holdout",
        ),
        now=NOW + timedelta(milliseconds=1),
    )
    policies = SQLiteStrategyPromotionPolicyRegistry(runtime)
    thresholds = build_strategy_promotion_threshold_policy(
        threshold_policy_id="thresholds-ui-w80",
        development_campaign_id="dev-ui-w80",
        holdout_campaign_id="holdout-ui-w80",
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
        metrics={"sharpe": 1.8, "net_return": 0.12},
        p_value=Decimal("0.01"),
        now=NOW + timedelta(seconds=3),
    )
    ledger.record_completed(
        trial_id="dev-b",
        metrics={"sharpe": 1.1, "net_return": 0.05},
        p_value=Decimal("0.20"),
        now=NOW + timedelta(seconds=4),
    )
    tournament = evaluate_strategy_tournament(
        ledger,
        TournamentSpec(
            tournament_id="tournament-ui-w80",
            campaign_id="dev-ui-w80",
            metric_name="sharpe",
            direction=RankingDirection.MAXIMIZE,
            candidate_trial_ids=("dev-a", "dev-b"),
        ),
    )
    policy = build_strategy_promotion_policy(
        policy_id="promotion-ui-w80",
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
    receipt = SQLiteStrategyPromotionAssessmentRegistry(runtime).assess_and_record(
        assessment_id="assessment-ui-w80-1",
        policy_registry=policies,
        policy_id=policy.policy_id,
        trial_ledger=ledger,
        tournament=tournament,
        now=NOW + timedelta(seconds=6),
    )
    return workspace, receipt.assessment_hash


def test_strategy_lab_keeps_w79_governance_separate_from_w80_assessment(tmp_path):
    workspace, assessment_hash = _workspace_with_assessment(tmp_path)
    value = StrategyLabPromotionReadModel(workspace / "core.sqlite3").snapshot(
        now=NOW + timedelta(seconds=10)
    ).to_dict()

    assert value["governance_state"] == "CANDIDATE_FROZEN"
    assert value["gate_evidence_state"] == "NOT_PERSISTED_BY_W79"
    assert value["paper_candidate_authorized"] is False
    assessments = value["promotion_assessments"]
    assert assessments["assessment_evidence_state"] == "DURABLE_W80_ASSESSMENT"
    assert assessments["assessment_count"] == 1
    assert assessments["policy_count"] == 1
    assert assessments["paper_candidate_authorized"] is False
    assert assessments["external_execution_authorized"] is False
    assert assessments["capital_authority"] == "NONE"
    assert assessments["live_trading"] == "BLOCKED"
    assert assessments["latest_assessments"][0]["assessment_hash"] == assessment_hash
    assert assessments["provenance_hash"] != value["provenance_hash"]


def test_dashboard_get_helper_exposes_w80_without_credentials_or_authority(tmp_path, monkeypatch):
    workspace, _ = _workspace_with_assessment(tmp_path)
    monkeypatch.setenv(dashboard.KEY_ENV, "must-not-be-consumed")
    monkeypatch.setenv(dashboard.SECRET_ENV, "must-not-be-consumed")

    payload = dashboard._strategy_lab_value(str(workspace))

    assert payload["ok"] is True
    assert payload["credentials_used"] is False
    assert payload["broker_network_used"] is False
    assert payload["broker_write_performed"] is False
    assert payload["paper_candidate_authorized"] is False
    assert payload["external_execution_authorized"] is False
    assert payload["capital_authority"] == "NONE"
    assert payload["live_trading"] == "BLOCKED"
    assessments = payload["strategy_lab"]["promotion_assessments"]
    assert assessments["assessment_evidence_state"] == "DURABLE_W80_ASSESSMENT"
    assert assessments["assessment_count"] == 1


def test_corrupt_w80_policy_binding_fails_entire_strategy_lab_read_closed(tmp_path):
    workspace, _ = _workspace_with_assessment(tmp_path)
    conn = sqlite3.connect(workspace / "core.sqlite3")
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM strategy_promotion_policies")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(
        StrategyLabReadModelIntegrityError,
        match="durable W80 assessment evidence failed independent verification",
    ):
        StrategyLabPromotionReadModel(workspace / "core.sqlite3").snapshot(
            now=NOW + timedelta(seconds=10)
        )


def test_strategy_lab_w80_ui_is_get_only_and_explains_separate_provenance_domains():
    html = HTML_PATH.read_text(encoding="utf-8")
    for anchor in (
        "W79 governance · W80 durable assessment",
        "Promotion Assessments W80 · durable",
        "Assessment ≠ autorización",
        "NO_DURABLE_W80_ASSESSMENT",
        "EVIDENCE_QUALIFIED",
        "NOT_PERSISTED_BY_W79",
        "los resultados de gates NO se sintetizan",
        "W79 snapshot",
        "W80 assessments",
        "PAPER CANDIDATE · FALSE",
        "CAPITAL · NONE",
        "LIVE · BLOCKED",
        "Broker POST: NO",
        'fetch("/api/strategy-lab?workspace="',
        'method:"GET"',
    ):
        assert anchor in html
    for forbidden in (
        'method:"POST"',
        "/api/action",
        "localStorage",
        "sessionStorage",
        'type="password"',
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
    ):
        assert forbidden not in html


def test_strategy_lab_remains_absent_from_post_allowlist():
    assert "strategy_lab" not in dashboard.SAFE_ACTIONS
    assert "strategy-lab" not in dashboard.SAFE_ACTIONS
    assert "/api/strategy-lab" not in dashboard.DashboardHandler.do_POST.__code__.co_consts
