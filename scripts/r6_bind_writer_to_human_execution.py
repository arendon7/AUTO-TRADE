from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRITER = ROOT / "src/autotrade/brokers/alpaca_paper_writer.py"
WRITER_TEST = ROOT / "tests/test_r6_paper_writer.py"
RESUME_TEST = ROOT / "tests/test_r6_writer_same_attempt_resume.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_writer() -> None:
    text = WRITER.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from .alpaca_paper_canary import PaperCanaryApproval\n",
        "from .alpaca_paper_canary import PaperCanaryApproval\n"
        "from .alpaca_paper_canary_coordinator import PreparedPaperCanaryPackage\n"
        "from .alpaca_paper_execution_bridge import PaperExecutionStageResult\n"
        "from .alpaca_paper_operator_decision import (\n"
        "    PaperOperatorDecision,\n"
        "    PaperOperatorDecisionContext,\n"
        "    PaperOperatorDecisionStatus,\n"
        "    SQLitePaperOperatorDecisionRegistry,\n"
        ")\n",
        "writer human execution imports",
    )
    text = replace_once(
        text,
        "    pre_consume_guard_hash: str\n    pre_io_guard_hash: str\n",
        "    pre_consume_guard_hash: str\n    pre_io_guard_hash: str\n"
        "    prepared_package_hash: str\n"
        "    operator_decision_hash: str\n",
        "writer result human evidence hashes",
    )
    text = replace_once(
        text,
        "        approval: PaperCanaryApproval,\n        permit_registry: SQLitePaperCanaryPermitRegistry,\n",
        "        approval: PaperCanaryApproval,\n"
        "        prepared_package: PreparedPaperCanaryPackage,\n"
        "        operator_decision: PaperOperatorDecision,\n"
        "        operator_registry: SQLitePaperOperatorDecisionRegistry,\n"
        "        execution_stage: PaperExecutionStageResult,\n"
        "        permit_registry: SQLitePaperCanaryPermitRegistry,\n",
        "writer signature human evidence",
    )
    validation_anchor = '''        if approval.client_order_id != expected_bracket.client_order_id:\n            raise PaperWriterBlocked("canary approval client_order_id mismatch")\n\n        state = submission_registry.get(expected_bracket.order_id)\n'''
    validation_block = '''        if approval.client_order_id != expected_bracket.client_order_id:\n            raise PaperWriterBlocked("canary approval client_order_id mismatch")\n\n        if not isinstance(prepared_package, PreparedPaperCanaryPackage):\n            raise PaperWriterBlocked("writer requires exact PreparedPaperCanaryPackage")\n        if not isinstance(operator_decision, PaperOperatorDecision):\n            raise PaperWriterBlocked("writer requires durable human operator decision")\n        if not isinstance(operator_registry, SQLitePaperOperatorDecisionRegistry):\n            raise PaperWriterBlocked("writer requires authoritative operator decision registry")\n        if not isinstance(execution_stage, PaperExecutionStageResult):\n            raise PaperWriterBlocked("writer requires certified execution bridge result")\n        if prepared_package.network_write_authorized is not False:\n            raise PaperWriterBlocked("prepared package cannot carry network authority")\n        if prepared_package.next_action != "OPERATOR_DECISION_REQUIRED":\n            raise PaperWriterBlocked("prepared package does not encode operator-decision gate")\n        if attempt_id != prepared_package.attempt_id:\n            raise PaperWriterBlocked("writer attempt_id does not match prepared package")\n        if now < prepared_package.prepared_at or now >= prepared_package.execution_deadline:\n            raise PaperWriterBlocked("prepared package execution deadline is not valid")\n        if prepared_package.order_id != expected_bracket.order_id:\n            raise PaperWriterBlocked("prepared package order_id mismatch")\n        if prepared_package.client_order_id != expected_bracket.client_order_id:\n            raise PaperWriterBlocked("prepared package client_order_id mismatch")\n        if prepared_package.bracket_payload_hash != expected_bracket.payload_hash:\n            raise PaperWriterBlocked("prepared package bracket payload hash mismatch")\n        if prepared_package.account_attestation_fingerprint != account_attestation.fingerprint:\n            raise PaperWriterBlocked("prepared package account attestation mismatch")\n        if prepared_package.canary_approval_hash != approval.approval_hash:\n            raise PaperWriterBlocked("prepared package canary approval mismatch")\n        if prepared_package.notional != approval.notional:\n            raise PaperWriterBlocked("prepared package canary notional mismatch")\n\n        expected_operator_context = PaperOperatorDecisionContext.from_prepared_package(\n            prepared_package\n        )\n        if operator_decision.context != expected_operator_context:\n            raise PaperWriterBlocked("operator decision does not match exact prepared package")\n        if not operator_decision.is_valid_at(now):\n            raise PaperWriterBlocked("human operator decision is expired or not yet valid")\n        try:\n            durable_operator = operator_registry.get(expected_operator_context.preparation_hash)\n        except Exception as exc:\n            raise PaperWriterBlocked("durable human operator decision verification failed") from exc\n        if durable_operator.decision != operator_decision:\n            raise PaperWriterBlocked("supplied human decision does not match durable evidence")\n        if durable_operator.status is not PaperOperatorDecisionStatus.CONSUMED:\n            raise PaperWriterBlocked("writer requires human decision already CONSUMED by execution bridge")\n        if durable_operator.consumed_attempt_id != attempt_id or durable_operator.consumed_at is None:\n            raise PaperWriterBlocked("human decision was not consumed by this exact attempt")\n\n        if execution_stage.package_hash != prepared_package.package_hash:\n            raise PaperWriterBlocked("execution bridge package hash mismatch")\n        if execution_stage.operator_decision_hash != operator_decision.decision_hash:\n            raise PaperWriterBlocked("execution bridge human decision hash mismatch")\n        if execution_stage.attempt_id != attempt_id:\n            raise PaperWriterBlocked("execution bridge attempt_id mismatch")\n        if execution_stage.handoff != external_handoff:\n            raise PaperWriterBlocked("execution bridge handoff mismatch")\n        if execution_stage.order.order_id != prepared_package.order_id:\n            raise PaperWriterBlocked("execution bridge order mismatch")\n\n        state = submission_registry.get(expected_bracket.order_id)\n'''
    text = replace_once(text, validation_anchor, validation_block, "writer human validation block")

    binding_anchor = '''        if approval.account_attestation_fingerprint != account_attestation.fingerprint:\n            raise PaperWriterBlocked("canary approval account attestation mismatch")\n\n        if external_handoff.handoff_id != approval.approval_hash:\n'''
    binding_block = '''        if approval.account_attestation_fingerprint != account_attestation.fingerprint:\n            raise PaperWriterBlocked("canary approval account attestation mismatch")\n        if prepared_package.submission_binding_hash != binding.fingerprint:\n            raise PaperWriterBlocked("prepared package frozen submission binding mismatch")\n        if prepared_package.intent_fingerprint != binding.intent_fingerprint:\n            raise PaperWriterBlocked("prepared package intent fingerprint mismatch")\n        if prepared_package.risk_decision_id != binding.risk_decision_id:\n            raise PaperWriterBlocked("prepared package risk decision mismatch")\n\n        if external_handoff.handoff_id != approval.approval_hash:\n'''
    text = replace_once(text, binding_anchor, binding_block, "writer package binding block")

    handoff_anchor = '''        if external_handoff.authorized_at < approval.issued_at or external_handoff.authorized_at >= approval.expires_at:\n            raise PaperWriterBlocked("OMS external handoff is outside canary approval window")\n        if now > external_handoff.decision_valid_until:\n            raise PaperWriterBlocked("OMS external handoff RiskDecision has expired")\n'''
    handoff_block = '''        if external_handoff.authorized_at < approval.issued_at or external_handoff.authorized_at >= approval.expires_at:\n            raise PaperWriterBlocked("OMS external handoff is outside canary approval window")\n        if durable_operator.consumed_at > external_handoff.authorized_at:\n            raise PaperWriterBlocked("OMS handoff predates durable human decision consumption")\n        if external_handoff.safety_state_version != prepared_package.risk_decision_safety_state_version:\n            raise PaperWriterBlocked("OMS handoff Safety version differs from human-reviewed package")\n        if external_handoff.market_fingerprint != prepared_package.market_fingerprint:\n            raise PaperWriterBlocked("OMS handoff market differs from human-reviewed package")\n        if external_handoff.decision_valid_until != prepared_package.risk_decision_valid_until:\n            raise PaperWriterBlocked("OMS handoff expiry differs from human-reviewed package")\n        if now > external_handoff.decision_valid_until:\n            raise PaperWriterBlocked("OMS external handoff RiskDecision has expired")\n'''
    text = replace_once(text, handoff_anchor, handoff_block, "writer handoff package controls")

    permit_anchor = '''        if (\n            permit.order_id != binding.order_id\n            or permit.client_order_id != binding.client_order_id\n            or permit.binding_hash != binding.fingerprint\n        ):\n            raise PaperWriterBlocked("durable canary permit does not match frozen submission")\n\n        try:\n'''
    permit_block = '''        if (\n            permit.order_id != binding.order_id\n            or permit.client_order_id != binding.client_order_id\n            or permit.binding_hash != binding.fingerprint\n        ):\n            raise PaperWriterBlocked("durable canary permit does not match frozen submission")\n        if (\n            prepared_package.permit_event_hash\n            != permit_registry.get_issued_event_hash(approval.approval_hash)\n        ):\n            raise PaperWriterBlocked("prepared package permit issuance evidence mismatch")\n\n        try:\n'''
    text = replace_once(text, permit_anchor, permit_block, "writer permit package evidence")

    result_anchor = '''            pre_consume_guard_hash=pre_consume_guard.attestation_hash,\n            pre_io_guard_hash=pre_io_guard.attestation_hash,\n        )\n'''
    result_block = '''            pre_consume_guard_hash=pre_consume_guard.attestation_hash,\n            pre_io_guard_hash=pre_io_guard.attestation_hash,\n            prepared_package_hash=prepared_package.package_hash,\n            operator_decision_hash=operator_decision.decision_hash,\n        )\n'''
    text = replace_once(text, result_anchor, result_block, "writer result human evidence")

    if "record_operator_approval(" in text:
        raise SystemExit("writer must never mint human decisions")
    WRITER.write_text(text, encoding="utf-8")


