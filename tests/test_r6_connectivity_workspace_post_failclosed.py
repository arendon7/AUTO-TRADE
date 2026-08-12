from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import json

import pytest

from autotrade.brokers.alpaca_paper_submission import PaperSubmissionStatus
from autotrade.brokers.alpaca_paper_writer import (
    AlpacaPaperWriteResponse,
    AlpacaPaperWriterConfig,
    PaperWriterPolicyError,
)
from autotrade.connectivity_workspace_post import (
    ConnectivityPaperPostObservation,
    ConnectivityWorkspaceOneShotExecutor,
    ConnectivityWorkspacePostAmbiguous,
    ConnectivityWorkspacePostBlocked,
    ConnectivityWorkspacePostConflict,
    ConnectivityWorkspaceReconciliationRuntime,
    load_connectivity_post_context,
)
from test_r6_connectivity_candidate import CREDS
from test_r6_connectivity_execution_freshness_binding import bound_workspace
from test_r6_connectivity_workspace_post import (
    Clock,
    FakeWriteTransport,
    Lookup404Transport,
    gateway,
)


def _rehash_preparation(path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(raw)
    unsigned.pop("preparation_hash", None)
    raw["preparation_hash"] = sha256(
        json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(raw, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return raw


def _context(ws, bound):
    return load_connectivity_post_context(
        ws,
        credentials=CREDS,
        order_id=bound.binding.order_id,
        client_order_id=bound.binding.client_order_id,
        attempt_id=bound.binding.attempt_id,
        expected_submission_status=PaperSubmissionStatus.PREPARED,
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"order_id": ""}, "identifiers"),
        ({"http_status": True}, "http_status"),
        ({"http_status": 99}, "http_status"),
        ({"request_id": ""}, "request_id"),
        ({"response_hash": "not-a-hash"}, "response_hash"),
    ],
)
def test_post_observation_rejects_noncanonical_fields(overrides, message):
    payload = {
        "order_id": "order-1",
        "client_order_id": "client-1",
        "attempt_id": "attempt-1",
        "http_status": 200,
        "request_id": "request-1",
        "broker_order_id_observed": None,
        "response_hash": "a" * 64,
        "provisionally_accepted": True,
    }
    payload.update(overrides)
    with pytest.raises(ValueError, match=message):
        ConnectivityPaperPostObservation(**payload)


