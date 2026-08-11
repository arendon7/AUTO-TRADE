from __future__ import annotations

from datetime import timedelta
import json
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_operational_execute import (
    PaperOperationalExecutionBlocked,
    PaperOperationalExecutionRuntime,
    _ExistingHealthStateReader,
    _NoBrokerExecutionSurface,
    _discover_portfolio_health_entity_id,
    _read_account_attestation,
    _read_core_order_read_only,
)
from autotrade.brokers.alpaca_paper_operator_decision import (
    PaperOperatorDecisionContext,
    PaperOperatorDecisionStatus,
    SQLitePaperOperatorDecisionRegistry,
)
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.brokers.alpaca_paper_writer import (
    AlpacaPaperSingleShotWriter,
    AlpacaPaperWriteResponse,
    AlpacaPaperWriterConfig,
)
from autotrade.domain import OrderStatus
from autotrade.health_bridge import HealthEntityKind
from autotrade.persistence import (
    SQLiteOrderStore,
    SQLiteRuntime,
    SQLiteSafetyStateStore,
)
from test_r6_operational_prepare import NOW, build, run_prepare


class FakeWriteTransport:
    def __init__(self) -> None:
        self.requests = []

    def write(self, request):
        self.requests.append(request)
        payload = json.loads(request.body.decode("utf-8"))
        body = json.dumps(
            {
                "id": "paper-parent-order-001",
                "client_order_id": payload["client_order_id"],
            },
            sort_keys=True,
        ).encode("utf-8")
        return AlpacaPaperWriteResponse(
            status_code=200,
            body=body,
            final_url="https://paper-api.alpaca.markets/v2/orders",
            headers={
                "content-type": "application/json",
                "x-request-id": "runtime-submit-request-001",
            },
        )


class CrashBeforeSubmitWriter(AlpacaPaperSingleShotWriter):
    def __init__(self) -> None:
        super().__init__(config=AlpacaPaperWriterConfig(enabled=True))

    def submit_once(self, **_kwargs):
        raise RuntimeError("simulated process loss after bridge staging")


def credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials(
        key_id="coordinator-paper-key",
        secret_key="coordinator-paper-secret",
    )


def prepared_and_approved(tmp_path):
    preparer, workspace, broker, submission, permit = build(tmp_path)
    prepared = run_prepare(preparer, submission, permit)
    context = PaperOperatorDecisionContext.from_prepared_package(prepared.result.package)
    registry = SQLitePaperOperatorDecisionRegistry(SQLiteRuntime(workspace.operator_db_path))
    state = registry.record_operator_approval(
        context=context,
        operator_id="operator:test",
        issued_at=NOW + timedelta(milliseconds=100),
        expires_at=NOW + timedelta(seconds=4),
    )
    assert state.status is PaperOperatorDecisionStatus.ISSUED
    assert broker.calls == 0
    return prepared, workspace, registry


def enabled_writer(transport: FakeWriteTransport) -> AlpacaPaperSingleShotWriter:
    return AlpacaPaperSingleShotWriter(
        config=AlpacaPaperWriterConfig(enabled=True),
        transport=transport,
    )


