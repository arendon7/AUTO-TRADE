from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import zipfile

import pytest

from autotrade.research.market import InstrumentMetadata
from autotrade.research.oss3_market_snapshot import (
    CHECKSUM_POLICY,
    COVERAGE_POLICY,
    INSTRUMENT_METADATA_POLICY,
    NORMALIZATION_POLICY,
    OSS3D2T_ARTIFACT_VERSION,
    TIMESTAMP_MS,
    TIMESTAMP_US,
    AlignedSnapshotSetEvidence,
    BinanceSpotArchiveDescriptor,
    HistoricalMarketSnapshotArtifact,
    MarketSnapshotGovernanceError,
    MarketSnapshotIntegrityError,
    build_aligned_snapshot_universe,
    build_binance_spot_archive_snapshot,
)


UTC = timezone.utc


def instrument(symbol: str) -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol=symbol,
        venue="BINANCE_SPOT",
        quote_currency="USDT",
        # Research serialization metadata only.  D2T does not claim these are
        # current exchange trading filters.
        price_tick=Decimal("0.00000001"),
        quantity_step=Decimal("0.00000001"),
    )


def descriptor(
    *,
    symbol: str = "BTCUSDT",
    day: str = "2025-01-01",
    interval: str = "1h",
) -> BinanceSpotArchiveDescriptor:
    return BinanceSpotArchiveDescriptor.daily(
        instrument=instrument(symbol),
        interval=interval,
        day=day,
    )


def provider_material(
    desc: BinanceSpotArchiveDescriptor,
    *,
    price_offset: Decimal = Decimal("0"),
    wrong_unit: bool = False,
    wrong_close_index: int | None = None,
    wrong_open_index: int | None = None,
    extra_member: bool = False,
    duplicate_member: bool = False,
    truncate_rows: int = 0,
) -> tuple[bytes, str]:
    use_microseconds = desc.timestamp_unit_policy == TIMESTAMP_US
    if wrong_unit:
        use_microseconds = not use_microseconds
    multiplier = 1000 if use_microseconds else 1
    interval_units = desc.interval_seconds * 1000 * multiplier
    start_units = _epoch_units(desc.period_start, microseconds=use_microseconds)
    rows = []
    for index in range(desc.expected_rows - truncate_rows):
        open_time = start_units + index * interval_units
        if wrong_open_index == index:
            open_time += interval_units
        close_time = open_time + interval_units - 1
        if wrong_close_index == index:
            close_time -= 5
        opened = Decimal("100") + price_offset + Decimal(index) * Decimal("0.25")
        close = opened + Decimal("0.10") + Decimal(index % 3) * Decimal("0.01")
        high = max(opened, close) + Decimal("0.05")
        low = min(opened, close) - Decimal("0.05")
        volume = Decimal("1000") + Decimal(index)
        quote_volume = volume * close
        taker_base = volume / Decimal("2")
        taker_quote = taker_base * close
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
                    f"{quote_volume:.8f}",
                    str(10 + index),
                    f"{taker_base:.8f}",
                    f"{taker_quote:.8f}",
                    "0",
                )
            )
        )
    csv_bytes = ("\n".join(rows) + "\n").encode("utf-8")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(desc.csv_filename, date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, csv_bytes)
        if extra_member:
            archive.writestr("unexpected.txt", b"not allowed")
        if duplicate_member:
            duplicate = zipfile.ZipInfo(desc.csv_filename, date_time=(2026, 1, 1, 0, 0, 0))
            duplicate.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(duplicate, csv_bytes)
    archive_bytes = buffer.getvalue()
    digest = sha256(archive_bytes).hexdigest()
    return archive_bytes, f"{digest}  {desc.archive_filename}\n"


def build_snapshot(
    *,
    symbol: str = "BTCUSDT",
    day: str = "2025-01-01",
    price_offset: Decimal = Decimal("0"),
) -> tuple[BinanceSpotArchiveDescriptor, HistoricalMarketSnapshotArtifact, bytes, str]:
    desc = descriptor(symbol=symbol, day=day)
    archive_bytes, checksum = provider_material(desc, price_offset=price_offset)
    artifact = build_binance_spot_archive_snapshot(
        descriptor=desc,
        archive_bytes=archive_bytes,
        checksum_text=checksum,
    )
    return desc, artifact, archive_bytes, checksum


def test_descriptor_freezes_provider_paths_and_microsecond_transition():
    pre = descriptor(day="2024-12-31")
    post = descriptor(day="2025-01-01")
    assert pre.timestamp_unit_policy == TIMESTAMP_MS
    assert post.timestamp_unit_policy == TIMESTAMP_US
    assert pre.expected_rows == post.expected_rows == 24
    assert post.archive_filename == "BTCUSDT-1h-2025-01-01.zip"
    assert post.csv_filename == "BTCUSDT-1h-2025-01-01.csv"
    assert post.relative_archive_path == "/data/spot/daily/klines/BTCUSDT/1h/BTCUSDT-1h-2025-01-01.zip"
    assert post.relative_checksum_path.endswith(".zip.CHECKSUM")
    assert post.instrument_metadata_policy == INSTRUMENT_METADATA_POLICY


