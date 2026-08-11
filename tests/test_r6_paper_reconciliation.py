from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from autotrade.brokers.alpaca_paper_bracket import (
    AlpacaEquityBracketBuilder,
    AlpacaNestedBracketResponseValidator,
    PaperEquityVenueRules,
)
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from autotrade.brokers.alpaca_paper_reconciliation import (
    AlpacaPaperBracketReconciler,
    PaperReconciliationBlocked,
    PaperReconciliationConflict,
)
from autotrade.brokers.alpaca_paper_reconciliation_gateway import (
    AlpacaPaperLookupRequest,
    AlpacaPaperLookupResponse,
    AlpacaPaperOrderLookupGateway,
    AlpacaPaperReconciliationConfig,
    PaperReconciliationGatewayDisabled,
    PaperReconciliationIntegrityError,
    PaperReconciliationPolicyError,
)
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionBinding,
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.domain import OrderIntent, OrderRecord, OrderStatus, OrderType, Side
from autotrade.persistence import SQLiteRuntime


UTC = timezone.utc
NOW = datetime(2026, 8, 11, 8, 30, tzinfo=UTC)
ACCOUNT_ID = "e6fe16f3-64a4-4921-8928-cadf02f92f98"


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(key_id="PAPERKEY123", secret_key="PAPERSECRET456")


def attestation(*, request_id: str = "account-request-001") -> AlpacaPaperAccountAttestation:
    creds = credentials()
    return AlpacaPaperAccountAttestation(
        account_id=ACCOUNT_ID,
        account_reference=h("paper-account-number"),
        credential_reference=creds.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=True,
        attested_at=NOW,
        request_id=request_id,
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )


def order() -> OrderRecord:
    intent = OrderIntent(
        intent_id="reconcile-intent-001",
        strategy_id="reconcile-strategy",
        symbol="AAPL",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        limit_price=Decimal("10"),
        idempotency_key="reconcile-idempotency-001",
        created_at=NOW - timedelta(seconds=2),
    )
    return OrderRecord(
        order_id="reconcile-order-001",
        intent=intent,
        status=OrderStatus.VALIDATED,
        risk_decision_id="reconcile-risk-001",
        created_at=NOW - timedelta(seconds=1),
    )


def bracket_request():
    return AlpacaEquityBracketBuilder().build(
        order=order(),
        venue_rules=PaperEquityVenueRules(
            symbol="AAPL",
            asset_class="us_equity",
            price_tick=Decimal("0.01"),
            quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"),
            instrument_master_fingerprint=h("instrument-master"),
        ),
        take_profit_price=Decimal("10.50"),
        stop_loss_price=Decimal("9.50"),
    )


def nested_payload(expected=None):
    expected = expected or bracket_request()
    p = expected.canonical_payload
    return {
        "id": "broker-parent-001",
        "client_order_id": expected.client_order_id,
        "symbol": "AAPL",
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
                "id": "broker-tp-001",
                "side": "sell",
                "type": "limit",
                "qty": p["qty"],
                "limit_price": p["take_profit"]["limit_price"],
                "stop_price": None,
                "status": "held",
            },
            {
                "id": "broker-stop-001",
                "side": "sell",
                "type": "stop",
                "qty": p["qty"],
                "limit_price": None,
                "stop_price": p["stop_loss"]["stop_price"],
                "status": "held",
            },
        ],
    }


def response(status: int, payload: object, url: str, request_id: str) -> AlpacaPaperLookupResponse:
    return AlpacaPaperLookupResponse(
        status_code=status,
        body=json.dumps(payload, separators=(",", ":")).encode(),
        final_url=url,
        headers={
            "content-type": "application/json; charset=utf-8",
            "x-request-id": request_id,
        },
    )


