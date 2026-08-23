from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
import sqlite3

import pytest

import autotrade.strategy_lab_promotion as promotion
import autotrade.strategy_promotion_assessment as writer
import autotrade.strategy_promotion_assessment_read_model as reader
from autotrade.persistence import SQLiteRuntime
from autotrade.research.tournament import RankingDirection, TournamentSpec, evaluate_strategy_tournament
from autotrade.research.trials import CampaignSpec, SQLiteTrialLedger, TrialPhase, TrialSpec
from autotrade.strategy_promotion_assessment import SQLiteStrategyPromotionAssessmentRegistry
from autotrade.strategy_promotion_assessment_read_model import (
    PromotionAssessmentReadIntegrityError,
    PromotionAssessmentReadMissing,
    PromotionAssessmentReadModel,
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
        code_version="w80-reader-test",
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
            code_version="w80-reader-test",
            purpose="reader development source",
        ),
        now=NOW,
    )
    ledger.create_campaign(
        CampaignSpec(
            campaign_id="holdout-campaign",
            family_id="family-holdout",
            expected_trial_ids=("holdout-a",),
            code_version="w80-reader-test",
            purpose="reader protected holdout",
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
    return db, ledger, policies, tournament, policy, assessments


def _record(registry, policies, ledger, tournament, *, assessment_id="assessment-1", now=None):
    return registry.assess_and_record(
        assessment_id=assessment_id,
        policy_registry=policies,
        policy_id="promotion-a",
        trial_ledger=ledger,
        tournament=tournament,
        now=now or NOW + timedelta(seconds=10),
    )


def _manual_view(policy, *, states=None, hashes=None):
    states = states or {}
    hashes = hashes or {}
    gates = []
    for gate_id in promotion.REQUIRED_W79_GATE_IDS:
        status = states.get(gate_id, promotion.PromotionGateStatus.MISSING)
        reasons = () if status is promotion.PromotionGateStatus.PASS else (f"{gate_id}_TEST_REASON",)
        gates.append(
            promotion.PromotionGateEvidence(
                gate_id=gate_id,
                status=status,
                reason_codes=reasons,
                evidence_hashes=hashes.get(gate_id, ()),
            )
        )
    gates = tuple(gates)
    statuses = {item.status for item in gates}
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
        "gates": gates,
        "evidence_complete": all(item.status is promotion.PromotionGateStatus.PASS for item in gates),
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


def _rewrite_receipt(db, sequence, mutate, *, update_columns=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM strategy_promotion_assessments WHERE sequence=?",
            (sequence,),
        ).fetchone()
        receipt = json.loads(row["receipt_json"])
        mutate(receipt)
        payload = dict(receipt)
        payload.pop("assessment_hash", None)
        receipt["assessment_hash"] = reader._hash(payload)
        updates = {
            "assessment_hash": receipt["assessment_hash"],
            "receipt_json": reader._canonical_json(receipt),
        }
        for key in update_columns:
            updates[key] = receipt[key]
        assignments = ", ".join(f"{key}=?" for key in updates)
        conn.execute(
            f"UPDATE strategy_promotion_assessments SET {assignments} WHERE sequence=?",
            (*updates.values(), sequence),
        )
        conn.commit()
    finally:
        conn.close()


def test_missing_database_and_symlink_fail_closed(tmp_path):
    with pytest.raises(PromotionAssessmentReadMissing, match="missing"):
        PromotionAssessmentReadModel(tmp_path / "missing.sqlite3")

    real = tmp_path / "real.sqlite3"
    sqlite3.connect(real).close()
    link = tmp_path / "linked.sqlite3"
    os.symlink(real, link)
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="symlink"):
        PromotionAssessmentReadModel(link)


def test_empty_database_projects_explicit_no_assessment_state_without_mutation(tmp_path):
    db = tmp_path / "core.sqlite3"
    sqlite3.connect(db).close()
    before = db.read_bytes()
    snapshot = PromotionAssessmentReadModel(db).snapshot(now=NOW)
    after = db.read_bytes()

    assert snapshot.assessment_evidence_state == "NO_DURABLE_W80_ASSESSMENT"
    assert snapshot.journal_schema_present is False
    assert snapshot.assessments == ()
    assert snapshot.latest_assessments == ()
    assert before == after
    assert snapshot.to_dict()["paper_candidate_authorized"] is False
    assert snapshot.to_dict()["external_execution_authorized"] is False
    assert snapshot.to_dict()["capital_authority"] == "NONE"
    assert snapshot.to_dict()["live_trading"] == "BLOCKED"
    assert snapshot.to_dict()["broker_network_used"] is False
    assert snapshot.to_dict()["broker_write_performed"] is False
    assert snapshot.to_dict()["credentials_used"] is False


def test_writer_receipt_is_independently_read_and_provenance_bound(tmp_path):
    db, ledger, policies, tournament, _, registry = _setup(tmp_path)
    receipt = _record(registry, policies, ledger, tournament)
    before = db.read_bytes()

    snapshot = PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))

    assert db.read_bytes() == before
    assert snapshot.assessment_evidence_state == "DURABLE_W80_ASSESSMENT"
    assert snapshot.journal_schema_present is True
    assert len(snapshot.assessments) == 1
    view = snapshot.assessments[0]
    assert view.assessment_id == receipt.assessment_id
    assert view.assessment_hash == receipt.assessment_hash
    assert view.ordinal == 1
    assert view.previous_assessment_hash == reader.ZERO_ASSESSMENT_HASH
    assert view.assessment_state is promotion.PromotionAssessmentState.INCOMPLETE
    assert view.evidence_complete is False
    assert snapshot.latest_assessments == (view,)
    assert snapshot.provenance_hash == reader._hash(snapshot._payload())


