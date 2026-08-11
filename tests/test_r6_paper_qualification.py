from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_bracket import (
    AlpacaEquityBracketBuilder,
    AlpacaNestedBracketAttestation,
    PaperEquityVenueRules,
)
from autotrade.brokers.alpaca_paper_qualification import (
    AlpacaPaperQualificationEvaluator,
    PaperQualificationIntegrityError,
    PaperQualificationPolicy,
    PaperQualificationRejected,
    PaperQualificationReport,
)
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionBinding,
    SQLitePaperSubmissionRegistry,
)
from autotrade.brokers.alpaca_paper_trade_updates import (
    PaperTradeUpdateParser,
    PaperTradeUpdateScope,
    SQLitePaperTradeUpdateLedger,
)
from autotrade.domain import OrderIntent, OrderRecord, OrderStatus, OrderType, Side
from autotrade.persistence import SQLiteRuntime
from test_r6_paper_trade_updates import frame, order_payload


UTC = timezone.utc
T0 = datetime(2026, 8, 11, 16, 30, tzinfo=UTC)


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def order(order_id="qualification-order-001", idempotency="qualification-idem-001"):
    return OrderRecord(
        order_id=order_id,
        intent=OrderIntent(
            intent_id=f"intent-{order_id}",
            idempotency_key=idempotency,
            strategy_id="qualification-strategy",
            symbol="AAPL",
            side=Side.BUY,
            quantity=Decimal("1"),
            order_type=OrderType.LIMIT,
            created_at=T0 - timedelta(seconds=2),
            limit_price=Decimal("10"),
        ),
        risk_decision_id=f"risk-{order_id}",
        status=OrderStatus.VALIDATED,
        created_at=T0 - timedelta(seconds=1),
    )


def bracket(current_order=None):
    current_order = current_order or order()
    return AlpacaEquityBracketBuilder().build(
        order=current_order,
        venue_rules=PaperEquityVenueRules(
            symbol="AAPL",
            asset_class="us_equity",
            price_tick=Decimal("0.01"),
            quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"),
            instrument_master_fingerprint=h("qualification-instrument"),
        ),
        take_profit_price=Decimal("10.50"),
        stop_loss_price=Decimal("9.50"),
    )


def attestation(expected=None, **overrides):
    expected = expected or bracket()
    values = {
        "parent_order_id": "parent-broker-001",
        "client_order_id": expected.client_order_id,
        "take_profit_order_id": "tp-broker-001",
        "stop_loss_order_id": "stop-broker-001",
        "request_id": "nested-request-001",
        "response_hash": h("nested-response"),
    }
    values.update(overrides)
    return AlpacaNestedBracketAttestation(**values)


def event_frame(
    expected,
    event="new",
    *,
    at=T0 + timedelta(seconds=3),
    filled_qty="0",
    execution_id="execution-001",
    price="10",
    fill_qty="1",
):
    status = {
        "new": "new",
        "partial_fill": "partially_filled",
        "fill": "filled",
        "canceled": "canceled",
        "expired": "expired",
        "rejected": "rejected",
        "accepted": "accepted",
    }.get(event, event)
    return frame(
        event,
        at=at,
        order=order_payload(
            broker_order_id="parent-broker-001",
            client_order_id=expected.client_order_id,
            side="buy",
            status=status,
            filled_qty=filled_qty,
            updated_at=at,
        ),
        execution_id=execution_id,
        price=price,
        fill_qty=fill_qty,
        position_qty=filled_qty if filled_qty != "0" else "0",
    )