def patch_writer_tests() -> None:
    text = WRITER_TEST.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from autotrade.brokers.alpaca_paper_canary_permit import (\n",
        "from autotrade.brokers.alpaca_paper_canary_coordinator import _build_package\n"
        "from autotrade.brokers.alpaca_paper_execution_bridge import PaperCanaryExecutionBridge\n"
        "from autotrade.brokers.alpaca_paper_operator_decision import (\n"
        "    PaperOperatorDecisionContext,\n"
        "    SQLitePaperOperatorDecisionRegistry,\n"
        ")\n"
        "from autotrade.brokers.alpaca_paper_canary_permit import (\n",
        "writer tests human execution imports",
    )
    text = replace_once(
        text,
        "        # stage_external_submission performs two authoritative Safety reads;\n        # PRE_CONSUME performs the third. Flip only at PRE_IO so this fixture\n        # continues to prove the post-permit, pre-network race.\n        if self.calls <= 3:\n            return SafetyControlState(version=0, updated_at=NOW)\n",
        "        # Execution Bridge uses a separate stable Safety store. This store\n"
        "        belongs only to the final writer guard: PRE_CONSUME is call 1 and\n"
        "        PRE_IO is call 2, where we deliberately flip fail-closed.\n"
        "        if self.calls == 1:\n"
        "            return SafetyControlState(version=0, updated_at=NOW)\n",
        "writer final guard race fixture",
    )
    text = replace_once(
        text,
        "def stack(tmp_path, *, safety_store=None):\n",
        "def stack(tmp_path, *, safety_store=None, attempt_id=\"writer-attempt-001\"):\n",
        "writer stack attempt parameter",
    )
    text = replace_once(
        text,
        "    permit_registry.issue(approval)\n\n    order_store = InMemoryOrderStore()\n",
        "    permit = permit_registry.issue(approval)\n\n    order_store = InMemoryOrderStore()\n",
        "writer stack retain permit evidence",
    )
    text = replace_once(
        text,
        "    safety_store = safety_store or InMemorySafetyStateStore()\n    health_bridge = HealthyBridge()\n",
        "    oms_safety_store = InMemorySafetyStateStore()\n"
        "    final_guard_safety_store = safety_store or oms_safety_store\n"
        "    health_bridge = HealthyBridge()\n",
        "writer stack split OMS/final Safety stores",
    )
    text = replace_once(
        text,
        "    oms = OrderManagementSystem(\n"
        "        broker=NeverCalledBroker(),\n"
        "        ledger=InMemoryEventLedger(),\n"
        "        order_store=order_store,\n"
        "        safety_state_store=safety_store,\n",
        "    oms = OrderManagementSystem(\n"
        "        broker=NeverCalledBroker(),\n"
        "        ledger=InMemoryEventLedger(),\n"
        "        order_store=order_store,\n"
        "        safety_state_store=oms_safety_store,\n",
        "writer stack OMS stable Safety",
    )
    old_stage = '''    _, external_handoff = oms.stage_external_submission(\n        order_id=current_order.order_id,\n        handoff_id=approval.approval_hash,\n        decision=risk_decision(current_order),\n        market=market(),\n        now=NOW + timedelta(milliseconds=100),\n    )\n'''
    new_stage = '''    current_decision = risk_decision(current_order)\n    prepared_package = _build_package(\n        order=current_order,\n        decision=current_decision,\n        binding=binding,\n        submission_state=submission_state,\n        bracket=expected,\n        approval=approval,\n        permit=permit,\n        attempt_id=attempt_id,\n        prepared_at=NOW,\n    )\n    operator_registry = SQLitePaperOperatorDecisionRegistry(\n        SQLiteRuntime(tmp_path / "operator.sqlite")\n    )\n    operator_context = PaperOperatorDecisionContext.from_prepared_package(prepared_package)\n    operator_decision = operator_registry.record_operator_approval(\n        context=operator_context,\n        operator_id="operator:writer-fixture",\n        issued_at=NOW + timedelta(milliseconds=50),\n        expires_at=NOW + timedelta(seconds=4),\n    ).decision\n    execution_stage = PaperCanaryExecutionBridge(oms=oms).stage_after_operator_decision(\n        package=prepared_package,\n        operator_decision=operator_decision,\n        operator_registry=operator_registry,\n        risk_decision=current_decision,\n        market=market(),\n        now=NOW + timedelta(milliseconds=100),\n    )\n    external_handoff = execution_stage.handoff\n'''
    text = replace_once(text, old_stage, new_stage, "writer stack human-gated OMS stage")
    text = replace_once(
        text,
        "        safety_state_store=safety_store,\n        portfolio_store=portfolio_store,\n",
        "        safety_state_store=final_guard_safety_store,\n        portfolio_store=portfolio_store,\n",
        "writer final guard Safety store",
    )
    text = replace_once(
        text,
        '        "safety_store": safety_store,\n',
        '        "safety_store": final_guard_safety_store,\n'
        '        "prepared_package": prepared_package,\n'
        '        "operator_registry": operator_registry,\n'
        '        "operator_decision": operator_decision,\n'
        '        "execution_stage": execution_stage,\n',
        "writer stack human evidence return",
    )
    text = replace_once(
        text,
        "def submit(instance, values, *, now=NOW + timedelta(seconds=1), attempt_id=\"writer-attempt-001\"):\n    return instance.submit_once(\n",
        "def submit(instance, values, *, now=NOW + timedelta(seconds=1), attempt_id=None):\n"
        "    attempt_id = attempt_id or values[\"prepared_package\"].attempt_id\n"
        "    return instance.submit_once(\n",
        "writer submit helper package attempt",
    )
    text = replace_once(
        text,
        "        approval=values[\"approval\"],\n        permit_registry=values[\"permit_registry\"],\n",
        "        approval=values[\"approval\"],\n"
        "        prepared_package=values[\"prepared_package\"],\n"
        "        operator_decision=values[\"operator_decision\"],\n"
        "        operator_registry=values[\"operator_registry\"],\n"
        "        execution_stage=values[\"execution_stage\"],\n"
        "        permit_registry=values[\"permit_registry\"],\n",
        "writer submit helper human evidence args",
    )
    text = replace_once(
        text,
        "    assert flipping.calls == 4\n",
        "    assert flipping.calls == 2\n",
        "writer final guard read count",
    )
    text = replace_once(
        text,
        "    assert result.pre_consume_guard_hash != result.pre_io_guard_hash\n",
        "    assert result.pre_consume_guard_hash != result.pre_io_guard_hash\n"
        "    assert result.prepared_package_hash == values[\"prepared_package\"].package_hash\n"
        "    assert result.operator_decision_hash == values[\"operator_decision\"].decision_hash\n",
        "writer result human evidence assertions",
    )
    WRITER_TEST.write_text(text, encoding="utf-8")


