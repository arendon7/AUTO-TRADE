from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_core_provenance import (
    PaperCoreProvenanceConflict,
    PaperCoreProvenanceMissing,
    PaperOperationalCoreProvenanceReader,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.brokers.alpaca_paper_preparation_snapshot import write_preparation_snapshot
from autotrade.domain import OrderStatus, PortfolioSnapshot
from autotrade.health_bridge import (
    HealthBridgeState,
    HealthRiskMode,
    SQLiteHealthBridgeStore,
)
from autotrade.persistence import (
    SQLiteOrderStore,
    SQLitePortfolioStore,
    SQLiteRuntime,
    _order_to_json,
)
from autotrade.research.health import (
    HealthControlState,
    HealthEntityKind,
    HealthState,
    SQLiteHealthStateStore,
)
from test_r6_paper_canary_coordinator import NOW, attestation, decision, market, prepare, stack


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id="r6-provenance-portfolio-001",
        equity=Decimal("100000"),
        gross_exposure=Decimal("0"),
        net_exposure=Decimal("0"),
        daily_pnl=Decimal("0"),
        drawdown=Decimal("0"),
        open_orders=0,
        signed_position_notional_by_symbol={},
        strategy_gross_exposure={},
        strategy_signed_position_notional_by_symbol={},
        reconciliation_ok=True,
        broker_state_known=True,
    )


def setup_provenance(tmp_path):
    coordinator, _, _, submission, permit = stack(tmp_path / "prepare")
    result = prepare(coordinator, submission, permit)
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    workspace.write_account_attestation(attestation())
    workspace.write_prepared_canary(result.package, result.bracket)
    write_preparation_snapshot(
        workspace,
        package=result.package,
        decision=decision(),
        market=market(),
        approval=result.approval,
    )

    runtime = SQLiteRuntime(workspace.core_db_path)
    SQLiteOrderStore(runtime).create_if_absent(result.order)
    SQLitePortfolioStore(runtime).initialize(portfolio(), now=NOW)
    health_store = SQLiteHealthStateStore(workspace.core_db_path)
    SQLiteHealthBridgeStore(runtime, health_reader=health_store)

    health = HealthControlState(
        entity_id=result.order.intent.strategy_id,
        entity_kind=HealthEntityKind.STRATEGY,
        state=HealthState.HEALTHY,
        version=1,
        distinct_quarantine_count=0,
        baseline_fingerprint=h("r6-provenance-baseline"),
        policy_fingerprint=h("r6-provenance-policy"),
        last_assessment_fingerprint=h("r6-provenance-assessment"),
        updated_at=NOW,
    )
    bridge = HealthBridgeState(
        entity_id=health.entity_id,
        entity_kind=health.entity_kind,
        mode=HealthRiskMode.NORMAL,
        risk_multiplier=Decimal("1"),
        health_state_version=health.version,
        health_state_fingerprint=health.fingerprint,
        baseline_fingerprint=health.baseline_fingerprint,
        policy_fingerprint=health.policy_fingerprint,
        bridge_version=1,
        updated_at=NOW,
    )
    conn = runtime.connect()
    try:
        conn.execute(
            """
            INSERT INTO health_state_v2(
                entity_kind,entity_id,state,version,distinct_quarantine_count,
                baseline_fingerprint,policy_fingerprint,last_assessment_fingerprint,
                updated_at,recovery_ack_head,state_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                health.entity_kind.value,
                health.entity_id,
                health.state.value,
                health.version,
                health.distinct_quarantine_count,
                health.baseline_fingerprint,
                health.policy_fingerprint,
                health.last_assessment_fingerprint,
                health.updated_at.isoformat(),
                health.recovery_ack_head,
                health.fingerprint,
            ),
        )
        conn.execute(
            """
            INSERT INTO health_bridge_state(
                entity_kind,entity_id,mode,risk_multiplier,health_state_version,
                health_state_fingerprint,baseline_fingerprint,policy_fingerprint,
                bridge_version,updated_at,state_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                bridge.entity_kind.value,
                bridge.entity_id,
                bridge.mode.value,
                str(bridge.risk_multiplier),
                bridge.health_state_version,
                bridge.health_state_fingerprint,
                bridge.baseline_fingerprint,
                bridge.policy_fingerprint,
                bridge.bridge_version,
                bridge.updated_at.isoformat(),
                bridge.fingerprint,
            ),
        )
    finally:
        conn.close()
    return workspace, result, runtime, health, bridge


