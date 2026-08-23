from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import sqlite3

import pytest

import autotrade.strategy_lab_promotion as promotion
import autotrade.strategy_promotion_assessment as assessment
from autotrade.persistence import SQLiteRuntime
from autotrade.research.tournament import RankingDirection, TournamentSpec, evaluate_strategy_tournament
from autotrade.research.trials import CampaignSpec, SQLiteTrialLedger, TrialPhase, TrialSpec
from autotrade.strategy_promotion_assessment import (
    ASSESSMENT_CONTRACT_VERSION,
    ZERO_ASSESSMENT_HASH,
    SQLiteStrategyPromotionAssessmentRegistry,
    StrategyPromotionAssessmentConflict,
    StrategyPromotionAssessmentIntegrityError,
)


NOW = datetime(2026, 8, 23, 17, 30, tzinfo=timezone.utc)


def _trial(*, trial_id: str, strategy_id: str) -> TrialSpec:
    return TrialSpec(
        trial_id=trial_id,
        campaign_id="dev-campaign",
        hypothesis_id=f"hyp-{trial_id}",
        strategy_id=strategy_id,
        strategy_version="v1",
        dataset_hash="a" * 64,
        split_name="development",
        phase=TrialPhase.DEVELOPMENT,
        parameters={"variant": strategy_id},
        code_version="w80-test-code",
        holdout_authorization_id="",
    )


