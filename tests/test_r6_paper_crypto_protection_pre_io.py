from __future__ import annotations

from dataclasses import dataclass
import json

import pytest

from autotrade.brokers.alpaca_paper_crypto_order import CryptoOrderRole
from autotrade.brokers.alpaca_paper_crypto_pre_io import (
    CryptoPreIoInterlockError,
    FinalGuardedCryptoProtectionTransport,
)
from autotrade.brokers.alpaca_paper_crypto_writer import (
    ALPACA_PAPER_TRADING_HOST,
    CRYPTO_ORDERS_PATH,
    AlpacaPaperCryptoWriteResponse,
    GuardedAlpacaPaperCryptoWriteTransport,
)
from test_r6_paper_crypto_protection_final_guard import _advance_to_preio, _preconsume


HEADERS = {
    "APCA-API-KEY-ID": "synthetic-paper-key",
    "APCA-API-SECRET-KEY": "synthetic-paper-secret",
    "Content-Type": "application/json",
}


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
                    "id": "protection-delegate-order-1",
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
            headers={"content-type": "application/json", "x-request-id": "protection-delegate-request-1"},
        )


def _body(prepared) -> bytes:
    return json.dumps(
        prepared.broker_order.to_payload(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _call(transport, body: bytes):
    return transport.post(
        host=ALPACA_PAPER_TRADING_HOST,
        path=CRYPTO_ORDERS_PATH,
        headers=HEADERS,
        body=body,
        timeout_seconds=1.0,
        max_response_bytes=4096,
    )


def test_protection_transport_is_nominal_role_bound_capability(tmp_path) -> None:
    setup_with_pre = _preconsume(tmp_path)
    setup, pre = setup_with_pre[:-1], setup_with_pre[-1]
    final = _advance_to_preio(setup, pre)
    prepared = setup[4]
    guarded = FinalGuardedCryptoProtectionTransport(
        delegate=RecordingDelegate(),
        authorizer=lambda: final,
    )

    assert isinstance(guarded, GuardedAlpacaPaperCryptoWriteTransport)
    assert guarded.role is CryptoOrderRole.PROTECTION
    assert final.lifecycle_status.value == "PROTECTION_SUBMISSION_UNKNOWN"
    assert final.protection_attempt_count == 1
    assert final.client_order_id == prepared.package.client_order_id


def test_protection_transport_delegates_exactly_once_after_valid_preio(tmp_path) -> None:
    setup_with_pre = _preconsume(tmp_path)
    setup, pre = setup_with_pre[:-1], setup_with_pre[-1]
    final = _advance_to_preio(setup, pre)
    prepared = setup[4]
    delegate = RecordingDelegate()
    guarded = FinalGuardedCryptoProtectionTransport(delegate=delegate, authorizer=lambda: final)

    response = _call(guarded, _body(prepared))

    assert response.status_code == 200
    assert guarded.last_attestation == final
    assert guarded.delegated_calls == 1
    assert delegate.calls == 1

    with pytest.raises(CryptoPreIoInterlockError, match="one-shot"):
        _call(guarded, _body(prepared))
    assert delegate.calls == 1


def test_protection_transport_rejects_preconsume_evidence_without_delegation(tmp_path) -> None:
    setup_with_pre = _preconsume(tmp_path)
    setup, pre = setup_with_pre[:-1], setup_with_pre[-1]
    prepared = setup[4]
    delegate = RecordingDelegate()
    guarded = FinalGuardedCryptoProtectionTransport(delegate=delegate, authorizer=lambda: pre)

    with pytest.raises(CryptoPreIoInterlockError, match="requires PRE_IO"):
        _call(guarded, _body(prepared))
    assert delegate.calls == 0


def test_protection_transport_rejects_client_order_id_drift_without_delegation(tmp_path) -> None:
    setup_with_pre = _preconsume(tmp_path)
    setup, pre = setup_with_pre[:-1], setup_with_pre[-1]
    final = _advance_to_preio(setup, pre)
    prepared = setup[4]
    payload = prepared.broker_order.to_payload()
    payload["client_order_id"] = "different-protection-client-order-id"
    delegate = RecordingDelegate()
    guarded = FinalGuardedCryptoProtectionTransport(delegate=delegate, authorizer=lambda: final)

    with pytest.raises(CryptoPreIoInterlockError, match="client_order_id differs"):
        _call(guarded, json.dumps(payload).encode("utf-8"))
    assert delegate.calls == 0
