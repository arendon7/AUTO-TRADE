from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import sqlite3
import zipfile

import pytest

from autotrade.research.market import InstrumentMetadata
from autotrade.research.oss3_market_collection import (
    ACQUISITION_BINDING_POLICY,
    OSS3D2U_PLAN_VERSION,
    PARTITION_POLICY,
    PLAN_POLICY,
    STITCH_POLICY,
    HistoricalCollectionPlan,
    MarketCollectionGovernanceError,
    MarketCollectionIntegrityError,
    SQLiteHistoricalCollectionPlanRegistry,
    assemble_historical_collection,
    canonical_oss3d2u_collection_plan,
)
from autotrade.research.oss3_market_snapshot import (
    TIMESTAMP_US,
    BinanceSpotArchiveDescriptor,
    build_binance_spot_archive_snapshot,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 7, 22, 0, tzinfo=UTC)
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
PERIODS = ("2025-01", "2025-02", "2025-03")


def _instrument(symbol: str) -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol=symbol,
        venue="BINANCE_SPOT",
        quote_currency="USDT",
        price_tick=Decimal("0.00000001"),
        quantity_step=Decimal("0.00000001"),
    )


def mini_plan() -> HistoricalCollectionPlan:
    descriptors = tuple(
        BinanceSpotArchiveDescriptor.monthly(
            instrument=_instrument(symbol),
            interval="1d",
            month=period,
        )
        for period in PERIODS
        for symbol in SYMBOLS
    )
    return HistoricalCollectionPlan(
        plan_version=OSS3D2U_PLAN_VERSION,
        collection_id="oss3d2u-mini-2025q1",
        descriptors=descriptors,
        symbols=SYMBOLS,
        interval="1d",
        granularity="monthly",
        collection_start="2025-01-01T00:00:00+00:00",
        train_start="2025-01-21T00:00:00+00:00",
        development_start="2025-03-01T00:00:00+00:00",
        development_end="2025-04-01T00:00:00+00:00",
        warmup_bars=20,
        plan_policy=PLAN_POLICY,
        stitch_policy=STITCH_POLICY,
        partition_policy=PARTITION_POLICY,
        final_holdout_descriptors_included=False,
        label_values_included=False,
        prediction_values_included=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )


def _provider_material(desc: BinanceSpotArchiveDescriptor, *, symbol_bias: int = 0) -> tuple[bytes, str]:
    assert desc.timestamp_unit_policy == TIMESTAMP_US
    multiplier = 1000
    interval_units = desc.interval_seconds * 1000 * multiplier
    start_units = _epoch_us(desc.period_start)
    rows: list[str] = []
    for index in range(desc.expected_rows):
        open_time = start_units + index * interval_units
        close_time = open_time + interval_units - 1
        absolute_day = (desc.period_start - datetime(2025, 1, 1, tzinfo=UTC)).days + index
        opened = Decimal("100") + Decimal(symbol_bias * 100) + Decimal(absolute_day) * Decimal("0.5")
        close = opened + Decimal("0.20") + Decimal((absolute_day + symbol_bias) % 4) * Decimal("0.03")
        high = max(opened, close) + Decimal("0.05")
        low = min(opened, close) - Decimal("0.05")
        volume = Decimal("1000") + Decimal(absolute_day * 2 + symbol_bias)
        rows.append(
            ",".join(
                (
                    str(open_time),
                    f"{opened:.8f}",
                    f"{high:.8f}",
                    f"{low:.8f}",
                    f"{close:.8f}",
                    f"{volume:.8f}",
                    str(close_time),
                    f"{(volume * close):.8f}",
                    str(100 + absolute_day),
                    f"{(volume / Decimal('2')):.8f}",
                    f"{(volume * close / Decimal('2')):.8f}",
                    "0",
                )
            )
        )
    csv_bytes = ("\n".join(rows) + "\n").encode("utf-8")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(desc.csv_filename, csv_bytes)
    archive_bytes = buffer.getvalue()
    digest = sha256(archive_bytes).hexdigest()
    return archive_bytes, f"{digest}  {desc.archive_filename}\n"


def _artifacts(plan: HistoricalCollectionPlan):
    result = []
    for descriptor in plan.descriptors:
        bias = SYMBOLS.index(descriptor.instrument.symbol)
        archive_bytes, checksum = _provider_material(descriptor, symbol_bias=bias)
        result.append(
            build_binance_spot_archive_snapshot(
                descriptor=descriptor,
                archive_bytes=archive_bytes,
                checksum_text=checksum,
            )
        )
    return tuple(result)


def _receipt_hashes(plan: HistoricalCollectionPlan) -> dict[str, str]:
    return {
        descriptor.fingerprint: sha256(("receipt:" + descriptor.fingerprint).encode("utf-8")).hexdigest()
        for descriptor in plan.descriptors
    }


def test_canonical_plan_is_finite_real_family_and_excludes_2026_archives():
    plan = canonical_oss3d2u_collection_plan()
    assert plan.symbols == SYMBOLS
    assert plan.interval == "1h"
    assert plan.periods[0] == "2023-04"
    assert plan.periods[-1] == "2025-12"
    assert len(plan.periods) == 33
    assert len(plan.descriptors) == 99
    assert all(descriptor.granularity == "monthly" for descriptor in plan.descriptors)
    assert all(not descriptor.period.startswith("2026-") for descriptor in plan.descriptors)
    assert plan.train_start == "2023-04-01T20:00:00+00:00"
    assert plan.development_start == "2025-01-01T00:00:00+00:00"
    assert plan.development_end == "2026-01-01T00:00:00+00:00"
    assert plan.final_holdout_descriptors_included is False
    assert plan.label_values_included is False
    assert plan.prediction_values_included is False
    assert plan.capital_authority == "NONE"
    assert plan.live_trading == "BLOCKED"


