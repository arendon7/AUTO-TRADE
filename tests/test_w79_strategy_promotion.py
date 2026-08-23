from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.paper_execution_lab import run_paper_execution_sensitivity
from autotrade.paper_execution_qualification import bind_research_costs_to_paper_execution
from autotrade.paper_execution_scenarios import (
    build_paper_execution_scenario,
    build_paper_execution_scenario_matrix,
)
from autotrade.persistence import SQLiteRuntime
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.multiple_testing import campaign_holm_evidence
from autotrade.research.registry import HoldoutPermit, SQLiteExperimentRegistry
from autotrade.research.tournament import (
    RankingDirection,
    TournamentSpec,
    evaluate_strategy_tournament,
)
from autotrade.research.trials import (
    CampaignSpec,
    SQLiteTrialLedger,
    TrialPhase,
    TrialSpec,
)
from autotrade.strategy_lab_promotion import (
    PERMANENT_W79_PROMOTION_BLOCKERS,
    PromotionAssessmentState,
    PromotionGateStatus,
    SQLiteStrategyPromotionPolicyRegistry,
    StrategyPromotionConflict,
    StrategyPromotionIntegrityError,
    build_strategy_promotion_policy,
    evaluate_strategy_promotion,
)


def _trial(
    *,
    trial_id: str,
    campaign_id: str,
    strategy_id: str,
    strategy_version: str = "v1",
    phase: TrialPhase = TrialPhase.DEVELOPMENT,
    split_name: str = "development",
    authorization: str = "",
):
    return TrialSpec(
        trial_id=trial_id,
        campaign_id=campaign_id,
        hypothesis_id=f"hyp-{trial_id}",
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        dataset_hash=("a" if phase is TrialPhase.DEVELOPMENT else "b") * 64,
        split_name=split_name,
        phase=phase,
        parameters={"lookback": 20, "variant": strategy_id},
        code_version="w79-test-code",
        holdout_authorization_id=authorization,
    )


def _setup_policy(
    tmp_path,
    now,
    *,
    max_holm: str = "0.05",
    min_execution_fill: str = "0.40",
    max_execution_slippage: str = "10",
):
    db = tmp_path / "w79.db"
    runtime = SQLiteRuntime(db)
    ledger = SQLiteTrialLedger(runtime)
    ledger.create_campaign(
        CampaignSpec(
            campaign_id="dev-campaign",
            family_id="family-a",
            expected_trial_ids=("dev-a", "dev-b"),
            code_version="w79-test-code",
            purpose="select candidate before protected holdout",
        ),
        now=now,
    )
    ledger.create_campaign(
        CampaignSpec(
            campaign_id="holdout-campaign",
            family_id="family-a-holdout",
            expected_trial_ids=("holdout-a",),
            code_version="w79-test-code",
            purpose="single protected final holdout",
        ),
        now=now + timedelta(milliseconds=1),
    )
    ledger.preregister(
        _trial(trial_id="dev-a", campaign_id="dev-campaign", strategy_id="strategy-a"),
        now=now + timedelta(seconds=1),
    )
    ledger.preregister(
        _trial(trial_id="dev-b", campaign_id="dev-campaign", strategy_id="strategy-b"),
        now=now + timedelta(seconds=2),
    )
    ledger.record_completed(
        trial_id="dev-a",
        metrics={"sharpe": 1.8, "net_return": 0.12},
        p_value=Decimal("0.01"),
        now=now + timedelta(seconds=3),
    )
    ledger.record_completed(
        trial_id="dev-b",
        metrics={"sharpe": 1.2, "net_return": 0.08},
        p_value=Decimal("0.20"),
        now=now + timedelta(seconds=4),
    )
    tournament = evaluate_strategy_tournament(
        ledger,
        TournamentSpec(
            tournament_id="dev-tournament",
            campaign_id="dev-campaign",
            metric_name="sharpe",
            direction=RankingDirection.MAXIMIZE,
            candidate_trial_ids=("dev-a", "dev-b"),
        ),
    )
    policy = build_strategy_promotion_policy(
        policy_id="promotion-a",
        development_campaign_id="dev-campaign",
        holdout_campaign_id="holdout-campaign",
        holdout_trial_id="holdout-a",
        trial_ledger=ledger,
        tournament=tournament,
        max_holm_adjusted_p=Decimal(max_holm),
        min_holdout_net_return=Decimal("0.02"),
        max_holdout_drawdown=Decimal("0.10"),
        min_holdout_fills=5,
        min_execution_fill_ratio=Decimal(min_execution_fill),
        max_execution_adverse_slippage_bps=Decimal(max_execution_slippage),
    )
    registry = SQLiteStrategyPromotionPolicyRegistry(runtime)
    registry.register(
        policy,
        trial_ledger=ledger,
        tournament=tournament,
        now=now + timedelta(seconds=5),
    )
    return db, ledger, tournament, policy, registry


