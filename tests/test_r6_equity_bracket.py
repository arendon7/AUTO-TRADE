from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from autotrade.brokers.alpaca_paper_bracket import (
    AlpacaEquityBracketBuilder,
    AlpacaNestedBracketResponseValidator,
    PaperBracketRejected,
    PaperBracketResponseIntegrityError,
    PaperEquityVenueRules,
)
from autotrade.domain import OrderIntent, OrderRecord, OrderStatus, OrderType, Side


UTC = timezone.utc
NOW = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def order(
    *,
    symbol: str = "AAPL",
    side: Side = Side.BUY,
    order_type: OrderType = OrderType.LIMIT,
    quantity: str = "1",
    limit_price: str | None = "10",
    status: OrderStatus = OrderStatus.VALIDATED,
) -> OrderRecord:
    intent = OrderIntent(
        intent_id="bracket-intent-001",
        strategy_id="bracket-strategy",
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=Decimal(quantity),
        limit_price=Decimal(limit_price) if limit_price is not None else None,
        idempotency_key="bracket-idempotency-001",
        created_at=NOW,
    )
    return OrderRecord(
        order_id="bracket-order-001",
        intent=intent,
        status=status,
        risk_decision_id="bracket-risk-001",
        created_at=NOW,
    )


def rules(**overrides) -> PaperEquityVenueRules:
    values = {
        "symbol": "AAPL",
        "asset_class": "us_equity",
        "price_tick": Decimal("0.01"),
        "quantity_step": Decimal("0.001"),
        "minimum_quantity": Decimal("0.001"),
        "instrument_master_fingerprint": h("instrument-master-AAPL"),
    }
    values.update(overrides)
    return PaperEquityVenueRules(**values)


def request(**kwargs):
    values = {
        "order": order(),
        "venue_rules": rules(),
        "take_profit_price": Decimal("10.50"),
        "stop_loss_price": Decimal("9.50"),
    }
    values.update(kwargs)
    return AlpacaEquityBracketBuilder().build(**values)


def nested_response(expected=None):
    expected = expected or request()
    p = expected.canonical_payload
    return {
        "id": "parent-broker-001",
        "client_order_id": expected.client_order_id,
        "symbol": p["symbol"],
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "order_class": "bracket",
        "extended_hours": False,
        "qty": p["qty"],
        "limit_price": p["limit_price"],
        "status": "accepted",
        "legs": [
            {
                "id": "tp-broker-001",
                "side": "sell",
                "type": "limit",
                "qty": p["qty"],
                "limit_price": p["take_profit"]["limit_price"],
                "stop_price": None,
                "status": "held",
            },
            {
                "id": "stop-broker-001",
                "side": "sell",
                "type": "stop",
                "qty": p["qty"],
                "limit_price": None,
                "stop_price": p["stop_loss"]["stop_price"],
                "status": "held",
            },
        ],
    }


def encode(payload) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def test_builder_emits_exact_canonical_first_canary_bracket_payload() -> None:
    built = request()
    payload = built.canonical_payload
    assert payload == {
        "client_order_id": built.client_order_id,
        "extended_hours": False,
        "limit_price": "10",
        "order_class": "bracket",
        "qty": "1",
        "side": "buy",
        "stop_loss": {"stop_price": "9.5"},
        "symbol": "AAPL",
        "take_profit": {"limit_price": "10.5"},
        "time_in_force": "day",
        "type": "limit",
    }
    assert json.loads(built.payload_json) == payload
    assert built.payload_hash == sha256(built.payload_json.encode()).hexdigest()
    assert built.instrument_master_fingerprint == rules().instrument_master_fingerprint
    assert len(built.client_order_id) <= 128


def test_semantically_equal_decimal_inputs_produce_same_payload_hash() -> None:
    first = request(
        order=order(quantity="1.000", limit_price="10.00"),
        take_profit_price=Decimal("10.5000"),
        stop_loss_price=Decimal("9.500"),
    )
    second = request()
    assert first.payload_json == second.payload_json
    assert first.payload_hash == second.payload_hash


@pytest.mark.parametrize(
    "current_order,reason",
    [
        (order(status=OrderStatus.SUBMITTED), "VALIDATED"),
        (order(symbol="MSFT"), "symbol mismatch"),
        (order(side=Side.SELL), "BUY-only"),
        (order(order_type=OrderType.MARKET, limit_price=None), "must be LIMIT"),
    ],
)
def test_builder_rejects_nonminimal_parent_surface(current_order, reason) -> None:
    with pytest.raises(PaperBracketRejected, match=reason):
        request(order=current_order)


def test_quantity_below_minimum_is_rejected_never_upsized() -> None:
    with pytest.raises(PaperBracketRejected, match="never auto-upsizes"):
        request(order=order(quantity="0.0005"))


def test_quantity_and_prices_must_align_exactly_to_authoritative_rules() -> None:
    with pytest.raises(PaperBracketRejected, match="quantity_step"):
        request(order=order(quantity="1.0005"))
    with pytest.raises(PaperBracketRejected, match="price_tick"):
        request(order=order(limit_price="10.005"))
    with pytest.raises(PaperBracketRejected, match="price_tick"):
        request(take_profit_price=Decimal("10.505"))
    with pytest.raises(PaperBracketRejected, match="price_tick"):
        request(stop_loss_price=Decimal("9.505"))


@pytest.mark.parametrize(
    "tp,stop",
    [
        (Decimal("10.00"), Decimal("9.50")),
        (Decimal("9.99"), Decimal("9.50")),
        (Decimal("10.50"), Decimal("10.00")),
        (Decimal("10.50"), Decimal("10.01")),
    ],
)
def test_buy_bracket_geometry_requires_tp_above_parent_above_stop(tp, stop) -> None:
    with pytest.raises(PaperBracketRejected, match="geometry"):
        request(take_profit_price=tp, stop_loss_price=stop)


