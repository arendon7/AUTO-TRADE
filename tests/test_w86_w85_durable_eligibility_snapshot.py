from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import sqlite3

import pytest

import autotrade.paper_candidate_admission_lifecycle as lifecycle
import autotrade.paper_candidate_eligibility_final as eligibility_final
import autotrade.paper_runtime_readiness_source as source_v1
import autotrade.paper_runtime_readiness_source_snapshot as snapshot
from autotrade.persistence import SQLiteRuntime
from test_w86_w85_durable_eligibility_source import _active_source_bundle


def _enable_wal(path) -> None:
    conn = SQLiteRuntime(path).connect()
    try:
        row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        assert row is not None
        assert str(row[0]).lower() == "wal"
    finally:
        conn.close()


def test_w86_v2_atomic_snapshot_reproves_active_w85_without_authority(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, _, eligibility, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    observed = reproved_at + timedelta(seconds=1)
    monkeypatch.setattr(snapshot, "_now_utc", lambda: observed)

    proof = snapshot.W85DurableEligibilitySnapshotReader(bundle["core"]).verify_current(
        proof_id="w86-v2-active",
        eligibility=eligibility,
        final_verification=verified,
    )

    assert proof.contract_version == snapshot.W85_DURABLE_ELIGIBILITY_SNAPSHOT_VERSION
    assert proof.admission_id == admitted.admission_id
    assert proof.admission_hash == admitted.admission_hash
    assert proof.policy_hash == admitted.policy_hash
    assert proof.current_state is lifecycle.PaperCandidateEligibilityState.ACTIVE
    assert proof.candidate_currently_eligible is True
    assert proof.lifecycle_events_count == 0
    assert proof.lifecycle_head_hash == lifecycle.ZERO_EVENT_HASH
    assert proof.sqlite_data_version >= 0
    assert proof.sqlite_read_only is True
    assert proof.sqlite_snapshot_consistent is True
    assert proof.concurrent_durable_change_detected is False
    assert proof.durable_admission_verified is True
    assert proof.durable_lifecycle_verified is True
    assert proof.paper_execution_authorized is False
    assert proof.external_execution_authorized is False
    assert proof.runtime_execution_authorized is False
    assert proof.capital_authority == "NONE"
    assert proof.live_trading == "BLOCKED"
    assert proof.to_dict()["proof_hash"] == proof.proof_hash


def test_w86_v2_detects_suspend_committed_during_read_snapshot(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, registry, eligibility, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    _enable_wal(bundle["core"])
    monkeypatch.setattr(snapshot, "_now_utc", lambda: reproved_at + timedelta(seconds=2))
    monkeypatch.setattr(lifecycle, "_now_utc", lambda: reproved_at + timedelta(seconds=1))

    original = source_v1._read_and_validate_lifecycle
    fired = False

    def interleaved(conn, receipt):
        nonlocal fired
        if not fired:
            fired = True
            registry.append(
                event_id="w86-v2-concurrent-suspend",
                admission_receipt=admitted,
                action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
                reason_code="CONCURRENT_RISK_REVIEW",
            )
        return original(conn, receipt)

    monkeypatch.setattr(source_v1, "_read_and_validate_lifecycle", interleaved)

    with pytest.raises(
        snapshot.PaperRuntimeReadinessSnapshotIntegrityError,
        match="changed during W86 source snapshot",
    ):
        snapshot.W85DurableEligibilitySnapshotReader(bundle["core"]).verify_current(
            proof_id="w86-v2-race",
            eligibility=eligibility,
            final_verification=verified,
        )
    assert fired is True


def test_w86_v2_post_snapshot_head_check_catches_change_even_if_data_version_is_masked(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, registry, eligibility, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    _enable_wal(bundle["core"])
    monkeypatch.setattr(snapshot, "_now_utc", lambda: reproved_at + timedelta(seconds=2))
    monkeypatch.setattr(lifecycle, "_now_utc", lambda: reproved_at + timedelta(seconds=1))
    monkeypatch.setattr(snapshot, "_data_version", lambda conn: 7)

    original = source_v1._read_and_validate_lifecycle
    fired = False

    def interleaved(conn, receipt):
        nonlocal fired
        if not fired:
            fired = True
            registry.append(
                event_id="w86-v2-head-suspend",
                admission_receipt=admitted,
                action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
                reason_code="HEAD_CHANGED",
            )
        return original(conn, receipt)

    monkeypatch.setattr(source_v1, "_read_and_validate_lifecycle", interleaved)
    with pytest.raises(
        snapshot.PaperRuntimeReadinessSnapshotIntegrityError,
        match="changed during W86 source snapshot",
    ):
        snapshot.W85DurableEligibilitySnapshotReader(bundle["core"]).verify_current(
            proof_id="w86-v2-head-race",
            eligibility=eligibility,
            final_verification=verified,
        )
    assert fired is True


def test_w86_v2_reproves_current_suspended_truth_without_execution_authority(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, registry, _, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    suspend_at = reproved_at + timedelta(seconds=1)
    monkeypatch.setattr(lifecycle, "_now_utc", lambda: suspend_at)
    event = registry.append(
        event_id="w86-v2-suspended",
        admission_receipt=admitted,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="RUNTIME_REVIEW",
    )
    eligibility_at = suspend_at + timedelta(seconds=1)
    monkeypatch.setattr(eligibility_final, "_now_utc", lambda: eligibility_at)
    suspended = eligibility_final.project_final_paper_candidate_eligibility(
        projection_id="w86-v2-suspended-eligibility",
        final_verification=verified,
        admission_receipt=admitted,
        lifecycle_registry=registry,
    )
    monkeypatch.setattr(snapshot, "_now_utc", lambda: eligibility_at + timedelta(seconds=1))

    proof = snapshot.W85DurableEligibilitySnapshotReader(bundle["core"]).verify_current(
        proof_id="w86-v2-suspended-proof",
        eligibility=suspended,
        final_verification=verified,
    )
    assert proof.current_state is lifecycle.PaperCandidateEligibilityState.SUSPENDED
    assert proof.lifecycle_head_hash == event.event_hash
    assert proof.lifecycle_events_count == 1
    assert proof.candidate_currently_eligible is False
    assert proof.runtime_execution_authorized is False
    assert proof.capital_authority == "NONE"


def test_w86_v2_rejects_stale_active_claim_after_preexisting_suspend(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, admitted, verified, registry, eligibility, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    monkeypatch.setattr(lifecycle, "_now_utc", lambda: reproved_at + timedelta(seconds=1))
    registry.append(
        event_id="w86-v2-preexisting-suspend",
        admission_receipt=admitted,
        action=lifecycle.PaperCandidateLifecycleAction.SUSPEND,
        reason_code="PREEXISTING_RISK_REVIEW",
    )
    monkeypatch.setattr(snapshot, "_now_utc", lambda: reproved_at + timedelta(seconds=2))

    with pytest.raises(
        snapshot.PaperRuntimeReadinessSnapshotIntegrityError,
        match="atomic V2 integrity validation",
    ):
        snapshot.W85DurableEligibilitySnapshotReader(bundle["core"]).verify_current(
            proof_id="w86-v2-stale-active",
            eligibility=eligibility,
            final_verification=verified,
        )


def test_w86_v2_connection_is_query_only_and_plain_read_transaction_allowed(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, _, _, _, _, _ = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    reader = snapshot.W85DurableEligibilitySnapshotReader(bundle["core"])
    conn = reader._connect_read_only()
    try:
        assert int(conn.execute("PRAGMA query_only").fetchone()[0]) == 1
        conn.execute("BEGIN")
        assert conn.in_transaction is True
        conn.execute("SELECT 1").fetchone()
        conn.execute("COMMIT")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE forbidden_w86_write(id INTEGER)")
    finally:
        conn.close()


def test_w86_v2_reader_rejects_missing_and_symlinked_core(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(
        snapshot.PaperRuntimeReadinessSnapshotIntegrityError,
        match="existing authoritative core",
    ):
        snapshot.W85DurableEligibilitySnapshotReader(missing)
    assert not missing.exists()

    real = tmp_path / "real.sqlite3"
    real.write_bytes(b"")
    link = tmp_path / "core-link.sqlite3"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink unavailable on this platform")
    with pytest.raises(
        snapshot.PaperRuntimeReadinessSnapshotIntegrityError,
        match="symlinked",
    ):
        snapshot.W85DurableEligibilitySnapshotReader(link)


@pytest.mark.parametrize(
    "changes",
    (
        {"paper_execution_authorized": True},
        {"external_execution_authorized": True},
        {"runtime_execution_authorized": True},
        {"capital_authority": "PAPER"},
        {"live_trading": "ENABLED"},
        {"sqlite_read_only": False},
        {"sqlite_snapshot_consistent": False},
        {"concurrent_durable_change_detected": True},
        {"durable_admission_verified": False},
        {"durable_lifecycle_verified": False},
        {"sqlite_data_version": -1},
    ),
)
def test_w86_v2_proof_rejects_authority_snapshot_or_truth_downgrade(
    tmp_path,
    monkeypatch,
    limits,
    market,
    empty_portfolio,
    market_buy_intent,
    changes,
):
    bundle, _, verified, _, eligibility, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    monkeypatch.setattr(snapshot, "_now_utc", lambda: reproved_at + timedelta(seconds=1))
    proof = snapshot.W85DurableEligibilitySnapshotReader(bundle["core"]).verify_current(
        proof_id="w86-v2-proof-guard",
        eligibility=eligibility,
        final_verification=verified,
    )
    with pytest.raises(snapshot.PaperRuntimeReadinessSnapshotIntegrityError):
        replace(proof, **changes)


def test_w86_v2_proof_rejects_hash_tamper(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, _, verified, _, eligibility, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    monkeypatch.setattr(snapshot, "_now_utc", lambda: reproved_at + timedelta(seconds=1))
    proof = snapshot.W85DurableEligibilitySnapshotReader(bundle["core"]).verify_current(
        proof_id="w86-v2-hash",
        eligibility=eligibility,
        final_verification=verified,
    )
    with pytest.raises(
        snapshot.PaperRuntimeReadinessSnapshotIntegrityError,
        match="hash mismatch",
    ):
        replace(proof, proof_hash="f" * 64)


def test_w86_v2_reader_type_guards_and_future_clock(
    tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
):
    bundle, _, verified, _, eligibility, reproved_at = _active_source_bundle(
        tmp_path, monkeypatch, limits, market, empty_portfolio, market_buy_intent
    )
    reader = snapshot.W85DurableEligibilitySnapshotReader(bundle["core"])
    with pytest.raises(TypeError, match="eligibility"):
        reader.verify_current(
            proof_id="w86-v2-type-eligibility",
            eligibility=object(),
            final_verification=verified,
        )
    with pytest.raises(TypeError, match="final_verification"):
        reader.verify_current(
            proof_id="w86-v2-type-final",
            eligibility=eligibility,
            final_verification=object(),
        )
    monkeypatch.setattr(snapshot, "_now_utc", lambda: eligibility.observed_at - timedelta(seconds=1))
    with pytest.raises(
        snapshot.PaperRuntimeReadinessSnapshotIntegrityError,
        match="predates supplied",
    ):
        reader.verify_current(
            proof_id="w86-v2-future-clock",
            eligibility=eligibility,
            final_verification=verified,
        )


def test_w86_v2_data_version_guard_rejects_invalid_value(monkeypatch):
    class FakeConnection:
        def execute(self, statement):
            assert statement == "PRAGMA data_version"
            return self

        def fetchone(self):
            return (-1,)

    with pytest.raises(
        snapshot.PaperRuntimeReadinessSnapshotIntegrityError,
        match="data_version invalid",
    ):
        snapshot._data_version(FakeConnection())