def patch_resume_tests() -> None:
    text = RESUME_TEST.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "def crashable_values(tmp_path):\n    values = stack(tmp_path)\n",
        "def crashable_values(tmp_path, *, attempt_id):\n    values = stack(tmp_path, attempt_id=attempt_id)\n",
        "resume crashable stack attempt",
    )
    text = replace_once(
        text,
        '    values = crashable_values(tmp_path)\n    transport = FakeWriteTransport(response=success_response(values["expected"]))\n    instance = writer(transport)\n    attempt_id = "writer-attempt-resume-001"\n',
        '    attempt_id = "writer-attempt-resume-001"\n    values = crashable_values(tmp_path, attempt_id=attempt_id)\n    transport = FakeWriteTransport(response=success_response(values["expected"]))\n    instance = writer(transport)\n',
        "resume same attempt fixture",
    )
    text = replace_once(
        text,
        '    values = crashable_values(tmp_path)\n    transport = FakeWriteTransport(response=success_response(values["expected"]))\n    instance = writer(transport)\n\n    with pytest.raises(SystemExit):\n        submit(instance, values, attempt_id="writer-attempt-original")\n',
        '    values = crashable_values(tmp_path, attempt_id="writer-attempt-original")\n    transport = FakeWriteTransport(response=success_response(values["expected"]))\n    instance = writer(transport)\n\n    with pytest.raises(SystemExit):\n        submit(instance, values, attempt_id="writer-attempt-original")\n',
        "resume different attempt fixture",
    )
    text = text.replace(
        'with pytest.raises(PaperWriterBlocked, match="another attempt"):',
        'with pytest.raises(PaperWriterBlocked, match="prepared package|another attempt"):')
    text = replace_once(
        text,
        '    values = crashable_values(tmp_path)\n    transport = FakeWriteTransport(response=success_response(values["expected"]))\n    instance = writer(transport)\n    attempt_id = "writer-attempt-expired-resume"\n',
        '    attempt_id = "writer-attempt-expired-resume"\n    values = crashable_values(tmp_path, attempt_id=attempt_id)\n    transport = FakeWriteTransport(response=success_response(values["expected"]))\n    instance = writer(transport)\n',
        "resume expiry fixture",
    )
    text = replace_once(
        text,
        '    values = stack(tmp_path)\n    attempt_id = "writer-attempt-unknown"\n',
        '    attempt_id = "writer-attempt-unknown"\n    values = stack(tmp_path, attempt_id=attempt_id)\n',
        "resume unknown fixture",
    )
    RESUME_TEST.write_text(text, encoding="utf-8")


def main() -> int:
    patch_writer()
    patch_writer_tests()
    patch_resume_tests()
    print("R6 writer now requires human-consumed execution stage before PAPER POST")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