def _complete_holdout(
    db,
    ledger,
    now,
    *,
    metrics=None,
    strategy_id="strategy-a",
    strategy_version="v1",
):
    permit = HoldoutPermit(permit_id="holdout-permit-a", issued_by="w79-test")
    SQLiteExperimentRegistry(db).consume_holdout_permit(
        permit=permit,
        now=now + timedelta(seconds=6),
    )
    ledger.preregister(
        _trial(
            trial_id="holdout-a",
            campaign_id="holdout-campaign",
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            phase=TrialPhase.FINAL_HOLDOUT,
            split_name="protected_final_holdout",
            authorization=permit.permit_id,
        ),
        now=now + timedelta(seconds=7),
    )
    ledger.record_completed(
        trial_id="holdout-a",
        metrics=metrics
        or {
            "net_return": 0.08,
            "max_drawdown": 0.04,
            "fills": 12,
            "sharpe": 1.1,
        },
        p_value=Decimal("0.03"),
        now=now + timedelta(seconds=8),
    )


def _execution_report(limits, market, empty_portfolio, market_buy_intent):
    baseline = build_paper_execution_scenario(
        scenario_id="baseline",
        purpose="W79 baseline execution",
        slippage_bps=Decimal("2"),
        max_fill_fraction=Decimal("1"),
        max_market_age=timedelta(seconds=2),
        max_spread_bps=Decimal("250"),
    )
    stress = build_paper_execution_scenario(
        scenario_id="liquidity_stress",
        purpose="W79 adverse liquidity execution",
        slippage_bps=Decimal("8"),
        max_fill_fraction=Decimal("0.5"),
        max_market_age=timedelta(seconds=2),
        max_spread_bps=Decimal("250"),
    )
    matrix = build_paper_execution_scenario_matrix((baseline, stress))
    qualification = bind_research_costs_to_paper_execution(
        cost_model=ExecutionCostModel(
            fee_bps=Decimal("5"),
            half_spread_bps=Decimal("2"),
            slippage_bps=Decimal("2"),
        ),
        matrix=matrix,
    )
    return run_paper_execution_sensitivity(
        qualification=qualification,
        matrix=matrix,
        limits=limits,
        intent=market_buy_intent,
        market=market,
        portfolio=empty_portfolio,
        now=market.observed_at,
    )


def _gate(view, gate_id):
    return next(item for item in view.gates if item.gate_id == gate_id)


def test_policy_freezes_after_development_selection_before_holdout(tmp_path, now):
    _, ledger, tournament, policy, registry = _setup_policy(tmp_path, now)

    loaded = registry.get(policy.policy_id)
    assert loaded == policy
    assert loaded.policy_hash == policy.policy_hash
    assert loaded.selected_trial_id == "dev-a"
    assert loaded.selected_strategy_id == "strategy-a"
    assert loaded.selected_strategy_version == "v1"
    assert loaded.external_execution_authorized is False
    assert loaded.live_trading == "BLOCKED"

    assert (
        registry.register(
            policy,
            trial_ledger=ledger,
            tournament=tournament,
            now=now + timedelta(seconds=6),
        )
        == policy
    )
    with pytest.raises(StrategyPromotionIntegrityError, match="policy hash mismatch"):
        replace(policy, min_holdout_net_return=Decimal("0.03"))


def test_policy_cannot_be_created_or_registered_after_holdout_preregistration(tmp_path, now):
    db, ledger, tournament, policy, _ = _setup_policy(tmp_path, now)
    _complete_holdout(db, ledger, now)

    with pytest.raises(StrategyPromotionIntegrityError, match="before HOLDOUT trial preregistration"):
        build_strategy_promotion_policy(
            policy_id="promotion-late",
            development_campaign_id="dev-campaign",
            holdout_campaign_id="holdout-campaign",
            holdout_trial_id="holdout-a",
            trial_ledger=ledger,
            tournament=tournament,
            max_holm_adjusted_p=Decimal("0.05"),
            min_holdout_net_return=Decimal("0.02"),
            max_holdout_drawdown=Decimal("0.10"),
            min_holdout_fills=5,
            min_execution_fill_ratio=Decimal("0.4"),
            max_execution_adverse_slippage_bps=Decimal("10"),
        )

    late_registry = SQLiteStrategyPromotionPolicyRegistry(tmp_path / "late-policy.db")
    with pytest.raises(StrategyPromotionIntegrityError, match="after HOLDOUT preregistration"):
        late_registry.register(
            policy,
            trial_ledger=ledger,
            tournament=tournament,
            now=now + timedelta(seconds=20),
        )


