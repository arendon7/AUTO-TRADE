from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_bracket import AlpacaNestedBracketAttestation
from autotrade.brokers.alpaca_paper_trade_updates import (
    PaperTradeUpdateEventType,
    PaperTradeUpdateIntegrityError,
    PaperTradeUpdateParser,
    PaperTradeUpdateScope,
    PaperTradeUpdateScopeError,
    SQLitePaperTradeUpdateLedger,
)
from autotrade.persistence import SQLiteRuntime


UTC = timezone.utc
T0 = datetime(2026, 8, 11, 16, 0, tzinfo=UTC)


def scope() -> PaperTradeUpdateScope:
    return PaperTradeUpdateScope.from_bracket(
        symbol="AAPL",
        attestation=AlpacaNestedBracketAttestation(
            parent_order_id="parent-broker-001",
            client_order_id="autotrade-parent-001",
            take_profit_order_id="tp-broker-001",
            stop_loss_order_id="stop-broker-001",
            request_id="request-001",
            response_hash="a" * 64,
        ),
    )


def order_payload(
    *,
    broker_order_id: str = "parent-broker-001",
    client_order_id: str = "autotrade-parent-001",
    side: str = "buy",
    status: str = "new",
    qty: str = "1",
    filled_qty: str = "0",
    symbol: str = "AAPL",
    asset_class: str = "us_equity",
    order_type: str = "limit",
    order_class: str = "bracket",
    updated_at: datetime = T0,
):
    return {
        "id": broker_order_id,
        "client_order_id": client_order_id,
        "symbol": symbol,
        "asset_class": asset_class,
        "side": side,
        "type": order_type,
        "order_type": order_type,
        "order_class": order_class,
        "status": status,
        "qty": qty,
        "filled_qty": filled_qty,
        "updated_at": updated_at.isoformat().replace("+00:00", "Z"),
    }


def frame(
    event: str = "new",
    *,
    order=None,
    at: datetime = T0,
    execution_id: str = "execution-001",
    price: str = "10",
    fill_qty: str = "1",
    position_qty: str = "1",
) -> bytes:
    order = order or order_payload(status=event if event != "fill" else "filled")
    data = {"event": event, "order": order}
    if event in {"fill", "partial_fill"}:
        data.update(
            {
                "execution_id": execution_id,
                "timestamp": at.isoformat().replace("+00:00", "Z"),
                "price": price,
                "qty": fill_qty,
                "position_qty": position_qty,
            }
        )
    elif event in {"canceled", "expired", "replaced", "rejected"}:
        data["timestamp"] = at.isoformat().replace("+00:00", "Z")
    return json.dumps(
        {"stream": "trade_updates", "data": data},
        separators=(",", ":"),
    ).encode()


def parse(raw: bytes):
    return PaperTradeUpdateParser().parse(raw, scope=scope())


def test_parser_requires_binary_trade_updates_frame() -> None:
    with pytest.raises(PaperTradeUpdateIntegrityError, match="binary"):
        PaperTradeUpdateParser().parse("{}", scope=scope())  # type: ignore[arg-type]
    with pytest.raises(PaperTradeUpdateIntegrityError, match="size"):
        parse(b"")
    with pytest.raises(PaperTradeUpdateIntegrityError):
        parse(b"not-json")
    with pytest.raises(PaperTradeUpdateIntegrityError):
        parse(b'{"stream":"trade_updates","data":{"event":NaN}}')


def test_new_parent_event_is_strictly_scoped() -> None:
    event = parse(frame())
    assert event.event_type is PaperTradeUpdateEventType.NEW
    assert event.broker_order_id == scope().parent_order_id
    assert event.client_order_id == scope().parent_client_order_id
    assert event.side == "buy"
    assert event.filled_qty == Decimal("0")
    assert event.execution_id is None
    assert len(event.frame_hash) == 64
    assert len(event.event_hash) == 64


