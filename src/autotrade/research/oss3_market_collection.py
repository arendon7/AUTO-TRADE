"""OSS-3D2U frozen historical collection and partition assembly.

D2U sits between D2T immutable single-archive snapshots and the existing D2R/D2S
raw-market research boundaries.  It freezes the finite archive family before
acquisition, requires durable preregistration of that plan, verifies a complete
set of D2T snapshots, stitches each symbol with exact no-gap/no-overlap rules,
and materializes only WARMUP/TRAIN/DEVELOPMENT universes.

This module is deliberately offline.  Network acquisition lives in a separate
lab adapter.  D2U never materializes FINAL_HOLDOUT values, labels, predictions,
metrics, Qlib runtime, broker state or execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterable, Mapping

from .market import InstrumentMetadata, MarketDataset
from .oss3_market_snapshot import (
    OSS3D2T_ARTIFACT_VERSION,
    PROVIDER_ID,
    BinanceSpotArchiveDescriptor,
    HistoricalMarketSnapshotArtifact,
)
from .universe import AlignedMarketUniverse


OSS3D2U_PLAN_VERSION = "OSS3D2U_HISTORICAL_COLLECTION_PLAN_V1"
OSS3D2U_BINDING_VERSION = "OSS3D2U_ACQUIRED_SNAPSHOT_BINDING_V1"
OSS3D2U_EVIDENCE_VERSION = "OSS3D2U_HISTORICAL_COLLECTION_ASSEMBLY_EVIDENCE_V1"
OSS3D2U_MATERIAL_VERSION = "OSS3D2U_HISTORICAL_RESEARCH_PARTITION_MATERIAL_V1"
PLAN_REGISTRY_TABLE = "oss3d2u_historical_collection_plans"
PLAN_POLICY = "FINITE_MONTHLY_ARCHIVE_FAMILY_PREREGISTERED_BEFORE_NETWORK_V1"
STITCH_POLICY = "EXACT_MONTH_TO_MONTH_NO_GAP_NO_OVERLAP_NO_FILL_V1"
PARTITION_POLICY = "WARMUP20_THEN_TRAIN_THEN_DEVELOPMENT_NO_HOLDOUT_VALUES_V1"
ACQUISITION_BINDING_POLICY = "EXACT_D2T_ARTIFACT_AND_D2U_RECEIPT_PER_DESCRIPTOR_V1"
CANONICAL_COLLECTION_ID = "oss3d2u-binance-spot-btc-eth-sol-1h-2023-2025-v1"
CANONICAL_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
CANONICAL_INTERVAL = "1h"
CANONICAL_FIRST_MONTH = "2023-01"
CANONICAL_LAST_MONTH = "2025-12"
CANONICAL_TRAIN_START = datetime(2023, 1, 1, 20, 0, tzinfo=timezone.utc)
CANONICAL_DEVELOPMENT_START = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
CANONICAL_DEVELOPMENT_END = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
CANONICAL_WARMUP_BARS = 20
MAX_DESCRIPTORS = 500

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


class MarketCollectionError(RuntimeError):
    pass


class MarketCollectionIntegrityError(MarketCollectionError):
    pass


class MarketCollectionGovernanceError(MarketCollectionError):
    pass


@dataclass(frozen=True, slots=True)
class HistoricalCollectionPlan:
    plan_version: str
    collection_id: str
    descriptors: tuple[BinanceSpotArchiveDescriptor, ...]
    symbols: tuple[str, ...]
    interval: str
    granularity: str
    collection_start: str
    train_start: str
    development_start: str
    development_end: str
    warmup_bars: int
    plan_policy: str
    stitch_policy: str
    partition_policy: str
    final_holdout_descriptors_included: bool
    label_values_included: bool
    prediction_values_included: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.plan_version != OSS3D2U_PLAN_VERSION:
            raise MarketCollectionIntegrityError("noncanonical D2U plan version")
        _require_id(self.collection_id, "collection_id")
        if not 2 <= len(self.symbols) <= 32:
            raise MarketCollectionGovernanceError("D2U requires between two and thirty-two symbols")
        if self.symbols != tuple(sorted(self.symbols)) or len(set(self.symbols)) != len(self.symbols):
            raise MarketCollectionIntegrityError("D2U symbols must be canonical and unique")
        if not self.descriptors or len(self.descriptors) > MAX_DESCRIPTORS:
            raise MarketCollectionGovernanceError("D2U descriptor count is outside bound")
        if self.granularity != "monthly":
            raise MarketCollectionGovernanceError("D2U v1 collection granularity must be monthly")
        if self.plan_policy != PLAN_POLICY or self.stitch_policy != STITCH_POLICY:
            raise MarketCollectionGovernanceError("D2U collection policy drifted")
        if self.partition_policy != PARTITION_POLICY:
            raise MarketCollectionGovernanceError("D2U partition policy drifted")
        if isinstance(self.warmup_bars, bool) or not isinstance(self.warmup_bars, int) or self.warmup_bars != 20:
            raise MarketCollectionGovernanceError("D2U v1 requires exactly twenty warmup bars")
        if (
            self.final_holdout_descriptors_included
            or self.label_values_included
            or self.prediction_values_included
        ):
            raise MarketCollectionGovernanceError("D2U plan may include raw WARMUP/TRAIN/DEVELOPMENT material only")
        _deny_authority(
            self.execution_authorized,
            self.paper_execution_authorized,
            self.capital_authority,
            self.live_trading,
        )

        collection_start = _parse_utc(self.collection_start, "collection_start")
        train_start = _parse_utc(self.train_start, "train_start")
        development_start = _parse_utc(self.development_start, "development_start")
        development_end = _parse_utc(self.development_end, "development_end")
        if not collection_start < train_start < development_start < development_end:
            raise MarketCollectionGovernanceError("D2U temporal partition ordering is invalid")

        ordered = tuple(sorted(self.descriptors, key=_descriptor_sort_key))
        if ordered != self.descriptors:
            raise MarketCollectionIntegrityError("D2U descriptors must be in canonical period/symbol order")
        if len({descriptor.fingerprint for descriptor in self.descriptors}) != len(self.descriptors):
            raise MarketCollectionIntegrityError("D2U descriptor fingerprints must be unique")
        for descriptor in self.descriptors:
            if not isinstance(descriptor, BinanceSpotArchiveDescriptor):
                raise TypeError("D2U descriptors must be BinanceSpotArchiveDescriptor")
            if descriptor.granularity != self.granularity:
                raise MarketCollectionIntegrityError("D2U descriptor granularity differs from plan")
            if descriptor.interval != self.interval:
                raise MarketCollectionIntegrityError("D2U descriptor interval differs from plan")
            if descriptor.instrument.symbol not in self.symbols:
                raise MarketCollectionIntegrityError("D2U descriptor symbol is outside plan")

        periods = self.periods
        expected_pairs = {(period, symbol) for period in periods for symbol in self.symbols}
        actual_pairs = {(descriptor.period, descriptor.instrument.symbol) for descriptor in self.descriptors}
        if actual_pairs != expected_pairs or len(self.descriptors) != len(expected_pairs):
            raise MarketCollectionIntegrityError("D2U requires one exact descriptor per symbol and month")
        if not _months_are_contiguous(periods):
            raise MarketCollectionGovernanceError("D2U monthly periods must be contiguous")

        first_period_start = min(descriptor.period_start for descriptor in self.descriptors)
        last_period_end = max(descriptor.period_end for descriptor in self.descriptors)
        if collection_start != first_period_start:
            raise MarketCollectionIntegrityError("D2U collection_start differs from first archive period")
        if development_end != last_period_end:
            raise MarketCollectionGovernanceError("D2U v1 collection must end exactly at DEVELOPMENT end")

        timeframe = self.descriptors[0].interval_seconds
        if train_start != collection_start + timedelta(seconds=self.warmup_bars * timeframe):
            raise MarketCollectionGovernanceError("D2U TRAIN must begin immediately after exact twenty-bar warmup")
        for boundary_name, boundary in (
            ("train_start", train_start),
            ("development_start", development_start),
            ("development_end", development_end),
        ):
            offset = (boundary - collection_start).total_seconds()
            if offset < 0 or offset % timeframe:
                raise MarketCollectionGovernanceError(f"D2U {boundary_name} is not aligned to bar interval")

    @property
    def periods(self) -> tuple[str, ...]:
        return tuple(sorted({descriptor.period for descriptor in self.descriptors}))

    @property
    def descriptor_fingerprints(self) -> tuple[str, ...]:
        return tuple(descriptor.fingerprint for descriptor in self.descriptors)

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def descriptor(self, *, symbol: str, period: str) -> BinanceSpotArchiveDescriptor:
        for item in self.descriptors:
            if item.instrument.symbol == symbol and item.period == period:
                return item
        raise KeyError((symbol, period))

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_version": self.plan_version,
            "collection_id": self.collection_id,
            "descriptors": [descriptor.to_dict() for descriptor in self.descriptors],
            "symbols": list(self.symbols),
            "interval": self.interval,
            "granularity": self.granularity,
            "collection_start": self.collection_start,
            "train_start": self.train_start,
            "development_start": self.development_start,
            "development_end": self.development_end,
            "warmup_bars": self.warmup_bars,
            "plan_policy": self.plan_policy,
            "stitch_policy": self.stitch_policy,
            "partition_policy": self.partition_policy,
            "final_holdout_descriptors_included": self.final_holdout_descriptors_included,
            "label_values_included": self.label_values_included,
            "prediction_values_included": self.prediction_values_included,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


class SQLiteHistoricalCollectionPlanRegistry:
    """Append-only durable D2U plan gate used before any network acquisition."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {PLAN_REGISTRY_TABLE} (
                    collection_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    canonical_json TEXT NOT NULL,
                    preregistered_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS {PLAN_REGISTRY_TABLE}_no_update
                BEFORE UPDATE ON {PLAN_REGISTRY_TABLE}
                BEGIN
                    SELECT RAISE(ABORT, 'OSS3D2U_APPEND_ONLY');
                END;
                CREATE TRIGGER IF NOT EXISTS {PLAN_REGISTRY_TABLE}_no_delete
                BEFORE DELETE ON {PLAN_REGISTRY_TABLE}
                BEGIN
                    SELECT RAISE(ABORT, 'OSS3D2U_APPEND_ONLY');
                END;
                """
            )

    def preregister(self, plan: HistoricalCollectionPlan, *, now: datetime) -> None:
        _require_aware(now, "now")
        canonical = _canonical_json(plan.to_dict())
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT fingerprint, canonical_json FROM {PLAN_REGISTRY_TABLE} WHERE collection_id = ?",
                (plan.collection_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    f"INSERT INTO {PLAN_REGISTRY_TABLE} (collection_id, fingerprint, canonical_json, preregistered_at) VALUES (?, ?, ?, ?)",
                    (
                        plan.collection_id,
                        plan.fingerprint,
                        canonical,
                        now.astimezone(timezone.utc).isoformat(),
                    ),
                )
                return
            if row != (plan.fingerprint, canonical):
                raise MarketCollectionGovernanceError("D2U collection id is already bound to different plan")

    def require_exact(self, plan: HistoricalCollectionPlan) -> None:
        canonical = _canonical_json(plan.to_dict())
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT fingerprint, canonical_json FROM {PLAN_REGISTRY_TABLE} WHERE collection_id = ?",
                (plan.collection_id,),
            ).fetchone()
        if row is None:
            raise MarketCollectionGovernanceError("durable D2U collection plan preregistration is required")
        if row != (plan.fingerprint, canonical):
            raise MarketCollectionIntegrityError("durable D2U collection plan differs from supplied plan")


@dataclass(frozen=True, slots=True)
class AcquiredSnapshotBinding:
    binding_version: str
    descriptor_fingerprint: str
    snapshot_artifact_hash: str
    acquisition_receipt_hash: str
    archive_sha256: str
    checksum_payload_sha256: str
    csv_payload_sha256: str
    normalized_dataset_hash: str

    def __post_init__(self) -> None:
        if self.binding_version != OSS3D2U_BINDING_VERSION:
            raise MarketCollectionIntegrityError("noncanonical D2U binding version")
        for name in (
            "descriptor_fingerprint",
            "snapshot_artifact_hash",
            "acquisition_receipt_hash",
            "archive_sha256",
            "checksum_payload_sha256",
            "csv_payload_sha256",
            "normalized_dataset_hash",
        ):
            _require_hash(getattr(self, name), name)

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, str]:
        return {
            "binding_version": self.binding_version,
            "descriptor_fingerprint": self.descriptor_fingerprint,
            "snapshot_artifact_hash": self.snapshot_artifact_hash,
            "acquisition_receipt_hash": self.acquisition_receipt_hash,
            "archive_sha256": self.archive_sha256,
            "checksum_payload_sha256": self.checksum_payload_sha256,
            "csv_payload_sha256": self.csv_payload_sha256,
            "normalized_dataset_hash": self.normalized_dataset_hash,
        }


@dataclass(frozen=True, slots=True)
class HistoricalCollectionAssemblyEvidence:
    evidence_version: str
    collection_id: str
    plan_fingerprint: str
    snapshot_bindings: tuple[AcquiredSnapshotBinding, ...]
    symbols: tuple[str, ...]
    interval: str
    collection_start: str
    train_start: str
    development_start: str
    development_end: str
    warmup_bars: int
    stitched_dataset_set_hash: str
    training_warmup_universe_hash: str
    training_universe_hash: str
    development_warmup_universe_hash: str
    development_universe_hash: str
    acquisition_binding_policy: str
    stitch_policy: str
    partition_policy: str
    exact_monthly_family_complete: bool
    exact_cross_file_continuity: bool
    exact_cross_asset_support: bool
    final_holdout_values_loaded: bool
    label_values_loaded: bool
    prediction_values_loaded: bool
    network_used_by_assembly: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D2U_EVIDENCE_VERSION:
            raise MarketCollectionIntegrityError("noncanonical D2U evidence version")
        _require_id(self.collection_id, "collection_id")
        for name in (
            "plan_fingerprint",
            "stitched_dataset_set_hash",
            "training_warmup_universe_hash",
            "training_universe_hash",
            "development_warmup_universe_hash",
            "development_universe_hash",
        ):
            _require_hash(getattr(self, name), name)
        if not self.snapshot_bindings:
            raise MarketCollectionIntegrityError("D2U evidence requires snapshot bindings")
        if len({binding.descriptor_fingerprint for binding in self.snapshot_bindings}) != len(self.snapshot_bindings):
            raise MarketCollectionIntegrityError("D2U evidence contains duplicate descriptor binding")
        if self.symbols != tuple(sorted(self.symbols)) or len(set(self.symbols)) != len(self.symbols):
            raise MarketCollectionIntegrityError("D2U evidence symbols are not canonical")
        if self.acquisition_binding_policy != ACQUISITION_BINDING_POLICY:
            raise MarketCollectionGovernanceError("D2U acquisition binding policy drifted")
        if self.stitch_policy != STITCH_POLICY or self.partition_policy != PARTITION_POLICY:
            raise MarketCollectionGovernanceError("D2U stitch/partition policy drifted")
        if not (
            self.exact_monthly_family_complete
            and self.exact_cross_file_continuity
            and self.exact_cross_asset_support
        ):
            raise MarketCollectionGovernanceError("D2U complete exact support is mandatory")
        if (
            self.final_holdout_values_loaded
            or self.label_values_loaded
            or self.prediction_values_loaded
            or self.network_used_by_assembly
        ):
            raise MarketCollectionGovernanceError("D2U assembly may contain raw WARMUP/TRAIN/DEVELOPMENT only")
        _deny_authority(
            self.execution_authorized,
            self.paper_execution_authorized,
            self.capital_authority,
            self.live_trading,
        )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_version": self.evidence_version,
            "collection_id": self.collection_id,
            "plan_fingerprint": self.plan_fingerprint,
            "snapshot_bindings": [binding.to_dict() for binding in self.snapshot_bindings],
            "symbols": list(self.symbols),
            "interval": self.interval,
            "collection_start": self.collection_start,
            "train_start": self.train_start,
            "development_start": self.development_start,
            "development_end": self.development_end,
            "warmup_bars": self.warmup_bars,
            "stitched_dataset_set_hash": self.stitched_dataset_set_hash,
            "training_warmup_universe_hash": self.training_warmup_universe_hash,
            "training_universe_hash": self.training_universe_hash,
            "development_warmup_universe_hash": self.development_warmup_universe_hash,
            "development_universe_hash": self.development_universe_hash,
            "acquisition_binding_policy": self.acquisition_binding_policy,
            "stitch_policy": self.stitch_policy,
            "partition_policy": self.partition_policy,
            "exact_monthly_family_complete": self.exact_monthly_family_complete,
            "exact_cross_file_continuity": self.exact_cross_file_continuity,
            "exact_cross_asset_support": self.exact_cross_asset_support,
            "final_holdout_values_loaded": self.final_holdout_values_loaded,
            "label_values_loaded": self.label_values_loaded,
            "prediction_values_loaded": self.prediction_values_loaded,
            "network_used_by_assembly": self.network_used_by_assembly,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class HistoricalResearchPartitionMaterial:
    material_version: str
    training_warmup: AlignedMarketUniverse
    training: AlignedMarketUniverse
    development_warmup: AlignedMarketUniverse
    development: AlignedMarketUniverse
    evidence: HistoricalCollectionAssemblyEvidence

    def __post_init__(self) -> None:
        if self.material_version != OSS3D2U_MATERIAL_VERSION:
            raise MarketCollectionIntegrityError("noncanonical D2U material version")
        universes = (
            self.training_warmup,
            self.training,
            self.development_warmup,
            self.development,
        )
        if any(not isinstance(item, AlignedMarketUniverse) for item in universes):
            raise TypeError("D2U material fields must be AlignedMarketUniverse")
        if any(item.symbols != self.evidence.symbols for item in universes):
            raise MarketCollectionIntegrityError("D2U material symbols differ across partitions")
        if any(item.timeframe_seconds != self.training.timeframe_seconds for item in universes):
            raise MarketCollectionIntegrityError("D2U material timeframe differs across partitions")
        if self.training_warmup.bar_count != self.evidence.warmup_bars:
            raise MarketCollectionGovernanceError("D2U TRAIN warmup count drifted")
        if self.development_warmup.bar_count != self.evidence.warmup_bars:
            raise MarketCollectionGovernanceError("D2U DEVELOPMENT warmup count drifted")
        if self.training_warmup.timestamps[-1] + timedelta(seconds=self.training_warmup.timeframe_seconds) != self.training.timestamps[0]:
            raise MarketCollectionIntegrityError("D2U TRAIN warmup does not end exactly at TRAIN start")
        if self.training.timestamps[-1] + timedelta(seconds=self.training.timeframe_seconds) != self.development.timestamps[0]:
            raise MarketCollectionIntegrityError("D2U TRAIN does not end exactly at DEVELOPMENT start")
        if self.development_warmup.timestamps[-1] + timedelta(seconds=self.development_warmup.timeframe_seconds) != self.development.timestamps[0]:
            raise MarketCollectionIntegrityError("D2U DEVELOPMENT warmup does not end exactly at DEVELOPMENT start")
        if self.training_warmup.universe_hash != self.evidence.training_warmup_universe_hash:
            raise MarketCollectionIntegrityError("D2U TRAIN warmup hash differs from evidence")
        if self.training.universe_hash != self.evidence.training_universe_hash:
            raise MarketCollectionIntegrityError("D2U TRAIN universe hash differs from evidence")
        if self.development_warmup.universe_hash != self.evidence.development_warmup_universe_hash:
            raise MarketCollectionIntegrityError("D2U DEVELOPMENT warmup hash differs from evidence")
        if self.development.universe_hash != self.evidence.development_universe_hash:
            raise MarketCollectionIntegrityError("D2U DEVELOPMENT universe hash differs from evidence")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "material_version": self.material_version,
                "evidence_fingerprint": self.evidence.fingerprint,
                "training_warmup_universe_hash": self.training_warmup.universe_hash,
                "training_universe_hash": self.training.universe_hash,
                "development_warmup_universe_hash": self.development_warmup.universe_hash,
                "development_universe_hash": self.development.universe_hash,
            }
        )


def assemble_historical_collection(
    *,
    plan: HistoricalCollectionPlan,
    artifacts: Iterable[HistoricalMarketSnapshotArtifact],
    acquisition_receipt_hashes: Mapping[str, str],
) -> HistoricalResearchPartitionMaterial:
    """Assemble one complete preregistered D2T family into raw research partitions."""
    if not isinstance(plan, HistoricalCollectionPlan):
        raise TypeError("plan must be HistoricalCollectionPlan")
    artifact_tuple = tuple(artifacts)
    if len(artifact_tuple) != len(plan.descriptors):
        raise MarketCollectionIntegrityError("D2U requires the complete planned snapshot family")
    by_descriptor: dict[str, HistoricalMarketSnapshotArtifact] = {}
    for artifact in artifact_tuple:
        if not isinstance(artifact, HistoricalMarketSnapshotArtifact):
            raise TypeError("artifacts must contain HistoricalMarketSnapshotArtifact")
        if artifact.artifact_version != OSS3D2T_ARTIFACT_VERSION:
            raise MarketCollectionIntegrityError("D2U snapshot artifact version drifted")
        fingerprint = artifact.manifest.descriptor_fingerprint
        if fingerprint in by_descriptor:
            raise MarketCollectionIntegrityError("D2U duplicate snapshot descriptor")
        by_descriptor[fingerprint] = artifact
    planned = set(plan.descriptor_fingerprints)
    if set(by_descriptor) != planned:
        raise MarketCollectionIntegrityError("D2U acquired snapshots differ from preregistered descriptors")
    if set(acquisition_receipt_hashes) != planned:
        raise MarketCollectionIntegrityError("D2U requires one acquisition receipt per descriptor")
    for value in acquisition_receipt_hashes.values():
        _require_hash(value, "acquisition_receipt_hash")

    stitched: list[MarketDataset] = []
    for symbol in plan.symbols:
        symbol_artifacts = [
            by_descriptor[plan.descriptor(symbol=symbol, period=period).fingerprint]
            for period in plan.periods
        ]
        first_instrument = symbol_artifacts[0].instrument
        bars = []
        previous_end: datetime | None = None
        artifact_hashes = []
        for artifact in symbol_artifacts:
            if artifact.instrument != first_instrument:
                raise MarketCollectionIntegrityError("D2U instrument metadata drifted across months")
            dataset = artifact.dataset
            if previous_end is not None and dataset.started_at != previous_end:
                raise MarketCollectionIntegrityError("D2U monthly snapshots contain gap or overlap")
            if dataset.gap_indexes():
                raise MarketCollectionIntegrityError("D2U monthly snapshot contains internal gap")
            bars.extend(dataset.bars)
            previous_end = dataset.ended_at
            artifact_hashes.append(artifact.artifact_hash)
        source = (
            f"OSS3D2U:{plan.fingerprint}:symbol={symbol}:"
            f"snapshots={_hash(artifact_hashes)}"
        )
        dataset = MarketDataset(
            instrument=first_instrument,
            bars=tuple(bars),
            source=source,
        )
        if dataset.gap_indexes():
            raise MarketCollectionIntegrityError("D2U stitched dataset contains gap")
        stitched.append(dataset)

    full = AlignedMarketUniverse.from_datasets(
        datasets=tuple(stitched),
        universe_name=f"{plan.collection_id}#full-raw",
    )
    collection_start = _parse_utc(plan.collection_start, "collection_start")
    train_start = _parse_utc(plan.train_start, "train_start")
    development_start = _parse_utc(plan.development_start, "development_start")
    development_end = _parse_utc(plan.development_end, "development_end")
    if full.timestamps[0] != collection_start:
        raise MarketCollectionIntegrityError("D2U stitched collection start differs from plan")
    if full.timestamps[-1] + timedelta(seconds=full.timeframe_seconds) != development_end:
        raise MarketCollectionIntegrityError("D2U stitched collection end differs from plan")

    train_index = _timestamp_index(full.timestamps, train_start, "train_start")
    development_index = _timestamp_index(full.timestamps, development_start, "development_start")
    if train_index != plan.warmup_bars:
        raise MarketCollectionGovernanceError("D2U exact warmup position drifted")
    if development_index - plan.warmup_bars < train_index:
        raise MarketCollectionGovernanceError("D2U TRAIN is too short for DEVELOPMENT warmup")

    training_warmup = _slice_universe(
        full,
        0,
        train_index,
        name=f"{plan.collection_id}#train-warmup",
    )
    training = _slice_universe(
        full,
        train_index,
        development_index,
        name=f"{plan.collection_id}#train",
    )
    development_warmup = _slice_universe(
        full,
        development_index - plan.warmup_bars,
        development_index,
        name=f"{plan.collection_id}#development-warmup",
    )
    development = _slice_universe(
        full,
        development_index,
        full.bar_count,
        name=f"{plan.collection_id}#development",
    )

    bindings = tuple(
        AcquiredSnapshotBinding(
            binding_version=OSS3D2U_BINDING_VERSION,
            descriptor_fingerprint=descriptor.fingerprint,
            snapshot_artifact_hash=by_descriptor[descriptor.fingerprint].artifact_hash,
            acquisition_receipt_hash=acquisition_receipt_hashes[descriptor.fingerprint],
            archive_sha256=by_descriptor[descriptor.fingerprint].manifest.archive_sha256,
            checksum_payload_sha256=by_descriptor[descriptor.fingerprint].manifest.checksum_payload_sha256,
            csv_payload_sha256=by_descriptor[descriptor.fingerprint].manifest.csv_payload_sha256,
            normalized_dataset_hash=by_descriptor[descriptor.fingerprint].manifest.normalized_dataset_hash,
        )
        for descriptor in plan.descriptors
    )
    stitched_dataset_set_hash = _hash(
        [[dataset.instrument.symbol, dataset.dataset_hash] for dataset in full.datasets]
    )
    evidence = HistoricalCollectionAssemblyEvidence(
        evidence_version=OSS3D2U_EVIDENCE_VERSION,
        collection_id=plan.collection_id,
        plan_fingerprint=plan.fingerprint,
        snapshot_bindings=bindings,
        symbols=plan.symbols,
        interval=plan.interval,
        collection_start=collection_start.isoformat(),
        train_start=train_start.isoformat(),
        development_start=development_start.isoformat(),
        development_end=development_end.isoformat(),
        warmup_bars=plan.warmup_bars,
        stitched_dataset_set_hash=stitched_dataset_set_hash,
        training_warmup_universe_hash=training_warmup.universe_hash,
        training_universe_hash=training.universe_hash,
        development_warmup_universe_hash=development_warmup.universe_hash,
        development_universe_hash=development.universe_hash,
        acquisition_binding_policy=ACQUISITION_BINDING_POLICY,
        stitch_policy=STITCH_POLICY,
        partition_policy=PARTITION_POLICY,
        exact_monthly_family_complete=True,
        exact_cross_file_continuity=True,
        exact_cross_asset_support=True,
        final_holdout_values_loaded=False,
        label_values_loaded=False,
        prediction_values_loaded=False,
        network_used_by_assembly=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )
    return HistoricalResearchPartitionMaterial(
        material_version=OSS3D2U_MATERIAL_VERSION,
        training_warmup=training_warmup,
        training=training,
        development_warmup=development_warmup,
        development=development,
        evidence=evidence,
    )


def canonical_oss3d2u_collection_plan() -> HistoricalCollectionPlan:
    """Finite real-data family frozen before any D2U network acquisition."""
    instruments = {
        symbol: InstrumentMetadata(
            symbol=symbol,
            venue="BINANCE_SPOT",
            quote_currency="USDT",
            price_tick=Decimal("0.00000001"),
            quantity_step=Decimal("0.00000001"),
        )
        for symbol in CANONICAL_SYMBOLS
    }
    periods = _month_range(CANONICAL_FIRST_MONTH, CANONICAL_LAST_MONTH)
    descriptors = tuple(
        BinanceSpotArchiveDescriptor.monthly(
            instrument=instruments[symbol],
            interval=CANONICAL_INTERVAL,
            month=period,
        )
        for period in periods
        for symbol in CANONICAL_SYMBOLS
    )
    return HistoricalCollectionPlan(
        plan_version=OSS3D2U_PLAN_VERSION,
        collection_id=CANONICAL_COLLECTION_ID,
        descriptors=descriptors,
        symbols=CANONICAL_SYMBOLS,
        interval=CANONICAL_INTERVAL,
        granularity="monthly",
        collection_start="2023-01-01T00:00:00+00:00",
        train_start=CANONICAL_TRAIN_START.isoformat(),
        development_start=CANONICAL_DEVELOPMENT_START.isoformat(),
        development_end=CANONICAL_DEVELOPMENT_END.isoformat(),
        warmup_bars=CANONICAL_WARMUP_BARS,
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


def _slice_universe(
    universe: AlignedMarketUniverse,
    start: int,
    stop: int,
    *,
    name: str,
) -> AlignedMarketUniverse:
    if not 0 <= start < stop <= universe.bar_count:
        raise MarketCollectionIntegrityError("D2U universe slice is invalid")
    return AlignedMarketUniverse.from_datasets(
        datasets=tuple(dataset.slice(start, stop) for dataset in universe.datasets),
        universe_name=name,
    )


def _timestamp_index(timestamps: tuple[datetime, ...], target: datetime, name: str) -> int:
    try:
        return timestamps.index(target)
    except ValueError as exc:
        raise MarketCollectionIntegrityError(f"D2U {name} is absent from exact collection support") from exc


def _descriptor_sort_key(descriptor: BinanceSpotArchiveDescriptor) -> tuple[str, str]:
    return descriptor.period, descriptor.instrument.symbol


def _months_are_contiguous(periods: tuple[str, ...]) -> bool:
    if not periods:
        return False
    return periods == _month_range(periods[0], periods[-1])


def _month_range(first: str, last: str) -> tuple[str, ...]:
    first_year, first_month = _parse_month(first)
    last_year, last_month = _parse_month(last)
    first_index = first_year * 12 + first_month - 1
    last_index = last_year * 12 + last_month - 1
    if first_index > last_index:
        raise ValueError("first month must not be after last month")
    result = []
    for index in range(first_index, last_index + 1):
        year, zero_month = divmod(index, 12)
        result.append(f"{year:04d}-{zero_month + 1:02d}")
    return tuple(result)


def _parse_month(value: str) -> tuple[int, int]:
    match = _MONTH_RE.fullmatch(value)
    if match is None:
        raise ValueError("D2U month must be YYYY-MM")
    year, month = (int(item) for item in match.groups())
    if year < 1970 or not 1 <= month <= 12:
        raise ValueError("invalid D2U month")
    return year, month


def _parse_utc(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MarketCollectionIntegrityError(f"invalid D2U {name}") from exc
    _require_aware(parsed, name)
    normalized = parsed.astimezone(timezone.utc)
    if parsed != normalized or value != normalized.isoformat():
        raise MarketCollectionIntegrityError(f"D2U {name} must be canonical UTC ISO-8601")
    return normalized


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase sha256")


def _require_id(value: str, name: str) -> None:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")


def _deny_authority(
    execution_authorized: bool,
    paper_execution_authorized: bool,
    capital_authority: str,
    live_trading: str,
) -> None:
    if execution_authorized or paper_execution_authorized:
        raise MarketCollectionGovernanceError("D2U execution authority is forbidden")
    if capital_authority != "NONE":
        raise MarketCollectionGovernanceError("D2U capital authority must be NONE")
    if live_trading != "BLOCKED":
        raise MarketCollectionGovernanceError("D2U LIVE trading must remain BLOCKED")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()