def test_provenance_verification_reads_same_core_without_mutating_database_bytes(tmp_path) -> None:
    workspace, result, _, health, bridge = setup_provenance(tmp_path)
    before = sha256(workspace.core_db_path.read_bytes()).hexdigest()
    proof = PaperOperationalCoreProvenanceReader(workspace).verify(now=NOW)
    after = sha256(workspace.core_db_path.read_bytes()).hexdigest()

    assert before == after == proof.core_db_sha256
    assert proof.order_id == result.package.order_id
    assert proof.order_status == OrderStatus.VALIDATED.value
    assert proof.strategy_id == result.order.intent.strategy_id
    assert proof.intent_fingerprint == result.package.intent_fingerprint
    assert proof.risk_decision_fingerprint == result.package.risk_decision_fingerprint
    assert proof.safety_version == result.package.risk_decision_safety_state_version
    assert proof.portfolio_version == 1
    assert proof.strategy_health_fingerprint == health.fingerprint
    assert proof.health_bridge_fingerprint == bridge.fingerprint


def test_provenance_requires_existing_regular_core_database(tmp_path) -> None:
    coordinator, _, _, submission, permit = stack(tmp_path / "prepare")
    result = prepare(coordinator, submission, permit)
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    workspace.write_account_attestation(attestation())
    workspace.write_prepared_canary(result.package, result.bracket)
    write_preparation_snapshot(
        workspace,
        package=result.package,
        decision=decision(),
        market=market(),
        approval=result.approval,
    )
    reader = PaperOperationalCoreProvenanceReader(workspace)
    with pytest.raises(PaperCoreProvenanceMissing, match="does not exist"):
        reader.verify(now=NOW)

    outside = tmp_path / "outside.sqlite3"
    outside.write_bytes(b"not-a-db")
    try:
        workspace.core_db_path.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(PaperCoreProvenanceMissing, match="symlink"):
        reader.verify(now=NOW)


def test_provenance_rejects_order_status_and_identity_tamper(tmp_path) -> None:
    workspace, result, runtime, _, _ = setup_provenance(tmp_path)
    conn = runtime.connect()
    try:
        forged = replace(result.order, status=OrderStatus.SUBMITTING)
        conn.execute(
            "UPDATE orders SET record_json=? WHERE order_id=?",
            (_order_to_json(forged), result.order.order_id),
        )
    finally:
        conn.close()
    with pytest.raises(PaperCoreProvenanceConflict, match="remain VALIDATED"):
        PaperOperationalCoreProvenanceReader(workspace).verify(now=NOW)


def test_provenance_rejects_kill_switch_and_safety_version_change(tmp_path) -> None:
    for field_sql, expected in (
        ("kill_switch_active=1, kill_switch_reason='test'", "kill switch is engaged"),
        ("version=version+1", "Safety version differs"),
    ):
        case = tmp_path / expected.replace(" ", "-")
        workspace, _, runtime, _, _ = setup_provenance(case)
        conn = runtime.connect()
        try:
            conn.execute(f"UPDATE safety_state SET {field_sql} WHERE singleton_id=1")
        finally:
            conn.close()
        with pytest.raises(PaperCoreProvenanceConflict, match=expected):
            PaperOperationalCoreProvenanceReader(workspace).verify(now=NOW)


