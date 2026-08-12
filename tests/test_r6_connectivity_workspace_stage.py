from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json

import pytest

from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.connectivity_workspace_stage import (
    ConnectivityWorkspaceStageConflict,
    ConnectivityWorkspaceStageRejected,
    ConnectivityWorkspaceStagingBridge,
)
from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteEventLedger, SQLiteOrderStore, SQLiteRuntime
from test_r6_connectivity_execution_freshness_binding import bound_workspace


def inside(result):
    return result.binding.issued_at + timedelta(milliseconds=100)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, payload):
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def durable_state(ws, order_id):
    core = SQLiteRuntime(ws.core_db_path)
    order = SQLiteOrderStore(core).get_by_order_id(order_id)
    submission = SQLitePaperSubmissionRegistry(SQLiteRuntime(ws.submission_db_path)).get(order_id)
    return order, submission


def test_workspace_stage_commits_handoff_then_unknown_barrier_without_post(tmp_path, monkeypatch) -> None:
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    bridge = ConnectivityWorkspaceStagingBridge(ws)
    result = bridge.stage(bound_result=bound, now=inside(bound))

    assert result.order.status is OrderStatus.SUBMITTING
    assert result.order.risk_decision_id == bound.final_freshness.fresh_risk_decision.decision_id
    assert result.handoff.execution_freshness_binding_hash == bound.binding.binding_hash
    assert result.handoff.final_freshness_permit_hash == bound.binding.final_freshness_permit_hash
    assert result.submission.status is PaperSubmissionStatus.UNKNOWN
    assert result.submission.attempt_count == 1
    assert result.submission.broker_order_id is None
    assert result.submission.broker_client_order_id is None

    persisted_order, persisted_submission = durable_state(ws, bound.binding.order_id)
    assert persisted_order == result.order
    assert persisted_submission == result.submission

    ledger = SQLiteEventLedger(SQLiteRuntime(ws.core_db_path))
    assert ledger.verify_integrity() is True
    events = [event for event in ledger.all_events() if event.event_id == result.handoff.event_id]
    assert len(events) == 1
    assert events[0].event_type == "CONNECTIVITY_EXTERNAL_HANDOFF_AUTHORIZED"

    artifact = read_json(result.artifact_path)
    assert artifact["environment"] == "PAPER"
    assert artifact["purpose"] == "CONNECTIVITY_CANARY"
    assert artifact["oms_status"] == "SUBMITTING"
    assert artifact["submission_status"] == "UNKNOWN"
    assert artifact["attempt_count"] == 1
    assert artifact["unknown_before_post_committed"] is True
    assert artifact["oms_staging_completed"] is True
    assert artifact["external_post_authorized"] is False
    assert artifact["external_order_submitted"] is False
    assert artifact["strategy_health_required"] is False
    assert artifact["strategy_trading_authorized"] is False
    assert artifact["capital_authority"] == "NONE"
    assert artifact["live_trading"] == "BLOCKED"
    assert artifact["next_action"] == "CONNECTIVITY_ONE_SHOT_EXECUTOR_REQUIRED"


def test_workspace_stage_rejects_expired_binding_without_mutation(tmp_path, monkeypatch) -> None:
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    with pytest.raises(ConnectivityWorkspaceStageRejected, match="expired"):
        ConnectivityWorkspaceStagingBridge(ws).stage(
            bound_result=bound,
            now=bound.binding.expires_at,
        )
    order, submission = durable_state(ws, bound.binding.order_id)
    assert order.status is OrderStatus.VALIDATED
    assert submission.status is PaperSubmissionStatus.PREPARED
    assert submission.attempt_count == 0
    assert not ws.root.joinpath("connectivity_staging.json").exists()


def test_workspace_stage_rejects_core_drift_before_opening_writable_runtime(tmp_path, monkeypatch) -> None:
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    with ws.core_db_path.open("ab") as handle:
        handle.write(b"core-drift")

    with pytest.raises(ConnectivityWorkspaceStageRejected, match="core.sqlite3 changed"):
        ConnectivityWorkspaceStagingBridge(ws).stage(
            bound_result=bound,
            now=inside(bound),
        )
    assert not ws.root.joinpath("connectivity_staging.json").exists()


def test_workspace_stage_rejects_tampered_execution_freshness_artifact(tmp_path, monkeypatch) -> None:
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_execution_freshness_binding.json"
    payload = read_json(path)
    payload["external_post_authorized"] = True
    write_json(path, payload)

    with pytest.raises(ConnectivityWorkspaceStageRejected, match="unsafe or non-canonical"):
        ConnectivityWorkspaceStagingBridge(ws).stage(
            bound_result=bound,
            now=inside(bound),
        )
    order, submission = durable_state(ws, bound.binding.order_id)
    assert order.status is OrderStatus.VALIDATED
    assert submission.status is PaperSubmissionStatus.PREPARED


