from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.brokers.alpaca_paper_operational_evidence import (
    PaperOperationalEvidenceBlocked,
    PaperOperationalEvidenceCollector,
)
from autotrade.brokers.alpaca_paper_qualification import PaperQualificationReport
from autotrade.brokers.alpaca_paper_reconciliation import AlpacaPaperBracketReconciler
from autotrade.brokers.alpaca_paper_reconciliation_gateway import AlpacaPaperLookupResult
from autotrade.brokers.alpaca_paper_submission import PaperSubmissionStatus
from autotrade.brokers.alpaca_paper_trade_updates_transport import (
    AlpacaPaperTradeUpdatesTransport,
    PaperTradeUpdatesConfig,
)
from test_r6_paper_canary_coordinator import NOW, attestation, prepare, stack
from test_r6_paper_trade_updates import frame, order_payload
from test_r6_paper_trade_updates_transport import (
    FakeConnector,
    FakeSocket,
    auth_ok,
    listening_ok,
)


PARENT_ID = "operational-parent-001"
TP_ID = "operational-tp-001"
STOP_ID = "operational-stop-001"


def credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(
        key_id="operational-paper-key",
        secret_key="operational-paper-secret",
    )


def nested_payload(expected):
    payload = expected.canonical_payload
    return {
        "id": PARENT_ID,
        "client_order_id": expected.client_order_id,
        "symbol": payload["symbol"],
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "order_class": "bracket",
        "extended_hours": False,
        "qty": payload["qty"],
        "limit_price": payload["limit_price"],
        "status": "accepted",
        "legs": [
            {
                "id": TP_ID,
                "side": "sell",
                "type": "limit",
                "qty": payload["qty"],
                "limit_price": payload["take_profit"]["limit_price"],
                "stop_price": None,
                "status": "held",
            },
            {
                "id": STOP_ID,
                "side": "sell",
                "type": "stop",
                "qty": payload["qty"],
                "limit_price": None,
                "stop_price": payload["stop_loss"]["stop_price"],
                "status": "held",
            },
        ],
    }


class FakeLookupGateway:
    def __init__(self, expected) -> None:
        self.expected = expected
        self.lookup_calls = 0
        self.detail_calls = 0

    def lookup_by_client_order_id(self, *, credentials, account_attestation, client_order_id):
        del credentials, account_attestation
        self.lookup_calls += 1
        assert client_order_id == self.expected.client_order_id
        return AlpacaPaperLookupResult(
            found=True,
            request_id="operational-lookup-001",
            client_order_id=client_order_id,
            broker_order_id=PARENT_ID,
            body=json.dumps(nested_payload(self.expected)).encode(),
        )

    def get_nested_order(self, *, credentials, account_attestation, broker_order_id):
        del credentials, account_attestation
        self.detail_calls += 1
        assert broker_order_id == PARENT_ID
        return AlpacaPaperLookupResult(
            found=True,
            request_id=f"operational-detail-{self.detail_calls:03d}",
            client_order_id=self.expected.client_order_id,
            broker_order_id=PARENT_ID,
            body=json.dumps(nested_payload(self.expected)).encode(),
        )


def application_frames(expected):
    new_at = NOW + timedelta(seconds=2)
    fill_at = NOW + timedelta(seconds=3)
    new_frame = frame(
        "new",
        at=new_at,
        order=order_payload(
            broker_order_id=PARENT_ID,
            client_order_id=expected.client_order_id,
            side="buy",
            status="new",
            qty=str(expected.canonical_payload["qty"]),
            filled_qty="0",
            updated_at=new_at,
        ),
    )
    fill_frame = frame(
        "fill",
        at=fill_at,
        order=order_payload(
            broker_order_id=PARENT_ID,
            client_order_id=expected.client_order_id,
            side="buy",
            status="filled",
            qty=str(expected.canonical_payload["qty"]),
            filled_qty=str(expected.canonical_payload["qty"]),
            updated_at=fill_at,
        ),
        execution_id="operational-fill-001",
        price=str(expected.canonical_payload["limit_price"]),
        fill_qty=str(expected.canonical_payload["qty"]),
        position_qty=str(expected.canonical_payload["qty"]),
    )
    return new_frame, fill_frame