def test_runtime_executes_exactly_one_paper_post_and_leaves_unknown(tmp_path) -> None:
    prepared, workspace, operator_registry = prepared_and_approved(tmp_path)
    transport = FakeWriteTransport()
    runtime = PaperOperationalExecutionRuntime(
        workspace=workspace,
        writer=enabled_writer(transport),
    )

    result = runtime.execute_once(
        credentials=credentials(),
        now=NOW + timedelta(seconds=1),
    )

    assert len(transport.requests) == 1
    assert result.stage.attempt_id == prepared.result.package.attempt_id
    assert result.submit.attempt_id == prepared.result.package.attempt_id
    assert result.submit.provisionally_accepted is True
    assert result.submit.durable_status is PaperSubmissionStatus.UNKNOWN
    assert result.submit.reconciliation_required is True
    assert result.portfolio_health_entity_id == "portfolio-r6-canary"

    submission = SQLitePaperSubmissionRegistry(SQLiteRuntime(workspace.submission_db_path)).get(
        prepared.result.package.order_id
    )
    assert submission.status is PaperSubmissionStatus.UNKNOWN
    assert submission.attempt_count == 1
    operator = operator_registry.get(
        PaperOperatorDecisionContext.from_prepared_package(
            prepared.result.package
        ).preparation_hash
    )
    assert operator.status is PaperOperatorDecisionStatus.CONSUMED
    assert operator.consumed_attempt_id == prepared.result.package.attempt_id
    durable_order = SQLiteOrderStore(SQLiteRuntime(workspace.core_db_path)).get_by_order_id(
        prepared.result.package.order_id
    )
    assert durable_order is not None
    assert durable_order.status is OrderStatus.SUBMITTING


def test_runtime_restart_after_bridge_staging_resumes_same_attempt_only(tmp_path) -> None:
    prepared, workspace, operator_registry = prepared_and_approved(tmp_path)
    first = PaperOperationalExecutionRuntime(
        workspace=workspace,
        writer=CrashBeforeSubmitWriter(),
    )
    with pytest.raises(RuntimeError, match="process loss"):
        first.execute_once(
            credentials=credentials(),
            now=NOW + timedelta(seconds=1),
        )

    context = PaperOperatorDecisionContext.from_prepared_package(prepared.result.package)
    operator = operator_registry.get(context.preparation_hash)
    assert operator.status is PaperOperatorDecisionStatus.CONSUMED
    assert operator.consumed_attempt_id == prepared.result.package.attempt_id
    durable_order = SQLiteOrderStore(SQLiteRuntime(workspace.core_db_path)).get_by_order_id(
        prepared.result.package.order_id
    )
    assert durable_order is not None and durable_order.status is OrderStatus.SUBMITTING
    before = SQLitePaperSubmissionRegistry(SQLiteRuntime(workspace.submission_db_path)).get(
        prepared.result.package.order_id
    )
    assert before.status is PaperSubmissionStatus.PREPARED
    assert before.attempt_count == 0

    transport = FakeWriteTransport()
    resumed = PaperOperationalExecutionRuntime(
        workspace=workspace,
        writer=enabled_writer(transport),
    )
    result = resumed.execute_once(
        credentials=credentials(),
        now=NOW + timedelta(milliseconds=1500),
    )
    assert len(transport.requests) == 1
    assert result.stage.attempt_id == prepared.result.package.attempt_id
    assert result.submit.durable_status is PaperSubmissionStatus.UNKNOWN


def test_runtime_never_reposts_after_unknown(tmp_path) -> None:
    prepared, workspace, _ = prepared_and_approved(tmp_path)
    first_transport = FakeWriteTransport()
    first = PaperOperationalExecutionRuntime(
        workspace=workspace,
        writer=enabled_writer(first_transport),
    )
    first.execute_once(
        credentials=credentials(),
        now=NOW + timedelta(seconds=1),
    )
    assert len(first_transport.requests) == 1

    second_transport = FakeWriteTransport()
    second = PaperOperationalExecutionRuntime(
        workspace=workspace,
        writer=enabled_writer(second_transport),
    )
    with pytest.raises(PaperOperationalExecutionBlocked, match="POST replay is forbidden"):
        second.execute_once(
            credentials=credentials(),
            now=NOW + timedelta(seconds=2),
        )
    assert second_transport.requests == []
    state = SQLitePaperSubmissionRegistry(SQLiteRuntime(workspace.submission_db_path)).get(
        prepared.result.package.order_id
    )
    assert state.status is PaperSubmissionStatus.UNKNOWN
    assert state.attempt_count == 1


