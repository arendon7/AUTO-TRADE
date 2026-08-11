from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_operator_decision import (
    PaperOperatorDecisionConflict,
    PaperOperatorDecisionContext,
    PaperOperatorDecisionExpired,
    PaperOperatorDecisionIntegrityError,
    PaperOperatorDecisionStatus,
    SQLitePaperOperatorDecisionRegistry,
    operator_confirmation_challenge,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_paper_writer import NOW, stack


def evidence(tmp_path, *, attempt_id="writer-attempt-001"):
    values = stack(tmp_path / "base")
    context = PaperOperatorDecisionContext.from_evidence(
        account_attestation=values["attestation"],
        expected_bracket=values["expected"],
        approval=values["approval"],
        binding=values["binding"],
        external_handoff=values["handoff"],
        attempt_id=attempt_id,
    )
    registry = SQLitePaperOperatorDecisionRegistry(
        SQLiteRuntime(tmp_path / "operator.sqlite")
    )
    return values, context, registry


def issue(registry, context, *, operator_id="operator:arendon7", issued_at=None):
    issued_at = issued_at or NOW + timedelta(milliseconds=200)
    return registry.record_operator_approval(
        context=context,
        operator_id=operator_id,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=30),
    )


def test_context_binds_exact_prepared_canary_and_has_deterministic_challenge(tmp_path) -> None:
    values, context, _ = evidence(tmp_path)
    assert context.environment == "PAPER"
    assert context.order_id == values["binding"].order_id
    assert context.client_order_id == values["binding"].client_order_id
    assert context.binding_hash == values["binding"].fingerprint
    assert context.bracket_payload_hash == values["expected"].payload_hash
    assert context.canary_approval_hash == values["approval"].approval_hash
    assert context.oms_handoff_hash == values["handoff"].handoff_hash
    assert context.notional == values["approval"].notional
    assert context.attempt_id == "writer-attempt-001"
    assert operator_confirmation_challenge(context) == f"APPROVE PAPER {context.preparation_hash[:12]}"
    assert PaperOperatorDecisionContext.from_dict(context.to_dict()) == context


def test_context_cross_evidence_mismatch_fails_closed(tmp_path) -> None:
    values, _, _ = evidence(tmp_path)
    wrong_binding = replace(
        values["binding"],
        account_attestation_fingerprint="f" * 64,
    )
    with pytest.raises(ValueError, match="binding/account"):
        PaperOperatorDecisionContext.from_evidence(
            account_attestation=values["attestation"],
            expected_bracket=values["expected"],
            approval=values["approval"],
            binding=wrong_binding,
            external_handoff=values["handoff"],
            attempt_id="writer-attempt-001",
        )


def test_issue_is_durable_idempotent_and_conflicting_reissue_is_rejected(tmp_path) -> None:
    _, context, registry = evidence(tmp_path)
    first = issue(registry, context)
    second = issue(registry, context)
    assert first == second
    assert first.status is PaperOperatorDecisionStatus.ISSUED
    assert first.decision.source == "HUMAN_OPERATOR"
    assert first.decision.action == "APPROVE_SINGLE_PAPER_CANARY"

    with pytest.raises(PaperOperatorDecisionConflict, match="different"):
        issue(registry, context, operator_id="operator:someone-else")


def test_decision_self_hash_and_ttl_are_fail_closed(tmp_path) -> None:
    _, context, registry = evidence(tmp_path)
    state = issue(registry, context)
    with pytest.raises(ValueError, match="hash mismatch"):
        replace(state.decision, decision_hash="f" * 64)
    with pytest.raises(ValueError, match="<=2 minutes"):
        registry.record_operator_approval(
            context=context,
            operator_id="operator:arendon7",
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=3),
        )


def test_consume_is_one_shot_same_attempt_idempotent_and_other_attempt_rejected(tmp_path) -> None:
    _, context, registry = evidence(tmp_path)
    issued = issue(registry, context)
    first = registry.consume(
        decision=issued.decision,
        attempt_id=context.attempt_id,
        now=NOW + timedelta(seconds=1),
    )
    second = registry.consume(
        decision=issued.decision,
        attempt_id=context.attempt_id,
        now=NOW + timedelta(seconds=2),
    )
    assert first == second
    assert first.status is PaperOperatorDecisionStatus.CONSUMED
    assert first.consumed_attempt_id == context.attempt_id
    with pytest.raises(PaperOperatorDecisionConflict, match="another attempt"):
        registry.consume(
            decision=issued.decision,
            attempt_id="writer-attempt-other",
            now=NOW + timedelta(seconds=2),
        )


def test_expired_decision_cannot_be_consumed(tmp_path) -> None:
    _, context, registry = evidence(tmp_path)
    issued = registry.record_operator_approval(
        context=context,
        operator_id="operator:arendon7",
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(PaperOperatorDecisionExpired):
        registry.consume(
            decision=issued.decision,
            attempt_id=context.attempt_id,
            now=NOW + timedelta(seconds=1),
        )


def test_event_mutation_tail_deletion_and_control_tamper_fail_closed(tmp_path) -> None:
    _, context, registry = evidence(tmp_path)
    issued = issue(registry, context)
    registry.consume(
        decision=issued.decision,
        attempt_id=context.attempt_id,
        now=NOW + timedelta(seconds=1),
    )
    db = tmp_path / "operator.sqlite"

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE alpaca_paper_operator_decision_events SET payload_json = ? WHERE sequence = 1",
        (json.dumps({"tampered": True}),),
    )
    conn.commit()
    conn.close()
    with pytest.raises(PaperOperatorDecisionIntegrityError):
        registry.get(context.preparation_hash)

    # Fresh registry for independent anchored-tail deletion check.
    _, context2, registry2 = evidence(tmp_path / "tail", attempt_id="writer-attempt-002")
    issue(registry2, context2)
    db2 = tmp_path / "tail" / "operator.sqlite"
    conn = sqlite3.connect(db2)
    conn.execute("DELETE FROM alpaca_paper_operator_decision_events WHERE sequence = 1")
    conn.commit()
    conn.close()
    with pytest.raises(PaperOperatorDecisionIntegrityError, match="count"):
        registry2.get(context2.preparation_hash)

    _, context3, registry3 = evidence(tmp_path / "control", attempt_id="writer-attempt-003")
    issue(registry3, context3)
    db3 = tmp_path / "control" / "operator.sqlite"
    conn = sqlite3.connect(db3)
    conn.execute(
        "UPDATE alpaca_paper_operator_decision_control SET event_head_hash = ? WHERE singleton = 1",
        ("f" * 64,),
    )
    conn.commit()
    conn.close()
    with pytest.raises(PaperOperatorDecisionIntegrityError, match="control hash"):
        registry3.get(context3.preparation_hash)