def test_two_receipts_preserve_chain_and_latest_projection(tmp_path, monkeypatch):
    db, ledger, policies, tournament, policy, registry = _setup(tmp_path)
    first_view = _manual_view(policy)
    second_view = _manual_view(
        policy,
        states={"MULTIPLE_TESTING": promotion.PromotionGateStatus.PASS},
        hashes={"MULTIPLE_TESTING": ("1" * 64,)},
    )
    views = iter((first_view, second_view))
    monkeypatch.setattr(writer, "evaluate_strategy_promotion", lambda **_: next(views))
    first = _record(registry, policies, ledger, tournament)
    second = _record(
        registry,
        policies,
        ledger,
        tournament,
        assessment_id="assessment-2",
        now=NOW + timedelta(seconds=11),
    )

    snapshot = PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))
    assert [item.ordinal for item in snapshot.assessments] == [1, 2]
    assert snapshot.assessments[1].previous_assessment_hash == first.assessment_hash
    assert snapshot.assessments[1].assessment_hash == second.assessment_hash
    assert snapshot.latest_assessments == (snapshot.assessments[1],)


def test_naive_snapshot_time_is_rejected(tmp_path):
    db = tmp_path / "core.sqlite3"
    sqlite3.connect(db).close()
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="timezone-aware"):
        PromotionAssessmentReadModel(db).snapshot(now=datetime(2026, 8, 23, 17, 30))


def test_receipt_json_and_hash_tampering_fail_closed(tmp_path):
    db, ledger, policies, tournament, _, registry = _setup(tmp_path)
    _record(registry, policies, ledger, tournament)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE strategy_promotion_assessments SET receipt_json=? WHERE sequence=1",
            ("{broken",),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="JSON is invalid"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


def test_receipt_hash_mismatch_fails_closed(tmp_path):
    db, ledger, policies, tournament, _, registry = _setup(tmp_path)
    _record(registry, policies, ledger, tournament)
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT receipt_json FROM strategy_promotion_assessments").fetchone()
        receipt = json.loads(row[0])
        receipt["assessment_state"] = "REJECTED"
        conn.execute(
            "UPDATE strategy_promotion_assessments SET receipt_json=? WHERE sequence=1",
            (reader._canonical_json(receipt),),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="hash mismatch"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("paper_candidate_authorized", True, "PAPER/execution"),
        ("external_execution_authorized", True, "PAPER/execution"),
        ("capital_authority", "SOME", "capital/LIVE"),
        ("live_trading", "ENABLED", "capital/LIVE"),
    ],
)
def test_authority_tampering_rehashed_still_fails_closed(tmp_path, field, value, message):
    db, ledger, policies, tournament, _, registry = _setup(tmp_path)
    _record(registry, policies, ledger, tournament)
    _rewrite_receipt(db, 1, lambda receipt: receipt.__setitem__(field, value))
    with pytest.raises(PromotionAssessmentReadIntegrityError, match=message):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