class QueueTransport:
    def __init__(self, responses: list[AlpacaPaperLookupResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[AlpacaPaperLookupRequest] = []

    def read(self, request: AlpacaPaperLookupRequest) -> AlpacaPaperLookupResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected extra reconciliation read")
        return self.responses.pop(0)


def lookup_url(client_order_id: str) -> str:
    return (
        "https://paper-api.alpaca.markets/v2/orders:by_client_order_id"
        f"?client_order_id={client_order_id}"
    )


def detail_url(order_id: str = "broker-parent-001") -> str:
    return f"https://paper-api.alpaca.markets/v2/orders/{order_id}?nested=true"


def gateway_with(responses: list[AlpacaPaperLookupResponse], *, enabled: bool = True):
    transport = QueueTransport(responses)
    gateway = AlpacaPaperOrderLookupGateway(
        config=AlpacaPaperReconciliationConfig(enabled=enabled),
        transport=transport,  # type: ignore[arg-type]
    )
    return gateway, transport


def unknown_registry(tmp_path, expected=None):
    expected = expected or bracket_request()
    registry = SQLitePaperSubmissionRegistry(SQLiteRuntime(tmp_path / "submission.sqlite"))
    frozen = PaperSubmissionBinding.from_order(
        order=order(),
        account_attestation_fingerprint=attestation().fingerprint,
        order_payload_hash=expected.payload_hash,
        created_at=NOW - timedelta(seconds=1),
    )
    registry.prepare(frozen)
    registry.mark_submit_attempt_unknown(
        order_id=frozen.order_id,
        attempt_id="attempt-001",
        now=NOW,
    )
    return registry, frozen, expected


def test_lookup_gateway_disabled_by_default_performs_zero_io() -> None:
    gateway, transport = gateway_with([], enabled=False)
    with pytest.raises(PaperReconciliationGatewayDisabled):
        gateway.lookup_by_client_order_id(
            credentials=credentials(),
            account_attestation=attestation(),
            client_order_id="autotrade-client-001",
        )
    assert transport.requests == []


def test_lookup_requires_same_credentials_as_account_attestation_before_io() -> None:
    gateway, transport = gateway_with([])
    wrong = AlpacaPaperCredentials(key_id="OTHERPAPERKEY", secret_key="OTHERSECRET")
    with pytest.raises(PaperReconciliationPolicyError, match="credentials"):
        gateway.lookup_by_client_order_id(
            credentials=wrong,
            account_attestation=attestation(),
            client_order_id="autotrade-client-001",
        )
    assert transport.requests == []


def test_explicit_404_is_absence_not_ack_and_stays_unknown(tmp_path) -> None:
    registry, frozen, expected = unknown_registry(tmp_path)
    lookup = response(
        404,
        {"message": "order not found"},
        lookup_url(frozen.client_order_id),
        "lookup-404-001",
    )
    gateway, transport = gateway_with([lookup])
    outcome = AlpacaPaperBracketReconciler(lookup_gateway=gateway).reconcile(
        registry=registry,
        order_id=frozen.order_id,
        credentials=credentials(),
        account_attestation=attestation(),
        expected_bracket=expected,
        now=NOW + timedelta(seconds=1),
    )

    assert outcome.found is False
    assert outcome.state.status is PaperSubmissionStatus.UNKNOWN
    assert outcome.state.absence_observation_count == 1
    assert outcome.state.submit_allowed is False
    assert outcome.detail_request_id is None
    assert len(transport.requests) == 1
    assert transport.requests[0].method == "GET"
    assert "/v2/orders:by_client_order_id?client_order_id=" in transport.requests[0].url


def test_repeated_404_with_new_request_id_accumulates_absence_but_never_rearms(tmp_path) -> None:
    registry, frozen, expected = unknown_registry(tmp_path)
    gateway, _ = gateway_with(
        [
            response(404, {"message": "not found"}, lookup_url(frozen.client_order_id), "lookup-404-001"),
            response(404, {"message": "not found"}, lookup_url(frozen.client_order_id), "lookup-404-002"),
        ]
    )
    reconciler = AlpacaPaperBracketReconciler(lookup_gateway=gateway)
    reconciler.reconcile(
        registry=registry,
        order_id=frozen.order_id,
        credentials=credentials(),
        account_attestation=attestation(),
        expected_bracket=expected,
        now=NOW + timedelta(seconds=1),
    )
    second = reconciler.reconcile(
        registry=registry,
        order_id=frozen.order_id,
        credentials=credentials(),
        account_attestation=attestation(),
        expected_bracket=expected,
        now=NOW + timedelta(seconds=2),
    )
    assert second.state.status is PaperSubmissionStatus.UNKNOWN
    assert second.state.absence_observation_count == 2
    assert second.state.submit_allowed is False


def test_found_order_requires_second_nested_read_and_exact_bracket_before_ack(tmp_path) -> None:
    registry, frozen, expected = unknown_registry(tmp_path)
    initial = {
        "id": "broker-parent-001",
        "client_order_id": frozen.client_order_id,
    }
    gateway, transport = gateway_with(
        [
            response(200, initial, lookup_url(frozen.client_order_id), "lookup-found-001"),
            response(200, nested_payload(expected), detail_url(), "detail-nested-001"),
        ]
    )
    outcome = AlpacaPaperBracketReconciler(lookup_gateway=gateway).reconcile(
        registry=registry,
        order_id=frozen.order_id,
        credentials=credentials(),
        account_attestation=attestation(),
        expected_bracket=expected,
        now=NOW + timedelta(seconds=1),
    )

    assert outcome.found is True
    assert outcome.state.status is PaperSubmissionStatus.ACKNOWLEDGED
    assert outcome.state.broker_order_id == "broker-parent-001"
    assert outcome.detail_request_id == "detail-nested-001"
    assert outcome.bracket_attestation is not None
    assert len(transport.requests) == 2
    assert all(item.method == "GET" for item in transport.requests)
    assert transport.requests[1].url == detail_url()


def test_nested_bracket_mismatch_keeps_durable_state_unknown(tmp_path) -> None:
    registry, frozen, expected = unknown_registry(tmp_path)
    bad_nested = nested_payload(expected)
    bad_nested["legs"][0]["limit_price"] = "99"
    gateway, _ = gateway_with(
        [
            response(
                200,
                {"id": "broker-parent-001", "client_order_id": frozen.client_order_id},
                lookup_url(frozen.client_order_id),
                "lookup-found-001",
            ),
            response(200, bad_nested, detail_url(), "detail-bad-001"),
        ]
    )
    with pytest.raises(Exception):
        AlpacaPaperBracketReconciler(
            lookup_gateway=gateway,
            response_validator=AlpacaNestedBracketResponseValidator(),
        ).reconcile(
            registry=registry,
            order_id=frozen.order_id,
            credentials=credentials(),
            account_attestation=attestation(),
            expected_bracket=expected,
            now=NOW + timedelta(seconds=1),
        )
    assert registry.get(frozen.order_id).status is PaperSubmissionStatus.UNKNOWN


def test_reconciliation_refuses_prepared_state_without_network_io(tmp_path) -> None:
    expected = bracket_request()
    registry = SQLitePaperSubmissionRegistry(SQLiteRuntime(tmp_path / "prepared.sqlite"))
    frozen = PaperSubmissionBinding.from_order(
        order=order(),
        account_attestation_fingerprint=attestation().fingerprint,
        order_payload_hash=expected.payload_hash,
        created_at=NOW,
    )
    registry.prepare(frozen)
    gateway, transport = gateway_with([])
    with pytest.raises(PaperReconciliationBlocked, match="UNKNOWN"):
        AlpacaPaperBracketReconciler(lookup_gateway=gateway).reconcile(
            registry=registry,
            order_id=frozen.order_id,
            credentials=credentials(),
            account_attestation=attestation(),
            expected_bracket=expected,
            now=NOW + timedelta(seconds=1),
        )
    assert transport.requests == []


def test_reconciliation_refuses_changed_expected_payload_before_network_io(tmp_path) -> None:
    registry, frozen, expected = unknown_registry(tmp_path)
    changed = AlpacaEquityBracketBuilder().build(
        order=order(),
        venue_rules=PaperEquityVenueRules(
            symbol="AAPL",
            asset_class="us_equity",
            price_tick=Decimal("0.01"),
            quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"),
            instrument_master_fingerprint=h("instrument-master"),
        ),
        take_profit_price=Decimal("10.60"),
        stop_loss_price=Decimal("9.50"),
    )
    gateway, transport = gateway_with([])
    with pytest.raises(PaperReconciliationConflict, match="payload hash"):
        AlpacaPaperBracketReconciler(lookup_gateway=gateway).reconcile(
            registry=registry,
            order_id=frozen.order_id,
            credentials=credentials(),
            account_attestation=attestation(),
            expected_bracket=changed,
            now=NOW + timedelta(seconds=1),
        )
    assert transport.requests == []


def test_client_lookup_identity_mismatch_fails_closed(tmp_path) -> None:
    registry, frozen, expected = unknown_registry(tmp_path)
    gateway, _ = gateway_with(
        [
            response(
                200,
                {"id": "broker-parent-001", "client_order_id": "different-client"},
                lookup_url(frozen.client_order_id),
                "lookup-mismatch-001",
            )
        ]
    )
    with pytest.raises(PaperReconciliationIntegrityError, match="client_order_id mismatch"):
        AlpacaPaperBracketReconciler(lookup_gateway=gateway).reconcile(
            registry=registry,
            order_id=frozen.order_id,
            credentials=credentials(),
            account_attestation=attestation(),
            expected_bracket=expected,
            now=NOW + timedelta(seconds=1),
        )
    assert registry.get(frozen.order_id).status is PaperSubmissionStatus.UNKNOWN


def test_discovered_order_that_disappears_on_nested_read_fails_closed(tmp_path) -> None:
    registry, frozen, expected = unknown_registry(tmp_path)
    gateway, _ = gateway_with(
        [
            response(
                200,
                {"id": "broker-parent-001", "client_order_id": frozen.client_order_id},
                lookup_url(frozen.client_order_id),
                "lookup-found-001",
            ),
            response(404, {"message": "not found"}, detail_url(), "detail-404-001"),
        ]
    )
    with pytest.raises(PaperReconciliationIntegrityError, match="no longer readable"):
        AlpacaPaperBracketReconciler(lookup_gateway=gateway).reconcile(
            registry=registry,
            order_id=frozen.order_id,
            credentials=credentials(),
            account_attestation=attestation(),
            expected_bracket=expected,
            now=NOW + timedelta(seconds=1),
        )
    assert registry.get(frozen.order_id).status is PaperSubmissionStatus.UNKNOWN


def test_404_must_have_json_error_and_x_request_id() -> None:
    client_id = "autotrade-client-001"
    bad_error = AlpacaPaperLookupResponse(
        status_code=404,
        body=b"not-json",
        final_url=lookup_url(client_id),
        headers={"content-type": "application/json", "x-request-id": "request-1"},
    )
    gateway, _ = gateway_with([bad_error])
    with pytest.raises(PaperReconciliationIntegrityError):
        gateway.lookup_by_client_order_id(
            credentials=credentials(),
            account_attestation=attestation(),
            client_order_id=client_id,
        )

    missing_request = AlpacaPaperLookupResponse(
        status_code=404,
        body=b'{"message":"not found"}',
        final_url=lookup_url(client_id),
        headers={"content-type": "application/json"},
    )
    gateway2, _ = gateway_with([missing_request])
    with pytest.raises(PaperReconciliationIntegrityError, match="X-Request-ID"):
        gateway2.lookup_by_client_order_id(
            credentials=credentials(),
            account_attestation=attestation(),
            client_order_id=client_id,
        )


def test_final_url_authority_change_is_rejected() -> None:
    client_id = "autotrade-client-001"
    gateway, _ = gateway_with(
        [
            response(
                404,
                {"message": "not found"},
                "https://example.com/v2/orders:by_client_order_id?client_order_id=autotrade-client-001",
                "request-1",
            )
        ]
    )
    with pytest.raises(PaperReconciliationPolicyError, match="final host"):
        gateway.lookup_by_client_order_id(
            credentials=credentials(),
            account_attestation=attestation(),
            client_order_id=client_id,
        )


def test_lookup_gateway_and_reconciler_have_no_write_surface() -> None:
    gateway, _ = gateway_with([])
    reconciler = AlpacaPaperBracketReconciler(lookup_gateway=gateway)
    forbidden = {"post", "submit", "submit_order", "send", "place_order", "create_order"}
    assert not (forbidden & set(dir(gateway)))
    assert not (forbidden & set(dir(reconciler)))
