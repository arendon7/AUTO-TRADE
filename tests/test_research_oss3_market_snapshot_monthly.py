from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import zipfile

import pytest

from autotrade.research.market import InstrumentMetadata
from autotrade.research.oss3_market_snapshot import (
    TIMESTAMP_MS,
    TIMESTAMP_US,
    BinanceSpotArchiveDescriptor,
    HistoricalMarketSnapshotArtifact,
    MarketSnapshotIntegrityError,
    build_binance_spot_archive_snapshot,
)


UTC = timezone.utc


def _instrument(symbol: str = "BTCUSDT") -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol=symbol,
        venue="BINANCE_SPOT",
        quote_currency="USDT",
        price_tick=Decimal("0.00000001"),
        quantity_step=Decimal("0.00000001"),
    )


def _monthly(month: str, *, interval: str = "1d") -> BinanceSpotArchiveDescriptor:
    return BinanceSpotArchiveDescriptor.monthly(
        instrument=_instrument(),
        interval=interval,
        month=month,
    )


def _archive(desc: BinanceSpotArchiveDescriptor, *, truncate: int = 0) -> tuple[bytes, str]:
    microseconds = desc.timestamp_unit_policy == TIMESTAMP_US
    multiplier = 1_000 if microseconds else 1
    interval_units = desc.interval_seconds * 1_000 * multiplier
    start_units = _epoch_units(desc.period_start, microseconds=microseconds)
    rows: list[str] = []
    for index in range(desc.expected_rows - truncate):
        open_time = start_units + index * interval_units
        close_time = open_time + interval_units - 1
        opened = Decimal("100") + Decimal(index)
        close = opened + Decimal("0.25")
        high = close + Decimal("0.10")
        low = opened - Decimal("0.10")
        volume = Decimal("1000") + Decimal(index)
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
                    str(100 + index),
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


def test_monthly_february_2025_is_exact_microsecond_snapshot(tmp_path):
    desc = _monthly("2025-02")
    assert desc.timestamp_unit_policy == TIMESTAMP_US
    assert desc.period_start == datetime(2025, 2, 1, tzinfo=UTC)
    assert desc.period_end == datetime(2025, 3, 1, tzinfo=UTC)
    assert desc.expected_rows == 28
    assert desc.relative_archive_path == (
        "/data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2025-02.zip"
    )

    archive_bytes, checksum = _archive(desc)
    artifact = build_binance_spot_archive_snapshot(
        descriptor=desc,
        archive_bytes=archive_bytes,
        checksum_text=checksum,
    )
    assert artifact.manifest.received_rows == 28
    assert artifact.dataset.bars[0].started_at == desc.period_start
    assert artifact.dataset.bars[-1].ended_at == desc.period_end

    path = tmp_path / "monthly-snapshot.json"
    artifact.write(path)
    restored = HistoricalMarketSnapshotArtifact.read(path)
    assert restored == artifact


def test_monthly_leap_february_2024_is_exact_millisecond_snapshot():
    desc = _monthly("2024-02")
    assert desc.timestamp_unit_policy == TIMESTAMP_MS
    assert desc.expected_rows == 29
    archive_bytes, checksum = _archive(desc)
    artifact = build_binance_spot_archive_snapshot(
        descriptor=desc,
        archive_bytes=archive_bytes,
        checksum_text=checksum,
    )
    assert len(artifact.dataset.bars) == 29
    assert artifact.dataset.bars[-1].ended_at == datetime(2024, 3, 1, tzinfo=UTC)


def test_monthly_snapshot_requires_complete_calendar_period():
    desc = _monthly("2025-04")
    archive_bytes, checksum = _archive(desc, truncate=1)
    with pytest.raises(MarketSnapshotIntegrityError, match="exact expected coverage"):
        build_binance_spot_archive_snapshot(
            descriptor=desc,
            archive_bytes=archive_bytes,
            checksum_text=checksum,
        )


def _epoch_units(value: datetime, *, microseconds: bool) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    total_us = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    return total_us if microseconds else total_us // 1_000