def test_plan_requires_complete_contiguous_symbol_month_grid():
    plan = mini_plan()
    with pytest.raises(MarketCollectionIntegrityError, match="one exact descriptor"):
        replace(plan, descriptors=plan.descriptors[:-1])

    removed_feb = tuple(item for item in plan.descriptors if item.period != "2025-02")
    with pytest.raises(MarketCollectionGovernanceError, match="monthly periods must be contiguous"):
        replace(plan, descriptors=removed_feb)


def test_plan_cannot_include_holdout_or_authority():
    plan = mini_plan()
    with pytest.raises(MarketCollectionGovernanceError):
        replace(plan, final_holdout_descriptors_included=True)
    with pytest.raises(MarketCollectionGovernanceError):
        replace(plan, label_values_included=True)
    with pytest.raises(MarketCollectionGovernanceError):
        replace(plan, execution_authorized=True)
    with pytest.raises(MarketCollectionGovernanceError):
        replace(plan, capital_authority="PAPER")


def test_plan_registry_is_exact_idempotent_and_append_only(tmp_path):
    plan = mini_plan()
    path = tmp_path / "d2u.sqlite3"
    registry = SQLiteHistoricalCollectionPlanRegistry(path)
    registry.preregister(plan, now=NOW)
    registry.preregister(plan, now=NOW)
    registry.require_exact(plan)

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="OSS3D2U_APPEND_ONLY"):
            connection.execute(
                "UPDATE oss3d2u_historical_collection_plans SET fingerprint = ? WHERE collection_id = ?",
                ("f" * 64, plan.collection_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="OSS3D2U_APPEND_ONLY"):
            connection.execute(
                "DELETE FROM oss3d2u_historical_collection_plans WHERE collection_id = ?",
                (plan.collection_id,),
            )

    valid_different_plan = replace(plan, development_start="2025-02-01T00:00:00+00:00")
    with pytest.raises(MarketCollectionGovernanceError, match="different plan"):
        registry.preregister(valid_different_plan, now=NOW)


def test_complete_d2t_family_stitches_into_exact_train_and_development_material():
    plan = mini_plan()
    material = assemble_historical_collection(
        plan=plan,
        artifacts=_artifacts(plan),
        acquisition_receipt_hashes=_receipt_hashes(plan),
    )
    assert material.training_warmup.bar_count == 20
    assert material.training.bar_count == 39
    assert material.development_warmup.bar_count == 20
    assert material.development.bar_count == 31
    assert material.training_warmup.symbols == SYMBOLS
    assert material.training.symbols == SYMBOLS
    assert material.development.symbols == SYMBOLS
    assert material.training.timestamps[0] == datetime(2025, 1, 21, tzinfo=UTC)
    assert material.development.timestamps[0] == datetime(2025, 3, 1, tzinfo=UTC)
    assert material.development.timestamps[-1] == datetime(2025, 3, 31, tzinfo=UTC)
    assert material.evidence.acquisition_binding_policy == ACQUISITION_BINDING_POLICY
    assert material.evidence.exact_monthly_family_complete is True
    assert material.evidence.exact_cross_file_continuity is True
    assert material.evidence.exact_cross_asset_support is True
    assert material.evidence.final_holdout_values_loaded is False
    assert material.evidence.label_values_loaded is False
    assert material.evidence.prediction_values_loaded is False
    assert material.evidence.network_used_by_assembly is False
    assert material.evidence.execution_authorized is False
    assert material.evidence.capital_authority == "NONE"
    assert material.evidence.live_trading == "BLOCKED"


def test_assembly_rejects_missing_snapshot_or_receipt():
    plan = mini_plan()
    artifacts = _artifacts(plan)
    with pytest.raises(MarketCollectionIntegrityError, match="complete planned snapshot family"):
        assemble_historical_collection(
            plan=plan,
            artifacts=artifacts[:-1],
            acquisition_receipt_hashes=_receipt_hashes(plan),
        )
    receipts = _receipt_hashes(plan)
    receipts.pop(next(iter(receipts)))
    with pytest.raises(MarketCollectionIntegrityError, match="one acquisition receipt"):
        assemble_historical_collection(
            plan=plan,
            artifacts=artifacts,
            acquisition_receipt_hashes=receipts,
        )


def test_assembly_rejects_snapshot_from_unplanned_descriptor():
    plan = mini_plan()
    artifacts = list(_artifacts(plan))
    foreign_descriptor = BinanceSpotArchiveDescriptor.monthly(
        instrument=_instrument("BTCUSDT"),
        interval="1d",
        month="2025-04",
    )
    archive_bytes, checksum = _provider_material(foreign_descriptor)
    artifacts[-1] = build_binance_spot_archive_snapshot(
        descriptor=foreign_descriptor,
        archive_bytes=archive_bytes,
        checksum_text=checksum,
    )
    with pytest.raises(MarketCollectionIntegrityError, match="differ from preregistered descriptors"):
        assemble_historical_collection(
            plan=plan,
            artifacts=tuple(artifacts),
            acquisition_receipt_hashes=_receipt_hashes(plan),
        )


def _epoch_us(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
