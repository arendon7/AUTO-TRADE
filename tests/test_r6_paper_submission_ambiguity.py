from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3

import pytest

from autotrade.domain import OrderIntent, OrderRecord, OrderStatus, OrderType, Side
from autotrade.persistence import SQLiteRuntime
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionBinding,
    PaperSubmissionBlocked,
    PaperSubmissionConflict,
    PaperSubmissionIntegrityError,
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
    deterministic_client_order_id,
)


UTC = timezone.utc
T0 = datetime(2026, 8, 11, 7, 0, tzinfo=UTC)


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def runtime(tmp_path) -> SQLiteRuntime:
    return SQLiteRuntime(tmp_path / "paper-submission.sqlite")


def binding(**overrides) -> PaperSubmissionBinding:
    values = {
        "order_id": "order-001",
        "client_order_id": "autotrade-client-001",
        "intent_id": "intent-001",
        "intent_fingerprint": h("intent"),
        "risk_decision_id": "risk-001",
        "account_attestation_fingerprint": h("account"),
        "order_payload_hash": h("payload"),
        "created_at": T0,
    }
    values.update(overrides)
    return PaperSubmissionBinding(**values)


def make_order(*, status: OrderStatus = OrderStatus.VALIDATED) -> OrderRecord:
    intent = OrderIntent(
        intent_id="intent-order-record",
        strategy_id="strategy-1",
        symbol="AAPL",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        limit_price=None,
        stop_price=None,
        idempotency_key="idem-order-record",
        created_at=T0,
    )
    return OrderRecord(
        order_id="order-record-001",
        intent=intent,
        status=status,
        risk_decision_id="risk-order-record",
        broker_order_id=None,
        updated_at=T0,
    )


def test_binding_from_validated_order_uses_deterministic_client_order_id() -> None:
    order = make_order()
    first = PaperSubmissionBinding.from_order(
        order=order,
        account_attestation_fingerprint=h("attestation"),
        order_payload_hash=h("external-payload"),
        created_at=T0,
    )
    second = PaperSubmissionBinding.from_order(
        order=order,
        account_attestation_fingerprint=h("attestation"),
        order_payload_hash=h("external-payload"),
        created_at=T0,
    )

    assert first.client_order_id == second.client_order_id == deterministic_client_order_id(order)
    assert first.client_order_id.startswith("autotrade-")
    assert len(first.client_order_id) <= 128
    assert first.intent_id == order.intent.intent_id
    assert first.risk_decision_id == order.risk_decision_id


def test_binding_from_nonvalidated_order_is_rejected() -> None:
    with pytest.raises(ValueError, match="VALIDATED or SUBMITTING"):
        PaperSubmissionBinding.from_order(
            order=make_order(status=OrderStatus.CREATED),
            account_attestation_fingerprint=h("attestation"),
            order_payload_hash=h("external-payload"),
            created_at=T0,
        )


def test_prepare_is_idempotent_and_persists_across_restart(tmp_path) -> None:
    rt = runtime(tmp_path)
    first_registry = SQLitePaperSubmissionRegistry(rt)
    first = first_registry.prepare(binding())
    replay = first_registry.prepare(binding())

    assert replay == first
    assert first.status is PaperSubmissionStatus.PREPARED
    assert first.submit_allowed is True
    assert first.reconciliation_required is False
    assert first.attempt_count == 0
    assert first.event_sequence == 1

    reopened = SQLitePaperSubmissionRegistry(rt)
    assert reopened.get("order-001") == first
    assert reopened.get_by_client_order_id("autotrade-client-001") == first
    assert len(reopened.events("order-001")) == 1


def test_same_order_with_changed_immutable_binding_conflicts(tmp_path) -> None:
    registry = SQLitePaperSubmissionRegistry(runtime(tmp_path))
    registry.prepare(binding())

    with pytest.raises(PaperSubmissionConflict):
        registry.prepare(binding(order_payload_hash=h("different")))


def test_same_client_order_id_cannot_bind_to_different_local_order(tmp_path) -> None:
    registry = SQLitePaperSubmissionRegistry(runtime(tmp_path))
    registry.prepare(binding())

    with pytest.raises(PaperSubmissionConflict):
        registry.prepare(
            binding(
                order_id="order-002",
                intent_id="intent-002",
                risk_decision_id="risk-002",
            )
        )


