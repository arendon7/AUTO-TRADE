from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from autotrade.connectivity_canary_authority import (
    CONNECTIVITY_CANARY_STRATEGY_ID,
    ConnectivityCanaryAuthority,
    ConnectivityCanaryAuthorityConflict,
    SQLiteConnectivityCanaryAuthorityStore,
)
from autotrade.persistence import SQLiteRuntime


NOW = datetime(2026, 8, 12, 3, 30, tzinfo=timezone.utc)


def authority(*, order_id: str = "order-connectivity-001") -> ConnectivityCanaryAuthority:
    return ConnectivityCanaryAuthority.issue(
        order_id=order_id,
        intent_fingerprint="1" * 64,
        risk_decision_id="risk-connectivity-001",
        risk_decision_fingerprint="2" * 64,
        market_fingerprint="3" * 64,
        safety_state_version=0,
        portfolio_version=1,
        portfolio_snapshot_id="r6-connectivity-baseline:test",
        portfolio_snapshot_hash="4" * 64,
        account_attestation_fingerprint="5" * 64,
        asset_attestation_fingerprint="6" * 64,
        baseline_flat_account_fingerprint="7" * 64,
        market_evidence_fingerprint="8" * 64,
        instrument_rules_fingerprint="9" * 64,
        max_notional=Decimal("10"),
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=15),
    )


def test_connectivity_authority_is_explicitly_non_executable_and_non_strategy() -> None:
    current = authority()
    payload = current.payload()
    assert current.strategy_id == CONNECTIVITY_CANARY_STRATEGY_ID
    assert current.max_quantity == Decimal("1")
    assert current.max_notional == Decimal("10")
    assert payload["strategy_health_required"] is False
    assert payload["strategy_trading_authorized"] is False
    assert payload["external_post_authorized"] is False
    assert payload["live_trading"] == "BLOCKED"
    assert current.is_valid_at(NOW)
    assert not current.is_valid_at(NOW + timedelta(seconds=15))


def test_connectivity_authority_store_is_immutable_and_ledger_bound(tmp_path) -> None:
    runtime = SQLiteRuntime(tmp_path / "core.sqlite3")
    store = SQLiteConnectivityCanaryAuthorityStore(runtime)
    current = authority()
    assert store.issue(current) == current
    assert store.issue(current) == current
    assert store.get_for_order(current.order_id) == current

    conn = runtime.connect()
    try:
        events = conn.execute(
            "SELECT event_type FROM ledger_events WHERE event_id=?",
            (f"connectivity-authority:{current.authority_id}",),
        ).fetchall()
        assert len(events) == 1
        assert events[0]["event_type"] == "CONNECTIVITY_CANARY_AUTHORITY_ISSUED"
    finally:
        conn.close()


def test_connectivity_authority_rejects_different_second_authority(tmp_path) -> None:
    runtime = SQLiteRuntime(tmp_path / "core.sqlite3")
    store = SQLiteConnectivityCanaryAuthorityStore(runtime)
    store.issue(authority())
    with pytest.raises(ConnectivityCanaryAuthorityConflict, match="different connectivity authority"):
        store.issue(authority(order_id="order-connectivity-002"))


def test_connectivity_authority_detects_row_tamper(tmp_path) -> None:
    runtime = SQLiteRuntime(tmp_path / "core.sqlite3")
    store = SQLiteConnectivityCanaryAuthorityStore(runtime)
    current = authority()
    store.issue(current)
    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE connectivity_canary_authority SET authority_hash=? WHERE order_id=?",
            ("a" * 64, current.order_id),
        )
    finally:
        conn.close()
    with pytest.raises(ConnectivityCanaryAuthorityConflict, match="hash mismatch"):
        store.get_for_order(current.order_id)


def test_connectivity_authority_detects_ledger_tamper(tmp_path) -> None:
    runtime = SQLiteRuntime(tmp_path / "core.sqlite3")
    store = SQLiteConnectivityCanaryAuthorityStore(runtime)
    current = authority()
    store.issue(current)
    conn = runtime.connect()
    try:
        row = conn.execute(
            "SELECT payload_json FROM ledger_events WHERE event_id=?",
            (f"connectivity-authority:{current.authority_id}",),
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["strategy_trading_authorized"] = "true"
        conn.execute(
            "UPDATE ledger_events SET payload_json=? WHERE event_id=?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), f"connectivity-authority:{current.authority_id}"),
        )
    finally:
        conn.close()
    with pytest.raises(ConnectivityCanaryAuthorityConflict, match="ledger binding mismatch"):
        store.get_for_order(current.order_id)
