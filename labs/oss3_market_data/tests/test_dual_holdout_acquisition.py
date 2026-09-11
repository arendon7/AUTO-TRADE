from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from autotrade.research.external_data import HttpResponse
from autotrade.research.market import InstrumentMetadata
from autotrade.research.oss3_market_snapshot import BinanceSpotArchiveDescriptor
from labs.oss3_market_data.dual_holdout_acquisition import (
    D3D_PLAN_FINGERPRINT,
    ECONOMIC_PURPOSE,
    PREDICTIVE_PURPOSE,
    D3EDescriptorSeal,
    DualHoldoutRawAcquisitionError,
    DualHoldoutRawAcquisitionGovernanceError,
    DualHoldoutRawAcquisitionIntegrityError,
    SQLiteD3EAcquisitionLedger,
    acquire_d3d_preregistered_descriptor,
    descriptor_purpose,
    require_exact_d3d_registry_read_only,
    run_dual_holdout_acquisition,
)
from labs.oss3_qlib.dual_holdout_acquisition_preregistration import (
    OSS3D3D_REGISTRY_TABLE,
    SQLiteDualHoldoutAcquisitionPlanRegistry,
    canonical_oss3d3d_dual_holdout_plan,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)


def _registry(tmp_path: Path) -> Path:
    path = tmp_path / "d3d.sqlite3"
    plan = canonical_oss3d3d_dual_holdout_plan()
    registry = SQLiteDualHoldoutAcquisitionPlanRegistry(path)
    registry.preregister(plan, now=NOW)
    registry.require_exact(plan)
    return path


def _archive(desc: BinanceSpotArchiveDescriptor) -> tuple[bytes, bytes]:
    multiplier = 1000  # all D3E descriptors are >= 2025 and use microseconds
    interval_units = desc.interval_seconds * 1_000 * multiplier
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = desc.period_start - epoch
    start_us = delta.days * 86_400_000_000 + delta.seconds * 1_000_000
    rows = []
    for index in range(desc.expected_rows):
        opened = Decimal("100") + Decimal(index) / Decimal("100")
        close = opened + Decimal("0.01")
        open_time = start_us + index * interval_units
        close_time = open_time + interval_units - 1
        rows.append(
            ",".join(
                (
                    str(open_time), f"{opened:.8f}", f"{(close + Decimal('0.01')):.8f}",
                    f"{(opened - Decimal('0.01')):.8f}", f"{close:.8f}", "1000.00000000",
                    str(close_time), "100000.00000000", "10", "500.00000000",
                    "50000.00000000", "0",
                )
            )
        )
    csv_bytes = ("\n".join(rows) + "\n").encode()
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(desc.csv_filename, csv_bytes)
    archive = buffer.getvalue()
    checksum = f"{sha256(archive).hexdigest()}  {desc.archive_filename}\n".encode()
    return archive, checksum


class FakeTransport:
    def __init__(self, desc: BinanceSpotArchiveDescriptor, *, redirect: bool = False, checksum_drift: bool = False, status: int = 200) -> None:
        self.desc = desc
        self.archive, self.checksum = _archive(desc)
        self.redirect = redirect
        self.checksum_drift = checksum_drift
        self.status = status
        self.requests: list[str] = []
        self.checksum_count = 0

    def send(self, request):
        self.requests.append(request.url)
        if request.url == self.desc.archive_url:
            body = self.archive
        else:
            self.checksum_count += 1
            body = self.checksum
            if self.checksum_drift and self.checksum_count == 2:
                body = body + b" "
        final_url = request.url + "?redirected=1" if self.redirect else request.url
        return HttpResponse(status_code=self.status, body=body, final_url=final_url, headers={})


def test_canonical_plan_is_exact_18_disjoint_descriptors():
    plan = canonical_oss3d3d_dual_holdout_plan()
    assert plan.fingerprint == D3D_PLAN_FINGERPRINT
    assert len(plan.predictive.descriptors) == 9
    assert len(plan.economic.descriptors) == 9
    assert not set(plan.predictive.descriptor_fingerprints) & set(plan.economic.descriptor_fingerprints)
    assert all(descriptor_purpose(plan, item) == PREDICTIVE_PURPOSE for item in plan.predictive.descriptors)
    assert all(descriptor_purpose(plan, item) == ECONOMIC_PURPOSE for item in plan.economic.descriptors)