def setup_evidence(
    tmp_path,
    *,
    partial=True,
    final_price="10",
    final_at=T0 + timedelta(seconds=4),
    acknowledge=True,
    include_fill=True,
    final_filled_qty="1",
):
    current_order = order()
    expected = bracket(current_order)
    bracket_attestation = attestation(expected)
    submission_registry = SQLitePaperSubmissionRegistry(
        SQLiteRuntime(tmp_path / "submission.sqlite")
    )
    binding = PaperSubmissionBinding.from_order(
        order=current_order,
        account_attestation_fingerprint=h("qualification-account"),
        order_payload_hash=expected.payload_hash,
        created_at=T0,
    )
    submission_registry.prepare(binding)
    submission_registry.mark_submit_attempt_unknown(
        order_id=current_order.order_id,
        attempt_id="qualification-attempt-001",
        now=T0 + timedelta(seconds=1),
    )
    if acknowledge:
        submission_registry.reconcile_acknowledged(
            order_id=current_order.order_id,
            broker_order_id=bracket_attestation.parent_order_id,
            broker_client_order_id=bracket_attestation.client_order_id,
            broker_order_payload_hash=expected.payload_hash,
            request_id="qualification-reconcile-001",
            now=T0 + timedelta(seconds=2),
        )

    scope = PaperTradeUpdateScope.from_bracket(
        symbol="AAPL", attestation=bracket_attestation
    )
    ledger = SQLitePaperTradeUpdateLedger(
        SQLiteRuntime(tmp_path / "updates.sqlite"), scope=scope
    )
    parser = PaperTradeUpdateParser()
    ledger.append(parser.parse(event_frame(expected, "new"), scope=scope))
    if partial:
        ledger.append(
            parser.parse(
                event_frame(
                    expected,
                    "partial_fill",
                    at=T0 + timedelta(seconds=3, milliseconds=250),
                    filled_qty="0.4",
                    execution_id="qualification-partial-001",
                    price="9.99",
                    fill_qty="0.4",
                ),
                scope=scope,
            )
        )
    if include_fill:
        ledger.append(
            parser.parse(
                event_frame(
                    expected,
                    "fill",
                    at=final_at,
                    filled_qty=final_filled_qty,
                    execution_id="qualification-fill-001",
                    price=final_price,
                    fill_qty="0.6" if partial else final_filled_qty,
                ),
                scope=scope,
            )
        )
    return current_order, expected, bracket_attestation, submission_registry, ledger


def qualify(values, *, policy=None, evaluated_at=T0 + timedelta(seconds=5)):
    _, expected, bracket_attestation, registry, ledger = values
    return AlpacaPaperQualificationEvaluator(policy).qualify(
        expected_bracket=expected,
        bracket_attestation=bracket_attestation,
        submission_registry=registry,
        trade_update_ledger=ledger,
        evaluated_at=evaluated_at,
    )


def test_successful_qualification_aggregates_partial_and_terminal_fill(tmp_path) -> None:
    report = qualify(setup_evidence(tmp_path))
    assert report.expected_quantity == Decimal("1")
    assert report.filled_quantity == Decimal("1.0")
    assert report.fill_count == 2
    assert report.average_fill_price == Decimal("9.996")
    assert report.signed_slippage_bps == Decimal("-4.0000")
    assert report.adverse_slippage_bps == Decimal("0")
    assert report.submit_to_fill_seconds == Decimal("3.0")
    assert report.report_hash == report.fingerprint
    assert report.parent_broker_order_id == "parent-broker-001"


def test_single_fill_success_and_adverse_slippage_calculation(tmp_path) -> None:
    values = setup_evidence(tmp_path, partial=False, final_price="10.02")
    report = qualify(
        values,
        policy=PaperQualificationPolicy(max_adverse_slippage_bps=Decimal("25")),
    )
    assert report.fill_count == 1
    assert report.average_fill_price == Decimal("10.02")
    assert report.signed_slippage_bps == Decimal("20.000")
    assert report.adverse_slippage_bps == Decimal("20.000")


def test_report_artifact_roundtrip_preserves_nonclaims(tmp_path) -> None:
    report = qualify(setup_evidence(tmp_path / "evidence"))
    target = tmp_path / "qualification.json"
    report.write(target)
    loaded = PaperQualificationReport.read(target)
    assert loaded == report
    document = json.loads(target.read_text())
    assert document["external_paper_qualified"] is True
    assert document["capital_authority"] == "NONE"
    assert document["live_trading"] == "BLOCKED"
    assert document["profitability_claim"] is False


def test_artifact_value_tamper_and_authority_tamper_are_detected(tmp_path) -> None:
    report = qualify(setup_evidence(tmp_path / "setup"))
    target = tmp_path / "qualification.json"
    report.write(target)

    document = json.loads(target.read_text())
    document["average_fill_price"] = "999"
    target.write_text(json.dumps(document))
    with pytest.raises(PaperQualificationIntegrityError, match="hash mismatch"):
        PaperQualificationReport.read(target)

    report.write(target)
    document = json.loads(target.read_text())
    document["live_trading"] = "ENABLED"
    target.write_text(json.dumps(document))
    with pytest.raises(PaperQualificationIntegrityError, match="non-claims"):
        PaperQualificationReport.read(target)


