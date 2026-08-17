from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import sqlite3

from autotrade.health_bridge import HealthBridgePolicy, SQLiteHealthBridgeStore
from autotrade.persistence import SQLiteRuntime
from autotrade.research.health import (
    HealthAssessment,
    HealthEntityKind,
    HealthPolicy,
    HealthState,
    SQLiteHealthStateStore,
)
from autotrade.risk_state import SQLiteR2SafetyStateStore

from scripts.mac_crypto_execution_health_readiness import (
    EXECUTION_STRATEGY_ID,
    inspect_health_readiness,
)


NOW = datetime(2026, 8, 17, 22, 0, tzinfo=timezone.utc)
PORTFOLIO_ID = "portfolio-r6-paper-main"


def _sha(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _health_policy() -> HealthPolicy:
    return HealthPolicy(
        min_observations=2,
        degraded_mean_loss_fraction=Decimal("0.20"),
        quarantined_mean_loss_fraction=Decimal("0.40"),
        degraded_volatility_ratio=Decimal("1.5"),
        quarantined_volatility_ratio=Decimal("2.0"),
        retire_after_distinct_quarantines=3,
        max_observation_age_seconds=3600,
    )


def _assessment(entity_id: str, kind: HealthEntityKind, policy: HealthPolicy, at: datetime) -> HealthAssessment:
    return HealthAssessment(
        entity_id=entity_id,
        entity_kind=kind,
        baseline_fingerprint=_sha(f"baseline:{kind.value}:{entity_id}"),
        observation_series_fingerprint=_sha(f"observed:{kind.value}:{entity_id}:{at.isoformat()}"),
        policy_fingerprint=policy.fingerprint,
        sample_count=20,
        current_mean_return=Decimal("0.01"),
        current_volatility=Decimal("0.02"),
        mean_loss_fraction=Decimal("0"),
        volatility_ratio=Decimal("1"),
        proposed_state=HealthState.HEALTHY,
        evaluated_at=at,
    )


def _commission(workspace: Path, *, portfolio_ids=(PORTFOLIO_ID,), at=NOW) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    db = workspace / "core.sqlite3"
    runtime = SQLiteRuntime(db)
    SQLiteR2SafetyStateStore(runtime)
    health_store = SQLiteHealthStateStore(db)
    policy = _health_policy()
    health_store.apply_assessment(
        _assessment(EXECUTION_STRATEGY_ID, HealthEntityKind.STRATEGY, policy, at),
        policy,
        now=at,
    )
    for portfolio_id in portfolio_ids:
        health_store.apply_assessment(
            _assessment(portfolio_id, HealthEntityKind.PORTFOLIO, policy, at),
            policy,
            now=at,
        )
    bridge = SQLiteHealthBridgeStore(
        runtime,
        health_reader=health_store,
        policy=HealthBridgePolicy(
            max_state_age_seconds=3600,
            require_strategy_state=True,
            require_portfolio_state=True,
        ),
    )
    bridge.sync_from_health(
        entity_id=EXECUTION_STRATEGY_ID,
        entity_kind=HealthEntityKind.STRATEGY,
        now=at,
    )
    for portfolio_id in portfolio_ids:
        bridge.sync_from_health(
            entity_id=portfolio_id,
            entity_kind=HealthEntityKind.PORTFOLIO,
            now=at,
        )
    return db


def test_missing_core_db_blocks_without_creating_database(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = inspect_health_readiness(workspace_path=workspace, now=NOW)
    assert result["status"] == "HEALTH_R4_EXECUTION_READINESS_BLOCKED"
    assert result["blockers"] == ["CORE_DB_MISSING"]
    assert result["broker_network_used"] is False
    assert result["broker_write_performed"] is False
    assert result["approval_consumed"] is False
    assert not (workspace / "core.sqlite3").exists()


def test_exact_healthy_strategy_and_single_portfolio_are_ready(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    db = _commission(workspace)
    before = db.stat().st_mtime_ns
    result = inspect_health_readiness(workspace_path=workspace, now=NOW + timedelta(seconds=5))
    after = db.stat().st_mtime_ns
    assert result["status"] == "HEALTH_R4_EXECUTION_READINESS_PASS"
    assert result["ready"] is True
    assert result["blockers"] == []
    assert result["strategy"]["health_state"] == "HEALTHY"
    assert result["strategy"]["bridge_mode"] == "NORMAL"
    assert result["portfolio"]["entity_id"] == PORTFOLIO_ID
    assert result["portfolio"]["bridge_risk_multiplier"] == "1"
    assert result["approval_consumed"] is False
    assert result["oms_submitting"] is False
    assert result["lifecycle_unknown"] is False
    assert result["external_post_authorized"] is False
    assert before == after


def test_execution_strategy_health_is_exact_not_any_strategy(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _commission(workspace)
    conn = sqlite3.connect(workspace / "core.sqlite3")
    try:
        conn.execute(
            "UPDATE health_state_v2 SET entity_id='other-strategy' WHERE entity_kind='STRATEGY'"
        )
        conn.execute(
            "UPDATE health_bridge_state SET entity_id='other-strategy' WHERE entity_kind='STRATEGY'"
        )
        conn.commit()
    finally:
        conn.close()
    result = inspect_health_readiness(workspace_path=workspace, now=NOW + timedelta(seconds=1))
    assert "STRATEGY_HEALTH_MISSING" in result["blockers"]


def test_ambiguous_portfolio_health_blocks(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _commission(workspace, portfolio_ids=(PORTFOLIO_ID, "portfolio-second"))
    result = inspect_health_readiness(workspace_path=workspace, now=NOW + timedelta(seconds=1))
    assert result["status"] == "HEALTH_R4_EXECUTION_READINESS_BLOCKED"
    assert "PORTFOLIO_HEALTH_IDENTITY_AMBIGUOUS" in result["blockers"]


def test_stale_authoritative_health_and_bridge_block(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _commission(workspace)
    result = inspect_health_readiness(
        workspace_path=workspace,
        now=NOW + timedelta(seconds=3601),
    )
    assert result["status"] == "HEALTH_R4_EXECUTION_READINESS_BLOCKED"
    assert any("STALE" in blocker for blocker in result["blockers"])


def test_tampered_health_hash_fails_closed(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _commission(workspace)
    conn = sqlite3.connect(workspace / "core.sqlite3")
    try:
        conn.execute(
            "UPDATE health_state_v2 SET state='DEGRADED' WHERE entity_kind='STRATEGY'"
        )
        conn.commit()
    finally:
        conn.close()
    result = inspect_health_readiness(workspace_path=workspace, now=NOW + timedelta(seconds=1))
    assert result["status"] == "HEALTH_R4_EXECUTION_READINESS_BLOCKED"
    assert result["blockers"] == ["HEALTH_INTEGRITY_FAILURE"]
    assert "hash mismatch" in result["reason"]


def test_tampered_bridge_hash_fails_closed(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _commission(workspace)
    conn = sqlite3.connect(workspace / "core.sqlite3")
    try:
        conn.execute(
            "UPDATE health_bridge_state SET risk_multiplier='0.5' WHERE entity_kind='PORTFOLIO'"
        )
        conn.commit()
    finally:
        conn.close()
    result = inspect_health_readiness(workspace_path=workspace, now=NOW + timedelta(seconds=1))
    assert result["status"] == "HEALTH_R4_EXECUTION_READINESS_BLOCKED"
    assert result["blockers"] == ["HEALTH_INTEGRITY_FAILURE"]


def test_missing_bridge_blocks_even_when_authoritative_health_exists(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _commission(workspace)
    conn = sqlite3.connect(workspace / "core.sqlite3")
    try:
        conn.execute("DELETE FROM health_bridge_state WHERE entity_kind='STRATEGY'")
        conn.commit()
    finally:
        conn.close()
    result = inspect_health_readiness(workspace_path=workspace, now=NOW + timedelta(seconds=1))
    assert "STRATEGY_HEALTH_BRIDGE_MISSING" in result["blockers"]
    assert result["external_post_authorized"] is False
