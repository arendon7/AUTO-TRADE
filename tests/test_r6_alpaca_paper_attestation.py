from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_LIVE_TRADING_HOST,
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountGateway,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperGatewayDisabled,
    AlpacaPaperHttpResponse,
    AlpacaPaperIntegrityError,
    AlpacaPaperPolicyError,
    AlpacaPaperReadPolicy,
    AlpacaPaperReadRequest,
    AlpacaPaperUnavailable,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 11, 7, 0, tzinfo=UTC)
ACCOUNT_ID = "e6fe16f3-64a4-4921-8928-cadf02f92f98"


class FakeTransport:
    def __init__(self, response: AlpacaPaperHttpResponse | None = None) -> None:
        self.response = response or response_ok()
        self.calls: list[AlpacaPaperReadRequest] = []

    def read(self, request: AlpacaPaperReadRequest) -> AlpacaPaperHttpResponse:
        self.calls.append(request)
        return self.response


def account_payload(**changes):
    payload = {
        "id": ACCOUNT_ID,
        "account_number": "PAPER123456",
        "status": "ACTIVE",
        "currency": "USD",
        "buying_power": "100000.50",
        "portfolio_value": "100001.25",
        "shorting_enabled": True,
        "trading_blocked": False,
        "account_blocked": False,
        "trade_suspended_by_user": False,
    }
    payload.update(changes)
    return payload


def response_ok(**changes) -> AlpacaPaperHttpResponse:
    values = {
        "status_code": 200,
        "body": json.dumps(account_payload(), separators=(",", ":")).encode(),
        "final_url": f"https://{ALPACA_PAPER_TRADING_HOST}{ALPACA_PAPER_ACCOUNT_PATH}",
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "x-request-id": "paper-request-123",
        },
    }
    values.update(changes)
    return AlpacaPaperHttpResponse(**values)


def credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(key_id="PAPERKEY123", secret_key="PAPERSECRET456")


def gateway(
    *,
    enabled: bool = True,
    transport: FakeTransport | None = None,
    base_url: str = f"https://{ALPACA_PAPER_TRADING_HOST}",
) -> tuple[AlpacaPaperAccountGateway, FakeTransport]:
    fake = transport or FakeTransport()
    instance = AlpacaPaperAccountGateway(
        config=AlpacaPaperGatewayConfig(enabled=enabled, base_url=base_url),
        transport=fake,
    )
    return instance, fake


def test_credentials_are_redacted_and_secret_is_not_in_reference() -> None:
    value = credentials()
    rendered = repr(value)
    assert "PAPERKEY123" not in rendered
    assert "PAPERSECRET456" not in rendered
    assert "redacted" in rendered
    assert len(value.credential_reference) == 64
    assert "PAPERSECRET456" not in value.credential_reference


@pytest.mark.parametrize(
    "key_id,secret",
    [
        ("", "secret"),
        ("key", ""),
        (" key", "secret"),
        ("key", "secret "),
        ("key\n", "secret"),
        ("same", "same"),
    ],
)
def test_credentials_fail_closed_on_malformed_secret_material(key_id, secret) -> None:
    with pytest.raises(ValueError):
        AlpacaPaperCredentials(key_id=key_id, secret_key=secret)


def test_gateway_is_disabled_by_default_and_performs_zero_io() -> None:
    fake = FakeTransport()
    instance = AlpacaPaperAccountGateway(transport=fake)
    assert instance.enabled is False

    with pytest.raises(AlpacaPaperGatewayDisabled):
        instance.attest_account(
            credentials=credentials(), expected_account_id=ACCOUNT_ID, now=NOW
        )
    assert fake.calls == []


def test_invalid_expected_account_id_rejects_before_io() -> None:
    instance, fake = gateway()
    with pytest.raises(ValueError, match="expected_account_id"):
        instance.attest_account(credentials=credentials(), expected_account_id="guess", now=NOW)
    assert fake.calls == []


def test_live_or_arbitrary_base_url_is_rejected_before_transport_io() -> None:
    for base in (
        f"https://{ALPACA_LIVE_TRADING_HOST}",
        "https://example.com",
        f"http://{ALPACA_PAPER_TRADING_HOST}",
        f"https://{ALPACA_PAPER_TRADING_HOST}:8443",
    ):
        instance, fake = gateway(base_url=base)
        with pytest.raises(AlpacaPaperPolicyError):
            instance.attest_account(
                credentials=credentials(), expected_account_id=ACCOUNT_ID, now=NOW
            )
        assert fake.calls == []


