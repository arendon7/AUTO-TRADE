from __future__ import annotations

from datetime import timedelta
import json
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_connectivity_candidate import PaperConnectivityCandidateBuilder
from autotrade.brokers.alpaca_paper_connectivity_prepare import CONNECTIVITY_PREP_ARTIFACT, PaperConnectivityPreparationBridge, PaperConnectivityPreparationRejected
from autotrade.brokers.alpaca_paper_canary_permit import PaperCanaryPermitStatus, SQLitePaperCanaryPermitRegistry
from autotrade.brokers.alpaca_paper_submission import PaperSubmissionStatus, SQLitePaperSubmissionRegistry
from autotrade.connectivity_preparation_binding import SQLiteConnectivityPreparationBindingStore
from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteEventLedger, SQLiteOrderStore, SQLiteRuntime
from test_r6_connectivity_candidate import NOW, workspace


def built_workspace(tmp_path):
    ws = workspace(tmp_path)
    candidate = PaperConnectivityCandidateBuilder(ws).build(now=NOW)
    return ws, candidate


def test_connectivity_preparation_happy_path_stops_before_operator_and_post(tmp_path) -> None:
    ws, candidate = built_workspace(tmp_path)
    prepared = PaperConnectivityPreparationBridge(ws).prepare(now=NOW + timedelta(seconds=1))
    assert prepared.artifact_path == ws.root / CONNECTIVITY_PREP_ARTIFACT
    assert prepared.artifact_path.is_file()
    assert not ws.prepared_package_path.exists()
    assert not ws.expected_bracket_path.exists()
    assert not ws.operator_context_path.exists()
    assert not ws.manifest_path.exists()
    assert not ws.operator_db_path.exists()
    order = SQLiteOrderStore(SQLiteRuntime(ws.core_db_path)).get_by_order_id(prepared.order_id)
    assert order is not None and order.status is OrderStatus.VALIDATED
    submission = SQLitePaperSubmissionRegistry(SQLiteRuntime(ws.submission_db_path)).get(prepared.order_id)
    assert submission.status is PaperSubmissionStatus.PREPARED
    assert submission.attempt_count == 0 and submission.broker_order_id is None
    permit_states = SQLitePaperCanaryPermitRegistry(SQLiteRuntime(ws.permit_db_path)).list_states()
    assert len(permit_states) == 1 and permit_states[0].status is PaperCanaryPermitStatus.ISSUED
    assert permit_states[0].attempt_id is None
    binding = SQLiteConnectivityPreparationBindingStore(SQLiteRuntime(ws.core_db_path)).get_for_order(prepared.order_id)
    assert binding is not None
    assert binding.connectivity_authority_id == candidate.authority_id
    assert binding.standard_package_hash == prepared.standard_package_hash
    assert SQLiteEventLedger(SQLiteRuntime(ws.core_db_path)).verify_integrity() is True
    conn = sqlite3.connect(ws.core_db_path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "health_state_v2" not in tables
    assert "health_bridge_state" not in tables
    payload = json.loads(prepared.artifact_path.read_text(encoding="utf-8"))
    assert payload["purpose"] == "CONNECTIVITY_CANARY"
    assert payload["expected_bracket"]["order_class"] == "bracket"
    assert payload["expected_bracket"]["quantity"] == "1"
    assert payload["expected_bracket"]["take_profit"]["limit_price"] == "5.12"
    assert payload["expected_bracket"]["stop_loss"]["stop_price"] == "4.95"
    for key in ("normal_prepared_package_created", "normal_expected_bracket_artifact_created", "operator_context_created", "normal_manifest_created", "strategy_health_required", "strategy_health_created", "strategy_trading_authorized", "operator_authority_created", "external_post_authorized", "external_order_submitted"):
        assert payload[key] is False
    assert payload["capital_authority"] == "NONE"
    assert payload["profitability_claim"] is False
    assert payload["live_trading"] == "BLOCKED"
    assert payload["next_action"] == "CONNECTIVITY_OPERATOR_BRIDGE_REQUIRED"


def test_connectivity_preparation_rejects_candidate_tamper_before_submission_state(tmp_path) -> None:
    ws, _ = built_workspace(tmp_path)
    path = ws.root / "connectivity_candidate.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["external_post_authorized"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PaperConnectivityPreparationRejected, match="external_post_authorized"):
        PaperConnectivityPreparationBridge(ws).prepare(now=NOW + timedelta(seconds=1))
    assert not ws.submission_db_path.exists() and not ws.permit_db_path.exists()


def test_connectivity_preparation_rejects_core_drift_before_control_db_creation(tmp_path) -> None:
    ws, _ = built_workspace(tmp_path)
    conn = sqlite3.connect(ws.core_db_path)
    try:
        conn.execute("CREATE TABLE unauthorized_drift(x INTEGER)")
    finally:
        conn.close()
    with pytest.raises(PaperConnectivityPreparationRejected, match="core.sqlite3 changed"):
        PaperConnectivityPreparationBridge(ws).prepare(now=NOW + timedelta(seconds=1))
    assert not ws.submission_db_path.exists() and not ws.permit_db_path.exists()


def test_connectivity_preparation_rejects_expired_authority(tmp_path) -> None:
    ws, _ = built_workspace(tmp_path)
    with pytest.raises(PaperConnectivityPreparationRejected, match="authority expired"):
        PaperConnectivityPreparationBridge(ws).prepare(now=NOW + timedelta(seconds=16))


def test_connectivity_preparation_rejects_stale_market_without_widening_window(tmp_path) -> None:
    ws, _ = built_workspace(tmp_path)
    with pytest.raises(PaperConnectivityPreparationRejected, match="market evidence is stale"):
        PaperConnectivityPreparationBridge(ws).prepare(now=NOW + timedelta(seconds=6))


def test_connectivity_preparation_refuses_normal_operator_artifacts(tmp_path) -> None:
    ws, _ = built_workspace(tmp_path)
    ws.operator_context_path.write_text("{}", encoding="utf-8")
    with pytest.raises(PaperConnectivityPreparationRejected, match="normal operator artifacts are forbidden"):
        PaperConnectivityPreparationBridge(ws).prepare(now=NOW + timedelta(seconds=1))