def test_sqlite_side_column_mismatch_fails_closed(tmp_path):
    db, ledger, policies, tournament, _, registry = _setup(tmp_path)
    _record(registry, policies, ledger, tournament)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE strategy_promotion_assessments SET source_view_hash=? WHERE sequence=1",
            ("f" * 64,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="column mismatch"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


def test_noncanonical_gate_status_and_fields_fail_closed(tmp_path):
    db, ledger, policies, tournament, _, registry = _setup(tmp_path)
    _record(registry, policies, ledger, tournament)

    def mutate(receipt):
        receipt["gates"][0]["status"] = "GREEN"

    _rewrite_receipt(db, 1, mutate)
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="gate status"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


def test_pass_gate_cannot_carry_reason_even_with_valid_hash(tmp_path):
    db, ledger, policies, tournament, _, registry = _setup(tmp_path)
    _record(registry, policies, ledger, tournament)

    def mutate(receipt):
        receipt["gates"][0]["status"] = "PASS"
        receipt["gates"][0]["reason_codes"] = ["SHOULD_NOT_EXIST"]

    _rewrite_receipt(db, 1, mutate)
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="PASS gate"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


def test_nonpass_gate_requires_reason(tmp_path):
    db, ledger, policies, tournament, _, registry = _setup(tmp_path)
    _record(registry, policies, ledger, tournament)

    def mutate(receipt):
        receipt["gates"][1]["reason_codes"] = []

    _rewrite_receipt(db, 1, mutate)
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="requires an explicit reason"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


def test_gate_set_order_and_evidence_complete_are_revalidated(tmp_path):
    db, ledger, policies, tournament, _, registry = _setup(tmp_path)
    _record(registry, policies, ledger, tournament)

    def mutate(receipt):
        receipt["gates"] = list(reversed(receipt["gates"]))

    _rewrite_receipt(db, 1, mutate)
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="gate set/order"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


def test_assessment_state_is_recomputed_from_gates(tmp_path):
    db, ledger, policies, tournament, _, registry = _setup(tmp_path)
    _record(registry, policies, ledger, tournament)
    _rewrite_receipt(db, 1, lambda receipt: receipt.__setitem__("assessment_state", "REJECTED"))
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="state does not match"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


def test_blocker_set_is_revalidated(tmp_path):
    db, ledger, policies, tournament, _, registry = _setup(tmp_path)
    _record(registry, policies, ledger, tournament)
    _rewrite_receipt(db, 1, lambda receipt: receipt.__setitem__("promotion_blockers", []))
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="blocker set"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


def test_chain_ordinal_and_predecessor_discontinuity_fail_closed(tmp_path, monkeypatch):
    db, ledger, policies, tournament, policy, registry = _setup(tmp_path)
    views = iter(
        (
            _manual_view(policy),
            _manual_view(
                policy,
                states={"MULTIPLE_TESTING": promotion.PromotionGateStatus.PASS},
                hashes={"MULTIPLE_TESTING": ("2" * 64,)},
            ),
        )
    )
    monkeypatch.setattr(writer, "evaluate_strategy_promotion", lambda **_: next(views))
    _record(registry, policies, ledger, tournament)
    _record(registry, policies, ledger, tournament, assessment_id="assessment-2", now=NOW + timedelta(seconds=11))
    _rewrite_receipt(db, 2, lambda receipt: receipt.__setitem__("ordinal", 3))
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="ordinal discontinuity"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


def test_chain_timestamp_regression_fails_closed(tmp_path, monkeypatch):
    db, ledger, policies, tournament, policy, registry = _setup(tmp_path)
    views = iter(
        (
            _manual_view(policy),
            _manual_view(
                policy,
                states={"MULTIPLE_TESTING": promotion.PromotionGateStatus.PASS},
                hashes={"MULTIPLE_TESTING": ("3" * 64,)},
            ),
        )
    )
    monkeypatch.setattr(writer, "evaluate_strategy_promotion", lambda **_: next(views))
    _record(registry, policies, ledger, tournament)
    _record(registry, policies, ledger, tournament, assessment_id="assessment-2", now=NOW + timedelta(seconds=11))

    def mutate(receipt):
        receipt["assessed_at"] = (NOW + timedelta(seconds=9)).isoformat()

    _rewrite_receipt(db, 2, mutate, update_columns=("assessed_at",))
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="timestamp regression"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


def test_chain_frozen_identity_change_fails_closed(tmp_path, monkeypatch):
    db, ledger, policies, tournament, policy, registry = _setup(tmp_path)
    views = iter(
        (
            _manual_view(policy),
            _manual_view(
                policy,
                states={"MULTIPLE_TESTING": promotion.PromotionGateStatus.PASS},
                hashes={"MULTIPLE_TESTING": ("4" * 64,)},
            ),
        )
    )
    monkeypatch.setattr(writer, "evaluate_strategy_promotion", lambda **_: next(views))
    _record(registry, policies, ledger, tournament)
    _record(registry, policies, ledger, tournament, assessment_id="assessment-2", now=NOW + timedelta(seconds=11))
    _rewrite_receipt(db, 2, lambda receipt: receipt.__setitem__("selected_strategy_version", "v2"))
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="changed frozen identity"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


def test_chain_evidence_regression_fails_closed(tmp_path, monkeypatch):
    db, ledger, policies, tournament, policy, registry = _setup(tmp_path)
    first_hash = "5" * 64
    second_hash = "6" * 64
    views = iter(
        (
            _manual_view(
                policy,
                states={"MULTIPLE_TESTING": promotion.PromotionGateStatus.PASS},
                hashes={"MULTIPLE_TESTING": (first_hash,)},
            ),
            _manual_view(
                policy,
                states={"MULTIPLE_TESTING": promotion.PromotionGateStatus.PASS},
                hashes={"MULTIPLE_TESTING": (first_hash, second_hash)},
            ),
        )
    )
    monkeypatch.setattr(writer, "evaluate_strategy_promotion", lambda **_: next(views))
    _record(registry, policies, ledger, tournament)
    _record(registry, policies, ledger, tournament, assessment_id="assessment-2", now=NOW + timedelta(seconds=11))

    def mutate(receipt):
        gate = next(item for item in receipt["gates"] if item["gate_id"] == "MULTIPLE_TESTING")
        gate["evidence_hashes"] = [second_hash]

    _rewrite_receipt(db, 2, mutate)
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="evidence regressed"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))


