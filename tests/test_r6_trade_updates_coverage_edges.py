from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_trade_updates import (
    PaperTradeUpdateEvent,
    PaperTradeUpdateEventType,
    PaperTradeUpdateIntegrityError,
    PaperTradeUpdateParser,
    PaperTradeUpdateScope,
    PaperTradeUpdateScopeError,
    SQLitePaperTradeUpdateLedger,
)
from autotrade.persistence import SQLiteRuntime
from test_r6_paper_trade_updates import T0, frame, order_payload, parse, scope


UTC = timezone.utc


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"symbol": "aapl"}, "symbol"),
        ({"parent_order_id": "bad id"}, "parent_order_id"),
        ({"parent_client_order_id": ""}, "parent_client_order_id"),
        ({"take_profit_order_id": "parent-broker-001"}, "distinct"),
        ({"stop_loss_order_id": "tp-broker-001"}, "distinct"),
    ],
)
def test_scope_constructor_rejects_noncanonical_identity(kwargs, reason) -> None:
    values = {
        "symbol": "AAPL",
        "parent_order_id": "parent-broker-001",
        "parent_client_order_id": "autotrade-parent-001",
        "take_profit_order_id": "tp-broker-001",
        "stop_loss_order_id": "stop-broker-001",
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=reason):
        PaperTradeUpdateScope(**values)


def valid_event(**overrides) -> PaperTradeUpdateEvent:
    base = parse(frame())
    values = {field: getattr(base, field) for field in base.__dataclass_fields__}
    values.update(overrides)
    return PaperTradeUpdateEvent(**values)


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"event_type": "new"}, "event_type"),
        ({"broker_order_id": "bad id"}, "broker_order_id"),
        ({"client_order_id": "bad id"}, "client_order_id"),
        ({"symbol": "aapl"}, "symbol"),
        ({"side": "hold"}, "side"),
        ({"asset_class": "crypto"}, "us_equity"),
        ({"order_qty": Decimal("0")}, "order_qty"),
        ({"filled_qty": Decimal("-1")}, "filled_qty"),
        ({"filled_qty": Decimal("2")}, "exceed"),
        ({"occurred_at": datetime(2026, 8, 11, 16, 0)}, "timezone-aware"),
        ({"frame_hash": "bad"}, "hashes"),
        ({"event_hash": "bad"}, "hashes"),
    ],
)
def test_event_dataclass_rejects_invalid_canonical_fields(overrides, reason) -> None:
    with pytest.raises(ValueError, match=reason):
        valid_event(**overrides)


def test_fill_event_dataclass_requires_all_fill_fields() -> None:
    fill = parse(
        frame(
            "fill",
            order=order_payload(status="filled", filled_qty="1"),
            execution_id="direct-fill-001",
        )
    )
    for field, replacement, reason in (
        ("execution_id", None, "execution_id"),
        ("fill_price", None, "fill_price"),
        ("fill_qty", Decimal("0"), "fill_qty"),
        ("position_qty", None, "position_qty"),
    ):
        values = {name: getattr(fill, name) for name in fill.__dataclass_fields__}
        values[field] = replacement
        with pytest.raises(ValueError, match=reason):
            PaperTradeUpdateEvent(**values)


def test_nonfill_event_dataclass_rejects_fill_only_fields() -> None:
    with pytest.raises(ValueError, match="fill-only"):
        valid_event(execution_id="unexpected-execution")


def test_terminal_parent_candidate_property_covers_terminal_and_nonterminal() -> None:
    assert parse(
        frame(
            "fill",
            order=order_payload(status="filled", filled_qty="1"),
            execution_id="terminal-property-fill",
        )
    ).terminal_parent_candidate
    assert not parse(frame()).terminal_parent_candidate


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"stream": "trade_updates", "data": []}, "data"),
        ({"stream": "trade_updates", "data": {"event": "new", "order": []}}, "order"),
    ],
)
def test_parser_rejects_nonobject_data_or_order(payload, reason) -> None:
    with pytest.raises(PaperTradeUpdateIntegrityError, match=reason):
        PaperTradeUpdateParser().parse(json.dumps(payload).encode(), scope=scope())


@pytest.mark.parametrize(
    "key,value,reason",
    [
        ("id", "bad id", "canonical identifier"),
        ("client_order_id", "bad id", "canonical identifier"),
        ("order_class", None, "order_class"),
        ("qty", 1, "decimal string"),
        ("filled_qty", "2", "exceeds"),
    ],
)
def test_parser_rejects_malformed_order_shape(key, value, reason) -> None:
    order = order_payload()
    order[key] = value
    with pytest.raises(PaperTradeUpdateIntegrityError, match=reason):
        parse(frame(order=order))


def test_parser_rejects_missing_type_or_invalid_timestamp() -> None:
    order = order_payload()
    order.pop("type")
    order.pop("order_type")
    with pytest.raises(PaperTradeUpdateIntegrityError, match="type is missing"):
        parse(frame(order=order))

    order = order_payload(updated_at=T0)
    order["updated_at"] = "not-a-timestamp"
    with pytest.raises(PaperTradeUpdateIntegrityError, match="invalid ISO"):
        parse(frame(order=order))


