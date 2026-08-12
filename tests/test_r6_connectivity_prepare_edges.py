from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import json

import pytest

from autotrade.brokers.alpaca_paper_connectivity_candidate import PaperConnectivityCandidateBuilder
from autotrade.brokers.alpaca_paper_connectivity_prepare import (
    CONNECTIVITY_PREP_ARTIFACT,
    PaperConnectivityPreparationBridge,
    PaperConnectivityPreparationRejected,
    _NoBrokerSurface,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.connectivity_preparation_binding import SQLiteConnectivityPreparationBindingStore
from autotrade.persistence import SQLiteRuntime
from test_r6_connectivity_candidate import NOW, workspace


def test_connectivity_preparation_requires_workspace_type() -> None:
    with pytest.raises(TypeError, match="PaperOperationalWorkspace"):
        PaperConnectivityPreparationBridge(object())  # type: ignore[arg-type]


def test_connectivity_preparation_requires_timezone_aware_now(tmp_path) -> None:
    ws = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    with pytest.raises(ValueError, match="timezone-aware"):
        PaperConnectivityPreparationBridge(ws).prepare(now=datetime(2026, 8, 12, 1, 2, 3))


def test_connectivity_preparation_requires_candidate_artifact(tmp_path) -> None:
    ws = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    with pytest.raises(PaperConnectivityPreparationRejected, match="connectivity_candidate.json"):
        PaperConnectivityPreparationBridge(ws).prepare(now=NOW)


def test_connectivity_preparation_rejects_existing_preparation_artifact(tmp_path) -> None:
    ws = workspace(tmp_path)
    PaperConnectivityCandidateBuilder(ws).build(now=NOW)
    (ws.root / CONNECTIVITY_PREP_ARTIFACT).write_text("{}", encoding="utf-8")
    with pytest.raises(PaperConnectivityPreparationRejected, match="already exists"):
        PaperConnectivityPreparationBridge(ws).prepare(now=NOW + timedelta(seconds=1))


def test_connectivity_preparation_rejects_missing_candidate_core(tmp_path) -> None:
    ws = workspace(tmp_path)
    PaperConnectivityCandidateBuilder(ws).build(now=NOW)
    ws.core_db_path.unlink()
    with pytest.raises(PaperConnectivityPreparationRejected, match="core.sqlite3 is missing"):
        PaperConnectivityPreparationBridge(ws).prepare(now=NOW + timedelta(seconds=1))


def test_connectivity_preparation_rejects_candidate_hash_tamper(tmp_path) -> None:
    ws = workspace(tmp_path)
    PaperConnectivityCandidateBuilder(ws).build(now=NOW)
    candidate_path = ws.root / "connectivity_candidate.json"
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    payload["candidate_hash"] = "0" * 64
    candidate_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    with pytest.raises(PaperConnectivityPreparationRejected, match="candidate hash mismatch"):
        PaperConnectivityPreparationBridge(ws).prepare(now=NOW + timedelta(seconds=1))


def test_connectivity_preparation_has_no_broker_surface() -> None:
    with pytest.raises(PaperConnectivityPreparationRejected, match="no broker submission surface"):
        _NoBrokerSurface().submit(symbol="FIVE")


def _binding(tmp_path):
    ws = workspace(tmp_path)
    PaperConnectivityCandidateBuilder(ws).build(now=NOW)
    prepared = PaperConnectivityPreparationBridge(ws).prepare(now=NOW + timedelta(seconds=1))
    binding = SQLiteConnectivityPreparationBindingStore(
        SQLiteRuntime(ws.core_db_path)
    ).get_for_order(prepared.order_id)
    assert binding is not None
    return binding


def test_connectivity_binding_rejects_invalid_hash_field(tmp_path) -> None:
    binding = _binding(tmp_path)
    with pytest.raises(ValueError, match="binding_hash"):
        replace(binding, binding_hash="not-a-hash")


def test_connectivity_binding_rejects_noncanonical_order_id(tmp_path) -> None:
    binding = _binding(tmp_path)
    with pytest.raises(ValueError, match="order_id"):
        replace(binding, order_id=" bad-order ")


def test_connectivity_binding_rejects_naive_prepared_at(tmp_path) -> None:
    binding = _binding(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(binding, prepared_at=binding.prepared_at.replace(tzinfo=None))
