from __future__ import annotations

from dataclasses import dataclass
import json

import pytest

from autotrade.brokers.alpaca_paper_crypto_final_guard import CryptoFinalWriteAttestation
from autotrade.brokers.alpaca_paper_crypto_pre_io import (
    CryptoPreIoInterlockError,
    DeterministicCryptoPaperSimulationTransport,
    FinalGuardedCryptoEntryTransport,
    _client_order_id_from_body,
)
from autotrade.brokers.alpaca_paper_crypto_writer import (
    ALPACA_PAPER_TRADING_HOST,
    CRYPTO_ORDERS_PATH,
    AlpacaPaperCryptoWriteResponse,
    CryptoPaperWriterPolicyError,
)
from test_r6_paper_crypto_final_guard import _advance_to_pre_io, _authorize_pre, _setup


HEADERS = {
    "APCA-API-KEY-ID": "simulation-paper-key",
    "APCA-API-SECRET-KEY": "simulation-paper-secret",
    "Content-Type": "application/json",
}


def _body(ctx) -> bytes:
    return json.dumps(
        ctx.broker_order.to_payload(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass
class RecordingDelegate:
    calls: int = 0

    def post(self, **kwargs):
        self.calls += 1
        payload = json.loads(kwargs["body"].decode("utf-8"))
        return AlpacaPaperCryptoWriteResponse(
            status_code=200,
            body=json.dumps(
                {
                    "id": "delegate-order-1",
                    "client_order_id": payload["client_order_id"],
                    "symbol": payload["symbol"],
                    "asset_class": "crypto",
                    "side": payload["side"],
                    "type": payload["type"],
                    "time_in_force": payload["time_in_force"],
                    "status": "accepted",
                    "qty": payload["qty"],
                    "filled_qty": "0",
                    "limit_price": payload.get("limit_price"),
                    "stop_price": payload.get("stop_price"),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            headers={"content-type": "application/json", "x-request-id": "delegate-request-1"},
        )


def _valid_final(ctx) -> CryptoFinalWriteAttestation:
    pre = _authorize_pre(ctx)
    return _advance_to_pre_io(ctx, pre)


def test_guarded_transport_constructor_requires_delegate_and_callable_authorizer() -> None:
    with pytest.raises(TypeError, match="delegate"):
        FinalGuardedCryptoEntryTransport(delegate=None, authorizer=lambda: None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="authorizer"):
        FinalGuardedCryptoEntryTransport(delegate=RecordingDelegate(), authorizer=object())  # type: ignore[arg-type]


def test_guarded_transport_rejects_wrong_host_path_and_invalid_body_before_authorizer(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    calls = 0

    def authorizer():
        nonlocal calls
        calls += 1
        raise AssertionError("authorizer must not run")

    guarded = FinalGuardedCryptoEntryTransport(delegate=RecordingDelegate(), authorizer=authorizer)
    with pytest.raises(CryptoPaperWriterPolicyError, match="exact PAPER orders endpoint"):
        guarded.post(
            host="api.alpaca.markets",
            path=CRYPTO_ORDERS_PATH,
            headers=HEADERS,
            body=_body(ctx),
            timeout_seconds=1.0,
            max_response_bytes=4096,
        )
    with pytest.raises(CryptoPaperWriterPolicyError, match="exact PAPER orders endpoint"):
        guarded.post(
            host=ALPACA_PAPER_TRADING_HOST,
            path="/v2/account",
            headers=HEADERS,
            body=_body(ctx),
            timeout_seconds=1.0,
            max_response_bytes=4096,
        )
    with pytest.raises(CryptoPreIoInterlockError, match="invalid JSON"):
        guarded.post(
            host=ALPACA_PAPER_TRADING_HOST,
            path=CRYPTO_ORDERS_PATH,
            headers=HEADERS,
            body=b"{bad-json",
            timeout_seconds=1.0,
            max_response_bytes=4096,
        )
    assert calls == 0


def test_guarded_transport_rejects_non_attestation_and_preconsume_evidence(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    body = _body(ctx)
    delegate = RecordingDelegate()

    guarded = FinalGuardedCryptoEntryTransport(delegate=delegate, authorizer=lambda: object())
    with pytest.raises(CryptoPreIoInterlockError, match="invalid evidence"):
        guarded.post(
            host=ALPACA_PAPER_TRADING_HOST,
            path=CRYPTO_ORDERS_PATH,
            headers=HEADERS,
            body=body,
            timeout_seconds=1.0,
            max_response_bytes=4096,
        )
    assert delegate.calls == 0

    pre = _authorize_pre(ctx)
    guarded = FinalGuardedCryptoEntryTransport(delegate=delegate, authorizer=lambda: pre)
    with pytest.raises(CryptoPreIoInterlockError, match="requires PRE_IO"):
        guarded.post(
            host=ALPACA_PAPER_TRADING_HOST,
            path=CRYPTO_ORDERS_PATH,
            headers=HEADERS,
            body=body,
            timeout_seconds=1.0,
            max_response_bytes=4096,
        )
    assert delegate.calls == 0


def test_guarded_transport_delegates_once_only_after_valid_preio(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    final = _valid_final(ctx)
    delegate = RecordingDelegate()
    guarded = FinalGuardedCryptoEntryTransport(delegate=delegate, authorizer=lambda: final)

    response = guarded.post(
        host=ALPACA_PAPER_TRADING_HOST,
        path=CRYPTO_ORDERS_PATH,
        headers=HEADERS,
        body=_body(ctx),
        timeout_seconds=1.0,
        max_response_bytes=4096,
    )

    assert response.status_code == 200
    assert guarded.last_attestation == final
    assert guarded.delegated_calls == 1
    assert delegate.calls == 1

    with pytest.raises(CryptoPreIoInterlockError, match="one-shot"):
        guarded.post(
            host=ALPACA_PAPER_TRADING_HOST,
            path=CRYPTO_ORDERS_PATH,
            headers=HEADERS,
            body=_body(ctx),
            timeout_seconds=1.0,
            max_response_bytes=4096,
        )
    assert delegate.calls == 1


def test_guarded_transport_rejects_request_client_order_id_drift(tmp_path) -> None:
    ctx = _setup(tmp_path / "ctx")
    final = _valid_final(ctx)
    payload = ctx.broker_order.to_payload()
    payload["client_order_id"] = "different-client-order-id"
    delegate = RecordingDelegate()
    guarded = FinalGuardedCryptoEntryTransport(delegate=delegate, authorizer=lambda: final)

    with pytest.raises(CryptoPreIoInterlockError, match="client_order_id differs"):
        guarded.post(
            host=ALPACA_PAPER_TRADING_HOST,
            path=CRYPTO_ORDERS_PATH,
            headers=HEADERS,
            body=json.dumps(payload).encode("utf-8"),
            timeout_seconds=1.0,
            max_response_bytes=4096,
        )
    assert delegate.calls == 0


def test_client_order_id_parser_rejects_non_object_missing_id_and_invalid_utf8() -> None:
    with pytest.raises(CryptoPreIoInterlockError, match="root must be object"):
        _client_order_id_from_body(b"[]")
    with pytest.raises(CryptoPreIoInterlockError, match="lacks client_order_id"):
        _client_order_id_from_body(b"{}")
    with pytest.raises(CryptoPreIoInterlockError, match="invalid JSON"):
        _client_order_id_from_body(b"\xff")


def _simulation_call(transport, *, host=ALPACA_PAPER_TRADING_HOST, path=CRYPTO_ORDERS_PATH, headers=None, body=None):
    canonical = {
        "client_order_id": "atr6c-entry-0123456789012345678901234567890123456789",
        "symbol": "BTC/USD",
        "side": "buy",
        "type": "limit",
        "time_in_force": "ioc",
        "qty": "0.001",
        "limit_price": "50000",
    }
    return transport.post(
        host=host,
        path=path,
        headers=HEADERS if headers is None else headers,
        body=json.dumps(canonical).encode("utf-8") if body is None else body,
        timeout_seconds=1.0,
        max_response_bytes=4096,
    )


def test_simulation_transport_rejects_wrong_endpoint_and_credentials() -> None:
    transport = DeterministicCryptoPaperSimulationTransport()
    with pytest.raises(CryptoPaperWriterPolicyError, match="exact PAPER orders endpoint"):
        _simulation_call(transport, host="api.alpaca.markets")
    with pytest.raises(CryptoPaperWriterPolicyError, match="exact PAPER orders endpoint"):
        _simulation_call(transport, path="/v2/account")

    bad_key = dict(HEADERS)
    bad_key["APCA-API-KEY-ID"] = "real-looking-key"
    with pytest.raises(CryptoPaperWriterPolicyError, match="synthetic credentials"):
        _simulation_call(transport, headers=bad_key)

    bad_secret = dict(HEADERS)
    bad_secret["APCA-API-SECRET-KEY"] = "real-looking-secret"
    with pytest.raises(CryptoPaperWriterPolicyError, match="synthetic credentials"):
        _simulation_call(transport, headers=bad_secret)
    assert transport.calls == 0


def test_simulation_transport_rejects_bad_json_root_and_missing_fields() -> None:
    for body, match in (
        (b"{bad", "invalid JSON"),
        (b"[]", "root must be object"),
        (b"{}", "missing canonical order fields"),
    ):
        transport = DeterministicCryptoPaperSimulationTransport()
        with pytest.raises(CryptoPreIoInterlockError, match=match):
            _simulation_call(transport, body=body)
        assert transport.calls == 0


def test_simulation_transport_success_is_deterministic_and_one_shot() -> None:
    transport = DeterministicCryptoPaperSimulationTransport()
    response = _simulation_call(transport)
    payload = json.loads(response.body)

    assert transport.calls == 1
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "simulation-request-1"
    assert payload["id"] == "simulation-broker-order-1"
    assert payload["client_order_id"].startswith("atr6c-entry-")
    assert payload["asset_class"] == "crypto"
    assert payload["status"] == "accepted"
    assert payload["filled_qty"] == "0"

    with pytest.raises(CryptoPreIoInterlockError, match="one-shot"):
        _simulation_call(transport)
