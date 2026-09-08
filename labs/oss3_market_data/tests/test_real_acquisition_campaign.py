from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sqlite3
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
from autotrade.research.oss3_market_snapshot import (
    TIMESTAMP_US,
    BinanceSpotArchiveDescriptor,
)
from labs.oss3_market_data.archive_acquisition import acquire_preregistered_archive
from labs.oss3_market_data.real_acquisition_campaign import (
    CAMPAIGN_POLICY,
    D2U_PLAN_LEDGER_FILENAME,
    LEDGER_FILENAME,
    NETWORK_POLICY,
    RESTART_POLICY,
    STORAGE_POLICY,
    DescriptorEvidenceSeal,
    RealAcquisitionCampaignGovernanceError,
    RealAcquisitionCampaignIntegrityError,
    SQLiteRealAcquisitionCampaignLedger,
    persist_material_with_atomic_directory_commit,
    run_restart_safe_campaign,
    validate_external_evidence_root,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 8, 4, 0, tzinfo=UTC)
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
        collection_id="oss3d2v-mini-2025q1",
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


def _provider_material(desc: BinanceSpotArchiveDescriptor, *, symbol_bias: int) -> tuple[bytes, bytes]:
    assert desc.timestamp_unit_policy == TIMESTAMP_US
    interval_units = desc.interval_seconds * 1_000_000
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
    checksum = f"{sha256(archive_bytes).hexdigest()}  {desc.archive_filename}\n".encode("utf-8")
    return archive_bytes, checksum


class FrozenArchiveTransport:
    def __init__(self, plan: HistoricalCollectionPlan) -> None:
        self.requests: list[ReadOnlyRequest] = []
        self._payloads: dict[str, bytes] = {}
        for descriptor in plan.descriptors:
            archive, checksum = _provider_material(
                descriptor,
                symbol_bias=SYMBOLS.index(descriptor.instrument.symbol),
            )
            self._payloads[descriptor.archive_url] = archive
            self._payloads[descriptor.checksum_url] = checksum

    def send(self, request: ReadOnlyRequest) -> HttpResponse:
        self.requests.append(request)
        payload = self._payloads[request.url]
        return HttpResponse(
            status_code=200,
            body=payload,
            final_url=request.url,
            headers={},
        )


def test_empty_status_mode_is_network_free_and_cannot_seal(tmp_path):
    plan = mini_plan()
    result, material = run_restart_safe_campaign(
        evidence_root=tmp_path / "evidence",
        plan=plan,
        now=NOW,
        allow_network=False,
    )
    assert material is None
    assert result.complete is False
    assert result.missing_without_network_authority == len(plan.descriptors)
    assert result.acquired_from_network == 0
    assert result.reused_after_seal_reverification == 0
    assert result.reconciled_unsealed_final_material == 0
    assert result.campaign_seal_fingerprint is None
    assert result.network_policy == NETWORK_POLICY
    ledger = SQLiteRealAcquisitionCampaignLedger(tmp_path / "evidence" / LEDGER_FILENAME)
    assert ledger.descriptor_seal_count() == 0
    assert ledger.get_campaign_seal(plan.collection_id) is None


def test_full_campaign_acquires_exact_family_seals_and_assembles(tmp_path):
    plan = mini_plan()
    transport = FrozenArchiveTransport(plan)
    root = tmp_path / "evidence"
    result, material = run_restart_safe_campaign(
        evidence_root=root,
        plan=plan,
        now=NOW,
        allow_network=True,
        transport=transport,
    )
    assert result.complete is True
    assert result.acquired_from_network == len(plan.descriptors)
    assert result.reused_after_seal_reverification == 0
    assert result.reconciled_unsealed_final_material == 0
    assert result.missing_without_network_authority == 0
    assert result.campaign_seal_fingerprint is not None
    assert len(transport.requests) == len(plan.descriptors) * 3
    for index, descriptor in enumerate(plan.descriptors):
        requests = transport.requests[index * 3 : index * 3 + 3]
        assert [item.method for item in requests] == ["GET", "GET", "GET"]
        assert [item.url for item in requests] == [
            descriptor.checksum_url,
            descriptor.archive_url,
            descriptor.checksum_url,
        ]
    assert material is not None
    assert material.training_warmup.bar_count == 20
    assert material.training.bar_count == 39
    assert material.development_warmup.bar_count == 20
    assert material.development.bar_count == 31
    assert material.evidence.final_holdout_values_loaded is False
    ledger = SQLiteRealAcquisitionCampaignLedger(root / LEDGER_FILENAME)
    assert ledger.descriptor_seal_count() == len(plan.descriptors)
    campaign = ledger.get_campaign_seal(plan.collection_id)
    assert campaign is not None
    assert campaign.fingerprint == result.campaign_seal_fingerprint
    assert campaign.campaign_policy == CAMPAIGN_POLICY
    assert campaign.restart_policy == RESTART_POLICY
    assert campaign.storage_policy == STORAGE_POLICY
    assert campaign.final_holdout_values_loaded is False
    assert campaign.capital_authority == "NONE"
    assert campaign.live_trading == "BLOCKED"


