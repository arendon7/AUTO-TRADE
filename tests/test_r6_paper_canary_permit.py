from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_canary import (
    PaperCanaryContext,
    PaperCanaryGate,
    PaperCanaryPolicy,
)
from autotrade.brokers.alpaca_paper_canary_permit import (
    PaperCanaryPermitConflict,
    PaperCanaryPermitExpired,
    PaperCanaryPermitIntegrityError,
    PaperCanaryPermitStatus,
    SQLitePaperCanaryPermitRegistry,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionBinding,
    SQLitePaperSubmissionRegistry,
)
from autotrade.domain import OrderIntent, OrderRecord, OrderStatus, OrderType, Side
from autotrade.persistence import SQLiteRuntime


UTC = timezone.utc
NOW = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
TRACKS = ("R0", "R1", "R2", "R3", "R4", "R5")


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def order(suffix: str = "001") -> OrderRecord:
    intent = OrderIntent(
        intent_id=f"permit-intent-{suffix}",
        strategy_id="permit-strategy",
        symbol="AAPL",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        limit_price=Decimal("10"),
        idempotency_key=f"permit-idempotency-{suffix}",
        created_at=NOW - timedelta(seconds=2),
    )
    return OrderRecord(
        order_id=f"permit-order-{suffix}",
        intent=intent,
        status=OrderStatus.VALIDATED,
        risk_decision_id=f"permit-risk-{suffix}",
        created_at=NOW - timedelta(seconds=1),
    )


def attestation() -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id="e6fe16f3-64a4-4921-8928-cadf02f92f98",
        account_reference=h("paper-account"),
        credential_reference=h("paper-key"),
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=True,
        attested_at=NOW,
        request_id="account-request-permit",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )


def approval(tmp_path, suffix: str = "001"):
    current_order = order(suffix)
    account = attestation()
    submission_runtime = SQLiteRuntime(tmp_path / f"submission-{suffix}.sqlite")
    submission = SQLitePaperSubmissionRegistry(submission_runtime)
    binding = PaperSubmissionBinding.from_order(
        order=current_order,
        account_attestation_fingerprint=account.fingerprint,
        order_payload_hash=h(f"payload-{suffix}"),
        created_at=NOW - timedelta(milliseconds=500),
    )
    state = submission.prepare(binding)
    return PaperCanaryGate(
        PaperCanaryPolicy(
            enabled=True,
            max_notional=Decimal("10"),
            max_account_fraction=Decimal("0.001"),
            max_attestation_age_seconds=30,
            approval_ttl_seconds=5,
        )
    ).approve(
        PaperCanaryContext(
            order=current_order,
            binding=binding,
            submission_state=state,
            account_attestation=account,
            now=NOW,
            certified_tracks=TRACKS,
            reconciliation_clean=True,
            unresolved_unknown_orders=0,
            kill_switch_engaged=False,
            health_allows_new_exposure=True,
            prior_canary_submissions=0,
        )
    )


def permit_runtime(tmp_path) -> SQLiteRuntime:
    return SQLiteRuntime(tmp_path / "permit.sqlite")


def test_issue_is_idempotent_and_persists_across_restart(tmp_path) -> None:
    approved = approval(tmp_path)
    rt = permit_runtime(tmp_path)
    registry = SQLitePaperCanaryPermitRegistry(rt)
    first = registry.issue(approved)
    replay = registry.issue(approved)

    assert first == replay
    assert first.status is PaperCanaryPermitStatus.ISSUED
    assert first.attempt_id is None
    assert first.consumed_at is None
    assert first.approval_hash == approved.approval_hash
    assert len(registry.list_states()) == 1
    assert SQLitePaperCanaryPermitRegistry(rt).get(approved.approval_hash) == first


def test_consume_is_one_shot_and_same_attempt_replay_is_idempotent(tmp_path) -> None:
    approved = approval(tmp_path)
    rt = permit_runtime(tmp_path)
    registry = SQLitePaperCanaryPermitRegistry(rt)
    registry.issue(approved)

    consumed = registry.consume(
        approval=approved,
        attempt_id="permit-attempt-001",
        now=NOW + timedelta(seconds=1),
    )
    replay = registry.consume(
        approval=approved,
        attempt_id="permit-attempt-001",
        now=NOW + timedelta(seconds=2),
    )

    assert consumed == replay
    assert consumed.status is PaperCanaryPermitStatus.CONSUMED
    assert consumed.attempt_id == "permit-attempt-001"
    assert consumed.consumed_at == NOW + timedelta(seconds=1)
    assert SQLitePaperCanaryPermitRegistry(rt).get(approved.approval_hash) == consumed

    with pytest.raises(PaperCanaryPermitConflict, match="another attempt"):
        registry.consume(
            approval=approved,
            attempt_id="permit-attempt-002",
            now=NOW + timedelta(seconds=2),
        )


def test_expired_or_preissued_consumption_is_rejected_without_state_change(tmp_path) -> None:
    approved = approval(tmp_path)
    registry = SQLitePaperCanaryPermitRegistry(permit_runtime(tmp_path))
    issued = registry.issue(approved)

    with pytest.raises(PaperCanaryPermitConflict, match="before issuance"):
        registry.consume(
            approval=approved,
            attempt_id="attempt-pre",
            now=NOW - timedelta(microseconds=1),
        )
    assert registry.get(approved.approval_hash) == issued

    with pytest.raises(PaperCanaryPermitExpired, match="expired"):
        registry.consume(
            approval=approved,
            attempt_id="attempt-expired",
            now=NOW + timedelta(seconds=5),
        )
    assert registry.get(approved.approval_hash) == issued


