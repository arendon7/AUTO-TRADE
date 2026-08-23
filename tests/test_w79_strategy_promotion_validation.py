from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrade.persistence import SQLiteRuntime
from autotrade.strategy_lab_promotion import (
    PERMANENT_W79_PROMOTION_BLOCKERS,
    REQUIRED_W79_GATE_IDS,
    PromotionAssessmentState,
    PromotionGateEvidence,
    PromotionGateStatus,
    SQLiteStrategyPromotionPolicyRegistry,
    StrategyPromotionEvidenceView,
    StrategyPromotionIntegrityError,
    StrategyPromotionPolicy,
    _assessment_state,
    _canonical_json,
    _hash,
    _policy_payload_from_values,
    _view_payload_from_values,
)


def _valid_policy() -> StrategyPromotionPolicy:
    values = {
        "policy_id": "policy-a",
        "development_campaign_id": "dev-a",
        "holdout_campaign_id": "holdout-a",
        "holdout_trial_id": "holdout-trial-a",
        "selected_trial_id": "dev-trial-a",
        "selected_trial_fingerprint": "1" * 64,
        "selected_strategy_id": "strategy-a",
        "selected_strategy_version": "v1",
        "tournament_fingerprint": "2" * 64,
        "max_holm_adjusted_p": Decimal("0.05"),
        "min_holdout_net_return": Decimal("0.01"),
        "max_holdout_drawdown": Decimal("0.10"),
        "min_holdout_fills": 5,
        "min_execution_fill_ratio": Decimal("0.50"),
        "max_execution_adverse_slippage_bps": Decimal("10"),
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    return StrategyPromotionPolicy(
        **values,
        policy_hash=_hash(_policy_payload_from_values(values)),
    )


def _pass_gate(gate_id: str, digit: str) -> PromotionGateEvidence:
    return PromotionGateEvidence(
        gate_id=gate_id,
        status=PromotionGateStatus.PASS,
        reason_codes=(),
        evidence_hashes=(digit * 64,),
    )


def _valid_gates() -> tuple[PromotionGateEvidence, ...]:
    return tuple(
        _pass_gate(gate_id, str(index))
        for index, gate_id in enumerate(REQUIRED_W79_GATE_IDS, start=3)
    )


def _valid_view() -> StrategyPromotionEvidenceView:
    gates = _valid_gates()
    values = {
        "policy_id": "policy-a",
        "policy_hash": "7" * 64,
        "selected_strategy_id": "strategy-a",
        "selected_strategy_version": "v1",
        "gates": gates,
        "evidence_complete": True,
        "assessment_state": PromotionAssessmentState.EVIDENCE_QUALIFIED,
        "promotion_blockers": tuple(sorted(PERMANENT_W79_PROMOTION_BLOCKERS)),
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    return StrategyPromotionEvidenceView(
        **values,
        view_hash=_hash(_view_payload_from_values(values)),
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"policy_id": ""}, "canonical identifier"),
        ({"development_campaign_id": "holdout-a"}, "campaigns must be distinct"),
        ({"selected_trial_fingerprint": "BAD"}, "lowercase sha256"),
        ({"max_holm_adjusted_p": 0.05}, "finite Decimal"),
        ({"max_holm_adjusted_p": Decimal("NaN")}, "finite Decimal"),
        ({"max_holm_adjusted_p": Decimal("1.1")}, "within"),
        ({"min_holdout_net_return": Decimal("-1")}, "greater than -1"),
        ({"max_holdout_drawdown": Decimal("1.1")}, "within"),
        ({"min_holdout_fills": True}, "must be integer"),
        ({"min_holdout_fills": 0}, "must be >=1"),
        ({"min_execution_fill_ratio": Decimal("-0.1")}, "within"),
        ({"max_execution_adverse_slippage_bps": Decimal("-1")}, "non-negative"),
        ({"external_execution_authorized": True}, "may not grant"),
        ({"live_trading": "ENABLED"}, "may not grant"),
        ({"policy_hash": "0" * 64}, "hash mismatch"),
    ),
)
def test_policy_constructor_rejects_invalid_or_tampered_values(changes, message):
    policy = _valid_policy()
    with pytest.raises(StrategyPromotionIntegrityError, match=message):
        replace(policy, **changes)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        (
            {
                "gate_id": "",
                "status": PromotionGateStatus.FAIL,
                "reason_codes": ("X",),
                "evidence_hashes": (),
            },
            "gate_id",
        ),
        (
            {
                "gate_id": "GATE",
                "status": "PASS",
                "reason_codes": (),
                "evidence_hashes": (),
            },
            "PromotionGateStatus",
        ),
        (
            {
                "gate_id": "GATE",
                "status": PromotionGateStatus.FAIL,
                "reason_codes": ("Z", "A"),
                "evidence_hashes": (),
            },
            "unique sorted",
        ),
        (
            {
                "gate_id": "GATE",
                "status": PromotionGateStatus.FAIL,
                "reason_codes": ("",),
                "evidence_hashes": (),
            },
            "reason code is invalid",
        ),
        (
            {
                "gate_id": "GATE",
                "status": PromotionGateStatus.FAIL,
                "reason_codes": ("X",),
                "evidence_hashes": ("b" * 64, "a" * 64),
            },
            "unique sorted",
        ),
        (
            {
                "gate_id": "GATE",
                "status": PromotionGateStatus.FAIL,
                "reason_codes": ("X",),
                "evidence_hashes": ("BAD",),
            },
            "lowercase sha256",
        ),
        (
            {
                "gate_id": "GATE",
                "status": PromotionGateStatus.PASS,
                "reason_codes": ("X",),
                "evidence_hashes": (),
            },
            "PASS gate",
        ),
    ),
)
def test_gate_constructor_rejects_noncanonical_evidence(kwargs, message):
    with pytest.raises(StrategyPromotionIntegrityError, match=message):
        PromotionGateEvidence(**kwargs)


