from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

import autotrade.strategy_lab_promotion as promotion
from autotrade.strategy_lab_read_model import (
    StrategyLabPromotionReadModel,
    StrategyLabReadModelIntegrityError,
    StrategyLabReadModelMissing,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
H = "a" * 64
T = "b" * 64


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _thresholds():
    return promotion.build_strategy_promotion_threshold_policy(
        threshold_policy_id="thresholds-001",
        development_campaign_id="development-001",
        holdout_campaign_id="holdout-001",
        holdout_trial_id="holdout-trial-001",
        max_holm_adjusted_p=Decimal("0.05"),
        min_holdout_net_return=Decimal("0.01"),
        max_holdout_drawdown=Decimal("0.10"),
        min_holdout_fills=5,
        min_execution_fill_ratio=Decimal("0.50"),
        max_execution_adverse_slippage_bps=Decimal("8"),
    )


def _candidate(thresholds=None, *, strategy_version: str = "v1"):
    thresholds = thresholds or _thresholds()
    values = {
        "policy_id": "promotion-001",
        "threshold_policy_id": thresholds.threshold_policy_id,
        "threshold_policy_hash": thresholds.threshold_policy_hash,
        "development_campaign_id": thresholds.development_campaign_id,
        "holdout_campaign_id": thresholds.holdout_campaign_id,
        "holdout_trial_id": thresholds.holdout_trial_id,
        "selected_trial_id": "development-trial-001",
        "selected_trial_fingerprint": H,
        "selected_strategy_id": "strategy-a",
        "selected_strategy_version": strategy_version,
        "tournament_fingerprint": T,
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    return promotion.StrategyPromotionPolicy(
        **values,
        policy_hash=promotion._hash(promotion._policy_payload_from_values(values)),
    )


def _schema(path: Path, *, thresholds: bool = True, candidates: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    if thresholds:
        conn.execute(
            """
            CREATE TABLE strategy_promotion_threshold_policies (
                threshold_policy_id TEXT PRIMARY KEY,
                threshold_policy_hash TEXT NOT NULL UNIQUE,
                development_campaign_id TEXT NOT NULL UNIQUE,
                holdout_campaign_id TEXT NOT NULL UNIQUE,
                registered_at TEXT NOT NULL,
                policy_json TEXT NOT NULL
            )
            """
        )
    if candidates:
        conn.execute(
            """
            CREATE TABLE strategy_promotion_policies (
                policy_id TEXT PRIMARY KEY,
                policy_hash TEXT NOT NULL UNIQUE,
                threshold_policy_id TEXT NOT NULL UNIQUE,
                threshold_policy_hash TEXT NOT NULL,
                development_campaign_id TEXT NOT NULL,
                holdout_campaign_id TEXT NOT NULL UNIQUE,
                registered_at TEXT NOT NULL,
                policy_json TEXT NOT NULL
            )
            """
        )
    return conn


def _insert_threshold(conn: sqlite3.Connection, thresholds=None, *, registered_at: str | None = None) -> None:
    thresholds = thresholds or _thresholds()
    conn.execute(
        """
        INSERT INTO strategy_promotion_threshold_policies VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            thresholds.threshold_policy_id,
            thresholds.threshold_policy_hash,
            thresholds.development_campaign_id,
            thresholds.holdout_campaign_id,
            registered_at or NOW.isoformat(),
            _canonical(thresholds.to_dict()),
        ),
    )


def _insert_candidate(conn: sqlite3.Connection, candidate=None, *, registered_at: str | None = None) -> None:
    candidate = candidate or _candidate()
    conn.execute(
        """
        INSERT INTO strategy_promotion_policies VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate.policy_id,
            candidate.policy_hash,
            candidate.threshold_policy_id,
            candidate.threshold_policy_hash,
            candidate.development_campaign_id,
            candidate.holdout_campaign_id,
            registered_at or NOW.isoformat(),
            _canonical(candidate.to_dict()),
        ),
    )


def test_missing_and_symlink_core_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(StrategyLabReadModelMissing, match="missing"):
        StrategyLabPromotionReadModel(tmp_path / "core.sqlite3")
    target = tmp_path / "target.sqlite3"
    sqlite3.connect(target).close()
    link = tmp_path / "core.sqlite3"
    link.symlink_to(target)
    with pytest.raises(StrategyLabReadModelIntegrityError, match="symlink"):
        StrategyLabPromotionReadModel(link)


def test_empty_existing_core_returns_fail_closed_no_governance_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "core.sqlite3"
    sqlite3.connect(path).close()
    snapshot = StrategyLabPromotionReadModel(path).snapshot(now=NOW)
    assert snapshot.governance_state == "NO_GOVERNANCE_DATA"
    assert snapshot.thresholds == ()
    assert snapshot.candidates == ()
    assert snapshot.gate_evidence_state == "NOT_PERSISTED_BY_W79"
    assert snapshot.paper_candidate_authorized is False
    assert snapshot.external_execution_authorized is False
    assert snapshot.capital_authority == "NONE"
    assert snapshot.live_trading == "BLOCKED"
    assert snapshot.to_dict()["broker_network_used"] is False
    assert snapshot.to_dict()["credentials_used"] is False


def test_partial_w79_schema_is_integrity_error(tmp_path: Path) -> None:
    path = tmp_path / "core.sqlite3"
    _schema(path, thresholds=True, candidates=False).close()
    with pytest.raises(StrategyLabReadModelIntegrityError, match="partial W79"):
        StrategyLabPromotionReadModel(path).snapshot(now=NOW)


def test_threshold_only_projection_is_hash_bound_and_read_only(tmp_path: Path) -> None:
    path = tmp_path / "core.sqlite3"
    conn = _schema(path)
    _insert_threshold(conn)
    conn.commit()
    conn.close()

    reader = StrategyLabPromotionReadModel(path)
    snapshot = reader.snapshot(now=NOW)
    assert snapshot.governance_state == "THRESHOLDS_PREREGISTERED"
    assert len(snapshot.thresholds) == 1
    threshold = snapshot.thresholds[0]
    assert threshold.threshold_policy_id == "thresholds-001"
    assert threshold.max_holm_adjusted_p == "0.05"
    assert threshold.min_holdout_net_return == "0.01"
    assert threshold.max_holdout_drawdown == "0.1"
    assert threshold.min_execution_fill_ratio == "0.5"
    assert threshold.max_execution_adverse_slippage_bps == "8"
    assert threshold.candidate_binding_state == "AWAITING_CANDIDATE"
    assert snapshot.provenance_hash == replace(snapshot).provenance_hash

    ro = reader._connect_read_only()
    try:
        assert ro.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("CREATE TABLE forbidden_write(x INTEGER)")
    finally:
        ro.close()


def test_candidate_projection_binds_exact_threshold_and_strategy_version(tmp_path: Path) -> None:
    path = tmp_path / "core.sqlite3"
    thresholds = _thresholds()
    candidate = _candidate(thresholds, strategy_version="v2026.08.23")
    conn = _schema(path)
    _insert_threshold(conn, thresholds)
    _insert_candidate(conn, candidate)
    conn.commit()
    conn.close()

    snapshot = StrategyLabPromotionReadModel(path).snapshot(now=NOW)
    assert snapshot.governance_state == "CANDIDATE_FROZEN"
    assert snapshot.thresholds[0].candidate_binding_state == "CANDIDATE_FROZEN"
    assert snapshot.candidates[0].selected_strategy_id == "strategy-a"
    assert snapshot.candidates[0].selected_strategy_version == "v2026.08.23"
    assert snapshot.candidates[0].threshold_policy_hash == snapshot.thresholds[0].threshold_policy_hash
    assert snapshot.required_gate_ids == promotion.REQUIRED_W79_GATE_IDS
    assert snapshot.promotion_blockers == tuple(sorted(promotion.PERMANENT_W79_PROMOTION_BLOCKERS))


def test_sqlite_side_column_tampering_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "core.sqlite3"
    conn = _schema(path)
    _insert_threshold(conn)
    conn.execute(
        "UPDATE strategy_promotion_threshold_policies SET development_campaign_id = ?",
        ("tampered-development",),
    )
    conn.commit()
    conn.close()
    with pytest.raises(StrategyLabReadModelIntegrityError, match="SQLite column mismatch"):
        StrategyLabPromotionReadModel(path).snapshot(now=NOW)


def test_json_hash_tampering_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "core.sqlite3"
    thresholds = _thresholds()
    conn = _schema(path)
    value = thresholds.to_dict()
    value["min_holdout_fills"] = 999
    conn.execute(
        "INSERT INTO strategy_promotion_threshold_policies VALUES (?, ?, ?, ?, ?, ?)",
        (
            thresholds.threshold_policy_id,
            thresholds.threshold_policy_hash,
            thresholds.development_campaign_id,
            thresholds.holdout_campaign_id,
            NOW.isoformat(),
            _canonical(value),
        ),
    )
    conn.commit()
    conn.close()
    with pytest.raises(promotion.StrategyPromotionIntegrityError, match="hash mismatch"):
        StrategyLabPromotionReadModel(path).snapshot(now=NOW)


def test_candidate_without_threshold_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "core.sqlite3"
    conn = _schema(path)
    _insert_candidate(conn)
    conn.commit()
    conn.close()
    with pytest.raises(StrategyLabReadModelIntegrityError, match="lost its threshold"):
        StrategyLabPromotionReadModel(path).snapshot(now=NOW)


def test_candidate_campaign_binding_mismatch_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "core.sqlite3"
    thresholds = _thresholds()
    values = {
        "policy_id": "promotion-001",
        "threshold_policy_id": thresholds.threshold_policy_id,
        "threshold_policy_hash": thresholds.threshold_policy_hash,
        "development_campaign_id": "development-other",
        "holdout_campaign_id": thresholds.holdout_campaign_id,
        "holdout_trial_id": thresholds.holdout_trial_id,
        "selected_trial_id": "development-trial-001",
        "selected_trial_fingerprint": H,
        "selected_strategy_id": "strategy-a",
        "selected_strategy_version": "v1",
        "tournament_fingerprint": T,
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    candidate = promotion.StrategyPromotionPolicy(
        **values,
        policy_hash=promotion._hash(promotion._policy_payload_from_values(values)),
    )
    conn = _schema(path)
    _insert_threshold(conn, thresholds)
    _insert_candidate(conn, candidate)
    conn.commit()
    conn.close()
    with pytest.raises(StrategyLabReadModelIntegrityError, match="campaign binding mismatch"):
        StrategyLabPromotionReadModel(path).snapshot(now=NOW)


def test_naive_registered_at_and_naive_snapshot_time_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "core.sqlite3"
    conn = _schema(path)
    _insert_threshold(conn, registered_at="2026-08-23T12:00:00")
    conn.commit()
    conn.close()
    with pytest.raises(StrategyLabReadModelIntegrityError, match="timezone-aware"):
        StrategyLabPromotionReadModel(path).snapshot(now=NOW)

    clean = tmp_path / "clean.sqlite3"
    sqlite3.connect(clean).close()
    with pytest.raises(StrategyLabReadModelIntegrityError, match="timezone-aware"):
        StrategyLabPromotionReadModel(clean).snapshot(now=datetime(2026, 8, 23, 12, 0))


def test_snapshot_object_rejects_authority_or_hash_tampering(tmp_path: Path) -> None:
    path = tmp_path / "core.sqlite3"
    sqlite3.connect(path).close()
    snapshot = StrategyLabPromotionReadModel(path).snapshot(now=NOW)
    with pytest.raises(StrategyLabReadModelIntegrityError, match="authorize PAPER"):
        replace(snapshot, paper_candidate_authorized=True)
    with pytest.raises(StrategyLabReadModelIntegrityError, match="provenance hash mismatch"):
        replace(snapshot, provenance_hash="0" * 64)
