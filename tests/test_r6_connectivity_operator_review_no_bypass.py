from __future__ import annotations

from datetime import timedelta

import pytest

import autotrade.connectivity_execution_freshness_binding as cefb
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.brokers.alpaca_paper_writer import AlpacaPaperWriterConfig
from autotrade.connectivity_execution_freshness_binding import ConnectivityBoundFinalFreshnessGuard
from autotrade.connectivity_workspace_post import ConnectivityWorkspaceOneShotExecutor
from autotrade.connectivity_workspace_stage import (
    ConnectivityWorkspaceStageRejected,
    ConnectivityWorkspaceStagingBridge,
)
from autotrade.domain import OrderStatus
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime
from test_r6_connectivity_candidate import CREDS, NOW
from test_r6_connectivity_execution_intent import issue, prepared
from test_r6_connectivity_final_freshness import clock_from, guard
from test_r6_connectivity_workspace_post import Clock, FakeWriteTransport


def _unreviewed_bound_workspace(tmp_path, monkeypatch):
    ws, _, _, _, intent_bridge, context = prepared(tmp_path)
    issue(intent_bridge, context)
    monkeypatch.setattr(cefb, "_utc_now", lambda: NOW + timedelta(seconds=22))
    result = ConnectivityBoundFinalFreshnessGuard(
        ws,
        final_guard=guard(ws, clock=clock_from(offset=22)),
    ).acquire(credentials=CREDS)
    assert not (ws.root / "connectivity_operator_review_receipt.json").exists()
    assert not (ws.root / "connectivity_execution_review_binding.json").exists()
    assert not (ws.root / "connectivity_review_final_freshness_binding.json").exists()
    return ws, result


def _durable_state(ws, order_id):
    order = SQLiteOrderStore(SQLiteRuntime(ws.core_db_path)).get_by_order_id(order_id)
    submission = SQLitePaperSubmissionRegistry(SQLiteRuntime(ws.submission_db_path)).get(order_id)
    return order, submission


def test_unreviewed_bound_final_freshness_cannot_mutate_oms_or_submission(tmp_path, monkeypatch):
    ws, bound = _unreviewed_bound_workspace(tmp_path, monkeypatch)
    now = bound.binding.issued_at + timedelta(milliseconds=50)

    with pytest.raises(
        ConnectivityWorkspaceStageRejected,
        match="reviewed human intent/freshness chain is missing or invalid",
    ):
        ConnectivityWorkspaceStagingBridge(ws).stage(
            bound_result=bound,
            now=now,
        )

    order, submission = _durable_state(ws, bound.binding.order_id)
    assert order is not None and order.status is OrderStatus.VALIDATED
    assert submission.status is PaperSubmissionStatus.PREPARED
    assert submission.attempt_count == 0
    assert submission.broker_order_id is None
    assert not (ws.root / "connectivity_staging.json").exists()


def test_unreviewed_bound_final_freshness_cannot_reach_one_shot_transport(tmp_path, monkeypatch):
    ws, bound = _unreviewed_bound_workspace(tmp_path, monkeypatch)
    first = bound.binding.issued_at + timedelta(milliseconds=50)
    second = bound.binding.issued_at + timedelta(milliseconds=100)
    transport = FakeWriteTransport()
    executor = ConnectivityWorkspaceOneShotExecutor(
        workspace=ws,
        config=AlpacaPaperWriterConfig(enabled=True),
        transport=transport,
        clock=Clock(first, second),
    )

    with pytest.raises(
        ConnectivityWorkspaceStageRejected,
        match="reviewed human intent/freshness chain is missing or invalid",
    ):
        executor.execute_once(credentials=CREDS, bound_result=bound)

    assert transport.requests == []
    order, submission = _durable_state(ws, bound.binding.order_id)
    assert order is not None and order.status is OrderStatus.VALIDATED
    assert submission.status is PaperSubmissionStatus.PREPARED
    assert submission.attempt_count == 0
    assert not (ws.root / "connectivity_staging.json").exists()
    assert not (ws.root / "connectivity_post_observation.json").exists()
    assert not (ws.root / "connectivity_post_ambiguity.json").exists()