def test_multiple_permits_share_one_anchored_append_only_chain(tmp_path) -> None:
    first = approval(tmp_path / "a", "001")
    second = approval(tmp_path / "b", "002")
    rt = permit_runtime(tmp_path)
    registry = SQLitePaperCanaryPermitRegistry(rt)
    registry.issue(first)
    registry.issue(second)
    registry.consume(
        approval=first,
        attempt_id="attempt-first",
        now=NOW + timedelta(seconds=1),
    )

    states = registry.list_states()
    assert len(states) == 2
    by_hash = {state.approval_hash: state for state in states}
    assert by_hash[first.approval_hash].status is PaperCanaryPermitStatus.CONSUMED
    assert by_hash[second.approval_hash].status is PaperCanaryPermitStatus.ISSUED

    with sqlite3.connect(rt.path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM alpaca_paper_canary_permit_events"
        ).fetchone()[0]
        anchor = conn.execute(
            "SELECT event_sequence FROM alpaca_paper_canary_permit_control WHERE singleton=1"
        ).fetchone()[0]
    assert count == anchor == 3


def test_tail_deletion_is_detected_by_control_anchor(tmp_path) -> None:
    approved = approval(tmp_path)
    rt = permit_runtime(tmp_path)
    registry = SQLitePaperCanaryPermitRegistry(rt)
    registry.issue(approved)
    registry.consume(
        approval=approved,
        attempt_id="attempt-001",
        now=NOW + timedelta(seconds=1),
    )

    with sqlite3.connect(rt.path) as conn:
        conn.execute("DELETE FROM alpaca_paper_canary_permit_events WHERE sequence = 2")
        conn.commit()
    with pytest.raises(PaperCanaryPermitIntegrityError, match="event count"):
        registry.get(approved.approval_hash)


def test_middle_deletion_and_reordering_are_detected(tmp_path) -> None:
    first = approval(tmp_path / "a", "001")
    second = approval(tmp_path / "b", "002")
    rt = permit_runtime(tmp_path)
    registry = SQLitePaperCanaryPermitRegistry(rt)
    registry.issue(first)
    registry.issue(second)
    registry.consume(
        approval=first,
        attempt_id="attempt-first",
        now=NOW + timedelta(seconds=1),
    )

    with sqlite3.connect(rt.path) as conn:
        conn.execute("DELETE FROM alpaca_paper_canary_permit_events WHERE sequence = 2")
        conn.commit()
    with pytest.raises(PaperCanaryPermitIntegrityError):
        registry.list_states()


def test_event_payload_mutation_is_detected(tmp_path) -> None:
    approved = approval(tmp_path)
    rt = permit_runtime(tmp_path)
    registry = SQLitePaperCanaryPermitRegistry(rt)
    registry.issue(approved)

    with sqlite3.connect(rt.path) as conn:
        raw = conn.execute(
            "SELECT payload_json FROM alpaca_paper_canary_permit_events WHERE sequence=1"
        ).fetchone()[0]
        payload = json.loads(raw)
        payload["order_id"] = "forged-order"
        conn.execute(
            "UPDATE alpaca_paper_canary_permit_events SET payload_json=? WHERE sequence=1",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")),),
        )
        conn.commit()
    with pytest.raises(PaperCanaryPermitIntegrityError, match="hash mismatch"):
        registry.get(approved.approval_hash)


def test_control_mutation_or_removal_is_detected(tmp_path) -> None:
    approved = approval(tmp_path)
    rt = permit_runtime(tmp_path)
    registry = SQLitePaperCanaryPermitRegistry(rt)
    registry.issue(approved)

    with sqlite3.connect(rt.path) as conn:
        conn.execute(
            "UPDATE alpaca_paper_canary_permit_control SET event_head_hash=? WHERE singleton=1",
            (h("forged"),),
        )
        conn.commit()
    with pytest.raises(PaperCanaryPermitIntegrityError, match="control hash"):
        registry.get(approved.approval_hash)

    rt2 = SQLiteRuntime(tmp_path / "permit2.sqlite")
    registry2 = SQLitePaperCanaryPermitRegistry(rt2)
    approved2 = approval(tmp_path / "c", "003")
    registry2.issue(approved2)
    with sqlite3.connect(rt2.path) as conn:
        conn.execute("DELETE FROM alpaca_paper_canary_permit_control WHERE singleton=1")
        conn.commit()
    with pytest.raises(PaperCanaryPermitIntegrityError, match="control anchor"):
        registry2.get(approved2.approval_hash)


def test_tampered_approval_object_is_rejected_before_ledger_mutation(tmp_path) -> None:
    approved = approval(tmp_path)
    registry = SQLitePaperCanaryPermitRegistry(permit_runtime(tmp_path))
    from dataclasses import replace

    tampered = replace(approved, notional=Decimal("9"))
    with pytest.raises(ValueError, match="approval hash"):
        registry.issue(tampered)
    assert registry.list_states() == ()


def test_registry_has_no_network_or_order_write_surface(tmp_path) -> None:
    registry = SQLitePaperCanaryPermitRegistry(permit_runtime(tmp_path))
    forbidden = {
        "post",
        "send",
        "submit",
        "submit_order",
        "create_order",
        "place_order",
        "cancel_order",
        "replace_order",
    }
    assert not (forbidden & set(dir(registry)))