def test_venue_rules_reject_crypto_unknown_and_nonexact_metadata() -> None:
    with pytest.raises(ValueError, match="us_equity"):
        rules(asset_class="crypto")
    with pytest.raises(ValueError, match="symbol"):
        rules(symbol="aapl")
    with pytest.raises(ValueError, match="quantity_step"):
        rules(quantity_step=Decimal("0"))
    with pytest.raises(ValueError, match="align exactly"):
        rules(quantity_step=Decimal("0.003"), minimum_quantity=Decimal("0.001"))
    with pytest.raises(ValueError, match="fingerprint"):
        rules(instrument_master_fingerprint="bad")


def test_nested_response_with_exact_two_coherent_legs_is_attested() -> None:
    expected = request()
    payload = nested_response(expected)
    attested = AlpacaNestedBracketResponseValidator().validate(
        response_body=encode(payload),
        request_id="alpaca-submit-request-001",
        expected=expected,
    )
    assert attested.parent_order_id == "parent-broker-001"
    assert attested.take_profit_order_id == "tp-broker-001"
    assert attested.stop_loss_order_id == "stop-broker-001"
    assert attested.client_order_id == expected.client_order_id
    assert len(attested.response_hash) == 64


@pytest.mark.parametrize("leg_count", [0, 1, 3])
def test_nested_response_requires_exactly_two_legs(leg_count) -> None:
    payload = nested_response()
    payload["legs"] = (payload["legs"] * 2)[:leg_count]
    with pytest.raises(PaperBracketResponseIntegrityError, match="exactly two"):
        AlpacaNestedBracketResponseValidator().validate(
            response_body=encode(payload), request_id="request-1", expected=request()
        )


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (("client_order_id", "wrong-client"), "client_order_id"),
        (("symbol", "MSFT"), "symbol"),
        (("side", "sell"), "side"),
        (("type", "market"), "type"),
        (("time_in_force", "gtc"), "TIF"),
        (("order_class", "simple"), "order_class"),
        (("extended_hours", True), "extended_hours"),
        (("qty", "2"), "quantity"),
        (("limit_price", "10.01"), "limit_price"),
        (("status", "rejected"), "status"),
    ],
)
def test_parent_response_identity_mismatch_fails_closed(mutation, reason) -> None:
    payload = nested_response()
    payload[mutation[0]] = mutation[1]
    with pytest.raises(PaperBracketResponseIntegrityError, match=reason):
        AlpacaNestedBracketResponseValidator().validate(
            response_body=encode(payload), request_id="request-1", expected=request()
        )


@pytest.mark.parametrize(
    "leg_index,key,value,reason",
    [
        (0, "side", "buy", "side"),
        (0, "qty", "2", "quantity"),
        (0, "limit_price", "10.6", "take-profit"),
        (0, "status", "canceled", "status"),
        (1, "side", "buy", "side"),
        (1, "qty", "2", "quantity"),
        (1, "stop_price", "9.4", "stop-loss"),
        (1, "status", "canceled", "status"),
    ],
)
def test_protection_leg_mismatch_fails_closed(leg_index, key, value, reason) -> None:
    payload = nested_response()
    payload["legs"][leg_index][key] = value
    with pytest.raises(PaperBracketResponseIntegrityError, match=reason):
        AlpacaNestedBracketResponseValidator().validate(
            response_body=encode(payload), request_id="request-1", expected=request()
        )


def test_duplicate_or_unknown_leg_types_fail_closed() -> None:
    duplicate = nested_response()
    duplicate["legs"][1]["type"] = "limit"
    duplicate["legs"][1]["limit_price"] = "10.5"
    duplicate["legs"][1]["stop_price"] = None
    with pytest.raises(PaperBracketResponseIntegrityError, match="duplicate take-profit"):
        AlpacaNestedBracketResponseValidator().validate(
            response_body=encode(duplicate), request_id="request-1", expected=request()
        )

    unknown = nested_response()
    unknown["legs"][1]["type"] = "stop_limit"
    with pytest.raises(PaperBracketResponseIntegrityError, match="exactly limit or stop"):
        AlpacaNestedBracketResponseValidator().validate(
            response_body=encode(unknown), request_id="request-1", expected=request()
        )


def test_parent_and_leg_ids_must_be_distinct() -> None:
    payload = nested_response()
    payload["legs"][1]["id"] = payload["id"]
    with pytest.raises(PaperBracketResponseIntegrityError, match="must be distinct"):
        AlpacaNestedBracketResponseValidator().validate(
            response_body=encode(payload), request_id="request-1", expected=request()
        )


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b"[]",
        b'{"id":NaN}',
        b"\xff",
    ],
)
def test_nested_response_requires_strict_utf8_json_object(raw) -> None:
    with pytest.raises(PaperBracketResponseIntegrityError):
        AlpacaNestedBracketResponseValidator().validate(
            response_body=raw, request_id="request-1", expected=request()
        )


def test_request_id_is_required_and_canonical() -> None:
    for request_id in ("", "bad request", "bad\nrequest"):
        with pytest.raises(PaperBracketResponseIntegrityError, match="X-Request-ID"):
            AlpacaNestedBracketResponseValidator().validate(
                response_body=encode(nested_response()),
                request_id=request_id,
                expected=request(),
            )


def test_builder_and_validator_have_no_network_submit_surface() -> None:
    forbidden = {"submit", "post", "send", "place_order", "create_order"}
    assert not (forbidden & set(dir(AlpacaEquityBracketBuilder())))
    assert not (forbidden & set(dir(AlpacaNestedBracketResponseValidator())))