def setup_operational_attempt(tmp_path, *, with_frames=True):
    coordinator, broker, _, submission, permit = stack(tmp_path / "coordinator")
    prepared = prepare(coordinator, submission, permit)
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    account = attestation()
    workspace.write_account_attestation(account)
    workspace.write_prepared_canary(prepared.package, prepared.bracket)
    submission.mark_submit_attempt_unknown(
        order_id=prepared.package.order_id,
        attempt_id=prepared.package.attempt_id,
        now=NOW + timedelta(milliseconds=500),
    )

    lookup = FakeLookupGateway(prepared.bracket)
    reconciler = AlpacaPaperBracketReconciler(lookup_gateway=lookup)  # type: ignore[arg-type]
    frames = [auth_ok(), listening_ok()]
    if with_frames:
        frames.extend(application_frames(prepared.bracket))
    socket = FakeSocket(frames)
    connector = FakeConnector(socket)
    transport = AlpacaPaperTradeUpdatesTransport(
        PaperTradeUpdatesConfig(enabled=True),
        connector=connector,
    )
    collector = PaperOperationalEvidenceCollector(
        workspace=workspace,
        reconciler=reconciler,
        trade_updates_transport=transport,
    )
    assert broker.calls == 0
    return collector, workspace, submission, prepared, account, lookup, connector, socket


def test_full_simulated_evidence_lifecycle_reconciles_captures_and_qualifies_without_order_write(tmp_path) -> None:
    collector, workspace, submission, prepared, account, lookup, connector, socket = (
        setup_operational_attempt(tmp_path)
    )

    reconciled = collector.reconcile_and_persist(
        registry=submission,
        credentials=credentials(),
        account_attestation=account,
        now=NOW + timedelta(seconds=1),
    )
    assert reconciled.found is True
    assert reconciled.submission_status == PaperSubmissionStatus.ACKNOWLEDGED.value
    assert reconciled.bracket_attestation_path == workspace.bracket_attestation_path
    assert lookup.lookup_calls == 1
    assert lookup.detail_calls == 1

    captured = collector.capture_trade_updates(
        registry=submission,
        credentials=credentials(),
        max_frames=2,
        max_idle_polls=1,
        timeout_seconds=1,
    )
    assert captured.received_frames == 2
    assert captured.appended_events == 2
    assert captured.ledger_state.event_count == 2
    assert captured.ledger_state.parent_filled_qty == prepared.order.intent.quantity
    assert len(connector.calls) == 1
    assert len(socket.sent) == 2
    assert socket.closed is True

    qualified = collector.qualify(
        registry=submission,
        evaluated_at=NOW + timedelta(seconds=4),
    )
    persisted = PaperQualificationReport.read(qualified.qualification_report_path)
    assert persisted == qualified.report
    evidence = json.loads(qualified.evidence_manifest_path.read_text(encoding="utf-8"))
    assert evidence["capital_authority"] == "NONE"
    assert evidence["external_paper_evidence_complete"] is True
    assert evidence["external_order_submitted"] is True
    assert evidence["profitability_claim"] is False
    assert evidence["live_trading"] == "BLOCKED"

    forbidden = {"submit", "submit_once", "stage_external_submission", "record_operator_approval", "post", "write"}
    assert not (forbidden & set(dir(collector)))