def test_missing_evidence_is_visible_and_never_promotes(tmp_path, now):
    _, ledger, tournament, policy, registry = _setup_policy(tmp_path, now)

    view = evaluate_strategy_promotion(
        registry=registry,
        policy_id=policy.policy_id,
        trial_ledger=ledger,
        tournament=tournament,
    )

    assert view.assessment_state is PromotionAssessmentState.INCOMPLETE
    assert view.evidence_complete is False
    assert _gate(view, "DEVELOPMENT_SELECTION").status is PromotionGateStatus.PASS
    assert _gate(view, "MULTIPLE_TESTING").status is PromotionGateStatus.MISSING
    assert _gate(view, "FINAL_HOLDOUT").status is PromotionGateStatus.MISSING
    assert _gate(view, "EXECUTION_SENSITIVITY").status is PromotionGateStatus.MISSING
    assert view.paper_candidate_authorized is False
    assert view.external_execution_authorized is False
    assert view.live_trading == "BLOCKED"
    assert view.promotion_blockers == tuple(sorted(PERMANENT_W79_PROMOTION_BLOCKERS))
    assert view.to_dict()["view_hash"] == view.view_hash


def test_all_implemented_gates_can_qualify_evidence_but_not_paper_candidate(
    tmp_path,
    now,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    db, ledger, tournament, policy, registry = _setup_policy(tmp_path, now)
    holm = campaign_holm_evidence(ledger, "dev-campaign")
    _complete_holdout(db, ledger, now)
    report = _execution_report(limits, market, empty_portfolio, market_buy_intent)

    view = evaluate_strategy_promotion(
        registry=registry,
        policy_id=policy.policy_id,
        trial_ledger=ledger,
        tournament=tournament,
        holm=holm,
        execution_report=report,
        execution_intent=market_buy_intent,
    )

    assert all(item.status is PromotionGateStatus.PASS for item in view.gates)
    assert view.evidence_complete is True
    assert view.assessment_state is PromotionAssessmentState.EVIDENCE_QUALIFIED
    assert view.paper_candidate_authorized is False
    assert set(view.promotion_blockers) == {
        "EXECUTION_STRATEGY_VERSION_UNBOUND",
        "FEE_ACCOUNTING_INCOMPLETE",
        "SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED",
        "TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN",
    }
    assert _gate(view, "EXECUTION_SENSITIVITY").evidence_hashes == tuple(
        sorted((report.measurement_report_hash, report.trace_report_hash))
    )


def test_policy_threshold_breach_rejects_without_widening_authority(
    tmp_path,
    now,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    db, ledger, tournament, policy, registry = _setup_policy(tmp_path, now)
    holm = campaign_holm_evidence(ledger, "dev-campaign")
    _complete_holdout(
        db,
        ledger,
        now,
        metrics={"net_return": -0.03, "max_drawdown": 0.20, "fills": 2},
    )
    report = _execution_report(limits, market, empty_portfolio, market_buy_intent)

    view = evaluate_strategy_promotion(
        registry=registry,
        policy_id=policy.policy_id,
        trial_ledger=ledger,
        tournament=tournament,
        holm=holm,
        execution_report=report,
        execution_intent=market_buy_intent,
    )

    holdout = _gate(view, "FINAL_HOLDOUT")
    assert holdout.status is PromotionGateStatus.FAIL
    assert holdout.reason_codes == (
        "HOLDOUT_DRAWDOWN_ABOVE_POLICY",
        "HOLDOUT_FILLS_BELOW_POLICY",
        "HOLDOUT_NET_RETURN_BELOW_POLICY",
    )
    assert view.assessment_state is PromotionAssessmentState.REJECTED
    assert view.paper_candidate_authorized is False


def test_execution_identity_mismatch_blocks_even_when_w78_report_is_green(
    tmp_path,
    now,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    db, ledger, tournament, policy, registry = _setup_policy(tmp_path, now)
    _complete_holdout(db, ledger, now)
    holm = campaign_holm_evidence(ledger, "dev-campaign")
    wrong_intent = replace(market_buy_intent, strategy_id="strategy-b")
    report = _execution_report(limits, market, empty_portfolio, wrong_intent)

    view = evaluate_strategy_promotion(
        registry=registry,
        policy_id=policy.policy_id,
        trial_ledger=ledger,
        tournament=tournament,
        holm=holm,
        execution_report=report,
        execution_intent=wrong_intent,
    )

    execution = _gate(view, "EXECUTION_SENSITIVITY")
    assert execution.status is PromotionGateStatus.BLOCKED
    assert execution.reason_codes == ("EXECUTION_STRATEGY_ID_MISMATCH",)
    assert view.assessment_state is PromotionAssessmentState.BLOCKED
    assert view.evidence_complete is False


def test_holm_and_execution_thresholds_fail_closed(
    tmp_path,
    now,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
):
    strict_dir = tmp_path / "strict"
    db, ledger, tournament, policy, registry = _setup_policy(
        strict_dir,
        now,
        max_holm="0.001",
        min_execution_fill="0.9",
        max_execution_slippage="5",
    )
    _complete_holdout(db, ledger, now)
    holm = campaign_holm_evidence(ledger, "dev-campaign")
    report = _execution_report(limits, market, empty_portfolio, market_buy_intent)

    view = evaluate_strategy_promotion(
        registry=registry,
        policy_id=policy.policy_id,
        trial_ledger=ledger,
        tournament=tournament,
        holm=holm,
        execution_report=report,
        execution_intent=market_buy_intent,
    )

    multiple = _gate(view, "MULTIPLE_TESTING")
    execution = _gate(view, "EXECUTION_SENSITIVITY")
    assert multiple.status is PromotionGateStatus.FAIL
    assert multiple.reason_codes == ("HOLM_ADJUSTED_P_ABOVE_POLICY",)
    assert execution.status is PromotionGateStatus.FAIL
    assert execution.reason_codes == (
        "EXECUTION_FILL_RATIO_BELOW_POLICY",
        "EXECUTION_SLIPPAGE_ABOVE_POLICY",
    )
    assert view.assessment_state is PromotionAssessmentState.REJECTED
    assert view.paper_candidate_authorized is False


def test_holdout_candidate_identity_mismatch_blocks(tmp_path, now):
    db, ledger, tournament, policy, registry = _setup_policy(tmp_path, now)
    _complete_holdout(db, ledger, now, strategy_version="v2")
    holm = campaign_holm_evidence(ledger, "dev-campaign")

    view = evaluate_strategy_promotion(
        registry=registry,
        policy_id=policy.policy_id,
        trial_ledger=ledger,
        tournament=tournament,
        holm=holm,
    )

    holdout = _gate(view, "FINAL_HOLDOUT")
    assert holdout.status is PromotionGateStatus.BLOCKED
    assert holdout.reason_codes == ("HOLDOUT_STRATEGY_VERSION_MISMATCH",)
    assert view.assessment_state is PromotionAssessmentState.BLOCKED


def test_registry_conflict_unknown_policy_and_invalid_thresholds_fail_closed(tmp_path, now):
    _, ledger, tournament, policy, registry = _setup_policy(tmp_path, now)
    with pytest.raises(StrategyPromotionIntegrityError, match="unknown frozen promotion policy"):
        evaluate_strategy_promotion(
            registry=registry,
            policy_id="does-not-exist",
            trial_ledger=ledger,
            tournament=tournament,
        )

    with pytest.raises(StrategyPromotionIntegrityError, match="policy hash mismatch"):
        replace(policy, policy_id="promotion-b", policy_hash="0" * 64)

    duplicate = build_strategy_promotion_policy(
        policy_id="promotion-b",
        development_campaign_id="dev-campaign",
        holdout_campaign_id="holdout-campaign",
        holdout_trial_id="holdout-a",
        trial_ledger=ledger,
        tournament=tournament,
        max_holm_adjusted_p=Decimal("0.05"),
        min_holdout_net_return=Decimal("0.02"),
        max_holdout_drawdown=Decimal("0.10"),
        min_holdout_fills=5,
        min_execution_fill_ratio=Decimal("0.40"),
        max_execution_adverse_slippage_bps=Decimal("10"),
    )
    with pytest.raises(StrategyPromotionConflict, match="HOLDOUT campaign already frozen"):
        registry.register(
            duplicate,
            trial_ledger=ledger,
            tournament=tournament,
            now=now + timedelta(seconds=10),
        )

    with pytest.raises(StrategyPromotionIntegrityError, match="max_holm_adjusted_p"):
        build_strategy_promotion_policy(
            policy_id="promotion-invalid",
            development_campaign_id="dev-campaign",
            holdout_campaign_id="holdout-campaign",
            holdout_trial_id="holdout-a",
            trial_ledger=ledger,
            tournament=tournament,
            max_holm_adjusted_p=Decimal("1.1"),
            min_holdout_net_return=Decimal("0.02"),
            max_holdout_drawdown=Decimal("0.10"),
            min_holdout_fills=5,
            min_execution_fill_ratio=Decimal("0.40"),
            max_execution_adverse_slippage_bps=Decimal("10"),
        )