def _setup(tmp_path):
    db = tmp_path / "core.sqlite3"
    runtime = SQLiteRuntime(db)
    ledger = SQLiteTrialLedger(runtime)
    ledger.create_campaign(
        CampaignSpec(
            campaign_id="dev-campaign",
            family_id="family-a",
            expected_trial_ids=("dev-a", "dev-b"),
            code_version="w80-test-code",
            purpose="W80 assessment source development",
        ),
        now=NOW,
    )
    ledger.create_campaign(
        CampaignSpec(
            campaign_id="holdout-campaign",
            family_id="family-holdout",
            expected_trial_ids=("holdout-a",),
            code_version="w80-test-code",
            purpose="W80 protected holdout",
        ),
        now=NOW + timedelta(milliseconds=1),
    )
    policies = promotion.SQLiteStrategyPromotionPolicyRegistry(runtime)
    thresholds = promotion.build_strategy_promotion_threshold_policy(
        threshold_policy_id="thresholds-a",
        development_campaign_id="dev-campaign",
        holdout_campaign_id="holdout-campaign",
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
    ledger.preregister(_trial(trial_id="dev-a", strategy_id="strategy-a"), now=NOW + timedelta(seconds=1))
    ledger.preregister(_trial(trial_id="dev-b", strategy_id="strategy-b"), now=NOW + timedelta(seconds=2))
    ledger.record_completed(
        trial_id="dev-a",
        metrics={"sharpe": 1.8, "net_return": 0.12},
        p_value=Decimal("0.01"),
        now=NOW + timedelta(seconds=3),
    )
    ledger.record_completed(
        trial_id="dev-b",
        metrics={"sharpe": 1.2, "net_return": 0.08},
        p_value=Decimal("0.20"),
        now=NOW + timedelta(seconds=4),
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
    policy = promotion.build_strategy_promotion_policy(
        policy_id="promotion-a",
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
    return db, runtime, ledger, policies, tournament, policy, assessments


def _manual_view(
    policy,
    *,
    states: dict[str, promotion.PromotionGateStatus] | None = None,
    hashes: dict[str, tuple[str, ...]] | None = None,
):
    states = states or {}
    hashes = hashes or {}
    gates = []
    for index, gate_id in enumerate(promotion.REQUIRED_W79_GATE_IDS, start=1):
        status = states.get(gate_id, promotion.PromotionGateStatus.MISSING)
        evidence_hashes = hashes.get(gate_id, ())
        reasons = () if status is promotion.PromotionGateStatus.PASS else (f"{gate_id}_TEST_REASON",)
        gates.append(
            promotion.PromotionGateEvidence(
                gate_id=gate_id,
                status=status,
                reason_codes=reasons,
                evidence_hashes=evidence_hashes,
            )
        )
    gates_tuple = tuple(gates)
    statuses = {item.status for item in gates_tuple}
    if promotion.PromotionGateStatus.BLOCKED in statuses:
        state = promotion.PromotionAssessmentState.BLOCKED
    elif promotion.PromotionGateStatus.FAIL in statuses:
        state = promotion.PromotionAssessmentState.REJECTED
    elif promotion.PromotionGateStatus.MISSING in statuses:
        state = promotion.PromotionAssessmentState.INCOMPLETE
    else:
        state = promotion.PromotionAssessmentState.EVIDENCE_QUALIFIED
    values = {
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "threshold_policy_hash": policy.threshold_policy_hash,
        "selected_strategy_id": policy.selected_strategy_id,
        "selected_strategy_version": policy.selected_strategy_version,
        "gates": gates_tuple,
        "evidence_complete": all(item.status is promotion.PromotionGateStatus.PASS for item in gates_tuple),
        "assessment_state": state,
        "promotion_blockers": tuple(sorted(promotion.PERMANENT_W79_PROMOTION_BLOCKERS)),
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    return promotion.StrategyPromotionEvidenceView(
        **values,
        view_hash=promotion._hash(promotion._view_payload_from_values(values)),
    )


def _record(registry, policies, ledger, tournament, *, assessment_id="assessment-1", now=None, **kwargs):
    return registry.assess_and_record(
        assessment_id=assessment_id,
        policy_registry=policies,
        policy_id="promotion-a",
        trial_ledger=ledger,
        tournament=tournament,
        now=now or NOW + timedelta(seconds=10),
        **kwargs,
    )


def test_real_w79_incomplete_view_is_persisted_without_authority(tmp_path):
    _, _, ledger, policies, tournament, _, registry = _setup(tmp_path)
    receipt = _record(registry, policies, ledger, tournament)

    assert receipt.contract_version == ASSESSMENT_CONTRACT_VERSION
    assert receipt.ordinal == 1
    assert receipt.previous_assessment_hash == ZERO_ASSESSMENT_HASH
    assert receipt.assessment_state is promotion.PromotionAssessmentState.INCOMPLETE
    assert receipt.evidence_complete is False
    assert receipt.gates[0].status is promotion.PromotionGateStatus.PASS
    assert receipt.paper_candidate_authorized is False
    assert receipt.external_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    assert registry.get(receipt.assessment_id) == receipt
    assert registry.latest_for_policy("promotion-a") == receipt
    assert registry.list_for_policy("promotion-a") == (receipt,)


def test_same_assessment_id_is_idempotent_only_for_same_view_and_timestamp(tmp_path):
    _, _, ledger, policies, tournament, _, registry = _setup(tmp_path)
    first = _record(registry, policies, ledger, tournament)
    assert _record(registry, policies, ledger, tournament) == first

    with pytest.raises(StrategyPromotionAssessmentConflict, match="identity conflict"):
        _record(
            registry,
            policies,
            ledger,
            tournament,
            now=NOW + timedelta(seconds=11),
        )


def test_unchanged_view_cannot_be_appended_under_new_identity(tmp_path):
    _, _, ledger, policies, tournament, _, registry = _setup(tmp_path)
    _record(registry, policies, ledger, tournament)
    with pytest.raises(StrategyPromotionAssessmentConflict, match="unchanged promotion view"):
        _record(
            registry,
            policies,
            ledger,
            tournament,
            assessment_id="assessment-2",
            now=NOW + timedelta(seconds=11),
        )


def test_hash_chain_advances_when_evidence_is_added(tmp_path, monkeypatch):
    _, _, ledger, policies, tournament, policy, registry = _setup(tmp_path)
    missing = _manual_view(policy)
    development_hash = "1" * 64
    multiple_hash = "2" * 64
    later = _manual_view(
        policy,
        states={
            "DEVELOPMENT_SELECTION": promotion.PromotionGateStatus.PASS,
            "MULTIPLE_TESTING": promotion.PromotionGateStatus.PASS,
        },
        hashes={
            "DEVELOPMENT_SELECTION": (development_hash,),
            "MULTIPLE_TESTING": (multiple_hash,),
        },
    )
    views = iter((missing, later))
    monkeypatch.setattr(assessment, "evaluate_strategy_promotion", lambda **_: next(views))

    first = _record(registry, policies, ledger, tournament)
    second = _record(
        registry,
        policies,
        ledger,
        tournament,
        assessment_id="assessment-2",
        now=NOW + timedelta(seconds=11),
    )

    assert first.ordinal == 1
    assert second.ordinal == 2
    assert second.previous_assessment_hash == first.assessment_hash
    assert second.source_view_hash == later.view_hash
    assert registry.list_for_policy("promotion-a") == (first, second)


def test_evidence_hashes_cannot_disappear_from_chain(tmp_path, monkeypatch):
    _, _, ledger, policies, tournament, policy, registry = _setup(tmp_path)
    prior_hash = "3" * 64
    first_view = _manual_view(
        policy,
        states={"DEVELOPMENT_SELECTION": promotion.PromotionGateStatus.PASS},
        hashes={"DEVELOPMENT_SELECTION": (prior_hash,)},
    )
    regressed = _manual_view(
        policy,
        states={"DEVELOPMENT_SELECTION": promotion.PromotionGateStatus.PASS},
        hashes={"DEVELOPMENT_SELECTION": ()},
    )
    views = iter((first_view, regressed))
    monkeypatch.setattr(assessment, "evaluate_strategy_promotion", lambda **_: next(views))
    _record(registry, policies, ledger, tournament)

    with pytest.raises(StrategyPromotionAssessmentIntegrityError, match="evidence hashes may not regress"):
        _record(
            registry,
            policies,
            ledger,
            tournament,
            assessment_id="assessment-2",
            now=NOW + timedelta(seconds=11),
        )


def test_nonmissing_gate_cannot_regress_to_missing(tmp_path, monkeypatch):
    _, _, ledger, policies, tournament, policy, registry = _setup(tmp_path)
    first_view = _manual_view(
        policy,
        states={"MULTIPLE_TESTING": promotion.PromotionGateStatus.FAIL},
    )
    regressed = _manual_view(policy)
    views = iter((first_view, regressed))
    monkeypatch.setattr(assessment, "evaluate_strategy_promotion", lambda **_: next(views))
    _record(registry, policies, ledger, tournament)

    with pytest.raises(StrategyPromotionAssessmentIntegrityError, match="regress to MISSING"):
        _record(
            registry,
            policies,
            ledger,
            tournament,
            assessment_id="assessment-2",
            now=NOW + timedelta(seconds=11),
        )


def test_fully_green_assessment_remains_non_authoritative(tmp_path, monkeypatch):
    _, _, ledger, policies, tournament, policy, registry = _setup(tmp_path)
    hashes = {
        gate_id: (f"{index:x}" * 64,)
        for index, gate_id in enumerate(promotion.REQUIRED_W79_GATE_IDS, start=1)
    }
    green = _manual_view(
        policy,
        states={gate_id: promotion.PromotionGateStatus.PASS for gate_id in promotion.REQUIRED_W79_GATE_IDS},
        hashes=hashes,
    )
    monkeypatch.setattr(assessment, "evaluate_strategy_promotion", lambda **_: green)
    receipt = _record(registry, policies, ledger, tournament)

    assert receipt.evidence_complete is True
    assert receipt.assessment_state is promotion.PromotionAssessmentState.EVIDENCE_QUALIFIED
    assert receipt.paper_candidate_authorized is False
    assert receipt.external_execution_authorized is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"
    assert set(receipt.promotion_blockers) == set(promotion.PERMANENT_W79_PROMOTION_BLOCKERS)


def test_blocked_has_precedence_over_fail_in_durable_receipt(tmp_path, monkeypatch):
    _, _, ledger, policies, tournament, policy, registry = _setup(tmp_path)
    view = _manual_view(
        policy,
        states={
            "DEVELOPMENT_SELECTION": promotion.PromotionGateStatus.FAIL,
            "EXECUTION_SENSITIVITY": promotion.PromotionGateStatus.BLOCKED,
        },
    )
    monkeypatch.setattr(assessment, "evaluate_strategy_promotion", lambda **_: view)
    receipt = _record(registry, policies, ledger, tournament)
    assert receipt.assessment_state is promotion.PromotionAssessmentState.BLOCKED


def test_assessment_timestamp_must_advance(tmp_path, monkeypatch):
    _, _, ledger, policies, tournament, policy, registry = _setup(tmp_path)
    views = iter((_manual_view(policy), _manual_view(policy, states={"MULTIPLE_TESTING": promotion.PromotionGateStatus.PASS}, hashes={"MULTIPLE_TESTING": ("4" * 64,)})))
    monkeypatch.setattr(assessment, "evaluate_strategy_promotion", lambda **_: next(views))
    _record(registry, policies, ledger, tournament)
    with pytest.raises(StrategyPromotionAssessmentIntegrityError, match="timestamps must advance"):
        _record(
            registry,
            policies,
            ledger,
            tournament,
            assessment_id="assessment-2",
            now=NOW + timedelta(seconds=9),
        )


def test_registry_requires_same_authoritative_runtime(tmp_path):
    _, _, ledger, policies, tournament, _, registry = _setup(tmp_path / "a")
    other_runtime = SQLiteRuntime(tmp_path / "b" / "core.sqlite3")
    other_ledger = SQLiteTrialLedger(other_runtime)
    with pytest.raises(StrategyPromotionAssessmentIntegrityError, match="one authoritative SQLite"):
        registry.assess_and_record(
            assessment_id="assessment-x",
            policy_registry=policies,
            policy_id="promotion-a",
            trial_ledger=other_ledger,
            tournament=tournament,
            now=NOW + timedelta(seconds=20),
        )


def test_registry_refuses_database_without_w79_promotion_schema(tmp_path):
    runtime = SQLiteRuntime(tmp_path / "bare.sqlite3")
    with pytest.raises(StrategyPromotionAssessmentIntegrityError, match="requires the initialized W79"):
        SQLiteStrategyPromotionAssessmentRegistry(runtime)


def test_receipt_and_sqlite_tampering_fail_closed(tmp_path):
    db, _, ledger, policies, tournament, _, registry = _setup(tmp_path)
    receipt = _record(registry, policies, ledger, tournament)
    with pytest.raises(StrategyPromotionAssessmentIntegrityError, match="may not authorize PAPER"):
        replace(receipt, paper_candidate_authorized=True)
    with pytest.raises(StrategyPromotionAssessmentIntegrityError, match="hash mismatch"):
        replace(receipt, assessment_hash="f" * 64)

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE strategy_promotion_assessments SET source_view_hash = ? WHERE assessment_id = ?",
        ("e" * 64, receipt.assessment_id),
    )
    conn.commit()
    conn.close()
    with pytest.raises(StrategyPromotionAssessmentIntegrityError, match="SQLite column mismatch"):
        registry.get(receipt.assessment_id)


def test_chain_predecessor_tampering_is_detected(tmp_path, monkeypatch):
    db, _, ledger, policies, tournament, policy, registry = _setup(tmp_path)
    views = iter((_manual_view(policy), _manual_view(policy, states={"MULTIPLE_TESTING": promotion.PromotionGateStatus.PASS}, hashes={"MULTIPLE_TESTING": ("5" * 64,)})))
    monkeypatch.setattr(assessment, "evaluate_strategy_promotion", lambda **_: next(views))
    _record(registry, policies, ledger, tournament)
    second = _record(
        registry,
        policies,
        ledger,
        tournament,
        assessment_id="assessment-2",
        now=NOW + timedelta(seconds=11),
    )
    payload = second.to_dict()
    payload["previous_assessment_hash"] = ZERO_ASSESSMENT_HASH
    payload_without_hash = dict(payload)
    payload_without_hash.pop("assessment_hash")
    # Deliberately recompute a valid content hash so chain continuity, not receipt self-hash, catches it.
    payload["assessment_hash"] = assessment._hash(payload_without_hash)
    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE strategy_promotion_assessments
        SET assessment_hash = ?, previous_assessment_hash = ?, receipt_json = ?
        WHERE assessment_id = ?
        """,
        (
            payload["assessment_hash"],
            ZERO_ASSESSMENT_HASH,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False),
            second.assessment_id,
        ),
    )
    conn.commit()
    conn.close()
    with pytest.raises(StrategyPromotionAssessmentIntegrityError, match="predecessor hash discontinuity"):
        registry.list_for_policy("promotion-a")