def test_fill_requires_execution_fields_and_exact_terminal_quantity() -> None:
    event = parse(
        frame(
            "fill",
            order=order_payload(status="filled", filled_qty="1"),
            execution_id="execution-parent-fill-001",
            price="9.99",
            fill_qty="1",
            position_qty="1",
        )
    )
    assert event.event_type is PaperTradeUpdateEventType.FILL
    assert event.fill_price == Decimal("9.99")
    assert event.fill_qty == Decimal("1")
    assert event.position_qty == Decimal("1")

    bad = json.loads(
        frame("fill", order=order_payload(status="filled", filled_qty="1")).decode()
    )
    bad["data"].pop("execution_id")
    with pytest.raises(PaperTradeUpdateIntegrityError, match="execution_id"):
        parse(json.dumps(bad).encode())

    with pytest.raises(PaperTradeUpdateIntegrityError, match="filled_qty == order qty"):
        parse(frame("fill", order=order_payload(status="filled", filled_qty="0.5")))


def test_partial_fill_requires_strict_cumulative_range() -> None:
    event = parse(
        frame(
            "partial_fill",
            order=order_payload(status="partially_filled", filled_qty="0.4"),
            execution_id="partial-execution-001",
            fill_qty="0.4",
        )
    )
    assert event.filled_qty == Decimal("0.4")
    for filled in ("0", "1"):
        with pytest.raises(PaperTradeUpdateIntegrityError, match="partial_fill"):
            parse(
                frame(
                    "partial_fill",
                    order=order_payload(status="partially_filled", filled_qty=filled),
                    execution_id=f"bad-partial-{filled}",
                )
            )


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (("id", "foreign-order"), "outside bracket scope"),
        (("client_order_id", "wrong-parent-client"), "client_order_id"),
        (("symbol", "MSFT"), "symbol"),
        (("asset_class", "crypto"), "us_equity"),
        (("side", "sell"), "parent trade update side"),
        (("status", "filled"), "event/status mismatch"),
    ],
)
def test_parent_scope_or_status_mismatch_fails_closed(mutation, reason) -> None:
    payload = order_payload()
    payload[mutation[0]] = mutation[1]
    with pytest.raises((PaperTradeUpdateScopeError, PaperTradeUpdateIntegrityError), match=reason):
        parse(frame(order=payload))


def test_protection_leg_requires_exact_broker_id_symbol_and_sell_side() -> None:
    leg = order_payload(
        broker_order_id=scope().take_profit_order_id,
        client_order_id="tp-client-opaque",
        side="sell",
        status="new",
    )
    parsed = parse(frame(order=leg))
    assert parsed.broker_order_id == scope().take_profit_order_id
    assert parsed.client_order_id == "tp-client-opaque"

    leg["side"] = "buy"
    with pytest.raises(PaperTradeUpdateScopeError, match="protection-leg"):
        parse(frame(order=leg))


def test_type_alias_conflict_and_noncanonical_decimals_fail_closed() -> None:
    payload = order_payload()
    payload["order_type"] = "market"
    with pytest.raises(PaperTradeUpdateIntegrityError, match="aliases conflict"):
        parse(frame(order=payload))

    for key, value in (("qty", "NaN"), ("filled_qty", "-1")):
        payload = order_payload()
        payload[key] = value
        with pytest.raises(PaperTradeUpdateIntegrityError):
            parse(frame(order=payload))


def test_unsupported_event_and_non_trade_stream_fail_closed() -> None:
    with pytest.raises(PaperTradeUpdateIntegrityError, match="unsupported"):
        parse(frame("mystery", order=order_payload(status="new")))
    payload = json.loads(frame().decode())
    payload["stream"] = "account_updates"
    with pytest.raises(PaperTradeUpdateIntegrityError, match="stream"):
        parse(json.dumps(payload).encode())


def test_ledger_is_append_only_idempotent_and_restart_safe(tmp_path) -> None:
    runtime = SQLiteRuntime(tmp_path / "trade-updates.sqlite")
    ledger = SQLitePaperTradeUpdateLedger(runtime, scope=scope())
    new_event = parse(frame())
    assert ledger.append(new_event) is True
    assert ledger.append(new_event) is False
    restarted = SQLitePaperTradeUpdateLedger(runtime, scope=scope())
    state = restarted.verify()
    assert state.event_count == 1
    assert state.parent_filled_qty == Decimal("0")
    assert restarted.events() == (new_event,)


