from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import zipfile

import pytest

from autotrade.research.market import InstrumentMetadata
from autotrade.research.oss3_market_collection import (
    OSS3D2U_PLAN_VERSION,
    PARTITION_POLICY,
    PLAN_POLICY,
    STITCH_POLICY,
    HistoricalCollectionPlan,
    assemble_historical_collection,
)
from autotrade.research.oss3_market_snapshot import (
    TIMESTAMP_US,
    BinanceSpotArchiveDescriptor,
    build_binance_spot_archive_snapshot,
)
from autotrade.research.oss3_real_descriptor_identity import StableDescriptorMaterialIdentity

from labs.oss3_qlib.stable_rehydrated_raw_split import (
    LINEAGE_POLICY,
    PARTITION_REPRODUCTION_POLICY,
    SCIENTIFIC_IDENTITY_POLICY,
    RehydratedDescriptorMaterial,
    StableRehydratedRawSplitGovernanceError,
    StableRehydratedRawSplitIntegrityError,
    _build_stable_rehydrated_raw_split,
)


UTC = timezone.utc
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
PERIODS = ("2025-01", "2025-02", "2025-03")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _instrument(symbol: str) -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol=symbol,
        venue="BINANCE_SPOT",
        quote_currency="USDT",
        price_tick=Decimal("0.00000001"),
        quantity_step=Decimal("0.00000001"),
    )


def _mini_plan() -> HistoricalCollectionPlan:
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
        collection_id="oss3d3a-mini-2025q1",
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
    multiplier = 1000
    interval_units = desc.interval_seconds * 1000 * multiplier
    start_units = int(desc.period_start.timestamp() * 1_000_000)
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
        info = zipfile.ZipInfo(desc.csv_filename, date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, csv_bytes)
    archive_bytes = buffer.getvalue()
    digest = sha256(archive_bytes).hexdigest()
    checksum_bytes = f"{digest}  {desc.archive_filename}\n".encode("utf-8")
    return archive_bytes, checksum_bytes


def _stable_entry(
    descriptor: BinanceSpotArchiveDescriptor,
    archive_bytes: bytes,
    checksum_bytes: bytes,
) -> tuple[StableDescriptorMaterialIdentity, object]:
    snapshot = build_binance_spot_archive_snapshot(
        descriptor=descriptor,
        archive_bytes=archive_bytes,
        checksum_text=checksum_bytes.decode("utf-8"),
    )
    vector = [
        descriptor.fingerprint,
        sha256(archive_bytes).hexdigest(),
        sha256(checksum_bytes).hexdigest(),
        snapshot.artifact_hash,
        snapshot.manifest.normalized_dataset_hash,
    ]
    return StableDescriptorMaterialIdentity(*vector, _hash(vector)), snapshot


def _fixture():
    plan = _mini_plan()
    entries = []
    material_a = []
    material_b = []
    snapshots = []
    receipts_a = {}
    for descriptor in plan.descriptors:
        bias = SYMBOLS.index(descriptor.instrument.symbol)
        archive_bytes, checksum_bytes = _provider_material(descriptor, symbol_bias=bias)
        entry, snapshot = _stable_entry(descriptor, archive_bytes, checksum_bytes)
        entries.append(entry)
        snapshots.append(snapshot)
        receipt_a = sha256(("receipt-a:" + descriptor.fingerprint).encode("utf-8")).hexdigest()
        receipt_b = sha256(("receipt-b:" + descriptor.fingerprint).encode("utf-8")).hexdigest()
        receipts_a[descriptor.fingerprint] = receipt_a
        material_a.append(
            RehydratedDescriptorMaterial(
                descriptor=descriptor,
                archive_bytes=archive_bytes,
                checksum_bytes=checksum_bytes,
                acquisition_receipt_hash=receipt_a,
            )
        )
        material_b.append(
            RehydratedDescriptorMaterial(
                descriptor=descriptor,
                archive_bytes=archive_bytes,
                checksum_bytes=checksum_bytes,
                acquisition_receipt_hash=receipt_b,
            )
        )

    reference = assemble_historical_collection(
        plan=plan,
        artifacts=tuple(snapshots),
        acquisition_receipt_hashes=receipts_a,
    )
    stable_root = _hash([entry.material_identity_hash for entry in entries])
    common = dict(
        plan=plan,
        entries=tuple(entries),
        d2y_seal_fingerprint=sha256(b"synthetic-d2y-seal").hexdigest(),
        d2z_manifest_file_sha256=sha256(b"synthetic-d2z-manifest").hexdigest(),
        d2z_stable_material_root=stable_root,
        original_d2y_partition_material_fingerprint=reference.fingerprint,
        expected_training_universe_hash=reference.training.universe_hash,
        expected_development_universe_hash=reference.development.universe_hash,
    )
    return plan, tuple(material_a), tuple(material_b), reference, common