def test_complete_rerun_uses_zero_network_and_reverifies_every_seal(tmp_path):
    plan = mini_plan()
    root = tmp_path / "evidence"
    transport = FrozenArchiveTransport(plan)
    first, first_material = run_restart_safe_campaign(
        evidence_root=root,
        plan=plan,
        now=NOW,
        allow_network=True,
        transport=transport,
    )
    assert first.complete and first_material is not None

    second, second_material = run_restart_safe_campaign(
        evidence_root=root,
        plan=plan,
        now=NOW,
        allow_network=False,
    )
    assert second.complete is True
    assert second.acquired_from_network == 0
    assert second.reused_after_seal_reverification == len(plan.descriptors)
    assert second.reconciled_unsealed_final_material == 0
    assert second.campaign_seal_fingerprint == first.campaign_seal_fingerprint
    assert second_material is not None
    assert second_material.fingerprint == first_material.fingerprint


def test_tampered_sealed_raw_archive_fails_before_any_possible_redownload(tmp_path):
    plan = mini_plan()
    root = tmp_path / "evidence"
    original_transport = FrozenArchiveTransport(plan)
    result, _ = run_restart_safe_campaign(
        evidence_root=root,
        plan=plan,
        now=NOW,
        allow_network=True,
        transport=original_transport,
    )
    assert result.complete

    descriptor = plan.descriptors[0]
    archive_path = root / descriptor.instrument.symbol / descriptor.period / descriptor.archive_filename
    archive_path.write_bytes(archive_path.read_bytes() + b"tamper")
    replacement_transport = FrozenArchiveTransport(plan)
    with pytest.raises(Exception):
        run_restart_safe_campaign(
            evidence_root=root,
            plan=plan,
            now=NOW,
            allow_network=True,
            transport=replacement_transport,
        )
    assert replacement_transport.requests == []


def test_unsealed_final_material_is_reconciled_without_network(tmp_path):
    plan = mini_plan()
    root = tmp_path / "evidence"
    root.mkdir()
    descriptor = plan.descriptors[0]
    d2u_registry = SQLiteHistoricalCollectionPlanRegistry(root / D2U_PLAN_LEDGER_FILENAME)
    d2u_registry.preregister(plan, now=NOW)
    transport = FrozenArchiveTransport(plan)
    acquired = acquire_preregistered_archive(
        registry=d2u_registry,
        plan=plan,
        descriptor=descriptor,
        transport=transport,
        now=NOW,
    )
    persist_material_with_atomic_directory_commit(root=root, material=acquired)
    assert len(transport.requests) == 3

    result, material = run_restart_safe_campaign(
        evidence_root=root,
        plan=plan,
        now=NOW,
        allow_network=False,
    )
    assert material is None
    assert result.complete is False
    assert result.reconciled_unsealed_final_material == 1
    assert result.reused_after_seal_reverification == 0
    assert result.missing_without_network_authority == len(plan.descriptors) - 1
    assert len(transport.requests) == 3
    ledger = SQLiteRealAcquisitionCampaignLedger(root / LEDGER_FILENAME)
    seal = ledger.get_descriptor_seal(descriptor.fingerprint)
    assert seal is not None
    assert seal.acquisition_receipt_hash == acquired.receipt.fingerprint


def test_append_only_descriptor_and_campaign_ledgers(tmp_path):
    plan = mini_plan()
    root = tmp_path / "evidence"
    result, _ = run_restart_safe_campaign(
        evidence_root=root,
        plan=plan,
        now=NOW,
        allow_network=True,
        transport=FrozenArchiveTransport(plan),
    )
    assert result.complete
    path = root / LEDGER_FILENAME
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="OSS3D2V_APPEND_ONLY"):
            connection.execute(
                "UPDATE oss3d2v_descriptor_seals SET collection_id = 'x' WHERE descriptor_fingerprint = ?",
                (plan.descriptors[0].fingerprint,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="OSS3D2V_APPEND_ONLY"):
            connection.execute(
                "DELETE FROM oss3d2v_campaign_seals WHERE collection_id = ?",
                (plan.collection_id,),
            )


def test_real_evidence_root_inside_repository_is_rejected():
    repo_root = Path(__file__).resolve().parents[3]
    with pytest.raises(RealAcquisitionCampaignGovernanceError, match="outside git repository"):
        validate_external_evidence_root(repo_root / ".oss3d2v-evidence", repository_root=repo_root)


def test_no_network_mode_rejects_transport_and_seal_authority_cannot_be_escalated(tmp_path):
    plan = mini_plan()
    with pytest.raises(RealAcquisitionCampaignGovernanceError, match="cannot receive a transport"):
        run_restart_safe_campaign(
            evidence_root=tmp_path / "evidence",
            plan=plan,
            now=NOW,
            allow_network=False,
            transport=FrozenArchiveTransport(plan),
        )

    root = tmp_path / "full"
    result, _ = run_restart_safe_campaign(
        evidence_root=root,
        plan=plan,
        now=NOW,
        allow_network=True,
        transport=FrozenArchiveTransport(plan),
    )
    assert result.complete
    ledger = SQLiteRealAcquisitionCampaignLedger(root / LEDGER_FILENAME)
    seal = ledger.get_descriptor_seal(plan.descriptors[0].fingerprint)
    assert seal is not None
    with pytest.raises(RealAcquisitionCampaignGovernanceError):
        replace(seal, execution_authorized=True)
    with pytest.raises(RealAcquisitionCampaignGovernanceError):
        replace(seal, capital_authority="PAPER")


def _epoch_us(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