def test_submit_attempt_is_persisted_as_unknown_before_any_future_network_write(tmp_path) -> None:
    rt = runtime(tmp_path)
    registry = SQLitePaperSubmissionRegistry(rt)
    registry.prepare(binding())

    state = registry.mark_submit_attempt_unknown(
        order_id="order-001", attempt_id="attempt-001", now=T0 + timedelta(seconds=1)
    )

    assert state.status is PaperSubmissionStatus.UNKNOWN
    assert state.submit_allowed is False
    assert state.reconciliation_required is True
    assert state.attempt_count == 1
    assert state.event_sequence == 2
    reopened = SQLitePaperSubmissionRegistry(rt)
    assert reopened.get("order-001") == state


def test_same_attempt_id_is_idempotent_but_second_attempt_is_blocked(tmp_path) -> None:
    registry = SQLitePaperSubmissionRegistry(runtime(tmp_path))
    registry.prepare(binding())
    first = registry.mark_submit_attempt_unknown(
        order_id="order-001", attempt_id="attempt-001", now=T0 + timedelta(seconds=1)
    )
    replay = registry.mark_submit_attempt_unknown(
        order_id="order-001", attempt_id="attempt-001", now=T0 + timedelta(seconds=2)
    )
    assert replay == first

    with pytest.raises(PaperSubmissionBlocked, match="reconcile first"):
        registry.mark_submit_attempt_unknown(
            order_id="order-001",
            attempt_id="attempt-002",
            now=T0 + timedelta(seconds=2),
        )
    assert registry.get("order-001").attempt_count == 1


def test_absence_reconciliation_keeps_unknown_and_never_rearms_submit(tmp_path) -> None:
    registry = SQLitePaperSubmissionRegistry(runtime(tmp_path))
    registry.prepare(binding())
    registry.mark_submit_attempt_unknown(
        order_id="order-001", attempt_id="attempt-001", now=T0 + timedelta(seconds=1)
    )

    first = registry.record_reconciliation_absent(
        order_id="order-001",
        request_id="lookup-request-001",
        now=T0 + timedelta(seconds=2),
    )
    replay = registry.record_reconciliation_absent(
        order_id="order-001",
        request_id="lookup-request-001",
        now=T0 + timedelta(seconds=3),
    )
    second = registry.record_reconciliation_absent(
        order_id="order-001",
        request_id="lookup-request-002",
        now=T0 + timedelta(seconds=4),
    )

    assert replay == first
    assert second.status is PaperSubmissionStatus.UNKNOWN
    assert second.absence_observation_count == 2
    assert second.submit_allowed is False
    with pytest.raises(PaperSubmissionBlocked):
        registry.mark_submit_attempt_unknown(
            order_id="order-001",
            attempt_id="attempt-002",
            now=T0 + timedelta(seconds=5),
        )


def test_absence_evidence_before_unknown_is_blocked(tmp_path) -> None:
    registry = SQLitePaperSubmissionRegistry(runtime(tmp_path))
    registry.prepare(binding())
    with pytest.raises(PaperSubmissionBlocked):
        registry.record_reconciliation_absent(
            order_id="order-001", request_id="lookup-1", now=T0 + timedelta(seconds=1)
        )


def test_ack_reconciliation_requires_exact_client_and_payload_binding(tmp_path) -> None:
    registry = SQLitePaperSubmissionRegistry(runtime(tmp_path))
    frozen = binding()
    registry.prepare(frozen)
    registry.mark_submit_attempt_unknown(
        order_id="order-001", attempt_id="attempt-001", now=T0 + timedelta(seconds=1)
    )

    with pytest.raises(PaperSubmissionConflict, match="client_order_id"):
        registry.reconcile_acknowledged(
            order_id="order-001",
            broker_order_id="broker-001",
            broker_client_order_id="different-client",
            broker_order_payload_hash=frozen.order_payload_hash,
            request_id="request-wrong-client",
            now=T0 + timedelta(seconds=2),
        )
    with pytest.raises(PaperSubmissionConflict, match="payload"):
        registry.reconcile_acknowledged(
            order_id="order-001",
            broker_order_id="broker-001",
            broker_client_order_id=frozen.client_order_id,
            broker_order_payload_hash=h("wrong"),
            request_id="request-wrong-payload",
            now=T0 + timedelta(seconds=2),
        )


