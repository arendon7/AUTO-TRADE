from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_reconciliation_gateway import (
    AlpacaPaperLookupResponse,
    AlpacaPaperOrderLookupGateway,
    AlpacaPaperReconciliationConfig,
)
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.brokers.alpaca_paper_writer import (
    AlpacaPaperWriteResponse,
    AlpacaPaperWriterConfig,
    PaperWriterAmbiguous,
    PaperWriterDisabled,
    PaperWriterPolicyError,
)
from autotrade.connectivity_workspace_post import (
    ConnectivityWorkspaceOneShotExecutor,
    ConnectivityWorkspacePostAmbiguous,
    ConnectivityWorkspacePostBlocked,
    ConnectivityWorkspacePostConflict,
    ConnectivityWorkspaceReconciliationRuntime,
    load_connectivity_post_context,
)
from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime, SQLiteSafetyStateStore
from test_r6_connectivity_candidate import CREDS
from test_r6_connectivity_execution_freshness_binding import bound_workspace


class Clock:
    def __init__(self, *times):
        self.times = list(times)
        self.last = times[-1]

    def __call__(self):
        if self.times:
            self.last = self.times.pop(0)
        return self.last


class CallbackClock:
    def __init__(self, first, second, callback):
        self.first = first
        self.second = second
        self.callback = callback
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls == 1:
            return self.first
        self.callback()
        return self.second


class FakeWriteTransport:
    def __init__(self, *, mode="success"):
        self.mode = mode
        self.requests = []

    def write(self, request):
        self.requests.append(request)
        if self.mode == "transport-error":
            raise PaperWriterAmbiguous("simulated uncertain network")
        payload = json.loads(request.body.decode("utf-8"))
        client_order_id = payload["client_order_id"]
        if self.mode == "client-mismatch":
            client_order_id = "wrong-client-order-id"
        if self.mode == "malformed":
            body = b"{"
        elif self.mode == "explicit-error":
            body = json.dumps({"message": "paper rejected"}, separators=(",", ":")).encode()
        else:
            body = json.dumps(
                {
                    "id": "broker-connectivity-parent-001",
                    "client_order_id": client_order_id,
                    "status": "accepted",
                },
                separators=(",", ":"),
            ).encode()
        return AlpacaPaperWriteResponse(
            status_code=422 if self.mode == "explicit-error" else 200,
            body=body,
            final_url="https://paper-api.alpaca.markets/v2/orders",
            headers={
                "content-type": "application/json",
                "x-request-id": "connectivity-post-request-001",
            },
        )


def executor(ws, bound, transport, *, config=None, second=None):
    first = bound.binding.issued_at + timedelta(milliseconds=50)
    second = second or bound.binding.issued_at + timedelta(milliseconds=100)
    return ConnectivityWorkspaceOneShotExecutor(
        workspace=ws,
        config=config or AlpacaPaperWriterConfig(enabled=True),
        transport=transport,
        clock=Clock(first, second),
    )


def durable(ws, order_id):
    order = SQLiteOrderStore(SQLiteRuntime(ws.core_db_path)).get_by_order_id(order_id)
    submission = SQLitePaperSubmissionRegistry(SQLiteRuntime(ws.submission_db_path)).get(order_id)
    return order, submission