def test_report_write_refuses_in_memory_hash_tamper(tmp_path) -> None:
    report = qualify(setup_evidence(tmp_path / "setup"))
    tampered = replace(report, report_hash="f" * 64)
    with pytest.raises(PaperQualificationIntegrityError, match="hash mismatch"):
        tampered.write(tmp_path / "bad.json")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_adverse_slippage_bps": Decimal("-1")},
        {"max_adverse_slippage_bps": Decimal("501")},
        {"max_adverse_slippage_bps": Decimal("NaN")},
        {"max_submit_to_fill_seconds": Decimal("0")},
        {"max_submit_to_fill_seconds": Decimal("3601")},
        {"max_submit_to_fill_seconds": Decimal("NaN")},
    ],
)
def test_policy_bounds_are_fail_closed(kwargs) -> None:
    with pytest.raises(ValueError):
        PaperQualificationPolicy(**kwargs)


def test_unacknowledged_submission_is_not_qualified(tmp_path) -> None:
    values = setup_evidence(tmp_path, acknowledge=False)
    with pytest.raises(PaperQualificationRejected, match="ACKNOWLEDGED"):
        qualify(values)


def test_binding_payload_or_client_identity_mismatch_is_rejected(tmp_path) -> None:
    values = setup_evidence(tmp_path)
    _, expected, attested, registry, ledger = values
    wrong_payload = replace(expected, payload_hash=h("different-payload"))
    with pytest.raises(PaperQualificationRejected, match="payload hash"):
        AlpacaPaperQualificationEvaluator().qualify(
            expected_bracket=wrong_payload,
            bracket_attestation=attested,
            submission_registry=registry,
            trade_update_ledger=ledger,
            evaluated_at=T0 + timedelta(seconds=5),
        )
    wrong_client = replace(expected, client_order_id="different-client")
    with pytest.raises(PaperQualificationRejected, match="client_order_id"):
        AlpacaPaperQualificationEvaluator().qualify(
            expected_bracket=wrong_client,
            bracket_attestation=attested,
            submission_registry=registry,
            trade_update_ledger=ledger,
            evaluated_at=T0 + timedelta(seconds=5),
        )


def test_bracket_attestation_identity_mismatch_is_rejected(tmp_path) -> None:
    values = setup_evidence(tmp_path)
    current_order, expected, attested, registry, ledger = values
    wrong = replace(attested, parent_order_id="different-parent")
    with pytest.raises(PaperQualificationRejected, match="parent broker"):
        AlpacaPaperQualificationEvaluator().qualify(
            expected_bracket=expected,
            bracket_attestation=wrong,
            submission_registry=registry,
            trade_update_ledger=ledger,
            evaluated_at=T0 + timedelta(seconds=5),
        )


def test_trade_update_scope_mismatch_is_rejected(tmp_path) -> None:
    values = setup_evidence(tmp_path / "base")
    _, expected, attested, registry, _ = values
    foreign_attestation = replace(attested, take_profit_order_id="different-tp")
    foreign_scope = PaperTradeUpdateScope.from_bracket(
        symbol="AAPL", attestation=foreign_attestation
    )
    ledger = SQLitePaperTradeUpdateLedger(
        SQLiteRuntime(tmp_path / "foreign.sqlite"), scope=foreign_scope
    )
    parser = PaperTradeUpdateParser()
    ledger.append(parser.parse(event_frame(expected, "new"), scope=foreign_scope))
    ledger.append(
        parser.parse(
            event_frame(
                expected,
                "fill",
                at=T0 + timedelta(seconds=4),
                filled_qty="1",
                execution_id="foreign-scope-fill",
            ),
            scope=foreign_scope,
        )
    )
    with pytest.raises(PaperQualificationRejected, match="take-profit"):
        AlpacaPaperQualificationEvaluator().qualify(
            expected_bracket=expected,
            bracket_attestation=attested,
            submission_registry=registry,
            trade_update_ledger=ledger,
            evaluated_at=T0 + timedelta(seconds=5),
        )


def test_no_fill_or_negative_terminal_parent_cannot_qualify(tmp_path) -> None:
    values = setup_evidence(tmp_path / "nofill", include_fill=False, partial=False)
    with pytest.raises(PaperQualificationRejected) as exc:
        qualify(values)
    assert "no fill evidence" in str(exc.value)
    assert "terminal fill" in str(exc.value)

    values = setup_evidence(tmp_path / "cancel", include_fill=False, partial=False)
    _, expected, _, _, ledger = values
    parser = PaperTradeUpdateParser()
    ledger.append(
        parser.parse(
            event_frame(
                expected,
                "canceled",
                at=T0 + timedelta(seconds=4),
            ),
            scope=ledger.scope,
        )
    )
    with pytest.raises(PaperQualificationRejected, match="terminated"):
        qualify(values)