def test_d3a_builds_exact_raw_d2r_d2s_sources_after_stable_material_gate():
    _, material_a, _, reference, common = _fixture()
    result = _build_stable_rehydrated_raw_split(materials=material_a, **common)

    assert result.partition_material.training.universe_hash == reference.training.universe_hash
    assert result.partition_material.development.universe_hash == reference.development.universe_hash
    assert result.training_source.training_universe.universe_hash == reference.training.universe_hash
    assert result.development_source.development_universe.universe_hash == reference.development.universe_hash
    assert result.evidence.scientific_identity_policy == SCIENTIFIC_IDENTITY_POLICY
    assert result.evidence.lineage_policy == LINEAGE_POLICY
    assert result.evidence.partition_reproduction_policy == PARTITION_REPRODUCTION_POLICY
    assert result.evidence.exact_stable_material_verified is True
    assert result.evidence.exact_d2y_training_universe_verified is True
    assert result.evidence.exact_d2y_development_universe_verified is True
    assert result.evidence.train_label_artifact_materialized is False
    assert result.evidence.development_label_artifact_materialized is False
    assert result.evidence.prediction_values_loaded is False
    assert result.evidence.qlib_runtime_used is False
    assert result.evidence.final_holdout_values_loaded is False
    assert result.evidence.capital_authority == "NONE"
    assert result.evidence.live_trading == "BLOCKED"


def test_receipt_timestamp_lineage_can_change_without_changing_scientific_identity():
    _, material_a, material_b, _, common = _fixture()
    first = _build_stable_rehydrated_raw_split(materials=material_a, **common)
    second = _build_stable_rehydrated_raw_split(materials=material_b, **common)

    assert first.evidence.reacquisition_lineage_root != second.evidence.reacquisition_lineage_root
    assert (
        first.evidence.reacquired_d2u_partition_material_fingerprint
        != second.evidence.reacquired_d2u_partition_material_fingerprint
    )
    assert first.evidence.fingerprint != second.evidence.fingerprint
    assert first.fingerprint != second.fingerprint

    assert first.scientific_fingerprint == second.scientific_fingerprint
    assert first.training_source.source_hash == second.training_source.source_hash
    assert first.development_source.source_hash == second.development_source.source_hash
    assert first.partition_material.training.universe_hash == second.partition_material.training.universe_hash
    assert first.partition_material.development.universe_hash == second.partition_material.development.universe_hash
    assert first.evidence.receipt_lineage_excluded_from_scientific_identity is True
    assert first.evidence.partition_material_fingerprint_reproduction_required is False
    assert first.evidence.lineage_timestamps_may_differ is True


def test_d3a_rejects_any_rehydrated_provider_byte_drift_before_assembly():
    _, material_a, _, _, common = _fixture()
    tampered = list(material_a)
    tampered[0] = replace(tampered[0], archive_bytes=tampered[0].archive_bytes + b"x")
    with pytest.raises(Exception, match="rehydrated archive differs"):
        _build_stable_rehydrated_raw_split(materials=tuple(tampered), **common)


def test_d3a_rejects_wrong_d2y_train_or_development_universe():
    _, material_a, _, _, common = _fixture()
    with pytest.raises(StableRehydratedRawSplitIntegrityError, match="TRAIN universe differs"):
        _build_stable_rehydrated_raw_split(
            materials=material_a,
            **{**common, "expected_training_universe_hash": "a" * 64},
        )
    with pytest.raises(StableRehydratedRawSplitIntegrityError, match="DEVELOPMENT universe differs"):
        _build_stable_rehydrated_raw_split(
            materials=material_a,
            **{**common, "expected_development_universe_hash": "b" * 64},
        )


def test_d3a_rejects_missing_duplicate_or_reordered_families():
    _, material_a, _, _, common = _fixture()
    with pytest.raises(StableRehydratedRawSplitIntegrityError, match="one D2Z identity and one material"):
        _build_stable_rehydrated_raw_split(materials=material_a[:-1], **common)

    entries = common["entries"]
    with pytest.raises(StableRehydratedRawSplitIntegrityError, match="exact canonical plan order"):
        _build_stable_rehydrated_raw_split(
            materials=material_a,
            **{**common, "entries": (entries[1], entries[0], *entries[2:])},
        )

    duplicate = (material_a[0], material_a[0], *material_a[2:])
    with pytest.raises(StableRehydratedRawSplitIntegrityError, match="material family differs"):
        _build_stable_rehydrated_raw_split(materials=duplicate, **common)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("partition_material_fingerprint_reproduction_required", True),
        ("network_used_by_handoff", True),
        ("train_label_artifact_materialized", True),
        ("development_label_artifact_materialized", True),
        ("prediction_values_loaded", True),
        ("development_metrics_computed", True),
        ("qlib_runtime_used", True),
        ("final_holdout_values_loaded", True),
        ("promotion_authorized", True),
        ("execution_authorized", True),
        ("paper_execution_authorized", True),
        ("capital_authority", "PAPER"),
        ("live_trading", "ENABLED"),
    ],
)
def test_d3a_evidence_cannot_escalate_authority_or_weaken_separation(field, value):
    _, material_a, _, _, common = _fixture()
    evidence = _build_stable_rehydrated_raw_split(materials=material_a, **common).evidence
    with pytest.raises(StableRehydratedRawSplitGovernanceError):
        replace(evidence, **{field: value})
