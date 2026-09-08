from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import zipfile

import pytest

from autotrade.research.external_data import HttpResponse, ReadOnlyRequest
from autotrade.research.market import InstrumentMetadata
from autotrade.research.oss3_market_collection import (
    OSS3D2U_PLAN_VERSION,
    PARTITION_POLICY,
    PLAN_POLICY,
    STITCH_POLICY,
    HistoricalCollectionPlan,
    SQLiteHistoricalCollectionPlanRegistry,
)
from autotrade.research.oss3_market_snapshot import BinanceSpotArchiveDescriptor
from labs.oss3_market_data.archive_acquisition import (
    ACQUISITION_ORDER_POLICY,
    REQUEST_POLICY,
    ArchiveAcquisitionGovernanceError,
    ArchiveAcquisitionIntegrityError,
    acquire_preregistered_archive,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 7, 22, 30, tzinfo=UTC)


def _instrument(symbol: str) -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol=symbol,
        venue="BINANCE_SPOT",
        quote_currency="USDT",
        price_tick=Decimal("0.00000001"),
        quantity_step=Decimal("0.00000001"),
    )


def _plan() -> HistoricalCollectionPlan:
    symbols = ("BTCUSDT", "ETHUSDT")
    periods = ("2025-01", "2025-02", "2025-03")
    descriptors = tuple(
        BinanceSpotArchiveDescriptor.monthly(
            instrument=_instrument(symbol),
            interval="1d",
            month=period,
        )
        for period in periods
        for symbol in symbols
    )
    return HistoricalCollectionPlan(
        plan_version=OSS3D2U_PLAN_VERSION,
        collection_id="oss3d2u-acquisition-fixture",
        descriptors=descriptors,
        symbols=symbols,
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


def _provider_material(desc: BinanceSpotArchiveDescriptor) -> tuple[bytes, bytes]:
    multiplier = 1000
    interval_units = desc.interval_seconds * 1000 * multiplier
    start_units = _epoch_us(desc.period_start)
    rows = []
    for index in range(desc.expected_rows):
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
    checksum = f"{sha256(archive_bytes).hexdigest()}  {desc.archive_filename}\n".encode("utf-8")
    return archive_bytes, checksum


class ScriptedTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[ReadOnlyRequest] = []

    def send(self, request: ReadOnlyRequest) -> HttpResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected extra network call")
        return self.responses.pop(0)


def _response(*, body: bytes, url: str, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status_code=status,
        body=body,
        final_url=url,
        headers={},
    )


def _transport_for(desc: BinanceSpotArchiveDescriptor) -> ScriptedTransport:
    archive, checksum = _provider_material(desc)
    return ScriptedTransport(
        [
            _response(body=checksum, url=desc.checksum_url),
            _response(body=archive, url=desc.archive_url),
            _response(body=checksum, url=desc.checksum_url),
        ]
    )


def test_acquisition_is_blocked_before_durable_plan_and_makes_zero_requests(tmp_path):
    plan = _plan()
    descriptor = plan.descriptors[0]
    transport = _transport_for(descriptor)
    registry = SQLiteHistoricalCollectionPlanRegistry(tmp_path / "d2u.sqlite3")
    with pytest.raises(Exception, match="durable D2U collection plan preregistration"):
        acquire_preregistered_archive(
            registry=registry,
            plan=plan,
            descriptor=descriptor,
            transport=transport,
            now=NOW,
        )
    assert transport.requests == []


def test_acquisition_performs_exact_checksum_zip_checksum_order_and_no_retry(tmp_path):
    plan = _plan()
    descriptor = plan.descriptors[0]
    registry = SQLiteHistoricalCollectionPlanRegistry(tmp_path / "d2u.sqlite3")
    registry.preregister(plan, now=NOW)
    transport = _transport_for(descriptor)

    material = acquire_preregistered_archive(
        registry=registry,
        plan=plan,
        descriptor=descriptor,
        transport=transport,
        now=NOW,
    )
    assert [request.url for request in transport.requests] == [
        descriptor.checksum_url,
        descriptor.archive_url,
        descriptor.checksum_url,
    ]
    assert all(request.method == "GET" for request in transport.requests)
    assert material.receipt.acquisition_order_policy == ACQUISITION_ORDER_POLICY
    assert material.receipt.request_policy == REQUEST_POLICY
    assert material.receipt.request_count == 3
    assert material.receipt.retries_performed == 0
    assert material.receipt.network_used is True
    assert material.receipt.provider_credentials_used is False
    assert material.receipt.trading_endpoints_used is False
    assert material.receipt.final_holdout_values_requested is False
    assert material.receipt.execution_authorized is False
    assert material.receipt.capital_authority == "NONE"
    assert material.receipt.live_trading == "BLOCKED"
    assert material.snapshot.manifest.archive_sha256 == material.receipt.archive_payload_sha256


def test_checksum_change_during_acquisition_fails_before_d2t_normalization(tmp_path):
    plan = _plan()
    descriptor = plan.descriptors[0]
    archive, checksum = _provider_material(descriptor)
    changed = checksum.replace(b"a", b"b", 1) if b"a" in checksum else b"0" + checksum[1:]
    registry = SQLiteHistoricalCollectionPlanRegistry(tmp_path / "d2u.sqlite3")
    registry.preregister(plan, now=NOW)
    transport = ScriptedTransport(
        [
            _response(body=checksum, url=descriptor.checksum_url),
            _response(body=archive, url=descriptor.archive_url),
            _response(body=changed, url=descriptor.checksum_url),
        ]
    )
    with pytest.raises(ArchiveAcquisitionIntegrityError, match="CHECKSUM changed"):
        acquire_preregistered_archive(
            registry=registry,
            plan=plan,
            descriptor=descriptor,
            transport=transport,
            now=NOW,
        )
    assert len(transport.requests) == 3


def test_redirect_is_forbidden_even_to_another_allowlisted_path(tmp_path):
    plan = _plan()
    descriptor = plan.descriptors[0]
    archive, checksum = _provider_material(descriptor)
    registry = SQLiteHistoricalCollectionPlanRegistry(tmp_path / "d2u.sqlite3")
    registry.preregister(plan, now=NOW)
    transport = ScriptedTransport(
        [
            _response(body=checksum, url=descriptor.archive_url),
            _response(body=archive, url=descriptor.archive_url),
            _response(body=checksum, url=descriptor.checksum_url),
        ]
    )
    with pytest.raises(ArchiveAcquisitionGovernanceError, match="redirects are forbidden"):
        acquire_preregistered_archive(
            registry=registry,
            plan=plan,
            descriptor=descriptor,
            transport=transport,
            now=NOW,
        )
    assert len(transport.requests) == 1


def test_unplanned_descriptor_fails_before_network(tmp_path):
    plan = _plan()
    foreign = BinanceSpotArchiveDescriptor.monthly(
        instrument=_instrument("BTCUSDT"),
        interval="1d",
        month="2025-04",
    )
    transport = _transport_for(foreign)
    registry = SQLiteHistoricalCollectionPlanRegistry(tmp_path / "d2u.sqlite3")
    registry.preregister(plan, now=NOW)
    with pytest.raises(ArchiveAcquisitionGovernanceError, match="not part of preregistered"):
        acquire_preregistered_archive(
            registry=registry,
            plan=plan,
            descriptor=foreign,
            transport=transport,
            now=NOW,
        )
    assert transport.requests == []


def test_wrong_archive_bytes_fail_d2t_after_stable_checksum_triplet(tmp_path):
    plan = _plan()
    descriptor = plan.descriptors[0]
    archive, checksum = _provider_material(descriptor)
    corrupted = archive[:-1] + bytes([archive[-1] ^ 0x01])
    registry = SQLiteHistoricalCollectionPlanRegistry(tmp_path / "d2u.sqlite3")
    registry.preregister(plan, now=NOW)
    transport = ScriptedTransport(
        [
            _response(body=checksum, url=descriptor.checksum_url),
            _response(body=corrupted, url=descriptor.archive_url),
            _response(body=checksum, url=descriptor.checksum_url),
        ]
    )
    with pytest.raises(Exception, match="CHECKSUM does not match archive bytes"):
        acquire_preregistered_archive(
            registry=registry,
            plan=plan,
            descriptor=descriptor,
            transport=transport,
            now=NOW,
        )
    assert len(transport.requests) == 3


def test_acquired_material_persists_raw_and_normalized_evidence_atomically(tmp_path):
    plan = _plan()
    descriptor = plan.descriptors[0]
    registry = SQLiteHistoricalCollectionPlanRegistry(tmp_path / "d2u.sqlite3")
    registry.preregister(plan, now=NOW)
    material = acquire_preregistered_archive(
        registry=registry,
        plan=plan,
        descriptor=descriptor,
        transport=_transport_for(descriptor),
        now=NOW,
    )
    paths = material.write(tmp_path / "evidence")
    assert set(paths) == {"archive", "checksum", "snapshot", "receipt"}
    assert all(Path(path).is_file() for path in paths.values())
    assert paths["archive"].read_bytes() == material.archive_bytes
    assert paths["checksum"].read_text(encoding="utf-8") == material.checksum_text
    assert not list((tmp_path / "evidence").rglob("*.tmp"))


def _epoch_us(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