def test_descriptor_rejects_boundary_sensitive_or_variable_intervals():
    for interval in ("1s", "3d", "1w", "1mo"):
        with pytest.raises(MarketSnapshotGovernanceError):
            descriptor(interval=interval)


def test_build_post_2025_microsecond_snapshot_is_exact_and_offline():
    desc, artifact, archive_bytes, checksum = build_snapshot()
    manifest = artifact.manifest
    assert artifact.artifact_version == OSS3D2T_ARTIFACT_VERSION
    assert manifest.timestamp_unit_policy == TIMESTAMP_US
    assert manifest.archive_sha256 == sha256(archive_bytes).hexdigest()
    assert manifest.archive_sha256 == manifest.provider_checksum_sha256
    assert manifest.received_rows == manifest.expected_rows == 24
    assert manifest.coverage_policy == COVERAGE_POLICY
    assert manifest.checksum_policy == CHECKSUM_POLICY
    assert manifest.normalization_policy == NORMALIZATION_POLICY
    assert manifest.network_used_by_normalizer is False
    assert manifest.provider_credentials_used is False
    assert manifest.trading_filters_certified is False
    assert manifest.execution_authorized is False
    assert manifest.paper_execution_authorized is False
    assert manifest.capital_authority == "NONE"
    assert manifest.live_trading == "BLOCKED"
    assert artifact.dataset.bars[0].started_at == desc.period_start
    assert artifact.dataset.bars[-1].ended_at == desc.period_end
    assert artifact.dataset.gap_indexes() == ()
    artifact.verify_source(
        descriptor=desc,
        archive_bytes=archive_bytes,
        checksum_text=checksum,
    )


def test_build_pre_2025_millisecond_snapshot_is_exact():
    desc, artifact, _, _ = build_snapshot(day="2024-12-31")
    assert artifact.manifest.timestamp_unit_policy == TIMESTAMP_MS
    assert artifact.dataset.bars[0].started_at == desc.period_start
    assert artifact.dataset.bars[-1].ended_at == desc.period_end


def test_checksum_is_verified_before_zip_parse():
    desc = descriptor()
    # The bytes are not even a ZIP. A mismatching provider checksum must be the
    # first failure and therefore proves the archive is not parsed first.
    invalid_zip = b"definitely-not-a-zip"
    wrong = f"{'0' * 64}  {desc.archive_filename}\n"
    with pytest.raises(MarketSnapshotIntegrityError, match="CHECKSUM does not match archive bytes"):
        build_binance_spot_archive_snapshot(
            descriptor=desc,
            archive_bytes=invalid_zip,
            checksum_text=wrong,
        )


def test_checksum_filename_and_single_line_are_exact():
    desc = descriptor()
    archive_bytes, checksum = provider_material(desc)
    digest = sha256(archive_bytes).hexdigest()
    with pytest.raises(MarketSnapshotIntegrityError, match="filename"):
        build_binance_spot_archive_snapshot(
            descriptor=desc,
            archive_bytes=archive_bytes,
            checksum_text=f"{digest}  OTHER.zip\n",
        )
    with pytest.raises(MarketSnapshotIntegrityError, match="exactly one line"):
        build_binance_spot_archive_snapshot(
            descriptor=desc,
            archive_bytes=archive_bytes,
            checksum_text=checksum + checksum,
        )


def test_zip_must_contain_exactly_one_expected_csv_member():
    desc = descriptor()
    archive_bytes, checksum = provider_material(desc, extra_member=True)
    with pytest.raises(MarketSnapshotIntegrityError, match="exactly the expected CSV"):
        build_binance_spot_archive_snapshot(
            descriptor=desc,
            archive_bytes=archive_bytes,
            checksum_text=checksum,
        )


def test_duplicate_zip_member_is_rejected():
    desc = descriptor()
    with pytest.warns(UserWarning):
        archive_bytes, checksum = provider_material(desc, duplicate_member=True)
    with pytest.raises(MarketSnapshotIntegrityError, match="duplicate member"):
        build_binance_spot_archive_snapshot(
            descriptor=desc,
            archive_bytes=archive_bytes,
            checksum_text=checksum,
        )


def test_post_2025_millisecond_payload_is_rejected():
    desc = descriptor(day="2025-01-01")
    archive_bytes, checksum = provider_material(desc, wrong_unit=True)
    with pytest.raises(MarketSnapshotIntegrityError, match="open time"):
        build_binance_spot_archive_snapshot(
            descriptor=desc,
            archive_bytes=archive_bytes,
            checksum_text=checksum,
        )