def test_incomplete_aggregated_fill_quantity_is_rejected(tmp_path) -> None:
    values = setup_evidence(
        tmp_path,
        partial=False,
        final_filled_qty="0.5",
        include_fill=False,
    )
    _, expected, _, _, ledger = values
    parser = PaperTradeUpdateParser()
    ledger.append(
        parser.parse(
            event_frame(
                expected,
                "partial_fill",
                at=T0 + timedelta(seconds=4),
                filled_qty="0.5",
                execution_id="incomplete-partial",
                fill_qty="0.5",
            ),
            scope=ledger.scope,
        )
    )
    with pytest.raises(PaperQualificationRejected, match="quantity"):
        qualify(values)


def test_slippage_and_latency_policy_breaches_reject(tmp_path) -> None:
    values = setup_evidence(tmp_path / "slippage", partial=False, final_price="10.06")
    with pytest.raises(PaperQualificationRejected, match="slippage"):
        qualify(
            values,
            policy=PaperQualificationPolicy(max_adverse_slippage_bps=Decimal("50")),
        )

    values = setup_evidence(
        tmp_path / "latency",
        partial=False,
        final_at=T0 + timedelta(seconds=130),
    )
    with pytest.raises(PaperQualificationRejected, match="latency"):
        qualify(values)


def test_evaluated_at_must_not_precede_terminal_evidence(tmp_path) -> None:
    values = setup_evidence(tmp_path)
    with pytest.raises(PaperQualificationRejected, match="precedes durable evidence"):
        qualify(values, evaluated_at=T0 + timedelta(seconds=3))


def test_second_attempted_submission_blocks_first_canary_qualification(tmp_path) -> None:
    values = setup_evidence(tmp_path)
    _, expected, _, registry, _ = values
    other = order("qualification-order-002", "qualification-idem-002")
    other_expected = bracket(other)
    other_binding = PaperSubmissionBinding.from_order(
        order=other,
        account_attestation_fingerprint=h("other-account"),
        order_payload_hash=other_expected.payload_hash,
        created_at=T0,
    )
    registry.prepare(other_binding)
    registry.mark_submit_attempt_unknown(
        order_id=other.order_id,
        attempt_id="other-attempt",
        now=T0 + timedelta(seconds=1),
    )
    with pytest.raises(PaperQualificationRejected, match="another attempted"):
        qualify(values)


def test_submission_or_trade_ledger_tamper_prevents_qualification(tmp_path) -> None:
    values = setup_evidence(tmp_path / "submission")
    registry = values[3]
    with sqlite3.connect(registry._runtime.path) as conn:
        conn.execute(
            "UPDATE alpaca_paper_submission_control SET control_hash=? WHERE order_id=?",
            ("f" * 64, values[0].order_id),
        )
        conn.commit()
    with pytest.raises(Exception):
        qualify(values)

    values = setup_evidence(tmp_path / "ledger")
    ledger = values[4]
    with sqlite3.connect(ledger._runtime.path) as conn:
        conn.execute(
            "UPDATE alpaca_paper_trade_update_control SET head_hash=? WHERE scope_hash=?",
            ("f" * 64, ledger._scope_hash),
        )
        conn.commit()
    with pytest.raises(Exception):
        qualify(values)


def test_artifact_rejects_unreadable_unsupported_or_changed_status(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(PaperQualificationIntegrityError, match="unreadable"):
        PaperQualificationReport.read(missing)

    target = tmp_path / "bad.json"
    target.write_text("[]")
    with pytest.raises(PaperQualificationIntegrityError, match="unsupported"):
        PaperQualificationReport.read(target)

    report = qualify(setup_evidence(tmp_path / "setup"))
    report.write(target)
    document = json.loads(target.read_text())
    document["external_paper_qualified"] = False
    target.write_text(json.dumps(document))
    with pytest.raises(PaperQualificationIntegrityError, match="status changed"):
        PaperQualificationReport.read(target)


def test_report_constructor_invariants_reject_invalid_shape(tmp_path) -> None:
    report = qualify(setup_evidence(tmp_path))
    with pytest.raises(ValueError, match="fill_count"):
        replace(report, fill_count=0)
    with pytest.raises(ValueError, match="filled_quantity must equal"):
        replace(report, filled_quantity=Decimal("0.5"))
    with pytest.raises(ValueError, match="finite and > 0"):
        replace(report, average_fill_price=Decimal("0"))
    with pytest.raises(ValueError, match="cannot be negative"):
        replace(report, adverse_slippage_bps=Decimal("-1"))
    with pytest.raises(ValueError, match="terminal fill cannot precede"):
        replace(report, terminal_fill_at=report.submit_attempt_at - timedelta(seconds=1))
