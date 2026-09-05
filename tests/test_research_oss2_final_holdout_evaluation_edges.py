from __future__ import annotations

from copy import copy
from datetime import timedelta
import sqlite3

import pytest

from autotrade.research.oss2_final_holdout_evaluation import (
    OSS2FinalHoldoutEvaluationGovernanceError,
    OSS2FinalHoldoutEvaluationIntegrityError,
    ProtectedOSS2FinalHoldout,
    _verify_candidate_binding,
    _verify_protocol,
    read_oss2_selected_candidate_read_only,
    read_oss2h_evaluation_read_only,
)
from autotrade.research.registry import HoldoutPermit

from test_research_oss2_final_holdout_evaluation import _setup, _universe


def _mutated(value, **changes):
    clone = copy(value)
    for name, replacement in changes.items():
        object.__setattr__(clone, name, replacement)
    return clone


def test_verify_protocol_rejects_wrong_type_and_contract_version(tmp_path, now):
    with pytest.raises(TypeError, match="protocol must"):
        _verify_protocol(object())

    _, protocol, _, _ = _setup(tmp_path, now)
    with pytest.raises(OSS2FinalHoldoutEvaluationGovernanceError, match="canonical OSS-2G"):
        _verify_protocol(_mutated(protocol, contract_version="OSS2G_WRONG"))


def test_verify_protocol_rejects_policy_and_one_shot_drift(tmp_path, now):
    _, protocol, _, _ = _setup(tmp_path, now)

    with pytest.raises(OSS2FinalHoldoutEvaluationGovernanceError, match="policy drifted"):
        _verify_protocol(_mutated(protocol, protocol_policy_fingerprint="f" * 64))

    for changes in (
        {"split_name": "DEVELOPMENT"},
        {"permit_purpose": "other"},
        {"max_evaluations": 2},
        {"retuning_allowed": True},
        {"reselection_allowed": True},
        {"second_attempt_allowed": True},
        {"failure_is_terminal": False},
    ):
        with pytest.raises(OSS2FinalHoldoutEvaluationGovernanceError, match="one-shot contract"):
            _verify_protocol(_mutated(protocol, **changes))


def test_verify_protocol_rejects_consumed_and_operational_authority(tmp_path, now):
    _, protocol, _, _ = _setup(tmp_path, now)

    for changes in (
        {"final_holdout_observed": True},
        {"final_holdout_consumed": True},
    ):
        with pytest.raises(OSS2FinalHoldoutEvaluationGovernanceError, match="unconsumed/unobserved"):
            _verify_protocol(_mutated(protocol, **changes))

    for changes in (
        {"paper_execution_authorized": True},
        {"capital_authority": "SOME"},
    ):
        with pytest.raises(OSS2FinalHoldoutEvaluationGovernanceError, match="forbidden authority"):
            _verify_protocol(_mutated(protocol, **changes))

    with pytest.raises(OSS2FinalHoldoutEvaluationGovernanceError, match="LIVE"):
        _verify_protocol(_mutated(protocol, live_trading="ENABLED"))


def test_verify_candidate_binding_rejects_type_campaign_and_trial_drift(tmp_path, now):
    _, protocol, candidate, _ = _setup(tmp_path, now)

    with pytest.raises(TypeError, match="candidate must"):
        _verify_candidate_binding(protocol=protocol, candidate=object())

    with pytest.raises(OSS2FinalHoldoutEvaluationGovernanceError, match="campaign differs"):
        _verify_candidate_binding(
            protocol=protocol,
            candidate=_mutated(candidate, campaign_id="another-campaign"),
        )

    with pytest.raises(OSS2FinalHoldoutEvaluationGovernanceError, match="trial differs"):
        _verify_candidate_binding(
            protocol=protocol,
            candidate=_mutated(candidate, trial_id="another-trial"),
        )


def test_evaluator_rejects_nonprotected_holdout_before_consumption(tmp_path, now):
    db, protocol, candidate, registry = _setup(tmp_path, now)

    with pytest.raises(TypeError, match="ProtectedOSS2FinalHoldout"):
        registry.evaluate_and_record(
            evaluation_id="oss2h-bad-holdout",
            protocol=protocol,
            candidate=candidate,
            holdout=object(),
            now=now + timedelta(seconds=3),
        )

    conn = sqlite3.connect(db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM holdout_permits WHERE permit_id = ?",
            (protocol.holdout_authorization_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_holdout_permit_constructor_rejects_non_final_validation_purpose():
    with pytest.raises(ValueError, match="restricted to final_validation"):
        HoldoutPermit(
            permit_id="authorization-1",
            issued_by="OSS2H_FINAL_HOLDOUT_EVALUATOR",
            purpose="other",
        )


def test_protected_checkout_rejects_wrong_permit_identity(tmp_path, now):
    _, protocol, _, _ = _setup(tmp_path, now)
    holdout = ProtectedOSS2FinalHoldout(_universe(now + timedelta(days=1)))

    for permit in (
        HoldoutPermit(
            permit_id="wrong-authorization",
            issued_by="OSS2H_FINAL_HOLDOUT_EVALUATOR",
            purpose="final_validation",
        ),
        HoldoutPermit(
            permit_id=protocol.holdout_authorization_id,
            issued_by="WRONG_ISSUER",
            purpose="final_validation",
        ),
    ):
        with pytest.raises(OSS2FinalHoldoutEvaluationGovernanceError, match="exact consumed"):
            holdout._checkout(
                permit=permit,
                expected_authorization_id=protocol.holdout_authorization_id,
            )


def test_selected_candidate_reader_fails_closed_on_missing_sources(tmp_path, now):
    _, protocol, _, _ = _setup(tmp_path / "source", now)

    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="does not exist"):
        read_oss2_selected_candidate_read_only(
            tmp_path / "missing.db",
            protocol=protocol,
        )

    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="research_trials table"):
        read_oss2_selected_candidate_read_only(empty, protocol=protocol)

    missing_trial = tmp_path / "missing-trial.db"
    conn = sqlite3.connect(missing_trial)
    try:
        conn.execute(
            "CREATE TABLE research_trials("
            "trial_id TEXT, campaign_id TEXT, fingerprint TEXT, spec_json TEXT, "
            "status TEXT, result_hash TEXT)"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="trial is missing"):
        read_oss2_selected_candidate_read_only(missing_trial, protocol=protocol)


def test_terminal_reader_fails_closed_on_missing_registry_sources(tmp_path):
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="does not exist"):
        read_oss2h_evaluation_read_only(tmp_path / "missing.db", campaign_id="campaign-1")

    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    with pytest.raises(OSS2FinalHoldoutEvaluationIntegrityError, match="durable tables"):
        read_oss2h_evaluation_read_only(empty, campaign_id="campaign-1")
