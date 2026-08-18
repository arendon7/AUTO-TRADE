from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from autotrade.persistence import SQLiteRuntime
from autotrade.research.health import SQLiteHealthStateStore


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "mac_crypto_health_commissioning.py"
SPEC = importlib.util.spec_from_file_location("mac_crypto_health_commissioning_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
commissioning = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = commissioning
SPEC.loader.exec_module(commissioning)


NOW = datetime(2026, 8, 17, 23, 0, tzinfo=timezone.utc)


def _tables(db: Path) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        conn.close()


def test_missing_core_commissions_schema_but_creates_no_health_evidence(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = commissioning.commission_health_core(workspace_path=workspace, now=NOW)
    core = workspace / "core.sqlite3"
    manifest = workspace / commissioning.MANIFEST_NAME

    assert result["status"] == "HEALTH_R4_CORE_COMMISSIONED_EVIDENCE_REQUIRED"
    assert core.is_file()
    assert manifest.is_file()
    assert {"health_state_v2", "health_bridge_state", "safety_state", "ledger_events"} <= _tables(core)
    assert result["kill_switch_active"] is True
    assert result["kill_switch_reason"] == commissioning.COMMISSIONING_KILL_REASON
    assert result["health_state_rows_created"] == 0
    assert result["health_bridge_rows_created"] == 0
    assert result["fabricated_health"] is False
    assert set(result["readiness_blockers"]) == {
        "STRATEGY_HEALTH_MISSING",
        "PORTFOLIO_HEALTH_MISSING",
    }
    assert result["broker_network_used"] is False
    assert result["credentials_read"] is False
    assert result["broker_write_performed"] is False
    assert result["external_post_authorized"] is False
    assert result["approval_consumed"] is False
    assert result["oms_submitting"] is False
    assert result["lifecycle_unknown"] is False
    assert result["capital_authority"] == "NONE"
    assert result["live_trading"] == "BLOCKED"

    conn = sqlite3.connect(core)
    try:
        assert conn.execute("SELECT COUNT(*) FROM health_state_v2").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM health_bridge_state").fetchone()[0] == 0
        kill = conn.execute(
            "SELECT kill_switch_active,kill_switch_reason FROM safety_state WHERE singleton_id=1"
        ).fetchone()
        assert kill == (1, commissioning.COMMISSIONING_KILL_REASON)
        event_count = conn.execute(
            "SELECT COUNT(*) FROM ledger_events WHERE event_id=?",
            (commissioning.COMMISSIONING_EVENT_ID,),
        ).fetchone()[0]
        assert event_count == 1
    finally:
        conn.close()


def test_commissioning_is_idempotent_and_does_not_reopen_kill_switch(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = commissioning.commission_health_core(workspace_path=workspace, now=NOW)
    second = commissioning.commission_health_core(workspace_path=workspace, now=NOW)
    assert second["manifest_hash"] == first["manifest_hash"]
    assert second["safety_state_version"] == first["safety_state_version"]
    core = workspace / "core.sqlite3"
    conn = sqlite3.connect(core)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM ledger_events WHERE event_id=?",
            (commissioning.COMMISSIONING_EVENT_ID,),
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM health_state_v2").fetchone()[0] == 0
    finally:
        conn.close()


def test_commissioning_manifest_is_hash_bound_and_tamper_fails_closed(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    commissioning.commission_health_core(workspace_path=workspace, now=NOW)
    manifest = workspace / commissioning.MANIFEST_NAME
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["fabricated_health"] = True
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(commissioning.CryptoHealthCommissioningError, match="hash mismatch"):
        commissioning.commission_health_core(workspace_path=workspace, now=NOW)


def test_commissioning_refuses_preexisting_authoritative_health_rows(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    core = workspace / "core.sqlite3"
    SQLiteRuntime(core)
    SQLiteHealthStateStore(core)
    conn = sqlite3.connect(core)
    try:
        conn.execute(
            """
            INSERT INTO health_state_v2(
                entity_kind,entity_id,state,version,distinct_quarantine_count,
                baseline_fingerprint,policy_fingerprint,last_assessment_fingerprint,
                updated_at,recovery_ack_head,state_hash
            ) VALUES('STRATEGY','existing','HEALTHY',1,0,?,?,?,?,?,'GENESIS',?)
            """,
            ("a" * 64, "b" * 64, "c" * 64, NOW.isoformat(), "d" * 64),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(commissioning.CryptoHealthCommissioningError, match="already exists"):
        commissioning.commission_health_core(workspace_path=workspace, now=NOW)


def test_commissioning_refuses_symlinked_core(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = tmp_path / "other.sqlite3"
    target.write_bytes(b"")
    (workspace / "core.sqlite3").symlink_to(target)
    with pytest.raises(commissioning.CryptoHealthCommissioningError, match="safe regular file"):
        commissioning.commission_health_core(workspace_path=workspace, now=NOW)