def test_parser_rejects_fill_payload_nonstring_or_nonfinite_fields() -> None:
    payload = json.loads(
        frame(
            "fill",
            order=order_payload(status="filled", filled_qty="1"),
            execution_id="fill-shape-001",
        ).decode()
    )
    payload["data"]["price"] = 10
    with pytest.raises(PaperTradeUpdateIntegrityError, match="decimal string"):
        parse(json.dumps(payload).encode())

    payload["data"]["price"] = "Infinity"
    with pytest.raises(PaperTradeUpdateIntegrityError, match="finite"):
        parse(json.dumps(payload).encode())


def test_parser_rejects_fill_qty_larger_than_order_qty() -> None:
    with pytest.raises(PaperTradeUpdateIntegrityError, match="fill qty exceeds"):
        parse(
            frame(
                "fill",
                order=order_payload(status="filled", filled_qty="1"),
                execution_id="oversized-fill-001",
                fill_qty="2",
            )
        )


def test_ledger_verify_empty_and_control_without_events_fail_closed(tmp_path) -> None:
    runtime = SQLiteRuntime(tmp_path / "empty.sqlite")
    ledger = SQLitePaperTradeUpdateLedger(runtime, scope=scope())
    with pytest.raises(PaperTradeUpdateIntegrityError, match="empty"):
        ledger.verify()

    with sqlite3.connect(runtime.path) as conn:
        conn.execute(
            """
            INSERT INTO alpaca_paper_trade_update_control(
                scope_hash,event_count,head_hash,parent_filled_qty,
                take_profit_filled_qty,stop_loss_filled_qty,last_event_at,control_hash
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (ledger._scope_hash, 1, "a" * 64, "0", "0", "0", T0.isoformat(), "b" * 64),
        )
        conn.commit()
    with pytest.raises(PaperTradeUpdateIntegrityError, match="control exists without events"):
        ledger.verify()


def seeded(tmp_path, name="seed.sqlite"):
    runtime = SQLiteRuntime(tmp_path / name)
    ledger = SQLitePaperTradeUpdateLedger(runtime, scope=scope())
    ledger.append(parse(frame()))
    return runtime, ledger


def test_missing_control_and_sequence_gap_are_detected(tmp_path) -> None:
    runtime, ledger = seeded(tmp_path, "missing-control.sqlite")
    with sqlite3.connect(runtime.path) as conn:
        conn.execute(
            "DELETE FROM alpaca_paper_trade_update_control WHERE scope_hash=?",
            (ledger._scope_hash,),
        )
        conn.commit()
    with pytest.raises(PaperTradeUpdateIntegrityError, match="control is missing"):
        ledger.verify()

    runtime, ledger = seeded(tmp_path, "gap.sqlite")
    second = parse(
        frame(
            "accepted",
            order=order_payload(status="accepted", updated_at=T0 + timedelta(seconds=1)),
        )
    )
    ledger.append(second)
    with sqlite3.connect(runtime.path) as conn:
        conn.execute(
            "UPDATE alpaca_paper_trade_update_events SET sequence=3 WHERE scope_hash=? AND sequence=2",
            (ledger._scope_hash,),
        )
        conn.commit()
    with pytest.raises(PaperTradeUpdateIntegrityError, match="sequence gap"):
        ledger.verify()


@pytest.mark.parametrize(
    "column,value,reason",
    [
        ("event_hash", "f" * 64, "event hash mismatch"),
        ("identity_key", "event:bad", "identity mismatch"),
        ("previous_hash", "f" * 64, "previous hash mismatch"),
        ("chain_hash", "f" * 64, "chain hash mismatch"),
        ("occurred_at", (T0 + timedelta(seconds=9)).isoformat(), "timestamp mismatch"),
    ],
)
def test_event_row_integrity_columns_are_recomputed(tmp_path, column, value, reason) -> None:
    runtime, ledger = seeded(tmp_path, f"{column}.sqlite")
    with sqlite3.connect(runtime.path) as conn:
        conn.execute(
            f"UPDATE alpaca_paper_trade_update_events SET {column}=? WHERE scope_hash=? AND sequence=1",
            (value, ledger._scope_hash),
        )
        conn.commit()
    with pytest.raises(PaperTradeUpdateIntegrityError, match=reason):
        ledger.verify()


def test_control_malformed_decimal_or_time_is_detected(tmp_path) -> None:
    for column, value in (("parent_filled_qty", "not-decimal"), ("last_event_at", "bad-time")):
        runtime, ledger = seeded(tmp_path, f"control-{column}.sqlite")
        with sqlite3.connect(runtime.path) as conn:
            conn.execute(
                f"UPDATE alpaca_paper_trade_update_control SET {column}=? WHERE scope_hash=?",
                (value, ledger._scope_hash),
            )
            conn.commit()
        with pytest.raises(PaperTradeUpdateIntegrityError):
            ledger.verify()


def test_ledger_direct_scope_mismatch_rejected_before_commit(tmp_path) -> None:
    runtime = SQLiteRuntime(tmp_path / "scope-direct.sqlite")
    ledger = SQLitePaperTradeUpdateLedger(runtime, scope=scope())
    foreign = replace(valid_event(), broker_order_id="foreign-broker-order")
    with pytest.raises(PaperTradeUpdateScopeError, match="outside ledger scope"):
        ledger.append(foreign)
    with pytest.raises(PaperTradeUpdateIntegrityError, match="empty"):
        ledger.verify()