def test_provenance_rejects_dirty_or_unknown_portfolio(tmp_path) -> None:
    for reconciliation_ok, broker_known, expected in (
        (False, True, "reconciliation is not clean"),
        (True, False, "broker state is unknown"),
    ):
        case = tmp_path / expected.replace(" ", "-")
        workspace, _, runtime, _, _ = setup_provenance(case)
        SQLitePortfolioStore(runtime).set_reconciliation_status(
            reconciliation_ok=reconciliation_ok,
            broker_state_known=broker_known,
            now=NOW,
        )
        with pytest.raises(PaperCoreProvenanceConflict, match=expected):
            PaperOperationalCoreProvenanceReader(workspace).verify(now=NOW)


def test_provenance_rejects_health_or_bridge_hash_tamper(tmp_path) -> None:
    for table, expected in (
        ("health_state_v2", "Health state integrity failed"),
        ("health_bridge_state", "Health Bridge integrity failed"),
    ):
        case = tmp_path / table
        workspace, result, runtime, _, _ = setup_provenance(case)
        conn = runtime.connect()
        try:
            conn.execute(
                f"UPDATE {table} SET state_hash=? WHERE entity_kind=? AND entity_id=?",
                ("f" * 64, HealthEntityKind.STRATEGY.value, result.order.intent.strategy_id),
            )
        finally:
            conn.close()
        with pytest.raises(PaperCoreProvenanceConflict, match=expected):
            PaperOperationalCoreProvenanceReader(workspace).verify(now=NOW)


def test_provenance_rejects_semantically_recomputed_non_normal_bridge(tmp_path) -> None:
    workspace, result, runtime, health, bridge = setup_provenance(tmp_path)
    reduced = replace(
        bridge,
        mode=HealthRiskMode.REDUCED,
        risk_multiplier=Decimal("0.5"),
    )
    conn = runtime.connect()
    try:
        conn.execute(
            """
            UPDATE health_bridge_state
            SET mode=?,risk_multiplier=?,state_hash=?
            WHERE entity_kind=? AND entity_id=?
            """,
            (
                reduced.mode.value,
                str(reduced.risk_multiplier),
                reduced.fingerprint,
                health.entity_kind.value,
                health.entity_id,
            ),
        )
    finally:
        conn.close()
    with pytest.raises(PaperCoreProvenanceConflict, match="does not allow full new exposure"):
        PaperOperationalCoreProvenanceReader(workspace).verify(now=NOW)


def test_provenance_rejects_health_bridge_binding_mismatch_even_with_valid_hash(tmp_path) -> None:
    workspace, result, runtime, health, bridge = setup_provenance(tmp_path)
    mismatched = replace(bridge, health_state_fingerprint=h("different-health"))
    conn = runtime.connect()
    try:
        conn.execute(
            """
            UPDATE health_bridge_state
            SET health_state_fingerprint=?,state_hash=?
            WHERE entity_kind=? AND entity_id=?
            """,
            (
                mismatched.health_state_fingerprint,
                mismatched.fingerprint,
                health.entity_kind.value,
                health.entity_id,
            ),
        )
    finally:
        conn.close()
    with pytest.raises(PaperCoreProvenanceConflict, match="not bound to authoritative Health"):
        PaperOperationalCoreProvenanceReader(workspace).verify(now=NOW)


def test_provenance_rejects_naive_verification_time(tmp_path) -> None:
    workspace, _, _, _, _ = setup_provenance(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        PaperOperationalCoreProvenanceReader(workspace).verify(now=NOW.replace(tzinfo=None))


def test_provenance_reader_exposes_no_execution_surface(tmp_path) -> None:
    workspace, _, _, _, _ = setup_provenance(tmp_path)
    reader = PaperOperationalCoreProvenanceReader(workspace)
    forbidden = {
        "submit",
        "submit_once",
        "stage_external_submission",
        "record_operator_approval",
        "consume",
        "write",
        "send",
        "connect",
    }
    assert not (forbidden & set(dir(reader)))