def test_post_context_requires_real_nonsymlink_preparation(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_preparation.json"
    copy = ws.root / "connectivity-preparation-copy.json"
    copy.write_bytes(path.read_bytes())
    path.unlink()
    with pytest.raises(ConnectivityWorkspacePostBlocked, match="preparation artifact"):
        _context(ws, bound)

    path.symlink_to(copy.name)
    with pytest.raises(ConnectivityWorkspacePostBlocked, match="preparation artifact"):
        _context(ws, bound)


def test_post_context_rejects_missing_hash_and_rehashed_binding_tamper(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_preparation.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["preparation_hash"] = None
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConnectivityWorkspacePostConflict, match="preparation_hash"):
        _context(ws, bound)

    ws2, _, _, bound2 = bound_workspace(tmp_path / "second", monkeypatch)
    path2 = ws2.root / "connectivity_preparation.json"
    raw2 = json.loads(path2.read_text(encoding="utf-8"))
    raw2["standard_prepared_package"]["submission_binding_hash"] = "f" * 64
    path2.write_text(json.dumps(raw2), encoding="utf-8")
    _rehash_preparation(path2)
    with pytest.raises(ConnectivityWorkspacePostConflict, match="submission binding"):
        _context(ws2, bound2)


def test_post_context_rejects_missing_instrument_fingerprint_after_valid_rehash(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_preparation.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["standard_prepared_package"]["instrument_master_fingerprint"] = None
    path.write_text(json.dumps(raw), encoding="utf-8")
    _rehash_preparation(path)
    with pytest.raises(ConnectivityWorkspacePostConflict, match="instrument master fingerprint"):
        _context(ws, bound)


def test_executor_rejects_expired_binding_before_staging_or_io(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    transport = FakeWriteTransport()
    runtime = ConnectivityWorkspaceOneShotExecutor(
        workspace=ws,
        config=AlpacaPaperWriterConfig(enabled=True),
        transport=transport,
        clock=Clock(bound.binding.expires_at),
    )
    with pytest.raises(ConnectivityWorkspacePostBlocked, match="expired before POST preflight"):
        runtime.execute_once(credentials=CREDS, bound_result=bound)
    assert transport.requests == []
    assert not (ws.root / "connectivity_staging.json").exists()


class InvalidResponseTransport:
    def __init__(self, mode: str):
        self.mode = mode
        self.requests = []

    def write(self, request):
        self.requests.append(request)
        headers = {"content-type": "application/json", "x-request-id": "request-1"}
        final_url = request.url
        status = 200
        body = json.dumps(
            {"id": "broker-1", "client_order_id": json.loads(request.body)["client_order_id"]}
        ).encode("utf-8")
        if self.mode == "bad-final-url":
            final_url = "https://api.alpaca.markets/v2/orders"
        elif self.mode == "missing-request-id":
            headers.pop("x-request-id")
        elif self.mode == "non2xx-without-message":
            status = 422
            body = b'{"code":42210000}'
        return AlpacaPaperWriteResponse(
            status_code=status,
            body=body,
            final_url=final_url,
            headers=headers,
        )


@pytest.mark.parametrize("mode", ["bad-final-url", "missing-request-id", "non2xx-without-message"])
def test_non_authoritative_http_response_is_ambiguous_and_never_acknowledged(tmp_path, monkeypatch, mode):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    first = bound.binding.issued_at + timedelta(milliseconds=50)
    second = bound.binding.issued_at + timedelta(milliseconds=100)
    transport = InvalidResponseTransport(mode)
    runtime = ConnectivityWorkspaceOneShotExecutor(
        workspace=ws,
        config=AlpacaPaperWriterConfig(enabled=True),
        transport=transport,
        clock=Clock(first, second),
    )
    with pytest.raises(ConnectivityWorkspacePostAmbiguous):
        runtime.execute_once(credentials=CREDS, bound_result=bound)
    assert len(transport.requests) == 1
    ambiguity = json.loads((ws.root / "connectivity_post_ambiguity.json").read_text())
    assert ambiguity["transport_invoked"] is True
    assert ambiguity["submission_status"] == "UNKNOWN"
    assert ambiguity["blind_retry_allowed"] is False


def test_reconciliation_rejects_tampered_staging_and_wrong_credentials(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    first = bound.binding.issued_at + timedelta(milliseconds=50)
    second = bound.binding.issued_at + timedelta(milliseconds=100)
    with pytest.raises(ConnectivityWorkspacePostAmbiguous):
        ConnectivityWorkspaceOneShotExecutor(
            workspace=ws,
            config=AlpacaPaperWriterConfig(enabled=True),
            transport=FakeWriteTransport(mode="transport-error"),
            clock=Clock(first, second),
        ).execute_once(credentials=CREDS, bound_result=bound)

    runtime = ConnectivityWorkspaceReconciliationRuntime(
        workspace=ws,
        lookup_gateway=gateway(Lookup404Transport()),
    )
    wrong = type(CREDS)(key_id="OTHERKEY123", secret_key="OTHERSECRET456")
    with pytest.raises(PaperWriterPolicyError, match="credentials"):
        runtime.reconcile_once(
            credentials=wrong,
            now=bound.binding.expires_at + timedelta(seconds=1),
        )

    path = ws.root / "connectivity_staging.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["live_trading"] = "ALLOWED"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConnectivityWorkspacePostConflict, match="unsafe staging field"):
        runtime.reconcile_once(
            credentials=CREDS,
            now=bound.binding.expires_at + timedelta(seconds=2),
        )