def test_ledger_tracks_monotonic_parent_partial_then_fill(tmp_path) -> None:
    ledger = SQLitePaperTradeUpdateLedger(
        SQLiteRuntime(tmp_path / "parent-progress.sqlite"), scope=scope()
    )
    ledger.append(parse(frame()))
    ledger.append(
        parse(
            frame(
                "partial_fill",
                at=T0 + timedelta(seconds=1),
                order=order_payload(
                    status="partially_filled",
                    filled_qty="0.4",
                    updated_at=T0 + timedelta(seconds=1),
                ),
                execution_id="partial-parent-001",
                fill_qty="0.4",
            )
        )
    )
    ledger.append(
        parse(
            frame(
                "fill",
                at=T0 + timedelta(seconds=2),
                order=order_payload(
                    status="filled",
                    filled_qty="1",
                    updated_at=T0 + timedelta(seconds=2),
                ),
                execution_id="fill-parent-001",
                fill_qty="0.6",
            )
        )
    )
    state = ledger.verify()
    assert state.event_count == 3
    assert state.parent_filled_qty == Decimal("1")


def test_ledger_rejects_cumulative_regression_or_duplicate_partial_progress(tmp_path) -> None:
    ledger = SQLitePaperTradeUpdateLedger(
        SQLiteRuntime(tmp_path / "regression.sqlite"), scope=scope()
    )
    first = parse(
        frame(
            "partial_fill",
            order=order_payload(status="partially_filled", filled_qty="0.4"),
            execution_id="partial-1",
            fill_qty="0.4",
        )
    )
    ledger.append(first)
    for filled, execution in (("0.3", "partial-2"), ("0.4", "partial-3")):
        later = parse(
            frame(
                "partial_fill",
                at=T0 + timedelta(seconds=1),
                order=order_payload(
                    status="partially_filled",
                    filled_qty=filled,
                    updated_at=T0 + timedelta(seconds=1),
                ),
                execution_id=execution,
                fill_qty="0.1",
            )
        )
        with pytest.raises(PaperTradeUpdateIntegrityError):
            ledger.append(later)


def test_ledger_rejects_time_regression_before_commit(tmp_path) -> None:
    ledger = SQLitePaperTradeUpdateLedger(
        SQLiteRuntime(tmp_path / "time.sqlite"), scope=scope()
    )
    later = parse(frame(order=order_payload(updated_at=T0 + timedelta(seconds=2))))
    ledger.append(later)
    earlier = parse(
        frame(
            "accepted",
            order=order_payload(
                status="accepted",
                updated_at=T0 + timedelta(seconds=1),
            ),
        )
    )
    with pytest.raises(PaperTradeUpdateIntegrityError, match="backwards"):
        ledger.append(earlier)
    assert ledger.verify().event_count == 1


def test_protection_leg_cannot_fill_before_parent_fill_evidence(tmp_path) -> None:
    ledger = SQLitePaperTradeUpdateLedger(
        SQLiteRuntime(tmp_path / "protection-before-parent.sqlite"), scope=scope()
    )
    tp_fill = parse(
        frame(
            "fill",
            order=order_payload(
                broker_order_id=scope().take_profit_order_id,
                client_order_id="tp-client",
                side="sell",
                status="filled",
                filled_qty="1",
            ),
            execution_id="tp-fill-001",
            position_qty="0",
        )
    )
    with pytest.raises(PaperTradeUpdateIntegrityError, match="before parent"):
        ledger.append(tp_fill)