def test_read_only_gate_requires_durable_exact_d3d_registry(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(DualHoldoutRawAcquisitionGovernanceError, match="preregistration"):
        require_exact_d3d_registry_read_only(missing)

    path = _registry(tmp_path)
    assert require_exact_d3d_registry_read_only(path).fingerprint == D3D_PLAN_FINGERPRINT

    conn = sqlite3.connect(path)
    try:
        conn.execute("DROP TRIGGER oss3d3d_dual_holdout_acquisition_plans_no_update")
        conn.execute(
            f"UPDATE {OSS3D3D_REGISTRY_TABLE} SET fingerprint = ?",
            ("0" * 64,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(DualHoldoutRawAcquisitionIntegrityError, match="differs"):
        require_exact_d3d_registry_read_only(path)


def test_single_predictive_descriptor_uses_exact_three_gets_and_d2t(tmp_path):
    registry = _registry(tmp_path)
    desc = canonical_oss3d3d_dual_holdout_plan().predictive.descriptors[0]
    transport = FakeTransport(desc)
    archive, checksum, snapshot, receipt = acquire_d3d_preregistered_descriptor(
        d3d_registry_path=registry,
        descriptor=desc,
        transport=transport,
        now=NOW,
    )
    assert len(transport.requests) == 3
    assert transport.requests == [desc.checksum_url, desc.archive_url, desc.checksum_url]
    assert sha256(archive).hexdigest() == receipt.archive_payload_sha256
    assert checksum.encode() if isinstance(checksum, str) else checksum
    assert snapshot.artifact_hash == receipt.d2t_snapshot_artifact_hash
    assert receipt.purpose == PREDICTIVE_PURPOSE
    assert receipt.raw_market_bytes_acquired is True
    assert receipt.d2t_integrity_normalization_performed is True
    assert receipt.scientific_holdout_evaluation_observed is False
    assert receipt.feature_values_materialized is False
    assert receipt.label_values_materialized is False
    assert receipt.prediction_values_materialized is False
    assert receipt.metrics_computed is False
    assert receipt.holdout_permit_issued is False
    assert receipt.capital_authority == "NONE"
    assert receipt.live_trading == "BLOCKED"


def test_descriptor_outside_d3d_plan_is_rejected_before_transport(tmp_path):
    registry = _registry(tmp_path)
    instrument = InstrumentMetadata(
        symbol="BTCUSDT", venue="BINANCE_SPOT", quote_currency="USDT",
        price_tick=Decimal("0.00000001"), quantity_step=Decimal("0.00000001"),
    )
    desc = BinanceSpotArchiveDescriptor.monthly(instrument=instrument, interval="1h", month="2026-07")
    transport = FakeTransport(desc)
    with pytest.raises(DualHoldoutRawAcquisitionGovernanceError, match="exactly one"):
        acquire_d3d_preregistered_descriptor(
            d3d_registry_path=registry, descriptor=desc, transport=transport, now=NOW
        )
    assert transport.requests == []


def test_redirect_checksum_drift_and_http_error_fail_closed(tmp_path):
    registry = _registry(tmp_path)
    desc = canonical_oss3d3d_dual_holdout_plan().predictive.descriptors[0]
    with pytest.raises(Exception):
        acquire_d3d_preregistered_descriptor(
            d3d_registry_path=registry, descriptor=desc, transport=FakeTransport(desc, redirect=True), now=NOW
        )
    with pytest.raises(DualHoldoutRawAcquisitionIntegrityError, match="CHECKSUM changed"):
        acquire_d3d_preregistered_descriptor(
            d3d_registry_path=registry, descriptor=desc, transport=FakeTransport(desc, checksum_drift=True), now=NOW
        )
    with pytest.raises(DualHoldoutRawAcquisitionError, match="status"):
        acquire_d3d_preregistered_descriptor(
            d3d_registry_path=registry, descriptor=desc, transport=FakeTransport(desc, status=503), now=NOW
        )


def test_offline_campaign_is_zero_network_and_reports_exact_18_missing(tmp_path):
    registry = _registry(tmp_path)
    root = tmp_path / "evidence"
    result = run_dual_holdout_acquisition(
        d3d_registry_path=registry,
        evidence_root=root,
        now=NOW,
        allow_network=False,
    )
    assert result.descriptor_count == 18
    assert result.acquired_from_network == 0
    assert result.missing_without_network_authority == 18
    assert result.complete is False
    assert result.campaign_seal_fingerprint is None
    assert result.expected_get_count_if_fresh == 54
    assert result.scientific_holdout_evaluation_observed is False
    assert result.capital_authority == "NONE"
    assert result.live_trading == "BLOCKED"


def test_evidence_root_inside_repo_is_forbidden(tmp_path):
    registry = _registry(tmp_path)
    repo_root = Path(__file__).resolve().parents[3]
    with pytest.raises(DualHoldoutRawAcquisitionGovernanceError, match="outside git"):
        run_dual_holdout_acquisition(
            d3d_registry_path=registry,
            evidence_root=repo_root / ".tmp-d3e-evidence",
            now=NOW,
            allow_network=False,
        )


def test_stale_staging_directory_is_never_silently_reused(tmp_path):
    registry = _registry(tmp_path)
    root = tmp_path / "evidence"
    desc = canonical_oss3d3d_dual_holdout_plan().predictive.descriptors[0]
    stage = root / ".oss3d3e-staging" / PREDICTIVE_PURPOSE / desc.instrument.symbol / desc.period
    stage.mkdir(parents=True)
    with pytest.raises(DualHoldoutRawAcquisitionGovernanceError, match="stale"):
        run_dual_holdout_acquisition(
            d3d_registry_path=registry,
            evidence_root=root,
            now=NOW,
            allow_network=False,
        )


def _seal() -> D3EDescriptorSeal:
    return D3EDescriptorSeal(
        seal_version="OSS3D3E_RAW_HOLDOUT_DESCRIPTOR_SEAL_V1",
        d3d_plan_fingerprint=D3D_PLAN_FINGERPRINT,
        purpose=PREDICTIVE_PURPOSE,
        descriptor_fingerprint="1" * 64,
        symbol="BTCUSDT",
        period="2026-01",
        archive_sha256="2" * 64,
        checksum_payload_sha256="3" * 64,
        d2t_snapshot_artifact_hash="4" * 64,
        d2t_normalized_dataset_hash="5" * 64,
        acquisition_receipt_fingerprint="6" * 64,
        evidence_relative_directory=f"{PREDICTIVE_PURPOSE}/BTCUSDT/2026-01",
        sealed_at=NOW.isoformat(),
        restart_policy="SEALED_REUSE_ONLY_AFTER_FULL_RAW_RECEIPT_D2T_REVERIFICATION_V1",
        staging_policy="ATOMIC_PURPOSE_SYMBOL_MONTH_DIRECTORY_RENAME_NO_STALE_STAGE_REUSE_V1",
        full_reverification_required_on_reuse=True,
        raw_market_bytes_present=True,
        scientific_holdout_evaluation_observed=False,
        feature_values_materialized=False,
        label_values_materialized=False,
        prediction_values_materialized=False,
        metrics_computed=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )


def test_descriptor_ledger_is_append_only_and_conflicts_fail_closed(tmp_path):
    ledger = SQLiteD3EAcquisitionLedger(tmp_path / "ledger.sqlite3")
    seal = _seal()
    ledger.put_descriptor(seal)
    ledger.put_descriptor(seal)
    assert ledger.get_descriptor(seal.descriptor_fingerprint) == seal
    with pytest.raises(DualHoldoutRawAcquisitionGovernanceError, match="different"):
        ledger.put_descriptor(replace(seal, sealed_at=datetime(2026, 9, 11, 17, tzinfo=UTC).isoformat()))
    conn = sqlite3.connect(ledger.path)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="OSS3D3E_APPEND_ONLY"):
            conn.execute("UPDATE oss3d3e_descriptor_seals SET purpose='x'")
        with pytest.raises(sqlite3.DatabaseError, match="OSS3D3E_APPEND_ONLY"):
            conn.execute("DELETE FROM oss3d3e_descriptor_seals")
    finally:
        conn.close()


def test_seal_rejects_any_scientific_observation_or_authority():
    seal = _seal()
    with pytest.raises(DualHoldoutRawAcquisitionGovernanceError):
        replace(seal, metrics_computed=True)
    with pytest.raises(DualHoldoutRawAcquisitionGovernanceError):
        replace(seal, scientific_holdout_evaluation_observed=True)
    with pytest.raises(DualHoldoutRawAcquisitionGovernanceError):
        replace(seal, paper_execution_authorized=True)
    with pytest.raises(DualHoldoutRawAcquisitionGovernanceError):
        replace(seal, capital_authority="PAPER")
