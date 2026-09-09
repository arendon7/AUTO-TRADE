from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import zipfile

import pytest

from autotrade.research.market import InstrumentMetadata
from autotrade.research.oss3_market_collection import canonical_oss3d2u_collection_plan
from autotrade.research.oss3_market_snapshot import BinanceSpotArchiveDescriptor, build_binance_spot_archive_snapshot
from autotrade.research.oss3_real_descriptor_identity import (
    DESCRIPTOR_COUNT,
    ENTRY_SCHEMA,
    IDENTITY_POLICY,
    MATERIAL_MANIFEST_FILE_SHA256,
    MATERIAL_MANIFEST_PATH,
    STABLE_MATERIAL_ROOT,
    DescriptorMaterialManifestIntegrityError,
    DurableDescriptorMaterialManifest,
    StableDescriptorMaterialIdentity,
    load_canonical_oss3d2z_descriptor_material_manifest,
    verify_durable_descriptor_material_manifest,
    verify_rehydrated_descriptor_material,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def test_canonical_d2z_manifest_binds_exact_d2y_and_d2u_v2_order():
    manifest = load_canonical_oss3d2z_descriptor_material_manifest()
    plan = canonical_oss3d2u_collection_plan()

    assert manifest.identity_policy == IDENTITY_POLICY
    assert manifest.entry_schema == ENTRY_SCHEMA
    assert manifest.descriptor_count == DESCRIPTOR_COUNT == len(manifest.entries) == len(plan.descriptors) == 99
    assert manifest.stable_material_root == STABLE_MATERIAL_ROOT
    assert sha256(MATERIAL_MANIFEST_PATH.read_bytes()).hexdigest() == MATERIAL_MANIFEST_FILE_SHA256
    assert tuple(entry.descriptor_fingerprint for entry in manifest.entries) == tuple(
        descriptor.fingerprint for descriptor in plan.descriptors
    )


def test_canonical_d2z_material_identity_root_is_deterministic():
    manifest = load_canonical_oss3d2z_descriptor_material_manifest()
    recomputed = []
    for entry in manifest.entries:
        identity = sha256(_canonical(list(entry.stable_identity_vector()))).hexdigest()
        assert identity == entry.material_identity_hash
        recomputed.append(identity)
    assert sha256(_canonical(recomputed)).hexdigest() == STABLE_MATERIAL_ROOT


def test_manifest_contains_no_time_dependent_receipt_or_seal_bytes():
    raw = MATERIAL_MANIFEST_PATH.read_text(encoding="utf-8").lower()
    for forbidden in ("acquired_at", "sealed_at", "acquisition_receipt_hash", "source_receipt_file_sha256"):
        assert forbidden not in raw
    assert "receipt_timestamp_independent" in raw


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("descriptor_fingerprint", "1" * 64),
        ("archive_sha256", "2" * 64),
        ("checksum_payload_sha256", "3" * 64),
        ("snapshot_artifact_hash", "4" * 64),
        ("normalized_dataset_hash", "5" * 64),
    ],
)
def test_stable_descriptor_identity_rejects_material_drift(field, value):
    entry = load_canonical_oss3d2z_descriptor_material_manifest().entries[0]
    with pytest.raises(DescriptorMaterialManifestIntegrityError):
        replace(entry, **{field: value})


def test_swapped_order_fails_even_with_recomputed_root():
    manifest = load_canonical_oss3d2z_descriptor_material_manifest()
    swapped = (manifest.entries[1], manifest.entries[0], *manifest.entries[2:])
    swapped_root = sha256(_canonical([entry.material_identity_hash for entry in swapped])).hexdigest()
    structurally_valid = replace(manifest, entries=swapped, stable_material_root=swapped_root)
    with pytest.raises(DescriptorMaterialManifestIntegrityError):
        verify_durable_descriptor_material_manifest(structurally_valid)


def test_duplicate_or_missing_entries_fail_closed():
    manifest = load_canonical_oss3d2z_descriptor_material_manifest()
    with pytest.raises(DescriptorMaterialManifestIntegrityError):
        replace(manifest, entries=(manifest.entries[0], *manifest.entries[:-1]))
    with pytest.raises(DescriptorMaterialManifestIntegrityError):
        replace(manifest, entries=manifest.entries[:-1], descriptor_count=98)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("d2y_seal_fingerprint", "6" * 64),
        ("source_inventory_sha256", "7" * 64),
        ("source_evidence_tar_sha256", "8" * 64),
        ("source_artifact_zip_sha256", "9" * 64),
        ("plan_fingerprint", "a" * 64),
    ],
)
def test_manifest_header_crosswire_fails_upstream_rebind(field, value):
    manifest = load_canonical_oss3d2z_descriptor_material_manifest()
    tampered = replace(manifest, **{field: value})
    with pytest.raises(DescriptorMaterialManifestIntegrityError):
        verify_durable_descriptor_material_manifest(tampered)