def test_ack_reconciliation_is_durable_and_idempotent_for_same_broker_order(tmp_path) -> None:
    rt = runtime(tmp_path)
    registry = SQLitePaperSubmissionRegistry(rt)
    frozen = binding()
    registry.prepare(frozen)
    registry.mark_submit_attempt_unknown(
        order_id="order-001", attempt_id="attempt-001", now=T0 + timedelta(seconds=1)
    )
    acknowledged = registry.reconcile_acknowledged(
        order_id="order-001",
        broker_order_id="broker-001",
        broker_client_order_id=frozen.client_order_id,
        broker_order_payload_hash=frozen.order_payload_hash,
        request_id="request-ack-001",
        now=T0 + timedelta(seconds=2),
    )

    assert acknowledged.status is PaperSubmissionStatus.ACKNOWLEDGED
    assert acknowledged.broker_order_id == "broker-001"
    assert acknowledged.broker_client_order_id == frozen.client_order_id
    assert acknowledged.submit_allowed is False
    assert acknowledged.reconciliation_required is False

    replay = registry.reconcile_acknowledged(
        order_id="order-001",
        broker_order_id="broker-001",
        broker_client_order_id=frozen.client_order_id,
        broker_order_payload_hash=frozen.order_payload_hash,
        request_id="request-ack-replay",
        now=T0 + timedelta(seconds=3),
    )
    assert replay == acknowledged
    assert SQLitePaperSubmissionRegistry(rt).get("order-001") == acknowledged


def test_ack_before_unknown_and_conflicting_second_broker_order_are_blocked(tmp_path) -> None:
    registry = SQLitePaperSubmissionRegistry(runtime(tmp_path))
    frozen = binding()
    registry.prepare(frozen)
    with pytest.raises(PaperSubmissionBlocked):
        registry.reconcile_acknowledged(
            order_id="order-001",
            broker_order_id="broker-001",
            broker_client_order_id=frozen.client_order_id,
            broker_order_payload_hash=frozen.order_payload_hash,
            request_id="request-001",
            now=T0 + timedelta(seconds=1),
        )

    registry.mark_submit_attempt_unknown(
        order_id="order-001", attempt_id="attempt-001", now=T0 + timedelta(seconds=1)
    )
    registry.reconcile_acknowledged(
        order_id="order-001",
        broker_order_id="broker-001",
        broker_client_order_id=frozen.client_order_id,
        broker_order_payload_hash=frozen.order_payload_hash,
        request_id="request-002",
        now=T0 + timedelta(seconds=2),
    )
    with pytest.raises(PaperSubmissionConflict, match="different broker order"):
        registry.reconcile_acknowledged(
            order_id="order-001",
            broker_order_id="broker-002",
            broker_client_order_id=frozen.client_order_id,
            broker_order_payload_hash=frozen.order_payload_hash,
            request_id="request-003",
            now=T0 + timedelta(seconds=3),
        )


def test_reconciliation_request_id_cannot_be_reused_for_different_evidence(tmp_path) -> None:
    registry = SQLitePaperSubmissionRegistry(runtime(tmp_path))
    frozen = binding()
    registry.prepare(frozen)
    registry.mark_submit_attempt_unknown(
        order_id="order-001", attempt_id="attempt-001", now=T0 + timedelta(seconds=1)
    )
    registry.record_reconciliation_absent(
        order_id="order-001", request_id="request-shared", now=T0 + timedelta(seconds=2)
    )

    with pytest.raises(PaperSubmissionConflict, match="X-Request-ID"):
        registry.reconcile_acknowledged(
            order_id="order-001",
            broker_order_id="broker-001",
            broker_client_order_id=frozen.client_order_id,
            broker_order_payload_hash=frozen.order_payload_hash,
            request_id="request-shared",
            now=T0 + timedelta(seconds=3),
        )


def test_tail_event_deletion_is_detected_by_control_anchor(tmp_path) -> None:
    rt = runtime(tmp_path)
    registry = SQLitePaperSubmissionRegistry(rt)
    registry.prepare(binding())
    registry.mark_submit_attempt_unknown(
        order_id="order-001", attempt_id="attempt-001", now=T0 + timedelta(seconds=1)
    )

    with sqlite3.connect(rt.path) as conn:
        conn.execute(
            "DELETE FROM alpaca_paper_submission_events WHERE order_id = ? AND sequence = 2",
            ("order-001",),
        )
        conn.commit()

    with pytest.raises(PaperSubmissionIntegrityError, match="control anchor"):
        registry.get("order-001")


