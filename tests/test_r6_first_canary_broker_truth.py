from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import runpy

import pytest

from autotrade.brokers.alpaca_paper_crypto_account_status import (
    AlpacaPaperCryptoAccountNotActive,
    attest_active_crypto_account,
)
from autotrade.brokers.alpaca_paper_crypto_broker_truth_transport import (
    BrokerTruthAlpacaPaperCryptoWriteTransport,
)
from autotrade.brokers.alpaca_paper_crypto_writer import (
    AlpacaPaperCryptoWriteResponse,
    CryptoPaperWriterAmbiguous,
)
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
    AlpacaPaperHttpResponse,
)


ACCOUNT_ID = "11111111-2222-3333-4444-555555555555"
NOW = datetime(2026, 8, 19, 19, 30, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
AUTO_SETTLE = ROOT / "scripts/mac_first_canary_unified_auto_settle.py"


class FakeReadTransport:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0

    def read(self, request):
        self.calls += 1
        return AlpacaPaperHttpResponse(
            status_code=200,
            body=json.dumps(self.payload).encode("utf-8"),
            final_url=f"https://{ALPACA_PAPER_TRADING_HOST}{ALPACA_PAPER_ACCOUNT_PATH}",
            headers={
                "content-type": "application/json",
                "x-request-id": "req-crypto-status-1",
            },
        )


class FakeWriteTransport:
    def __init__(self, response: AlpacaPaperCryptoWriteResponse) -> None:
        self.response = response
        self.calls = 0

    def post(self, **kwargs):
        self.calls += 1
        return self.response


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(key_id="paper-key-id", secret_key="paper-secret-key")


def test_crypto_status_active_is_attested_by_get_only() -> None:
    transport = FakeReadTransport({"id": ACCOUNT_ID, "crypto_status": "ACTIVE"})
    result = attest_active_crypto_account(
        credentials=_credentials(),
        expected_account_id=ACCOUNT_ID,
        now=NOW,
        transport=transport,
    )
    assert result.crypto_ready is True
    assert result.crypto_status == "ACTIVE"
    assert transport.calls == 1


def test_crypto_status_inactive_blocks_before_any_crypto_post() -> None:
    transport = FakeReadTransport({"id": ACCOUNT_ID, "crypto_status": "INACTIVE"})
    with pytest.raises(AlpacaPaperCryptoAccountNotActive, match="ACTIVE is required before any crypto POST"):
        attest_active_crypto_account(
            credentials=_credentials(),
            expected_account_id=ACCOUNT_ID,
            now=NOW,
            transport=transport,
        )
    assert transport.calls == 1


def test_crypto_status_account_identity_mismatch_fails_closed() -> None:
    transport = FakeReadTransport(
        {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "crypto_status": "ACTIVE"}
    )
    with pytest.raises(Exception, match="differs from workspace account"):
        attest_active_crypto_account(
            credentials=_credentials(),
            expected_account_id=ACCOUNT_ID,
            now=NOW,
            transport=transport,
        )


def test_broker_truth_transport_preserves_sanitized_422_without_retry() -> None:
    delegate = FakeWriteTransport(
        AlpacaPaperCryptoWriteResponse(
            status_code=422,
            body=json.dumps(
                {"code": 40010001, "message": "qty must respect broker minimum"}
            ).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-request-id": "req-order-reject-422",
            },
        )
    )
    transport = BrokerTruthAlpacaPaperCryptoWriteTransport(delegate=delegate)
    with pytest.raises(CryptoPaperWriterAmbiguous) as caught:
        transport.post(
            host="paper-api.alpaca.markets",
            path="/v2/orders",
            headers={},
            body=b"{}",
            timeout_seconds=5.0,
            max_response_bytes=1024,
        )
    text = str(caught.value)
    assert "http_status=422" in text
    assert "broker_code=40010001" in text
    assert "qty must respect broker minimum" in text
    assert "GET-only" in text
    assert "paper-secret-key" not in text
    assert delegate.calls == 1


def test_broker_truth_transport_leaves_success_response_unchanged() -> None:
    response = AlpacaPaperCryptoWriteResponse(
        status_code=200,
        body=b"{}",
        headers={"content-type": "application/json", "x-request-id": "req-ok"},
    )
    delegate = FakeWriteTransport(response)
    transport = BrokerTruthAlpacaPaperCryptoWriteTransport(delegate=delegate)
    returned = transport.post(
        host="paper-api.alpaca.markets",
        path="/v2/orders",
        headers={},
        body=b"{}",
        timeout_seconds=5.0,
        max_response_bytes=1024,
    )
    assert returned is response
    assert delegate.calls == 1


def test_auto_settlement_recognizes_sanitized_broker_rejection(monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    namespace = runpy.run_path(str(AUTO_SETTLE))
    classify = namespace["_broker_rejection"]
    result = {
        "execution": {
            "json": {
                "broker_diagnostic": {
                    "writer_error": (
                        "Alpaca PAPER order response rejected; http_status=422; "
                        "broker_code=40010001; message=qty too small; request_id=req-1; "
                        "POST outcome remains burned and reconciliation is GET-only"
                    )
                }
            }
        }
    }
    message = classify(result)
    assert isinstance(message, str)
    assert "http_status=422" in message
    assert "qty too small" in message


def test_broker_truth_surface_reuses_existing_network_stacks_only() -> None:
    diagnostic_source = (
        ROOT / "src/autotrade/brokers/alpaca_paper_crypto_broker_truth_transport.py"
    ).read_text(encoding="utf-8")
    status_source = (
        ROOT / "src/autotrade/brokers/alpaca_paper_crypto_account_status.py"
    ).read_text(encoding="utf-8")
    assert "HttpsAlpacaPaperCryptoWriteTransport" in diagnostic_source
    for forbidden in ("http.client", "requests", "httpx", "socket", "HTTPSConnection"):
        assert forbidden not in diagnostic_source
    assert "UrllibAlpacaPaperReadTransport" in status_source
    for forbidden in ("requests", "httpx", "HTTPSConnection"):
        assert forbidden not in status_source