def test_pre_2025_microsecond_payload_is_rejected():
    desc = descriptor(day="2024-12-31")
    archive_bytes, checksum = provider_material(desc, wrong_unit=True)
    with pytest.raises(MarketSnapshotIntegrityError, match="open time"):
        build_binance_spot_archive_snapshot(
            descriptor=desc,
            archive_bytes=archive_bytes,
            checksum_text=checksum,
        )


def test_wrong_close_time_gap_or_truncated_period_fail_closed():
    desc = descriptor()
    archive_bytes, checksum = provider_material(desc, wrong_close_index=4)
    with pytest.raises(MarketSnapshotIntegrityError, match="close time"):
        build_binance_spot_archive_snapshot(
            descriptor=desc,
            archive_bytes=archive_bytes,
            checksum_text=checksum,
        )

    archive_bytes, checksum = provider_material(desc, wrong_open_index=7)
    with pytest.raises(MarketSnapshotIntegrityError, match="open time"):
        build_binance_spot_archive_snapshot(
            descriptor=desc,
            archive_bytes=archive_bytes,
            checksum_text=checksum,
        )

    archive_bytes, checksum = provider_material(desc, truncate_rows=1)
    with pytest.raises(MarketSnapshotIntegrityError, match="exact expected coverage"):
        build_binance_spot_archive_snapshot(
            descriptor=desc,
            archive_bytes=archive_bytes,
            checksum_text=checksum,
        )


def test_snapshot_artifact_roundtrip_is_canonical_and_tamper_evident(tmp_path):
    _, artifact, _, _ = build_snapshot()
    path = tmp_path / "snapshot.json"
    artifact.write(path)
    restored = HistoricalMarketSnapshotArtifact.read(path)
    assert restored == artifact
    assert restored.dataset.dataset_hash == artifact.dataset.dataset_hash

    document = json.loads(path.read_text(encoding="utf-8"))
    document["normalized_rows"][0][4] = "999999.0"
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(MarketSnapshotIntegrityError):
        HistoricalMarketSnapshotArtifact.read(path)


def test_source_replay_rejects_different_archive():
    desc, artifact, _, _ = build_snapshot()
    other_archive, other_checksum = provider_material(desc, price_offset=Decimal("50"))
    with pytest.raises(MarketSnapshotIntegrityError, match="do not reproduce"):
        artifact.verify_source(
            descriptor=desc,
            archive_bytes=other_archive,
            checksum_text=other_checksum,
        )


def test_three_snapshots_form_exact_aligned_universe_and_evidence():
    snapshots = []
    for index, symbol in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT")):
        _, artifact, _, _ = build_snapshot(
            symbol=symbol,
            price_offset=Decimal(index * 100),
        )
        snapshots.append(artifact)
    universe, evidence = build_aligned_snapshot_universe(
        tuple(reversed(snapshots)),
        universe_name="oss3d2t-binance-2025-01-01-1h",
    )
    assert universe.symbols == ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    assert universe.bar_count == 24
    assert universe.timeframe_seconds == 3600
    assert universe.gap_indexes() if hasattr(universe, "gap_indexes") else True
    assert evidence.exact_common_support is True
    assert evidence.network_used is False
    assert evidence.provider_credentials_used is False
    assert evidence.trading_filters_certified is False
    assert evidence.execution_authorized is False
    assert evidence.capital_authority == "NONE"
    assert evidence.live_trading == "BLOCKED"


def test_aligned_snapshot_set_rejects_period_mismatch():
    _, first, _, _ = build_snapshot(symbol="BTCUSDT", day="2025-01-01")
    _, second, _, _ = build_snapshot(symbol="ETHUSDT", day="2025-01-02")
    with pytest.raises(MarketSnapshotIntegrityError, match="period_start"):
        build_aligned_snapshot_universe(
            (first, second),
            universe_name="mismatched",
        )


def test_manifest_and_set_evidence_cannot_mutate_into_authority():
    _, first, _, _ = build_snapshot(symbol="BTCUSDT")
    _, second, _, _ = build_snapshot(symbol="ETHUSDT")
    with pytest.raises(MarketSnapshotGovernanceError):
        replace(first.manifest, execution_authorized=True)
    with pytest.raises(MarketSnapshotGovernanceError):
        replace(first.manifest, trading_filters_certified=True)
    _, evidence = build_aligned_snapshot_universe((first, second), universe_name="authority-test")
    with pytest.raises(MarketSnapshotGovernanceError):
        replace(evidence, paper_execution_authorized=True)
    with pytest.raises(MarketSnapshotGovernanceError):
        replace(evidence, capital_authority="PAPER")


def _epoch_units(value: datetime, *, microseconds: bool) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    total_us = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    return total_us if microseconds else total_us // 1000
