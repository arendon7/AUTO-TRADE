from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.paper_close_control_plane import prepare_paper_close_control_plane
from autotrade.paper_close_execution_bridge import (
    PaperCloseExecutionBridge,
    PaperCloseExecutionBridgeBlocked,
    bind_paper_close_execution_authority,
)
from autotrade.paper_close_plan import prepare_crypto_close_plan
from autotrade.paper_close_writer import (
    PaperCloseWriteReceipt,
    PaperCloseWriter,
    issue_paper_close_operator_decision,
)
from test_r7_paper_close_control_plane import NOW, _setup


class _CapturingWriter(PaperCloseWriter):
    def __init__(self) -> None:
        self.calls = []

    def submit_once(self, **kwargs):
        self.calls.append(kwargs)
        return PaperCloseWriteReceipt(
            attempt_id=kwargs["attempt_id"],
            plan_hash=kwargs["plan"].plan_hash,
            decision_hash=kwargs["decision"].decision_hash,
            client_order_id="atr7-close-test",
            request_payload_sha256="a" * 64,
            broker_order_id="broker-close-test",
            broker_status="accepted",
            request_id="req-close-test",
            response_sha256="b" * 64,
            submitted_at=kwargs["now"],
        )


def _authority_chain():
    _, _, safety, oms, portfolio, plan, market, source, lifecycle = _setup()
    prepared = prepare_paper_close_control_plane(
        attempt_id="r7-close-bridge-001",
        plan=plan,
        broker_portfolio=portfolio,
        market=market,
        source_entry_order=source,
        source_lifecycle=lifecycle,
        safety=safety,
        oms=oms,
        now=NOW,
    )
    operator = issue_paper_close_operator_decision(
        attempt_id=prepared.attempt_id,
        plan=plan,
        confirmation="CERRAR PAPER",
        now=NOW,
    )
    _, handoff = oms.stage_risk_reducing_external_submission(
        prepared=prepared,
        market=market,
        now=NOW + timedelta(milliseconds=1),
    )
    authority = bind_paper_close_execution_authority(
        plan=plan,
        operator_decision=operator,
        control_plane=prepared,
        oms_handoff=handoff,
        now=NOW + timedelta(milliseconds=2),
    )
    return portfolio, plan, prepared, operator, handoff, authority


def test_authority_binds_human_safety_and_oms_handoff() -> None:
    _, plan, prepared, operator, handoff, authority = _authority_chain()
    assert authority.attempt_id == prepared.attempt_id
    assert authority.plan_hash == plan.plan_hash
    assert authority.operator_decision_hash == operator.decision_hash
    assert authority.control_plane_fingerprint == prepared.fingerprint
    assert authority.oms_handoff_hash == handoff.handoff_hash
    assert authority.risk_decision_id == prepared.decision.decision_id
    assert len(authority.authority_hash) == 64


def test_bridge_is_only_wrapper_that_invokes_low_level_writer_once() -> None:
    portfolio, plan, prepared, operator, _handoff, authority = _authority_chain()
    writer = _CapturingWriter()
    bridge = PaperCloseExecutionBridge(writer=writer)
    receipt = bridge.execute_once(
        authority=authority,
        plan=plan,
        operator_decision=operator,
        control_plane=prepared,
        lifecycle=object(),  # capturing writer deliberately performs no lifecycle I/O
        fresh_portfolio=portfolio,
        credentials=AlpacaPaperCredentials("paper-key", "paper-secret"),
        now=NOW + timedelta(milliseconds=3),
    )
    assert receipt.attempt_id == prepared.attempt_id
    assert len(writer.calls) == 1
    assert writer.calls[0]["attempt_id"] == prepared.attempt_id
    assert writer.calls[0]["plan"] == plan
    assert writer.calls[0]["decision"] == operator


def test_binding_rejects_valid_human_decision_for_other_attempt() -> None:
    _, plan, prepared, _operator, handoff, _ = _authority_chain()
    other = issue_paper_close_operator_decision(
        attempt_id="r7-close-bridge-other",
        plan=plan,
        confirmation="CERRAR PAPER",
        now=NOW,
    )
    with pytest.raises(PaperCloseExecutionBridgeBlocked, match="attempt differs"):
        bind_paper_close_execution_authority(
            plan=plan,
            operator_decision=other,
            control_plane=prepared,
            oms_handoff=handoff,
            now=NOW + timedelta(milliseconds=2),
        )


def test_binding_rejects_valid_human_decision_for_other_plan() -> None:
    portfolio, plan, prepared, _operator, handoff, _ = _authority_chain()
    other_plan = prepare_crypto_close_plan(
        portfolio=portfolio,
        symbol="BTC/USD",
        now=NOW,
        limit_price=Decimal("72770"),
    )
    other = issue_paper_close_operator_decision(
        attempt_id=prepared.attempt_id,
        plan=other_plan,
        confirmation="CERRAR PAPER",
        now=NOW,
    )
    with pytest.raises(PaperCloseExecutionBridgeBlocked, match="plan hash"):
        bind_paper_close_execution_authority(
            plan=plan,
            operator_decision=other,
            control_plane=prepared,
            oms_handoff=handoff,
            now=NOW + timedelta(milliseconds=2),
        )


def test_binding_rejects_valid_handoff_from_other_oms_order() -> None:
    _, plan, prepared, operator, _handoff, _ = _authority_chain()
    _, _, _, _other_operator, other_handoff, _ = _authority_chain()
    assert other_handoff.order_id != prepared.order.order_id
    with pytest.raises(PaperCloseExecutionBridgeBlocked, match="handoff order"):
        bind_paper_close_execution_authority(
            plan=plan,
            operator_decision=operator,
            control_plane=prepared,
            oms_handoff=other_handoff,
            now=NOW + timedelta(milliseconds=2),
        )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("operator_plan", "decision hash mismatch"),
        ("handoff_order", "event_id mismatch"),
        ("handoff_safety", "handoff hash mismatch"),
        ("handoff_risk", "handoff hash mismatch"),
    ],
)
def test_hash_sealed_authority_objects_reject_direct_tamper(mutation: str, match: str) -> None:
    _, _plan, _prepared, operator, handoff, _ = _authority_chain()
    with pytest.raises(ValueError, match=match):
        if mutation == "operator_plan":
            replace(operator, plan_hash="f" * 64)
        elif mutation == "handoff_order":
            replace(handoff, order_id="different-order")
        elif mutation == "handoff_safety":
            replace(handoff, safety_state_version=handoff.safety_state_version + 1)
        elif mutation == "handoff_risk":
            replace(handoff, risk_decision_id="different-risk")
        else:  # pragma: no cover - parametrization is closed above
            raise AssertionError(mutation)


def test_execute_rejects_tampered_authority_before_writer() -> None:
    portfolio, plan, prepared, operator, _handoff, authority = _authority_chain()
    writer = _CapturingWriter()
    bridge = PaperCloseExecutionBridge(writer=writer)
    tampered = replace(authority, plan_hash="f" * 64)
    with pytest.raises(PaperCloseExecutionBridgeBlocked, match="authority hash mismatch"):
        bridge.execute_once(
            authority=tampered,
            plan=plan,
            operator_decision=operator,
            control_plane=prepared,
            lifecycle=object(),
            fresh_portfolio=portfolio,
            credentials=AlpacaPaperCredentials("paper-key", "paper-secret"),
            now=NOW + timedelta(milliseconds=3),
        )
    assert writer.calls == []