def test_view_requires_exact_gate_and_blocker_sets():
    view = _valid_view()
    with pytest.raises(StrategyPromotionIntegrityError, match="exact W79 gate set"):
        replace(view, gates=view.gates[:-1])
    with pytest.raises(StrategyPromotionIntegrityError, match="sorted by gate_id"):
        replace(view, gates=tuple(reversed(view.gates)))
    with pytest.raises(StrategyPromotionIntegrityError, match="canonical blocker set"):
        replace(view, promotion_blockers=("SOMETHING_ELSE",))


def test_view_consistency_and_authority_are_fail_closed():
    view = _valid_view()
    blocked_gate = PromotionGateEvidence(
        gate_id="EXECUTION_SENSITIVITY",
        status=PromotionGateStatus.BLOCKED,
        reason_codes=("IDENTITY_MISMATCH",),
        evidence_hashes=("8" * 64,),
    )
    blocked_gates = tuple(
        blocked_gate if item.gate_id == blocked_gate.gate_id else item
        for item in view.gates
    )
    with pytest.raises(StrategyPromotionIntegrityError, match="evidence_complete"):
        replace(view, gates=blocked_gates)
    with pytest.raises(StrategyPromotionIntegrityError, match="assessment_state"):
        replace(view, assessment_state=PromotionAssessmentState.REJECTED)
    with pytest.raises(StrategyPromotionIntegrityError, match="PAPER candidate"):
        replace(view, paper_candidate_authorized=True)
    with pytest.raises(StrategyPromotionIntegrityError, match="execution/LIVE"):
        replace(view, live_trading="ENABLED")
    with pytest.raises(StrategyPromotionIntegrityError, match="view hash mismatch"):
        replace(view, view_hash="0" * 64)


def test_blocked_integrity_dominates_statistical_failure():
    gates = (
        PromotionGateEvidence(
            gate_id="A",
            status=PromotionGateStatus.FAIL,
            reason_codes=("THRESHOLD",),
            evidence_hashes=(),
        ),
        PromotionGateEvidence(
            gate_id="B",
            status=PromotionGateStatus.BLOCKED,
            reason_codes=("INTEGRITY",),
            evidence_hashes=(),
        ),
    )
    assert _assessment_state(gates) is PromotionAssessmentState.BLOCKED


def _insert_policy_row(runtime: SQLiteRuntime, policy: StrategyPromotionPolicy) -> None:
    conn = runtime.connect()
    try:
        conn.execute(
            """
            INSERT INTO strategy_promotion_policies(
                policy_id, policy_hash, development_campaign_id,
                holdout_campaign_id, registered_at, policy_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                policy.policy_id,
                policy.policy_hash,
                policy.development_campaign_id,
                policy.holdout_campaign_id,
                datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc).isoformat(),
                _canonical_json(policy.to_dict()),
            ),
        )
    finally:
        conn.close()


def test_registry_get_and_list_verify_complete_durable_row(tmp_path):
    runtime = SQLiteRuntime(tmp_path / "promotion-integrity.db")
    registry = SQLiteStrategyPromotionPolicyRegistry(runtime)
    policy = _valid_policy()
    _insert_policy_row(runtime, policy)

    assert registry.get(policy.policy_id) == policy
    assert registry.list_policies() == (policy,)


@pytest.mark.parametrize(
    ("column", "value", "message"),
    (
        ("policy_hash", "9" * 64, "does not match"),
        ("development_campaign_id", "tampered-dev", "does not match"),
        ("holdout_campaign_id", "tampered-holdout", "does not match"),
        ("registered_at", "2026-08-23T20:00:00", "timezone-aware"),
        ("registered_at", "not-a-time", "registered_at is invalid"),
        ("policy_json", "{", "policy JSON is invalid"),
    ),
)
def test_registry_detects_side_column_json_and_timestamp_tampering(
    tmp_path, column, value, message
):
    runtime = SQLiteRuntime(tmp_path / f"tamper-{column}.db")
    registry = SQLiteStrategyPromotionPolicyRegistry(runtime)
    policy = _valid_policy()
    _insert_policy_row(runtime, policy)
    conn = runtime.connect()
    try:
        conn.execute(
            f"UPDATE strategy_promotion_policies SET {column} = ? WHERE policy_id = ?",
            (value, policy.policy_id),
        )
    finally:
        conn.close()

    with pytest.raises(StrategyPromotionIntegrityError, match=message):
        registry.get(policy.policy_id)