def test_one_shot_success_performs_exactly_one_post_and_stays_unknown(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    transport = FakeWriteTransport()
    result = executor(ws, bound, transport).execute_once(credentials=CREDS, bound_result=bound)

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.url == "https://paper-api.alpaca.markets/v2/orders"
    assert request.timeout_seconds == 5.0
    assert json.loads(request.body.decode())["client_order_id"] == bound.binding.client_order_id

    order, submission = durable(ws, bound.binding.order_id)
    assert order.status is OrderStatus.SUBMITTING
    assert submission.status is PaperSubmissionStatus.UNKNOWN
    assert submission.attempt_count == 1
    assert submission.submit_allowed is False
    assert submission.broker_order_id is None
    assert result.observation.provisionally_accepted is True
    assert result.observation.broker_order_id_observed == "broker-connectivity-parent-001"

    artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert artifact["external_post_attempted"] is True
    assert artifact["external_post_attempt_count"] == 1
    assert artifact["broker_order_existence"] == "UNRESOLVED"
    assert artifact["submission_status"] == "UNKNOWN"
    assert artifact["reconciliation_required"] is True
    assert artifact["blind_retry_allowed"] is False
    assert artifact["capital_authority"] == "NONE"
    assert artifact["live_trading"] == "BLOCKED"
    assert artifact["next_action"] == "GET_ONLY_RECONCILIATION_REQUIRED"


def test_ambiguous_transport_never_allows_second_post(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    transport = FakeWriteTransport(mode="transport-error")
    runtime = executor(ws, bound, transport)

    with pytest.raises(ConnectivityWorkspacePostAmbiguous, match="reconciliation-only"):
        runtime.execute_once(credentials=CREDS, bound_result=bound)
    assert len(transport.requests) == 1
    order, submission = durable(ws, bound.binding.order_id)
    assert order.status is OrderStatus.SUBMITTING
    assert submission.status is PaperSubmissionStatus.UNKNOWN
    ambiguity = json.loads((ws.root / "connectivity_post_ambiguity.json").read_text())
    assert ambiguity["transport_invoked"] is True
    assert ambiguity["submission_status"] == "UNKNOWN"
    assert ambiguity["blind_retry_allowed"] is False

    second_transport = FakeWriteTransport()
    with pytest.raises(ConnectivityWorkspacePostBlocked):
        executor(ws, bound, second_transport).execute_once(credentials=CREDS, bound_result=bound)
    assert second_transport.requests == []


@pytest.mark.parametrize("mode", ["client-mismatch", "malformed"])
def test_non_authoritative_response_is_ambiguous_and_not_retried(tmp_path, monkeypatch, mode):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    transport = FakeWriteTransport(mode=mode)
    with pytest.raises(ConnectivityWorkspacePostAmbiguous):
        executor(ws, bound, transport).execute_once(credentials=CREDS, bound_result=bound)
    assert len(transport.requests) == 1
    _, submission = durable(ws, bound.binding.order_id)
    assert submission.status is PaperSubmissionStatus.UNKNOWN
    assert submission.attempt_count == 1


def test_explicit_non_2xx_is_observed_but_still_unknown(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    transport = FakeWriteTransport(mode="explicit-error")
    result = executor(ws, bound, transport).execute_once(credentials=CREDS, bound_result=bound)
    assert len(transport.requests) == 1
    assert result.observation.http_status == 422
    assert result.observation.provisionally_accepted is False
    assert result.observation.broker_order_id_observed is None
    _, submission = durable(ws, bound.binding.order_id)
    assert submission.status is PaperSubmissionStatus.UNKNOWN


def test_disabled_or_live_writer_blocks_before_unknown_and_before_io(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    disabled = FakeWriteTransport()
    with pytest.raises(PaperWriterDisabled):
        executor(
            ws,
            bound,
            disabled,
            config=AlpacaPaperWriterConfig(enabled=False),
        ).execute_once(credentials=CREDS, bound_result=bound)
    order, submission = durable(ws, bound.binding.order_id)
    assert order.status is OrderStatus.VALIDATED
    assert submission.status is PaperSubmissionStatus.PREPARED
    assert disabled.requests == []
    assert not (ws.root / "connectivity_staging.json").exists()

    live = FakeWriteTransport()
    with pytest.raises(PaperWriterPolicyError):
        executor(
            ws,
            bound,
            live,
            config=AlpacaPaperWriterConfig(
                enabled=True,
                base_url="https://api.alpaca.markets",
            ),
        ).execute_once(credentials=CREDS, bound_result=bound)
    assert live.requests == []
    order, submission = durable(ws, bound.binding.order_id)
    assert order.status is OrderStatus.VALIDATED
    assert submission.status is PaperSubmissionStatus.PREPARED


def test_freshness_expiring_after_unknown_blocks_io_and_restart(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    transport = FakeWriteTransport()
    runtime = executor(
        ws,
        bound,
        transport,
        second=bound.binding.expires_at,
    )
    with pytest.raises(ConnectivityWorkspacePostBlocked, match="freshness expired"):
        runtime.execute_once(credentials=CREDS, bound_result=bound)
    assert transport.requests == []
    order, submission = durable(ws, bound.binding.order_id)
    assert order.status is OrderStatus.SUBMITTING
    assert submission.status is PaperSubmissionStatus.UNKNOWN
    ambiguity = json.loads((ws.root / "connectivity_post_ambiguity.json").read_text())
    assert ambiguity["transport_invoked"] is False

    another = FakeWriteTransport()
    with pytest.raises(ConnectivityWorkspacePostBlocked):
        executor(ws, bound, another).execute_once(credentials=CREDS, bound_result=bound)
    assert another.requests == []


def test_safety_change_after_unknown_blocks_before_io(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    first = bound.binding.issued_at + timedelta(milliseconds=50)
    second = bound.binding.issued_at + timedelta(milliseconds=100)
    transport = FakeWriteTransport()

    def activate_kill():
        SQLiteSafetyStateStore(SQLiteRuntime(ws.core_db_path)).activate(
            reason="operator-emergency",
            now=second,
        )

    runtime = ConnectivityWorkspaceOneShotExecutor(
        workspace=ws,
        config=AlpacaPaperWriterConfig(enabled=True),
        transport=transport,
        clock=CallbackClock(first, second, activate_kill),
    )
    with pytest.raises(ConnectivityWorkspacePostBlocked, match="Safety state version changed"):
        runtime.execute_once(credentials=CREDS, bound_result=bound)
    assert transport.requests == []
    order, submission = durable(ws, bound.binding.order_id)
    assert order.status is OrderStatus.SUBMITTING
    assert submission.status is PaperSubmissionStatus.UNKNOWN
    ambiguity = json.loads((ws.root / "connectivity_post_ambiguity.json").read_text())
    assert ambiguity["transport_invoked"] is False
    assert ambiguity["reason"] == "FINAL_STATE_DRIFT_BEFORE_IO"


def test_wrong_credentials_or_tampered_preparation_block_before_staging(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    wrong = AlpacaPaperCredentials(key_id="OTHERKEY123", secret_key="OTHERSECRET456")
    transport = FakeWriteTransport()
    with pytest.raises(PaperWriterPolicyError, match="credentials"):
        executor(ws, bound, transport).execute_once(credentials=wrong, bound_result=bound)
    assert transport.requests == []
    order, submission = durable(ws, bound.binding.order_id)
    assert order.status is OrderStatus.VALIDATED
    assert submission.status is PaperSubmissionStatus.PREPARED

    path = ws.root / "connectivity_preparation.json"
    raw = json.loads(path.read_text())
    raw["external_post_authorized"] = True
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConnectivityWorkspacePostConflict):
        executor(ws, bound, transport).execute_once(credentials=CREDS, bound_result=bound)
    assert transport.requests == []


class Lookup404Transport:
    def __init__(self):
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        return AlpacaPaperLookupResponse(
            status_code=404,
            body=b'{"message":"order not found"}',
            final_url=request.url,
            headers={
                "content-type": "application/json",
                "x-request-id": "connectivity-reconcile-404",
            },
        )


class LookupFoundTransport:
    def __init__(self, expected):
        self.expected = expected
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        if "orders:by_client_order_id" in request.url:
            body = json.dumps(
                {
                    "id": "broker-connectivity-parent-001",
                    "client_order_id": self.expected.client_order_id,
                },
                separators=(",", ":"),
            ).encode()
        else:
            p = self.expected.canonical_payload
            body = json.dumps(
                {
                    "id": "broker-connectivity-parent-001",
                    "client_order_id": self.expected.client_order_id,
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
                            "id": "broker-connectivity-tp-001",
                            "side": "sell",
                            "type": "limit",
                            "qty": p["qty"],
                            "limit_price": p["take_profit"]["limit_price"],
                            "stop_price": None,
                            "status": "held",
                        },
                        {
                            "id": "broker-connectivity-stop-001",
                            "side": "sell",
                            "type": "stop",
                            "qty": p["qty"],
                            "limit_price": None,
                            "stop_price": p["stop_loss"]["stop_price"],
                            "status": "held",
                        },
                    ],
                },
                separators=(",", ":"),
            ).encode()
        return AlpacaPaperLookupResponse(
            status_code=200,
            body=body,
            final_url=request.url,
            headers={
                "content-type": "application/json",
                "x-request-id": f"connectivity-reconcile-{len(self.requests)}",
            },
        )


def gateway(transport):
    return AlpacaPaperOrderLookupGateway(
        config=AlpacaPaperReconciliationConfig(enabled=True),
        transport=transport,
    )


def test_restart_reconciliation_404_is_get_only_and_cannot_reenable_submit(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    post_transport = FakeWriteTransport(mode="transport-error")
    with pytest.raises(ConnectivityWorkspacePostAmbiguous):
        executor(ws, bound, post_transport).execute_once(credentials=CREDS, bound_result=bound)

    read_transport = Lookup404Transport()
    outcome = ConnectivityWorkspaceReconciliationRuntime(
        workspace=ws,
        lookup_gateway=gateway(read_transport),
    ).reconcile_once(
        credentials=CREDS,
        now=bound.binding.expires_at + timedelta(seconds=1),
    )
    assert outcome.found is False
    assert outcome.state.status is PaperSubmissionStatus.UNKNOWN
    assert outcome.state.submit_allowed is False
    assert outcome.state.absence_observation_count == 1
    assert len(read_transport.requests) == 1
    assert read_transport.requests[0].method == "GET"
    assert post_transport.requests and len(post_transport.requests) == 1


def test_restart_reconciliation_found_validates_nested_bracket_and_acknowledges(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    post_transport = FakeWriteTransport()
    executor(ws, bound, post_transport).execute_once(credentials=CREDS, bound_result=bound)
    context = load_connectivity_post_context(
        ws,
        credentials=CREDS,
        order_id=bound.binding.order_id,
        client_order_id=bound.binding.client_order_id,
        attempt_id=bound.binding.attempt_id,
        expected_submission_status=PaperSubmissionStatus.UNKNOWN,
    )
    read_transport = LookupFoundTransport(context.bracket)
    outcome = ConnectivityWorkspaceReconciliationRuntime(
        workspace=ws,
        lookup_gateway=gateway(read_transport),
    ).reconcile_once(
        credentials=CREDS,
        now=bound.binding.expires_at + timedelta(seconds=1),
    )
    assert outcome.found is True
    assert outcome.state.status is PaperSubmissionStatus.ACKNOWLEDGED
    assert outcome.state.submit_allowed is False
    assert outcome.bracket_attestation is not None
    assert outcome.bracket_attestation.parent_order_id == "broker-connectivity-parent-001"
    assert len(read_transport.requests) == 2
    assert all(request.method == "GET" for request in read_transport.requests)


def test_post_runtime_type_guards_and_context_status_guard(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    with pytest.raises(TypeError):
        ConnectivityWorkspaceOneShotExecutor(workspace=object())
    with pytest.raises(TypeError):
        ConnectivityWorkspaceOneShotExecutor(workspace=ws).execute_once(
            credentials=object(),
            bound_result=bound,
        )
    with pytest.raises(TypeError):
        ConnectivityWorkspaceOneShotExecutor(workspace=ws).execute_once(
            credentials=CREDS,
            bound_result=object(),
        )
    with pytest.raises(TypeError):
        ConnectivityWorkspaceReconciliationRuntime(
            workspace=ws,
            lookup_gateway=object(),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        ConnectivityWorkspaceReconciliationRuntime(
            workspace=ws,
            lookup_gateway=gateway(Lookup404Transport()),
        ).reconcile_once(
            credentials=CREDS,
            now=bound.binding.issued_at.replace(tzinfo=None),
        )

    with pytest.raises(ConnectivityWorkspacePostBlocked):
        load_connectivity_post_context(
            ws,
            credentials=CREDS,
            order_id=bound.binding.order_id,
            client_order_id=bound.binding.client_order_id,
            attempt_id=bound.binding.attempt_id,
            expected_submission_status=PaperSubmissionStatus.UNKNOWN,
        )


def test_post_context_rejects_acknowledged_as_execution_source(tmp_path, monkeypatch):
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="PREPARED or UNKNOWN"):
        load_connectivity_post_context(
            ws,
            credentials=CREDS,
            order_id=bound.binding.order_id,
            client_order_id=bound.binding.client_order_id,
            attempt_id=bound.binding.attempt_id,
            expected_submission_status=PaperSubmissionStatus.ACKNOWLEDGED,
        )