def test_acknowledged_crash_before_attestation_artifact_recovers_by_nested_get_only(tmp_path) -> None:
    collector, workspace, submission, prepared, account, lookup, _, _ = setup_operational_attempt(
        tmp_path,
        with_frames=False,
    )
    first = collector.reconcile_and_persist(
        registry=submission,
        credentials=credentials(),
        account_attestation=account,
        now=NOW + timedelta(seconds=1),
    )
    assert first.found is True
    assert lookup.lookup_calls == 1
    assert lookup.detail_calls == 1

    workspace.bracket_attestation_path.unlink()
    recovered = collector.reconcile_and_persist(
        registry=submission,
        credentials=credentials(),
        account_attestation=account,
        now=NOW + timedelta(seconds=2),
    )
    assert recovered.found is True
    assert workspace.bracket_attestation_path.is_file()
    assert lookup.lookup_calls == 1
    assert lookup.detail_calls == 2
    assert submission.get(prepared.package.order_id).status is PaperSubmissionStatus.ACKNOWLEDGED


def test_persisted_attestation_replay_performs_zero_reconciliation_reads(tmp_path) -> None:
    collector, _, submission, _, account, lookup, _, _ = setup_operational_attempt(tmp_path, with_frames=False)
    collector.reconcile_and_persist(
        registry=submission,
        credentials=credentials(),
        account_attestation=account,
        now=NOW + timedelta(seconds=1),
    )
    lookup_calls = (lookup.lookup_calls, lookup.detail_calls)
    replay = collector.reconcile_and_persist(
        registry=submission,
        credentials=credentials(),
        account_attestation=account,
        now=NOW + timedelta(seconds=2),
    )
    assert replay.found is True
    assert (lookup.lookup_calls, lookup.detail_calls) == lookup_calls


def test_account_mismatch_and_prepared_state_block_before_network(tmp_path) -> None:
    collector, _, submission, _, account, lookup, connector, _ = setup_operational_attempt(
        tmp_path,
        with_frames=False,
    )
    wrong_account = replace(account, request_id="different-account-evidence")
    with pytest.raises(PaperOperationalEvidenceBlocked, match="differs"):
        collector.reconcile_and_persist(
            registry=submission,
            credentials=credentials(),
            account_attestation=wrong_account,
            now=NOW + timedelta(seconds=1),
        )
    assert lookup.lookup_calls == 0
    assert lookup.detail_calls == 0
    assert connector.calls == []

    coordinator, _, _, prepared_registry, permit = stack(tmp_path / "prepared-only")
    prepared = prepare(coordinator, prepared_registry, permit)
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "prepared-workspace")
    workspace.write_account_attestation(attestation())
    workspace.write_prepared_canary(prepared.package, prepared.bracket)
    prepared_lookup = FakeLookupGateway(prepared.bracket)
    prepared_collector = PaperOperationalEvidenceCollector(
        workspace=workspace,
        reconciler=AlpacaPaperBracketReconciler(lookup_gateway=prepared_lookup),  # type: ignore[arg-type]
        trade_updates_transport=AlpacaPaperTradeUpdatesTransport(),
    )
    with pytest.raises(PaperOperationalEvidenceBlocked, match="UNKNOWN or ACKNOWLEDGED"):
        prepared_collector.reconcile_and_persist(
            registry=prepared_registry,
            credentials=credentials(),
            account_attestation=attestation(),
            now=NOW + timedelta(seconds=1),
        )
    assert prepared_lookup.lookup_calls == 0


def test_capture_requires_reconciled_scope_and_bounded_arguments_before_wss(tmp_path) -> None:
    collector, _, submission, _, _, _, connector, _ = setup_operational_attempt(
        tmp_path,
        with_frames=False,
    )
    with pytest.raises(PaperOperationalEvidenceBlocked, match="persisted reconciled"):
        collector.capture_trade_updates(registry=submission, credentials=credentials())
    assert connector.calls == []

    for kwargs in (
        {"max_frames": 0},
        {"max_frames": 257},
        {"max_idle_polls": 0},
        {"max_idle_polls": 21},
        {"timeout_seconds": 0},
        {"timeout_seconds": 61},
    ):
        with pytest.raises((TypeError, ValueError)):
            collector.capture_trade_updates(
                registry=submission,
                credentials=credentials(),
                **kwargs,
            )
    assert connector.calls == []