def test_runtime_wrong_credentials_fail_before_human_consumption_or_io(tmp_path) -> None:
    prepared, workspace, operator_registry = prepared_and_approved(tmp_path)
    transport = FakeWriteTransport()
    runtime = PaperOperationalExecutionRuntime(
        workspace=workspace,
        writer=enabled_writer(transport),
    )
    wrong = AlpacaPaperCredentials(key_id="wrong-paper-key", secret_key="wrong-secret")

    with pytest.raises(PaperOperationalExecutionBlocked, match="credentials do not match"):
        runtime.execute_once(
            credentials=wrong,
            now=NOW + timedelta(seconds=1),
        )
    assert transport.requests == []
    state = operator_registry.get(
        PaperOperatorDecisionContext.from_prepared_package(
            prepared.result.package
        ).preparation_hash
    )
    assert state.status is PaperOperatorDecisionStatus.ISSUED


def test_runtime_rejects_expired_human_decision_before_staging(tmp_path) -> None:
    prepared, workspace, operator_registry = prepared_and_approved(tmp_path)
    transport = FakeWriteTransport()
    runtime = PaperOperationalExecutionRuntime(
        workspace=workspace,
        writer=enabled_writer(transport),
    )
    with pytest.raises(PaperOperationalExecutionBlocked, match="human decision is expired"):
        runtime.execute_once(
            credentials=credentials(),
            now=NOW + timedelta(milliseconds=4500),
        )
    assert transport.requests == []
    state = operator_registry.get(
        PaperOperatorDecisionContext.from_prepared_package(
            prepared.result.package
        ).preparation_hash
    )
    assert state.status is PaperOperatorDecisionStatus.ISSUED


def test_runtime_detects_core_safety_drift_before_human_consumption(tmp_path) -> None:
    prepared, workspace, operator_registry = prepared_and_approved(tmp_path)
    SQLiteSafetyStateStore(SQLiteRuntime(workspace.core_db_path)).activate(
        reason="runtime-provenance-race",
        now=NOW + timedelta(milliseconds=500),
    )
    transport = FakeWriteTransport()
    runtime = PaperOperationalExecutionRuntime(
        workspace=workspace,
        writer=enabled_writer(transport),
    )
    with pytest.raises(Exception, match="core provenance"):
        runtime.execute_once(
            credentials=credentials(),
            now=NOW + timedelta(seconds=1),
        )
    assert transport.requests == []
    state = operator_registry.get(
        PaperOperatorDecisionContext.from_prepared_package(
            prepared.result.package
        ).preparation_hash
    )
    assert state.status is PaperOperatorDecisionStatus.ISSUED


def test_runtime_disabled_writer_fails_before_loading_authority(tmp_path) -> None:
    _, workspace, _ = prepared_and_approved(tmp_path)
    runtime = PaperOperationalExecutionRuntime(
        workspace=workspace,
        writer=AlpacaPaperSingleShotWriter(),
    )
    with pytest.raises(PaperOperationalExecutionBlocked, match="writer is disabled"):
        runtime.execute_once(
            credentials=credentials(),
            now=NOW + timedelta(seconds=1),
        )


def test_runtime_requires_existing_regular_control_databases(tmp_path) -> None:
    _, workspace, _ = prepared_and_approved(tmp_path)
    workspace.permit_db_path.unlink()
    runtime = PaperOperationalExecutionRuntime(
        workspace=workspace,
        writer=enabled_writer(FakeWriteTransport()),
    )
    with pytest.raises(PaperOperationalExecutionBlocked, match="permit SQLite database"):
        runtime.execute_once(
            credentials=credentials(),
            now=NOW + timedelta(seconds=1),
        )


def test_account_artifact_round_trip_and_tamper_detection(tmp_path) -> None:
    _, workspace, _ = prepared_and_approved(tmp_path)
    account = _read_account_attestation(workspace.account_attestation_path)
    assert account.credential_reference == credentials().credential_reference

    raw = json.loads(workspace.account_attestation_path.read_text(encoding="utf-8"))
    raw["credentials_persisted"] = True
    workspace.account_attestation_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(Exception, match="not canonical"):
        _read_account_attestation(workspace.account_attestation_path)