def test_successful_attestation_is_bound_to_exact_request_and_account() -> None:
    instance, fake = gateway()
    attestation = instance.attest_account(
        credentials=credentials(), expected_account_id=ACCOUNT_ID, now=NOW
    )

    assert len(fake.calls) == 1
    request = fake.calls[0]
    assert request.method == "GET"
    assert request.url == f"https://{ALPACA_PAPER_TRADING_HOST}/v2/account"
    assert request.headers["APCA-API-KEY-ID"] == "PAPERKEY123"
    assert request.headers["APCA-API-SECRET-KEY"] == "PAPERSECRET456"
    assert attestation.account_id == ACCOUNT_ID
    assert attestation.status == "ACTIVE"
    assert attestation.currency == "USD"
    assert attestation.buying_power == Decimal("100000.50")
    assert attestation.portfolio_value == Decimal("100001.25")
    assert attestation.shorting_enabled is True
    assert attestation.request_id == "paper-request-123"
    assert attestation.source_host == ALPACA_PAPER_TRADING_HOST
    assert attestation.source_path == "/v2/account"
    assert len(attestation.fingerprint) == 64


def test_attestation_has_no_order_write_surface() -> None:
    instance, _ = gateway()
    forbidden = {
        "submit",
        "submit_order",
        "place_order",
        "create_order",
        "cancel",
        "replace",
        "send_order",
    }
    assert not (forbidden & set(dir(instance)))


@pytest.mark.parametrize(
    "payload_change",
    [
        {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
        {"status": "SUBMITTED"},
        {"currency": "EUR"},
        {"trading_blocked": True},
        {"account_blocked": True},
        {"trade_suspended_by_user": True},
        {"trading_blocked": "false"},
        {"account_blocked": 0},
        {"shorting_enabled": "true"},
        {"buying_power": "NaN"},
        {"buying_power": "-1"},
        {"portfolio_value": "Infinity"},
        {"account_number": ""},
    ],
)
def test_account_integrity_restrictions_fail_closed(payload_change) -> None:
    body = json.dumps(account_payload(**payload_change), separators=(",", ":")).encode()
    instance, _ = gateway(transport=FakeTransport(response_ok(body=body)))
    with pytest.raises(AlpacaPaperIntegrityError):
        instance.attest_account(
            credentials=credentials(), expected_account_id=ACCOUNT_ID, now=NOW
        )


@pytest.mark.parametrize(
    "response",
    [
        response_ok(status_code=201),
        response_ok(headers={"content-type": "text/html", "x-request-id": "request"}),
        response_ok(headers={"content-type": "application/json"}),
        response_ok(headers={"content-type": "application/json", "x-request-id": "bad\nrequest"}),
        response_ok(body=b"not-json"),
        response_ok(body=b"[]"),
        response_ok(body=b'{"id":NaN}'),
        response_ok(final_url="https://example.com/v2/account"),
        response_ok(final_url=f"https://{ALPACA_PAPER_TRADING_HOST}/v2/orders"),
    ],
)
def test_response_envelope_or_final_url_ambiguity_fails_closed(response) -> None:
    instance, _ = gateway(transport=FakeTransport(response))
    with pytest.raises((AlpacaPaperIntegrityError, AlpacaPaperPolicyError, AlpacaPaperUnavailable)):
        instance.attest_account(
            credentials=credentials(), expected_account_id=ACCOUNT_ID, now=NOW
        )


def test_attestation_requires_timezone_aware_now_before_io() -> None:
    instance, fake = gateway()
    with pytest.raises(ValueError, match="timezone-aware"):
        instance.attest_account(
            credentials=credentials(), expected_account_id=ACCOUNT_ID,
            now=datetime(2026, 8, 11, 7, 0),
        )
    assert fake.calls == []


@pytest.mark.parametrize(
    "url",
    [
        f"https://user:pass@{ALPACA_PAPER_TRADING_HOST}/v2/account",
        f"https://{ALPACA_PAPER_TRADING_HOST}/v2/account?x=1",
        f"https://{ALPACA_PAPER_TRADING_HOST}/v2/account#fragment",
        f"https://{ALPACA_PAPER_TRADING_HOST}/v2/orders",
    ],
)
def test_policy_rejects_noncanonical_url(url) -> None:
    request = AlpacaPaperReadRequest(
        method="GET",
        url=url,
        timeout_seconds=1,
        headers={
            "Accept": "application/json",
            "User-Agent": "AUTO-TRADE-R6/0.28R",
            "APCA-API-KEY-ID": "key",
            "APCA-API-SECRET-KEY": "secret",
        },
    )
    with pytest.raises(AlpacaPaperPolicyError):
        AlpacaPaperReadPolicy().validate(request)


def test_policy_rejects_extra_or_missing_headers_and_non_get() -> None:
    base_headers = {
        "Accept": "application/json",
        "User-Agent": "AUTO-TRADE-R6/0.28R",
        "APCA-API-KEY-ID": "key",
        "APCA-API-SECRET-KEY": "secret",
    }
    for method, headers in (
        ("POST", base_headers),
        ("GET", {**base_headers, "X-Extra": "forbidden"}),
        ("GET", {key: value for key, value in base_headers.items() if key != "APCA-API-SECRET-KEY"}),
    ):
        with pytest.raises(AlpacaPaperPolicyError):
            AlpacaPaperReadPolicy().validate(
                AlpacaPaperReadRequest(
                    method=method,
                    url=f"https://{ALPACA_PAPER_TRADING_HOST}/v2/account",
                    timeout_seconds=1,
                    headers=headers,
                )
            )
