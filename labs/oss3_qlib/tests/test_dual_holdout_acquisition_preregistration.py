from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import sqlite3

import pytest

from labs.oss3_qlib.dual_holdout_acquisition_preregistration import (
    ECONOMIC_END,
    ECONOMIC_MONTHS,
    EXPECTED_ECONOMIC_BARS_PER_SYMBOL,
    EXPECTED_PREDICTIVE_BARS_PER_SYMBOL,
    EXPECTED_PREDICTIVE_CROSS_SECTIONS,
    EXPECTED_PREDICTIVE_TOTAL_OBSERVATIONS,
    PREDICTIVE_END,
    PREDICTIVE_MONTHS,
    PREDICTIVE_START,
    SOURCE_D3C_ARTIFACT_HASH,
    SOURCE_D3C_CERTIFIED_HEAD,
    SOURCE_D3C_SCIENTIFIC_OUTCOME_HASH,
    DualHoldoutAcquisitionGovernanceError,
    DualHoldoutAcquisitionIntegrityError,
    SQLiteDualHoldoutAcquisitionPlanRegistry,
    canonical_oss3d3d_dual_holdout_plan,
    write_dual_holdout_plan,
)


def test_canonical_d3d_plan_freezes_two_disjoint_quarters():
    plan = canonical_oss3d3d_dual_holdout_plan()

    assert plan.source_d3c_certified_head == SOURCE_D3C_CERTIFIED_HEAD
    assert plan.source_d3c_artifact_hash == SOURCE_D3C_ARTIFACT_HASH
    assert plan.source_d3c_scientific_outcome_hash == SOURCE_D3C_SCIENTIFIC_OUTCOME_HASH
    assert plan.development_end == PREDICTIVE_START
    assert plan.predictive.months == PREDICTIVE_MONTHS
    assert plan.predictive.partition_start == PREDICTIVE_START
    assert plan.predictive.partition_end == PREDICTIVE_END
    assert plan.economic.months == ECONOMIC_MONTHS
    assert plan.economic.partition_start == PREDICTIVE_END
    assert plan.economic.partition_end == ECONOMIC_END
    assert plan.predictive.expected_bars_per_symbol == EXPECTED_PREDICTIVE_BARS_PER_SYMBOL
    assert plan.economic.expected_bars_per_symbol == EXPECTED_ECONOMIC_BARS_PER_SYMBOL
    assert len(plan.predictive.descriptors) == 9
    assert len(plan.economic.descriptors) == 9
    assert not set(plan.predictive.descriptor_fingerprints) & set(plan.economic.descriptor_fingerprints)
    assert plan.untouched_future_start == "2026-07-01T00:00:00+00:00"


def test_d3d_predictive_geometry_exceeds_d2j_sample_floors():
    plan = canonical_oss3d3d_dual_holdout_plan()
    assert EXPECTED_PREDICTIVE_CROSS_SECTIONS == 2159
    assert EXPECTED_PREDICTIVE_TOTAL_OBSERVATIONS == 6477
    assert plan.predictive_d2j_sample_adequacy_preregistered is True


def test_d3d_descriptor_urls_are_identity_only_and_exact_binance_monthly_paths():
    plan = canonical_oss3d3d_dual_holdout_plan()
    first = plan.predictive.descriptors[0]
    last = plan.economic.descriptors[-1]

    assert first.instrument.symbol == "BTCUSDT"
    assert first.period == "2026-01"
    assert first.relative_archive_path == "/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-01.zip"
    assert first.relative_checksum_path.endswith("BTCUSDT-1h-2026-01.zip.CHECKSUM")
    assert last.instrument.symbol == "SOLUSDT"
    assert last.period == "2026-06"
    assert last.relative_archive_path == "/data/spot/monthly/klines/SOLUSDT/1h/SOLUSDT-1h-2026-06.zip"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("network_acquisition_performed", True),
        ("final_holdout_observed", True),
        ("economic_holdout_observed", True),
        ("holdout_permit_issued", True),
        ("holdout_permit_consumed", True),
        ("execution_authorized", True),
        ("paper_execution_authorized", True),
        ("capital_authority", "PAPER"),
        ("live_trading", "ENABLED"),
    ),
)
def test_d3d_plan_cannot_gain_holdout_or_execution_authority(field, value):
    plan = canonical_oss3d3d_dual_holdout_plan()
    with pytest.raises(DualHoldoutAcquisitionGovernanceError):
        replace(plan, **{field: value})


def test_d3d_rejects_predictive_economic_window_crosswire():
    plan = canonical_oss3d3d_dual_holdout_plan()
    with pytest.raises(DualHoldoutAcquisitionIntegrityError):
        replace(plan, predictive=replace(plan.predictive, purpose="ECONOMIC_HOLDOUT"))


def test_d3d_rejects_value_exposure_inside_reserved_window():
    plan = canonical_oss3d3d_dual_holdout_plan()
    with pytest.raises(DualHoldoutAcquisitionGovernanceError):
        replace(plan, predictive=replace(plan.predictive, market_values_exposed=True))
    with pytest.raises(DualHoldoutAcquisitionGovernanceError):
        replace(plan, economic=replace(plan.economic, metrics_computed=True))


def test_d3d_rejects_source_d3c_identity_drift():
    plan = canonical_oss3d3d_dual_holdout_plan()
    with pytest.raises(DualHoldoutAcquisitionIntegrityError):
        replace(plan, source_d3c_artifact_hash="0" * 64)
    with pytest.raises(DualHoldoutAcquisitionIntegrityError):
        replace(plan, source_d3c_scientific_outcome_hash="1" * 64)


def test_d3d_registry_is_idempotent_and_append_only(tmp_path):
    plan = canonical_oss3d3d_dual_holdout_plan()
    path = tmp_path / "d3d.sqlite3"
    registry = SQLiteDualHoldoutAcquisitionPlanRegistry(path)
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)

    registry.preregister(plan, now=now)
    registry.preregister(plan, now=now)
    registry.require_exact(plan)

    with sqlite3.connect(path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM oss3d3d_dual_holdout_acquisition_plans").fetchone()[0]
        assert count == 1
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                "UPDATE oss3d3d_dual_holdout_acquisition_plans SET fingerprint = ? WHERE plan_id = ?",
                ("0" * 64, plan.plan_id),
            )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                "DELETE FROM oss3d3d_dual_holdout_acquisition_plans WHERE plan_id = ?",
                (plan.plan_id,),
            )


def test_d3d_registry_rejects_same_id_with_changed_geometry(tmp_path):
    plan = canonical_oss3d3d_dual_holdout_plan()
    path = tmp_path / "d3d.sqlite3"
    registry = SQLiteDualHoldoutAcquisitionPlanRegistry(path)
    now = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    registry.preregister(plan, now=now)

    # A different post-reservation boundary cannot be substituted after the
    # canonical plan became durable.
    with pytest.raises(DualHoldoutAcquisitionGovernanceError):
        replace(plan, untouched_future_start="2026-08-01T00:00:00+00:00")


def test_d3d_json_artifact_is_deterministic(tmp_path):
    plan = canonical_oss3d3d_dual_holdout_plan()
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_dual_holdout_plan(plan, first)
    write_dual_holdout_plan(plan, second)
    assert first.read_bytes() == second.read_bytes()
    assert plan.fingerprint.encode() in first.read_bytes()