def test_workspace_stage_rejects_final_freshness_artifact_hash_drift(tmp_path, monkeypatch) -> None:
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_final_freshness.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(ConnectivityWorkspaceStageRejected, match="Final Freshness artifact hash changed"):
        ConnectivityWorkspaceStagingBridge(ws).stage(
            bound_result=bound,
            now=inside(bound),
        )
    order, submission = durable_state(ws, bound.binding.order_id)
    assert order.status is OrderStatus.VALIDATED
    assert submission.status is PaperSubmissionStatus.PREPARED


def test_workspace_stage_rejects_submission_already_unknown_before_oms_handoff(tmp_path, monkeypatch) -> None:
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    registry = SQLitePaperSubmissionRegistry(SQLiteRuntime(ws.submission_db_path))
    registry.mark_submit_attempt_unknown(
        order_id=bound.binding.order_id,
        attempt_id=bound.binding.attempt_id,
        now=inside(bound),
    )

    with pytest.raises(ConnectivityWorkspaceStageRejected, match="PREPARED/0"):
        ConnectivityWorkspaceStagingBridge(ws).stage(
            bound_result=bound,
            now=inside(bound),
        )
    order, submission = durable_state(ws, bound.binding.order_id)
    assert order.status is OrderStatus.VALIDATED
    assert submission.status is PaperSubmissionStatus.UNKNOWN
    assert submission.attempt_count == 1
    assert not ws.root.joinpath("connectivity_staging.json").exists()


def test_workspace_stage_never_overwrites_existing_staging_artifact(tmp_path, monkeypatch) -> None:
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_staging.json"
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ConnectivityWorkspaceStageRejected, match="already exists"):
        ConnectivityWorkspaceStagingBridge(ws).stage(
            bound_result=bound,
            now=inside(bound),
        )
    order, submission = durable_state(ws, bound.binding.order_id)
    assert order.status is OrderStatus.VALIDATED
    assert submission.status is PaperSubmissionStatus.PREPARED


def test_workspace_stage_constructor_and_call_type_guards(tmp_path, monkeypatch) -> None:
    with pytest.raises(TypeError, match="PaperOperationalWorkspace"):
        ConnectivityWorkspaceStagingBridge(object())  # type: ignore[arg-type]

    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    bridge = ConnectivityWorkspaceStagingBridge(ws)
    with pytest.raises(TypeError, match="ConnectivityBoundFinalFreshnessResult"):
        bridge.stage(bound_result=object(), now=inside(bound))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone-aware"):
        bridge.stage(
            bound_result=bound,
            now=inside(bound).replace(tzinfo=None),
        )


def test_workspace_stage_rejects_invalid_json_prerequisite(tmp_path, monkeypatch) -> None:
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_execution_freshness_binding.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ConnectivityWorkspaceStageRejected, match="invalid staging prerequisite artifact"):
        ConnectivityWorkspaceStagingBridge(ws).stage(
            bound_result=bound,
            now=inside(bound),
        )


def test_workspace_stage_rejects_non_object_json_prerequisite(tmp_path, monkeypatch) -> None:
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_execution_freshness_binding.json"
    path.write_text("[]\n", encoding="utf-8")

    with pytest.raises(ConnectivityWorkspaceStageRejected, match="must be a JSON object"):
        ConnectivityWorkspaceStagingBridge(ws).stage(
            bound_result=bound,
            now=inside(bound),
        )


def test_workspace_stage_rejects_symlinked_execution_freshness_artifact(tmp_path, monkeypatch) -> None:
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_execution_freshness_binding.json"
    copy = ws.root / "binding-copy.json"
    copy.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(copy.name)

    with pytest.raises(ConnectivityWorkspaceStageRejected, match="path is not canonical"):
        ConnectivityWorkspaceStagingBridge(ws).stage(
            bound_result=bound,
            now=inside(bound),
        )


def test_workspace_stage_rejects_symlinked_final_freshness_artifact(tmp_path, monkeypatch) -> None:
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    path = ws.root / "connectivity_final_freshness.json"
    copy = ws.root / "freshness-copy.json"
    copy.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(copy.name)

    with pytest.raises(ConnectivityWorkspaceStageRejected, match="Final Freshness artifact path is not canonical"):
        ConnectivityWorkspaceStagingBridge(ws).stage(
            bound_result=bound,
            now=inside(bound),
        )


def test_workspace_stage_rejects_forged_fresh_risk_decision_object(tmp_path, monkeypatch) -> None:
    ws, _, _, bound = bound_workspace(tmp_path, monkeypatch)
    forged_decision = replace(
        bound.final_freshness.fresh_risk_decision,
        reason_detail="forged-after-final-freshness",
    )
    forged_final = replace(
        bound.final_freshness,
        fresh_risk_decision=forged_decision,
    )
    forged_bound = replace(bound, final_freshness=forged_final)

    with pytest.raises(ConnectivityWorkspaceStageConflict, match="fresh RiskDecision binding changed"):
        ConnectivityWorkspaceStagingBridge(ws).stage(
            bound_result=forged_bound,
            now=inside(bound),
        )
