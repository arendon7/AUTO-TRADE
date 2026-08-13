from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from autotrade.persistence import SQLiteRuntime
from autotrade.brokers.alpaca_paper_crypto_protection_operator_decision import (
    CryptoProtectionOperatorDecisionConflict,
    CryptoProtectionOperatorDecisionContext,
    CryptoProtectionOperatorDecisionIntegrityError,
    CryptoProtectionOperatorDecisionStatus,
    SQLiteCryptoProtectionOperatorDecisionRegistry,
)
from test_r6_paper_crypto_canary_coordinator import NOW
from test_r6_paper_crypto_protection_coordinator import _prepare


def _registry(tmp_path):
    return SQLiteCryptoProtectionOperatorDecisionRegistry(
        SQLiteRuntime(tmp_path / "protection-operator.sqlite3")
    )


def _context(package, *, attempt_id="r6c-protect-attempt-001"):
    return CryptoProtectionOperatorDecisionContext.from_prepared_package(
        package,
        attempt_id=attempt_id,
    )


def _approval(tmp_path, *, now=None, ttl=timedelta(seconds=20)):
    _, _, prepared = _prepare(tmp_path / "prepared")
    registry = _registry(tmp_path)
    context = _context(prepared.package)
    decision = registry.record_operator_approval(
        context=context,
        operator_id="human-operator-001",
        note="Approve exact reconciled-position protective stop-limit only.",
        now=now or (NOW + timedelta(seconds=7)),
        ttl=ttl,
    )
    return prepared, registry, context, decision


def test_protection_approval_is_separate_hash_bound_and_durable(tmp_path) -> None:
    prepared, registry, context, decision = _approval(tmp_path)

    assert context.prepared_package_hash == prepared.package.package_hash
    assert context.lifecycle_id == prepared.package.lifecycle_id
    assert context.order_id == prepared.package.order_id
    assert context.client_order_id == prepared.package.client_order_id
    assert context.entry_reconciliation_fingerprint == prepared.package.entry_reconciliation_fingerprint
    assert context.quantity == format(prepared.package.quantity, "f")
    assert context.stop_price == format(prepared.package.stop_price, "f")
    assert context.limit_price == format(prepared.package.limit_price, "f")
    assert decision.context == context
    assert decision.operator_id == "human-operator-001"
    assert decision.is_valid_at(NOW + timedelta(seconds=8))

    state = registry.get(context.preparation_hash)
    assert state.decision == decision
    assert state.status is CryptoProtectionOperatorDecisionStatus.ISSUED
    assert state.consumed_attempt_id is None
    assert state.event_sequence == 1
    assert len(state.event_head_hash) == 64


def test_protection_approval_consumes_once_for_exact_attempt_and_replays_idempotently(tmp_path) -> None:
    _, registry, context, decision = _approval(tmp_path)
    now = NOW + timedelta(seconds=8)

    consumed = registry.consume(
        decision=decision,
        attempt_id=context.attempt_id,
        now=now,
    )
    replay = registry.consume(
        decision=decision,
        attempt_id=context.attempt_id,
        now=now + timedelta(milliseconds=10),
    )

    assert consumed.status is CryptoProtectionOperatorDecisionStatus.CONSUMED
    assert consumed.consumed_attempt_id == context.attempt_id
    assert consumed.event_sequence == 2
    assert replay == consumed


def test_protection_approval_cannot_rebind_attempt_or_package(tmp_path) -> None:
    _, registry, context, decision = _approval(tmp_path)

    with pytest.raises(CryptoProtectionOperatorDecisionConflict, match="attempt binding mismatch"):
        registry.consume(
            decision=decision,
            attempt_id="r6c-protect-attempt-002",
            now=NOW + timedelta(seconds=8),
        )

    with pytest.raises(ValueError, match="context hash mismatch"):
        replace(context, client_order_id="different-protection-client-order")


def test_protection_approval_duplicate_and_expired_authority_fail_closed(tmp_path) -> None:
    _, registry, context, decision = _approval(
        tmp_path,
        ttl=timedelta(seconds=1),
    )

    with pytest.raises(CryptoProtectionOperatorDecisionConflict, match="already has human authority"):
        registry.record_operator_approval(
            context=context,
            operator_id="human-operator-001",
            note="duplicate",
            now=NOW + timedelta(seconds=7, milliseconds=100),
        )

    with pytest.raises(CryptoProtectionOperatorDecisionConflict, match="expired"):
        registry.consume(
            decision=decision,
            attempt_id=context.attempt_id,
            now=NOW + timedelta(seconds=8, milliseconds=1),
        )


def test_protection_approval_rejects_invalid_ttl_and_naive_time(tmp_path) -> None:
    _, _, prepared = _prepare(tmp_path / "prepared")
    registry = _registry(tmp_path)
    context = _context(prepared.package)

    with pytest.raises(ValueError, match="ttl"):
        registry.record_operator_approval(
            context=context,
            operator_id="human-operator-001",
            note="bad ttl",
            now=NOW + timedelta(seconds=7),
            ttl=timedelta(0),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        registry.record_operator_approval(
            context=context,
            operator_id="human-operator-001",
            note="bad time",
            now=NOW.replace(tzinfo=None),
        )


def test_protection_operator_event_hash_tamper_is_detected(tmp_path) -> None:
    _, registry, context, _ = _approval(tmp_path)
    conn = registry._runtime.connect()
    try:
        conn.execute(
            """
            UPDATE alpaca_crypto_protection_operator_events
            SET event_hash = ?
            WHERE preparation_hash = ? AND event_sequence = 1
            """,
            ("f" * 64, context.preparation_hash),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(CryptoProtectionOperatorDecisionIntegrityError, match="event hash mismatch"):
        registry.get(context.preparation_hash)


def test_protection_operator_event_chain_tamper_is_detected(tmp_path) -> None:
    _, registry, context, decision = _approval(tmp_path)
    registry.consume(
        decision=decision,
        attempt_id=context.attempt_id,
        now=NOW + timedelta(seconds=8),
    )
    conn = registry._runtime.connect()
    try:
        conn.execute(
            """
            UPDATE alpaca_crypto_protection_operator_events
            SET previous_event_hash = ?
            WHERE preparation_hash = ? AND event_sequence = 2
            """,
            ("e" * 64, context.preparation_hash),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(CryptoProtectionOperatorDecisionIntegrityError, match="event chain mismatch"):
        registry.get(context.preparation_hash)
