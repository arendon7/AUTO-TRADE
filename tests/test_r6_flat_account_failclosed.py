from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from autotrade.brokers.alpaca_paper_flat_account import (
    AlpacaPaperFlatAccountGateway,
    PaperFlatAccountAttestation,
    PaperFlatAccountDisabled,
    PaperFlatAccountIntegrityError,
    PaperFlatAccountReadPolicy,
)
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
    AlpacaPaperIntegrityError,
    AlpacaPaperPolicyError,
    AlpacaPaperReadRequest,
    AlpacaPaperUnavailable,
)


NOW = datetime(2026, 8, 12, 2, 15, tzinfo=timezone.utc)
CREDS = AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret")
HASH = "a" * 64


class OneResponseTransport:
    def __init__(self, response: AlpacaPaperHttpResponse) -> None:
        self.response = response
        self.calls = 0

    def read(self, request: AlpacaPaperReadRequest) -> AlpacaPaperHttpResponse:
        self.calls += 1
        return self.response


def response(
    *,
    status: int = 200,
    body: bytes = b"[]",
    final_url: str = "https://paper-api.alpaca.markets/v2/positions",
    content_type: str = "application/json",
    request_id: str = "req-flat",
) -> AlpacaPaperHttpResponse:
    return AlpacaPaperHttpResponse(
        status_code=status,
        body=body,
        final_url=final_url,
        headers={"content-type": content_type, "x-request-id": request_id},
    )


def gateway(resp: AlpacaPaperHttpResponse) -> AlpacaPaperFlatAccountGateway:
    return AlpacaPaperFlatAccountGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        transport=OneResponseTransport(resp),
    )


def request(url: str, *, method: str = "GET", headers=None) -> AlpacaPaperReadRequest:
    return AlpacaPaperReadRequest(
        method=method,
        url=url,
        timeout_seconds=5,
        headers=headers
        or {
            "Accept": "application/json",
            "User-Agent": "AUTO-TRADE-R6/0.28R",
            "APCA-API-KEY-ID": "key",
            "APCA-API-SECRET-KEY": "secret",
        },
    )


def test_flat_attestation_rejects_invalid_hashes_counts_time_and_request_ids() -> None:
    base = dict(
        account_attestation_fingerprint=HASH,
        credential_reference="b" * 64,
        position_count=0,
        open_order_count=0,
        positions_response_hash="c" * 64,
        orders_response_hash="d" * 64,
        positions_request_id="req-pos",
        orders_request_id="req-order",
        attested_at=NOW,
    )
    with pytest.raises(ValueError, match="sha256"):
        PaperFlatAccountAttestation(**{**base, "positions_response_hash": "bad"})
    with pytest.raises(ValueError, match="cannot be negative"):
        PaperFlatAccountAttestation(**{**base, "position_count": -1})
    with pytest.raises(ValueError, match="timezone-aware"):
        PaperFlatAccountAttestation(**{**base, "attested_at": NOW.replace(tzinfo=None)})
    with pytest.raises(ValueError, match="request id"):
        PaperFlatAccountAttestation(**{**base, "orders_request_id": "bad request id"})


def test_flat_gateway_is_disabled_by_default_and_requires_aware_now() -> None:
    with pytest.raises(PaperFlatAccountDisabled):
        AlpacaPaperFlatAccountGateway().attest_flatness(
            credentials=CREDS,
            account_attestation_fingerprint=HASH,
            expected_credential_reference=CREDS.credential_reference,
            now=NOW,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        gateway(response()).attest_flatness(
            credentials=CREDS,
            account_attestation_fingerprint=HASH,
            expected_credential_reference=CREDS.credential_reference,
            now=NOW.replace(tzinfo=None),
        )


def test_flat_gateway_rejects_non_200_and_non_json_before_second_read() -> None:
    transport = OneResponseTransport(response(status=503))
    gw = AlpacaPaperFlatAccountGateway(
        config=AlpacaPaperGatewayConfig(enabled=True), transport=transport
    )
    with pytest.raises(AlpacaPaperUnavailable, match="status"):
        gw.attest_flatness(
            credentials=CREDS,
            account_attestation_fingerprint=HASH,
            expected_credential_reference=CREDS.credential_reference,
            now=NOW,
        )
    assert transport.calls == 1

    with pytest.raises(AlpacaPaperIntegrityError, match="application/json"):
        gateway(response(content_type="text/plain")).attest_flatness(
            credentials=CREDS,
            account_attestation_fingerprint=HASH,
            expected_credential_reference=CREDS.credential_reference,
            now=NOW,
        )


def test_flat_gateway_rejects_invalid_json_root_and_request_id() -> None:
    with pytest.raises(PaperFlatAccountIntegrityError, match="strict JSON"):
        gateway(response(body=b"[NaN]")).attest_flatness(
            credentials=CREDS,
            account_attestation_fingerprint=HASH,
            expected_credential_reference=CREDS.credential_reference,
            now=NOW,
        )
    with pytest.raises(PaperFlatAccountIntegrityError, match="root must be an array"):
        gateway(response(body=json.dumps({"symbol": "AAPL"}).encode())).attest_flatness(
            credentials=CREDS,
            account_attestation_fingerprint=HASH,
            expected_credential_reference=CREDS.credential_reference,
            now=NOW,
        )
    with pytest.raises(PaperFlatAccountIntegrityError, match="X-Request-ID"):
        gateway(response(request_id="")).attest_flatness(
            credentials=CREDS,
            account_attestation_fingerprint=HASH,
            expected_credential_reference=CREDS.credential_reference,
            now=NOW,
        )


def test_flat_policy_rejects_scheme_host_query_paths_and_bad_final_url() -> None:
    policy = PaperFlatAccountReadPolicy()
    for bad_url in (
        "http://paper-api.alpaca.markets/v2/positions",
        "https://api.alpaca.markets/v2/positions",
        "https://paper-api.alpaca.markets/v2/positions?foo=bar",
        "https://paper-api.alpaca.markets/v2/account",
        "https://paper-api.alpaca.markets/v2/orders?status=all&limit=500&direction=asc&nested=true",
    ):
        with pytest.raises(AlpacaPaperPolicyError):
            policy.validate(request(bad_url))
    with pytest.raises(AlpacaPaperPolicyError):
        policy.validate_final_url("https://api.alpaca.markets/v2/positions")
    with pytest.raises(AlpacaPaperPolicyError):
        policy.validate_final_url("https://paper-api.alpaca.markets/v2/orders?status=all")


def test_flat_policy_rejects_noncanonical_headers_accept_and_credentials() -> None:
    policy = PaperFlatAccountReadPolicy()
    positions = "https://paper-api.alpaca.markets/v2/positions"
    with pytest.raises(AlpacaPaperPolicyError, match="non-canonical"):
        policy.validate(request(positions, headers={"Accept": "application/json"}))
    bad_accept = {
        "Accept": "text/plain",
        "User-Agent": "AUTO-TRADE-R6/0.28R",
        "APCA-API-KEY-ID": "key",
        "APCA-API-SECRET-KEY": "secret",
    }
    with pytest.raises(AlpacaPaperPolicyError, match="Accept"):
        policy.validate(request(positions, headers=bad_accept))
    bad_secret = {
        "Accept": "application/json",
        "User-Agent": "AUTO-TRADE-R6/0.28R",
        "APCA-API-KEY-ID": "key",
        "APCA-API-SECRET-KEY": " secret ",
    }
    with pytest.raises(AlpacaPaperPolicyError, match="credentials"):
        policy.validate(request(positions, headers=bad_secret))