def test_both_bracket_protection_legs_cannot_fill(tmp_path) -> None:
    ledger = SQLitePaperTradeUpdateLedger(
        SQLiteRuntime(tmp_path / "oco.sqlite"), scope=scope()
    )
    parent = parse(
        frame(
            "fill",
            order=order_payload(status="filled", filled_qty="1"),
            execution_id="parent-fill-oco",
        )
    )
    ledger.append(parent)
    tp = parse(
        frame(
            "fill",
            at=T0 + timedelta(seconds=1),
            order=order_payload(
                broker_order_id=scope().take_profit_order_id,
                client_order_id="tp-client",
                side="sell",
                status="filled",
                filled_qty="1",
                updated_at=T0 + timedelta(seconds=1),
            ),
            execution_id="tp-fill-oco",
            position_qty="0",
        )
    )
    ledger.append(tp)
    stop = parse(
        frame(
            "fill",
            at=T0 + timedelta(seconds=2),
            order=order_payload(
                broker_order_id=scope().stop_loss_order_id,
                client_order_id="stop-client",
                side="sell",
                status="filled",
                filled_qty="1",
                updated_at=T0 + timedelta(seconds=2),
            ),
            execution_id="stop-fill-oco",
            position_qty="0",
        )
    )
    with pytest.raises(PaperTradeUpdateIntegrityError, match="both bracket"):
        ledger.append(stop)


def test_same_execution_id_with_changed_content_is_conflict(tmp_path) -> None:
    ledger = SQLitePaperTradeUpdateLedger(
        SQLiteRuntime(tmp_path / "execution-conflict.sqlite"), scope=scope()
    )
    first = parse(
        frame(
            "partial_fill",
            order=order_payload(status="partially_filled", filled_qty="0.4"),
            execution_id="same-execution",
            price="10",
            fill_qty="0.4",
        )
    )
    ledger.append(first)
    conflicting = parse(
        frame(
            "partial_fill",
            order=order_payload(status="partially_filled", filled_qty="0.4"),
            execution_id="same-execution",
            price="10.01",
            fill_qty="0.4",
        )
    )
    with pytest.raises(PaperTradeUpdateIntegrityError, match="conflicting"):
        ledger.append(conflicting)


def test_tail_deletion_control_mutation_and_event_mutation_are_detected(tmp_path) -> None:
    runtime = SQLiteRuntime(tmp_path / "tamper.sqlite")
    ledger = SQLitePaperTradeUpdateLedger(runtime, scope=scope())
    ledger.append(parse(frame()))
    second = parse(
        frame(
            "accepted",
            order=order_payload(
                status="accepted",
                updated_at=T0 + timedelta(seconds=1),
            ),
        )
    )
    ledger.append(second)
    with sqlite3.connect(runtime.path) as conn:
        conn.execute(
            "DELETE FROM alpaca_paper_trade_update_events WHERE scope_hash=? AND sequence=2",
            (ledger._scope_hash,),  # adversarial direct corruption
        )
        conn.commit()
    with pytest.raises(PaperTradeUpdateIntegrityError, match="anchored control"):
        ledger.verify()

    runtime2 = SQLiteRuntime(tmp_path / "control.sqlite")
    ledger2 = SQLitePaperTradeUpdateLedger(runtime2, scope=scope())
    ledger2.append(parse(frame()))
    with sqlite3.connect(runtime2.path) as conn:
        conn.execute(
            "UPDATE alpaca_paper_trade_update_control SET head_hash=? WHERE scope_hash=?",
            ("f" * 64, ledger2._scope_hash),
        )
        conn.commit()
    with pytest.raises(PaperTradeUpdateIntegrityError):
        ledger2.verify()

    runtime3 = SQLiteRuntime(tmp_path / "event.sqlite")
    ledger3 = SQLitePaperTradeUpdateLedger(runtime3, scope=scope())
    ledger3.append(parse(frame()))
    with sqlite3.connect(runtime3.path) as conn:
        conn.execute(
            "UPDATE alpaca_paper_trade_update_events SET event_json=? WHERE scope_hash=? AND sequence=1",
            ("{}", ledger3._scope_hash),
        )
        conn.commit()
    with pytest.raises(PaperTradeUpdateIntegrityError):
        ledger3.verify()
