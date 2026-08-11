from __future__ import annotations

from dataclasses import replace

import pytest

from autotrade.brokers.alpaca_paper_operator_decision import (
    PaperOperatorDecisionConflict,
    PaperOperatorDecisionStatus,
    SQLitePaperOperatorDecisionRegistry,
)
from autotrade.brokers.alpaca_paper_writer import PaperWriterBlocked
from autotrade.persistence import SQLiteRuntime
from test_r6_paper_writer import (
    FakeWriteTransport,
    stack,
    submit,
    success_response,
    writer,
)


def instance_and_values(tmp_path):
    values = stack(tmp_path)
    transport = FakeWriteTransport(response=success_response(values["expected"]))
    return writer(transport), values, transport


def test_merely_issued_human_decision_never_reaches_transport(tmp_path) -> None:
    instance, values, transport = instance_and_values(tmp_path)
    original = values["operator_decision"]
    issued_only_registry = SQLitePaperOperatorDecisionRegistry(
        SQLiteRuntime(tmp_path / "issued-only-operator.sqlite")
    )
    issued = issued_only_registry.record_operator_approval(
        context=original.context,
        operator_id=original.operator_id,
        issued_at=original.issued_at,
        expires_at=original.expires_at,
    )
    altered = dict(values)
    altered["operator_registry"] = issued_only_registry
    altered["operator_decision"] = issued.decision

    with pytest.raises(PaperWriterBlocked, match="CONSUMED"):
        submit(instance, altered)
    assert transport.requests == []


def test_execution_stage_package_hash_mismatch_never_reaches_transport(tmp_path) -> None:
    instance, values, transport = instance_and_values(tmp_path)
    altered = dict(values)
    altered["execution_stage"] = replace(
        values["execution_stage"],
        package_hash="f" * 64,
    )
    with pytest.raises(PaperWriterBlocked, match="execution bridge package hash mismatch"):
        submit(instance, altered)
    assert transport.requests == []


def test_execution_stage_human_decision_hash_mismatch_never_reaches_transport(tmp_path) -> None:
    instance, values, transport = instance_and_values(tmp_path)
    altered = dict(values)
    altered["execution_stage"] = replace(
        values["execution_stage"],
        operator_decision_hash="f" * 64,
    )
    with pytest.raises(PaperWriterBlocked, match="human decision hash mismatch"):
        submit(instance, altered)
    assert transport.requests == []


def test_execution_stage_attempt_mismatch_never_reaches_transport(tmp_path) -> None:
    instance, values, transport = instance_and_values(tmp_path)
    altered = dict(values)
    altered["execution_stage"] = replace(
        values["execution_stage"],
        attempt_id="writer-attempt-other-stage",
    )
    with pytest.raises(PaperWriterBlocked, match="execution bridge attempt_id mismatch"):
        submit(instance, altered)
    assert transport.requests == []


def test_human_decision_expiry_after_bridge_blocks_writer_zero_io(tmp_path) -> None:
    instance, values, transport = instance_and_values(tmp_path)
    with pytest.raises(PaperWriterBlocked, match="human operator decision is expired"):
        submit(
            instance,
            values,
            now=values["operator_decision"].expires_at,
        )
    assert transport.requests == []


def test_prepared_package_from_another_attempt_is_rejected_zero_io(tmp_path) -> None:
    instance, values, transport = instance_and_values(tmp_path / "first")
    other = stack(tmp_path / "other", attempt_id="writer-attempt-other-package")
    altered = dict(values)
    altered["prepared_package"] = other["prepared_package"]
    with pytest.raises(PaperWriterBlocked, match="operator decision|attempt_id|prepared package"):
        submit(instance, altered)
    assert transport.requests == []


@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        ("prepared_package", None, "PreparedPaperCanaryPackage"),
        ("operator_decision", None, "human operator decision"),
        ("operator_registry", object(), "operator decision registry"),
        ("execution_stage", None, "execution bridge result"),
    ],
)
def test_writer_requires_typed_human_execution_evidence_zero_io(
    tmp_path, field, bad_value, message
) -> None:
    instance, values, transport = instance_and_values(tmp_path / field)
    altered = dict(values)
    altered[field] = bad_value
    with pytest.raises(PaperWriterBlocked, match=message):
        submit(
            instance,
            altered,
            attempt_id=values["prepared_package"].attempt_id,
        )
    assert transport.requests == []


def test_writer_attempt_must_match_human_reviewed_package_zero_io(tmp_path) -> None:
    instance, values, transport = instance_and_values(tmp_path)
    with pytest.raises(PaperWriterBlocked, match="attempt_id does not match prepared package"):
        submit(instance, values, attempt_id="writer-attempt-not-reviewed")
    assert transport.requests == []


def test_missing_durable_human_decision_never_reaches_transport(tmp_path) -> None:
    instance, values, transport = instance_and_values(tmp_path)
    altered = dict(values)
    altered["operator_registry"] = SQLitePaperOperatorDecisionRegistry(
        SQLiteRuntime(tmp_path / "empty-operator.sqlite")
    )
    with pytest.raises(PaperWriterBlocked, match="durable human operator decision verification failed"):
        submit(instance, altered)
    assert transport.requests == []


def test_operator_registry_blocks_cross_attempt_consume_before_writer(tmp_path) -> None:
    _, values, transport = instance_and_values(tmp_path)
    original = values["operator_decision"]
    registry = SQLitePaperOperatorDecisionRegistry(
        SQLiteRuntime(tmp_path / "wrong-attempt-operator.sqlite")
    )
    issued = registry.record_operator_approval(
        context=original.context,
        operator_id=original.operator_id,
        issued_at=original.issued_at,
        expires_at=original.expires_at,
    )
    with pytest.raises(PaperOperatorDecisionConflict, match="another attempt"):
        registry.consume(
            decision=issued.decision,
            attempt_id="writer-attempt-consumed-elsewhere",
            now=original.issued_at,
        )
    assert registry.get(issued.decision.context.preparation_hash).status is PaperOperatorDecisionStatus.ISSUED
    assert transport.requests == []