def test_middle_event_deletion_is_detected_as_sequence_gap(tmp_path) -> None:
    rt = runtime(tmp_path)
    registry = SQLitePaperSubmissionRegistry(rt)
    registry.prepare(binding())
    registry.mark_submit_attempt_unknown(
        order_id="order-001", attempt_id="attempt-001", now=T0 + timedelta(seconds=1)
    )
    registry.record_reconciliation_absent(
        order_id="order-001", request_id="lookup-1", now=T0 + timedelta(seconds=2)
    )
    registry.record_reconciliation_absent(
        order_id="order-001", request_id="lookup-2", now=T0 + timedelta(seconds=3)
    )

    with sqlite3.connect(rt.path) as conn:
        conn.execute(
            "DELETE FROM alpaca_paper_submission_events WHERE order_id = ? AND sequence = 3",
            ("order-001",),
        )
        conn.commit()

    with pytest.raises(PaperSubmissionIntegrityError, match="sequence gap"):
        registry.get("order-001")


def test_event_payload_mutation_is_detected(tmp_path) -> None:
    rt = runtime(tmp_path)
    registry = SQLitePaperSubmissionRegistry(rt)
    registry.prepare(binding())
    registry.mark_submit_attempt_unknown(
        order_id="order-001", attempt_id="attempt-001", now=T0 + timedelta(seconds=1)
    )

    with sqlite3.connect(rt.path) as conn:
        raw = conn.execute(
            "SELECT payload_json FROM alpaca_paper_submission_events WHERE order_id = ? AND sequence = 2",
            ("order-001",),
        ).fetchone()[0]
        payload = json.loads(raw)
        payload["client_order_id"] = "forged-client"
        conn.execute(
            "UPDATE alpaca_paper_submission_events SET payload_json = ? WHERE order_id = ? AND sequence = 2",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), "order-001"),
        )
        conn.commit()

    with pytest.raises(PaperSubmissionIntegrityError, match="hash mismatch"):
        registry.get("order-001")


def test_binding_and_control_mutation_are_detected(tmp_path) -> None:
    rt = runtime(tmp_path)
    registry = SQLitePaperSubmissionRegistry(rt)
    registry.prepare(binding())

    with sqlite3.connect(rt.path) as conn:
        conn.execute(
            "UPDATE alpaca_paper_submission_control SET status = 'UNKNOWN' WHERE order_id = ?",
            ("order-001",),
        )
        conn.commit()
    with pytest.raises(PaperSubmissionIntegrityError, match="control hash mismatch"):
        registry.get("order-001")

    rt2 = SQLiteRuntime(tmp_path / "binding-tamper.sqlite")
    registry2 = SQLitePaperSubmissionRegistry(rt2)
    registry2.prepare(binding())
    with sqlite3.connect(rt2.path) as conn:
        raw = conn.execute(
            "SELECT binding_json FROM alpaca_paper_submission_bindings WHERE order_id = ?",
            ("order-001",),
        ).fetchone()[0]
        payload = json.loads(raw)
        payload["risk_decision_id"] = "risk-forged"
        conn.execute(
            "UPDATE alpaca_paper_submission_bindings SET binding_json = ? WHERE order_id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), "order-001"),
        )
        conn.commit()
    with pytest.raises(PaperSubmissionIntegrityError, match="binding hash mismatch"):
        registry2.get("order-001")


def test_backward_timestamp_is_rejected_without_poisoning_state(tmp_path) -> None:
    registry = SQLitePaperSubmissionRegistry(runtime(tmp_path))
    prepared = registry.prepare(binding())

    with pytest.raises((ValueError, PaperSubmissionIntegrityError)):
        registry.mark_submit_attempt_unknown(
            order_id="order-001",
            attempt_id="attempt-backward",
            now=T0 - timedelta(seconds=1),
        )
    assert registry.get("order-001") == prepared


def test_registry_has_no_network_or_order_post_surface(tmp_path) -> None:
    registry = SQLitePaperSubmissionRegistry(runtime(tmp_path))
    forbidden = {
        "submit",
        "submit_order",
        "post",
        "send",
        "place_order",
        "create_order",
        "retry_submit",
    }
    assert not (forbidden & set(dir(registry)))