def _instrument() -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol="BTCUSDT",
        venue="BINANCE_SPOT",
        quote_currency="USDT",
        price_tick=Decimal("0.00000001"),
        quantity_step=Decimal("0.00000001"),
    )


def _provider_material(desc: BinanceSpotArchiveDescriptor) -> tuple[bytes, bytes]:
    multiplier = desc.timestamp_unit_multiplier_per_millisecond
    start_units = int(desc.period_start.timestamp() * 1000) * multiplier
    interval_units = desc.interval_seconds * 1000 * multiplier
    rows = []
    for index in range(desc.expected_rows):
        opened_at = start_units + index * interval_units
        closed_at = opened_at + interval_units - 1
        opened = Decimal("100") + Decimal(index) / Decimal("100")
        closed = opened + Decimal("0.01")
        high = closed + Decimal("0.01")
        low = opened - Decimal("0.01")
        volume = Decimal("1000") + Decimal(index)
        rows.append(
            ",".join(
                (
                    str(opened_at),
                    f"{opened:.8f}",
                    f"{high:.8f}",
                    f"{low:.8f}",
                    f"{closed:.8f}",
                    f"{volume:.8f}",
                    str(closed_at),
                    f"{(volume * closed):.8f}",
                    str(100 + index),
                    f"{(volume / 2):.8f}",
                    f"{(volume * closed / 2):.8f}",
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
    archive_bytes = buffer.getvalue()
    checksum = f"{sha256(archive_bytes).hexdigest()}  {desc.archive_filename}\n".encode("utf-8")
    return archive_bytes, checksum


def _stable_entry(
    descriptor: BinanceSpotArchiveDescriptor,
    archive_bytes: bytes,
    checksum_bytes: bytes,
) -> StableDescriptorMaterialIdentity:
    snapshot = build_binance_spot_archive_snapshot(
        descriptor=descriptor,
        archive_bytes=archive_bytes,
        checksum_text=checksum_bytes.decode("utf-8"),
    )
    core = [
        descriptor.fingerprint,
        sha256(archive_bytes).hexdigest(),
        sha256(checksum_bytes).hexdigest(),
        snapshot.artifact_hash,
        snapshot.manifest.normalized_dataset_hash,
    ]
    return StableDescriptorMaterialIdentity(*core, sha256(_canonical(core)).hexdigest())


def test_offline_rehydration_gate_rederives_exact_d2t_material():
    descriptor = BinanceSpotArchiveDescriptor.monthly(
        instrument=_instrument(),
        interval="1h",
        month="2024-02",
    )
    archive_bytes, checksum_bytes = _provider_material(descriptor)
    entry = _stable_entry(descriptor, archive_bytes, checksum_bytes)

    snapshot = verify_rehydrated_descriptor_material(
        entry,
        descriptor=descriptor,
        archive_bytes=archive_bytes,
        checksum_bytes=checksum_bytes,
    )
    assert snapshot.artifact_hash == entry.snapshot_artifact_hash
    assert snapshot.manifest.normalized_dataset_hash == entry.normalized_dataset_hash


def test_offline_rehydration_gate_rejects_archive_checksum_and_descriptor_drift():
    descriptor = BinanceSpotArchiveDescriptor.monthly(
        instrument=_instrument(),
        interval="1h",
        month="2024-02",
    )
    archive_bytes, checksum_bytes = _provider_material(descriptor)
    entry = _stable_entry(descriptor, archive_bytes, checksum_bytes)

    with pytest.raises(DescriptorMaterialManifestIntegrityError):
        verify_rehydrated_descriptor_material(
            entry,
            descriptor=descriptor,
            archive_bytes=archive_bytes + b"x",
            checksum_bytes=checksum_bytes,
        )
    with pytest.raises(DescriptorMaterialManifestIntegrityError):
        verify_rehydrated_descriptor_material(
            entry,
            descriptor=descriptor,
            archive_bytes=archive_bytes,
            checksum_bytes=checksum_bytes + b"x",
        )
    other = BinanceSpotArchiveDescriptor.monthly(
        instrument=_instrument(),
        interval="1h",
        month="2024-03",
    )
    with pytest.raises(DescriptorMaterialManifestIntegrityError):
        verify_rehydrated_descriptor_material(
            entry,
            descriptor=other,
            archive_bytes=archive_bytes,
            checksum_bytes=checksum_bytes,
        )


def test_d2z_contract_has_no_execution_or_capital_authority_fields():
    fields = set(DurableDescriptorMaterialManifest.__dataclass_fields__) | set(
        StableDescriptorMaterialIdentity.__dataclass_fields__
    )
    for forbidden in (
        "promotion_authorized",
        "execution_authorized",
        "paper_execution_authorized",
        "capital_authority",
        "live_trading",
        "final_holdout_values_loaded",
    ):
        assert forbidden not in fields