def test_chain_gate_cannot_regress_to_missing(tmp_path, monkeypatch):
    db, ledger, policies, tournament, policy, registry = _setup(tmp_path)
    views = iter(
        (
            _manual_view(policy, states={"MULTIPLE_TESTING": promotion.PromotionGateStatus.FAIL}),
            _manual_view(policy, states={"MULTIPLE_TESTING": promotion.PromotionGateStatus.FAIL, "FINAL_HOLDOUT": promotion.PromotionGateStatus.FAIL}),
        )
    )
    monkeypatch.setattr(writer, "evaluate_strategy_promotion", lambda **_: next(views))
    _record(registry, policies, ledger, tournament)
    _record(registry, policies, ledger, tournament, assessment_id="assessment-2", now=NOW + timedelta(seconds=11))

    def mutate(receipt):
        gate = next(item for item in receipt["gates"] if item["gate_id"] == "MULTIPLE_TESTING")
        gate["status"] = "MISSING"
        gate["reason_codes"] = ["EVIDENCE_MISSING"]
        receipt["assessment_state"] = "REJECTED"

    _rewrite_receipt(db, 2, mutate)
    with pytest.raises(PromotionAssessmentReadIntegrityError, match="regressed to MISSING"):
        PromotionAssessmentReadModel(db).snapshot(now=NOW + timedelta(seconds=20))