def test_health_reader_validates_identity_hash_and_missing_state(tmp_path) -> None:
    _, workspace, _ = prepared_and_approved(tmp_path)
    reader = _ExistingHealthStateReader(workspace.core_db_path)
    strategy = reader.get("coordinator-strategy", HealthEntityKind.STRATEGY)
    assert strategy is not None
    assert strategy.entity_id == "coordinator-strategy"
    assert reader.get("missing-strategy", HealthEntityKind.STRATEGY) is None
    with pytest.raises(PaperOperationalExecutionBlocked, match="entity_id"):
        reader.get(" bad ", HealthEntityKind.STRATEGY)
    with pytest.raises(PaperOperationalExecutionBlocked, match="entity_kind"):
        reader.get("coordinator-strategy", object())  # type: ignore[arg-type]

    conn = sqlite3.connect(workspace.core_db_path)
    try:
        conn.execute(
            "UPDATE health_state_v2 SET state_hash=? WHERE entity_kind=? AND entity_id=?",
            ("0" * 64, HealthEntityKind.STRATEGY.value, "coordinator-strategy"),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperOperationalExecutionBlocked, match="hash mismatch"):
        reader.get("coordinator-strategy", HealthEntityKind.STRATEGY)


def test_portfolio_health_identity_is_derived_from_core_not_caller_input(tmp_path) -> None:
    _, workspace, _ = prepared_and_approved(tmp_path)
    assert _discover_portfolio_health_entity_id(workspace.core_db_path) == "portfolio-r6-canary"


def test_portfolio_health_identity_fails_closed_when_ambiguous(tmp_path) -> None:
    _, workspace, _ = prepared_and_approved(tmp_path)
    conn = sqlite3.connect(workspace.core_db_path)
    try:
        conn.execute(
            """
            INSERT INTO health_state_v2(
                entity_kind, entity_id, state, version, distinct_quarantine_count,
                baseline_fingerprint, policy_fingerprint, last_assessment_fingerprint,
                updated_at, recovery_ack_head, state_hash
            )
            SELECT entity_kind, ?, state, version, distinct_quarantine_count,
                   baseline_fingerprint, policy_fingerprint, last_assessment_fingerprint,
                   updated_at, recovery_ack_head, state_hash
            FROM health_state_v2 WHERE entity_kind=? AND entity_id=?
            """,
            ("portfolio-r6-extra", HealthEntityKind.PORTFOLIO.value, "portfolio-r6-canary"),
        )
        conn.execute(
            """
            INSERT INTO health_bridge_state(
                entity_kind, entity_id, mode, risk_multiplier, health_state_version,
                health_state_fingerprint, baseline_fingerprint, policy_fingerprint,
                bridge_version, updated_at, state_hash
            )
            SELECT entity_kind, ?, mode, risk_multiplier, health_state_version,
                   health_state_fingerprint, baseline_fingerprint, policy_fingerprint,
                   bridge_version, updated_at, state_hash
            FROM health_bridge_state WHERE entity_kind=? AND entity_id=?
            """,
            ("portfolio-r6-extra", HealthEntityKind.PORTFOLIO.value, "portfolio-r6-canary"),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PaperOperationalExecutionBlocked, match="exactly one canonical"):
        _discover_portfolio_health_entity_id(workspace.core_db_path)


def test_read_only_order_lookup_and_legacy_broker_surface_fail_closed(tmp_path) -> None:
    prepared, workspace, _ = prepared_and_approved(tmp_path)
    order = _read_core_order_read_only(
        workspace.core_db_path,
        order_id=prepared.result.package.order_id,
    )
    assert order.status is OrderStatus.VALIDATED
    with pytest.raises(PaperOperationalExecutionBlocked, match="missing or duplicated"):
        _read_core_order_read_only(workspace.core_db_path, order_id="missing-order")
    with pytest.raises(PaperOperationalExecutionBlocked, match="legacy OMS broker"):
        _NoBrokerExecutionSurface().submit()
