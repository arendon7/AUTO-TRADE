"""OSS-3D2T immutable historical market-data snapshot.

D2T is an offline normalization/integrity boundary for already-downloaded
Binance Spot public-data archives.  It does not perform network I/O.  Given the
exact ZIP bytes plus the provider-published CHECKSUM text, it:

* validates the provider path/filename contract;
* validates SHA-256 before opening the archive;
* requires exactly one expected CSV member;
* handles the documented Binance Spot timestamp-unit transition from
  milliseconds to microseconds on 2025-01-01;
* validates exact kline open/close-time geometry and full requested coverage;
* converts the immutable provider rows into AUTO-TRADE MarketDataset bars; and
* emits a canonical snapshot artifact binding raw archive, checksum, CSV and
  normalized dataset identities.

The caller supplies InstrumentMetadata only for research serialization and
stable cross-partition identity.  D2T does not certify Binance trading filters,
exchange execution precision, broker authority or live tradability.

Research only: no model, label, Qlib, FINAL_HOLDOUT, broker, OMS, Safety,
OrderIntent, PAPER, capital or LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO, StringIO
import csv
import json
from pathlib import Path
import re
import zipfile
from typing import Iterable, Mapping, Sequence

from .external_data import FIXED_INTERVAL_MS
from .market import Bar, InstrumentMetadata, MarketDataset
from .universe import AlignedMarketUniverse


OSS3D2T_DESCRIPTOR_VERSION = "OSS3D2T_BINANCE_SPOT_ARCHIVE_DESCRIPTOR_V1"
OSS3D2T_MANIFEST_VERSION = "OSS3D2T_IMMUTABLE_MARKET_SNAPSHOT_MANIFEST_V1"
OSS3D2T_ARTIFACT_VERSION = "OSS3D2T_IMMUTABLE_MARKET_SNAPSHOT_ARTIFACT_V1"
OSS3D2T_SET_EVIDENCE_VERSION = "OSS3D2T_ALIGNED_SNAPSHOT_SET_EVIDENCE_V1"
PROVIDER_ID = "BINANCE_SPOT_PUBLIC_DATA_ARCHIVE"
PROVIDER_BASE_URL = "https://data.binance.vision"
PROVIDER_MARKET = "SPOT"
PROVIDER_DATA_KIND = "klines"
TIMESTAMP_TRANSITION_UTC = datetime(2025, 1, 1, tzinfo=timezone.utc)
TIMESTAMP_MS = "MILLISECONDS_PRE_2025_01_01"
TIMESTAMP_US = "MICROSECONDS_FROM_2025_01_01"
INSTRUMENT_METADATA_POLICY = "RESEARCH_SERIALIZATION_ONLY_NOT_TRADING_FILTER_AUTHORITY_V1"
COVERAGE_POLICY = "EXACT_FULL_ARCHIVE_PERIOD_NO_GAPS_NO_DUPLICATES_V1"
ZIP_MEMBER_POLICY = "EXACT_SINGLE_EXPECTED_CSV_MEMBER_V1"
CHECKSUM_POLICY = "PROVIDER_SHA256_MUST_MATCH_ARCHIVE_BYTES_BEFORE_ZIP_PARSE_V1"
NORMALIZATION_POLICY = "OPEN_TIME_TO_UTC_BAR_START_FIXED_SECONDS_V1"
MAX_ARCHIVE_BYTES = 128_000_000
MAX_CSV_BYTES = 512_000_000
MAX_ROWS = 2_000_000
MAX_ARTIFACT_BYTES = 768_000_000

# D2T v1 intentionally excludes variable or archive-boundary-sensitive fixed
# intervals (3d/1w) and variable-month 1mo.  Every accepted interval divides a
# UTC day exactly, so daily/monthly coverage has one unambiguous geometry.
ARCHIVE_FIXED_INTERVALS = frozenset(
    {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"}
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,24}$")
_PERIOD_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_PERIOD_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_CHECKSUM_RE = re.compile(r"^([0-9a-fA-F]{64})[ \t]+\*?([^\r\n]+?)[ \t]*$")
_ALLOWED_ZIP_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_NORMALIZED_ROW_KEYS = frozenset(
    {"started_at", "open", "high", "low", "close", "volume"}
)

SEMANTIC_FILES = (
    "src/autotrade/research/oss3_market_snapshot.py",
    "src/autotrade/research/external_data.py",
    "src/autotrade/research/market.py",
    "src/autotrade/research/universe.py",
)


class MarketSnapshotError(RuntimeError):
    """Base OSS-3D2T failure."""


class MarketSnapshotIntegrityError(MarketSnapshotError):
    """Provider bytes, checksum, schema or normalized identity drifted."""


class MarketSnapshotGovernanceError(MarketSnapshotError):
    """Requested snapshot is outside strict research-only D2T policy."""


@dataclass(frozen=True, slots=True)
class BinanceSpotArchiveDescriptor:
    descriptor_version: str
    instrument: InstrumentMetadata
    interval: str
    granularity: str
    period: str
    instrument_metadata_policy: str = INSTRUMENT_METADATA_POLICY

    def __post_init__(self) -> None:
        if self.descriptor_version != OSS3D2T_DESCRIPTOR_VERSION:
            raise MarketSnapshotIntegrityError("noncanonical D2T descriptor version")
        if not isinstance(self.instrument, InstrumentMetadata):
            raise TypeError("instrument must be InstrumentMetadata")
        if self.instrument.venue != "BINANCE_SPOT":
            raise MarketSnapshotGovernanceError("D2T v1 venue must be BINANCE_SPOT")
        if not _SYMBOL_RE.fullmatch(self.instrument.symbol):
            raise MarketSnapshotGovernanceError("invalid Binance Spot archive symbol")
        if self.interval not in ARCHIVE_FIXED_INTERVALS:
            raise MarketSnapshotGovernanceError("D2T v1 interval is unsupported or boundary-sensitive")
        if self.interval not in FIXED_INTERVAL_MS:
            raise MarketSnapshotIntegrityError("D2T interval missing from canonical R3 interval registry")
        if FIXED_INTERVAL_MS[self.interval] % 1000:
            raise MarketSnapshotGovernanceError("D2T v1 requires whole-second bars")
        if 86_400_000 % FIXED_INTERVAL_MS[self.interval]:
            raise MarketSnapshotGovernanceError("D2T v1 interval must divide a UTC day exactly")
        if self.granularity not in {"daily", "monthly"}:
            raise MarketSnapshotGovernanceError("D2T granularity must be daily or monthly")
        _parse_period(self.granularity, self.period)
        if self.instrument_metadata_policy != INSTRUMENT_METADATA_POLICY:
            raise MarketSnapshotGovernanceError("D2T instrument metadata cannot imply trading-filter authority")

    @classmethod
    def daily(
        cls,
        *,
        instrument: InstrumentMetadata,
        interval: str,
        day: str,
    ) -> "BinanceSpotArchiveDescriptor":
        return cls(
            descriptor_version=OSS3D2T_DESCRIPTOR_VERSION,
            instrument=instrument,
            interval=interval,
            granularity="daily",
            period=day,
        )

    @classmethod
    def monthly(
        cls,
        *,
        instrument: InstrumentMetadata,
        interval: str,
        month: str,
    ) -> "BinanceSpotArchiveDescriptor":
        return cls(
            descriptor_version=OSS3D2T_DESCRIPTOR_VERSION,
            instrument=instrument,
            interval=interval,
            granularity="monthly",
            period=month,
        )

    @property
    def period_start(self) -> datetime:
        return _parse_period(self.granularity, self.period)[0]

    @property
    def period_end(self) -> datetime:
        return _parse_period(self.granularity, self.period)[1]

    @property
    def timestamp_unit_policy(self) -> str:
        return TIMESTAMP_US if self.period_start >= TIMESTAMP_TRANSITION_UTC else TIMESTAMP_MS

    @property
    def timestamp_unit_multiplier_per_millisecond(self) -> int:
        return 1000 if self.timestamp_unit_policy == TIMESTAMP_US else 1

    @property
    def interval_seconds(self) -> int:
        return FIXED_INTERVAL_MS[self.interval] // 1000

    @property
    def expected_rows(self) -> int:
        seconds = int((self.period_end - self.period_start).total_seconds())
        if seconds % self.interval_seconds:
            raise MarketSnapshotIntegrityError("D2T period is not exactly divisible by interval")
        result = seconds // self.interval_seconds
        if not 1 <= result <= MAX_ROWS:
            raise MarketSnapshotGovernanceError("D2T expected row count is outside bound")
        return result

    @property
    def archive_filename(self) -> str:
        return f"{self.instrument.symbol}-{self.interval}-{self.period}.zip"

    @property
    def csv_filename(self) -> str:
        return f"{self.instrument.symbol}-{self.interval}-{self.period}.csv"

    @property
    def checksum_filename(self) -> str:
        return f"{self.archive_filename}.CHECKSUM"

    @property
    def relative_archive_path(self) -> str:
        return (
            f"/data/spot/{self.granularity}/klines/{self.instrument.symbol}/"
            f"{self.interval}/{self.archive_filename}"
        )

    @property
    def relative_checksum_path(self) -> str:
        return self.relative_archive_path + ".CHECKSUM"

    @property
    def archive_url(self) -> str:
        return PROVIDER_BASE_URL + self.relative_archive_path

    @property
    def checksum_url(self) -> str:
        return PROVIDER_BASE_URL + self.relative_checksum_path

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "descriptor_version": self.descriptor_version,
            "provider_id": PROVIDER_ID,
            "provider_market": PROVIDER_MARKET,
            "provider_data_kind": PROVIDER_DATA_KIND,
            "symbol": self.instrument.symbol,
            "venue": self.instrument.venue,
            "quote_currency": self.instrument.quote_currency,
            "price_tick": str(self.instrument.price_tick),
            "quantity_step": str(self.instrument.quantity_step),
            "instrument_metadata_policy": self.instrument_metadata_policy,
            "interval": self.interval,
            "granularity": self.granularity,
            "period": self.period,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "timestamp_unit_policy": self.timestamp_unit_policy,
            "expected_rows": self.expected_rows,
            "archive_filename": self.archive_filename,
            "csv_filename": self.csv_filename,
            "checksum_filename": self.checksum_filename,
            "relative_archive_path": self.relative_archive_path,
            "relative_checksum_path": self.relative_checksum_path,
        }


@dataclass(frozen=True, slots=True)
class HistoricalMarketSnapshotManifest:
    manifest_version: str
    producer_code_hash: str
    provider_id: str
    provider_base_url: str
    provider_market: str
    provider_data_kind: str
    descriptor_fingerprint: str
    symbol: str
    interval: str
    granularity: str
    period: str
    timestamp_unit_policy: str
    archive_filename: str
    checksum_filename: str
    csv_filename: str
    relative_archive_path: str
    archive_sha256: str
    provider_checksum_sha256: str
    checksum_payload_sha256: str
    csv_payload_sha256: str
    normalized_row_payload_sha256: str
    normalized_dataset_hash: str
    period_start: str
    period_end: str
    expected_rows: int
    received_rows: int
    coverage_policy: str
    zip_member_policy: str
    checksum_policy: str
    normalization_policy: str
    instrument_metadata_policy: str
    network_used_by_normalizer: bool
    provider_credentials_used: bool
    trading_filters_certified: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.manifest_version != OSS3D2T_MANIFEST_VERSION:
            raise MarketSnapshotIntegrityError("noncanonical D2T manifest version")
        if self.provider_id != PROVIDER_ID or self.provider_base_url != PROVIDER_BASE_URL:
            raise MarketSnapshotIntegrityError("D2T provider identity drifted")
        if self.provider_market != PROVIDER_MARKET or self.provider_data_kind != PROVIDER_DATA_KIND:
            raise MarketSnapshotIntegrityError("D2T provider market/data kind drifted")
        for name in (
            "producer_code_hash",
            "descriptor_fingerprint",
            "archive_sha256",
            "provider_checksum_sha256",
            "checksum_payload_sha256",
            "csv_payload_sha256",
            "normalized_row_payload_sha256",
            "normalized_dataset_hash",
        ):
            _require_hash(getattr(self, name), name)
        if self.archive_sha256 != self.provider_checksum_sha256:
            raise MarketSnapshotIntegrityError("provider checksum does not equal archive bytes")
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise MarketSnapshotIntegrityError("invalid D2T manifest symbol")
        if self.interval not in ARCHIVE_FIXED_INTERVALS:
            raise MarketSnapshotIntegrityError("invalid D2T manifest interval")
        if self.granularity not in {"daily", "monthly"}:
            raise MarketSnapshotIntegrityError("invalid D2T manifest granularity")
        start = _parse_canonical_utc(self.period_start, "period_start")
        end = _parse_canonical_utc(self.period_end, "period_end")
        if not start < end:
            raise MarketSnapshotIntegrityError("D2T manifest period is invalid")
        expected_unit = TIMESTAMP_US if start >= TIMESTAMP_TRANSITION_UTC else TIMESTAMP_MS
        if self.timestamp_unit_policy != expected_unit:
            raise MarketSnapshotIntegrityError("D2T timestamp-unit policy differs from provider epoch")
        if not isinstance(self.expected_rows, int) or isinstance(self.expected_rows, bool) or self.expected_rows < 1:
            raise MarketSnapshotIntegrityError("invalid D2T expected_rows")
        if self.received_rows != self.expected_rows:
            raise MarketSnapshotIntegrityError("D2T snapshot requires exact full coverage")
        if self.coverage_policy != COVERAGE_POLICY:
            raise MarketSnapshotGovernanceError("D2T coverage policy drifted")
        if self.zip_member_policy != ZIP_MEMBER_POLICY:
            raise MarketSnapshotGovernanceError("D2T ZIP member policy drifted")
        if self.checksum_policy != CHECKSUM_POLICY:
            raise MarketSnapshotGovernanceError("D2T checksum policy drifted")
        if self.normalization_policy != NORMALIZATION_POLICY:
            raise MarketSnapshotGovernanceError("D2T normalization policy drifted")
        if self.instrument_metadata_policy != INSTRUMENT_METADATA_POLICY:
            raise MarketSnapshotGovernanceError("D2T instrument metadata policy drifted")
        if self.network_used_by_normalizer or self.provider_credentials_used or self.trading_filters_certified:
            raise MarketSnapshotGovernanceError("D2T normalizer cannot claim network/credentials/trading filters")
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
            "manifest_version": self.manifest_version,
            "producer_code_hash": self.producer_code_hash,
            "provider_id": self.provider_id,
            "provider_base_url": self.provider_base_url,
            "provider_market": self.provider_market,
            "provider_data_kind": self.provider_data_kind,
            "descriptor_fingerprint": self.descriptor_fingerprint,
            "symbol": self.symbol,
            "interval": self.interval,
            "granularity": self.granularity,
            "period": self.period,
            "timestamp_unit_policy": self.timestamp_unit_policy,
            "archive_filename": self.archive_filename,
            "checksum_filename": self.checksum_filename,
            "csv_filename": self.csv_filename,
            "relative_archive_path": self.relative_archive_path,
            "archive_sha256": self.archive_sha256,
            "provider_checksum_sha256": self.provider_checksum_sha256,
            "checksum_payload_sha256": self.checksum_payload_sha256,
            "csv_payload_sha256": self.csv_payload_sha256,
            "normalized_row_payload_sha256": self.normalized_row_payload_sha256,
            "normalized_dataset_hash": self.normalized_dataset_hash,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "expected_rows": self.expected_rows,
            "received_rows": self.received_rows,
            "coverage_policy": self.coverage_policy,
            "zip_member_policy": self.zip_member_policy,
            "checksum_policy": self.checksum_policy,
            "normalization_policy": self.normalization_policy,
            "instrument_metadata_policy": self.instrument_metadata_policy,
            "network_used_by_normalizer": self.network_used_by_normalizer,
            "provider_credentials_used": self.provider_credentials_used,
            "trading_filters_certified": self.trading_filters_certified,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class HistoricalMarketSnapshotArtifact:
    artifact_version: str
    manifest: HistoricalMarketSnapshotManifest
    instrument: InstrumentMetadata
    normalized_rows: tuple[tuple[str, str, str, str, str, str], ...]
    artifact_hash: str

    def __post_init__(self) -> None:
        if self.artifact_version != OSS3D2T_ARTIFACT_VERSION:
            raise MarketSnapshotIntegrityError("noncanonical D2T artifact version")
        _require_hash(self.artifact_hash, "artifact_hash")
        if not isinstance(self.instrument, InstrumentMetadata):
            raise TypeError("D2T artifact instrument must be InstrumentMetadata")
        if self.instrument.symbol != self.manifest.symbol:
            raise MarketSnapshotIntegrityError("D2T artifact instrument symbol differs from manifest")
        rows = _validate_normalized_rows(self.normalized_rows)
        if len(rows) != self.manifest.received_rows:
            raise MarketSnapshotIntegrityError("D2T normalized row count differs from manifest")
        if _normalized_rows_hash(rows) != self.manifest.normalized_row_payload_sha256:
            raise MarketSnapshotIntegrityError("D2T normalized row payload hash mismatch")
        dataset = _dataset_from_normalized_rows(
            instrument=self.instrument,
            rows=rows,
            source=self.provenance,
            interval=self.manifest.interval,
        )
        if dataset.dataset_hash != self.manifest.normalized_dataset_hash:
            raise MarketSnapshotIntegrityError("D2T normalized dataset hash mismatch")
        _validate_normalized_coverage(
            dataset=dataset,
            start=_parse_canonical_utc(self.manifest.period_start, "period_start"),
            end=_parse_canonical_utc(self.manifest.period_end, "period_end"),
            interval=self.manifest.interval,
        )
        expected_hash = _artifact_hash(
            artifact_version=self.artifact_version,
            manifest=self.manifest,
            instrument=self.instrument,
            normalized_rows=rows,
        )
        if expected_hash != self.artifact_hash:
            raise MarketSnapshotIntegrityError("D2T artifact hash mismatch")

    @property
    def provenance(self) -> str:
        return (
            f"{PROVIDER_ID}:archive={self.manifest.archive_sha256}:"
            f"csv={self.manifest.csv_payload_sha256}:descriptor={self.manifest.descriptor_fingerprint}"
        )

    @property
    def dataset(self) -> MarketDataset:
        return _dataset_from_normalized_rows(
            instrument=self.instrument,
            rows=self.normalized_rows,
            source=self.provenance,
            interval=self.manifest.interval,
        )

    @property
    def fingerprint(self) -> str:
        return self.artifact_hash

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_version": self.artifact_version,
            "manifest": self.manifest.to_dict(),
            "instrument": _instrument_dict(self.instrument),
            "normalized_rows": [list(row) for row in self.normalized_rows],
            "artifact_hash": self.artifact_hash,
        }

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = _canonical_json_bytes(self.to_dict()) + b"\n"
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise MarketSnapshotGovernanceError("D2T artifact exceeds size limit")
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(raw)
        temporary.replace(target)

    @classmethod
    def read(cls, path: str | Path) -> "HistoricalMarketSnapshotArtifact":
        target = Path(path)
        if not target.is_file():
            raise MarketSnapshotIntegrityError("D2T artifact does not exist")
        if target.stat().st_size > MAX_ARTIFACT_BYTES:
            raise MarketSnapshotGovernanceError("D2T artifact exceeds size limit")
        try:
            raw = target.read_bytes()
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_object)
        except MarketSnapshotError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MarketSnapshotIntegrityError("D2T artifact is not canonical UTF-8 JSON") from exc
        if raw != _canonical_json_bytes(document) + b"\n":
            raise MarketSnapshotIntegrityError("D2T artifact serialization is not canonical")
        return _artifact_from_mapping(document)

    def verify_source(
        self,
        *,
        descriptor: BinanceSpotArchiveDescriptor,
        archive_bytes: bytes,
        checksum_text: str,
    ) -> None:
        rebuilt = build_binance_spot_archive_snapshot(
            descriptor=descriptor,
            archive_bytes=archive_bytes,
            checksum_text=checksum_text,
        )
        if rebuilt.artifact_hash != self.artifact_hash:
            raise MarketSnapshotIntegrityError("D2T source bytes do not reproduce snapshot artifact")


@dataclass(frozen=True, slots=True)
class AlignedSnapshotSetEvidence:
    evidence_version: str
    snapshot_artifact_hashes: tuple[str, ...]
    symbols: tuple[str, ...]
    interval: str
    period_start: str
    period_end: str
    dataset_set_hash: str
    aligned_universe_material_hash: str
    exact_common_support: bool
    network_used: bool
    provider_credentials_used: bool
    trading_filters_certified: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.evidence_version != OSS3D2T_SET_EVIDENCE_VERSION:
            raise MarketSnapshotIntegrityError("noncanonical D2T set evidence version")
        if not self.snapshot_artifact_hashes or len(self.snapshot_artifact_hashes) < 2:
            raise MarketSnapshotGovernanceError("D2T aligned snapshot set requires at least two assets")
        for value in self.snapshot_artifact_hashes:
            _require_hash(value, "snapshot_artifact_hash")
        if tuple(sorted(self.symbols)) != self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise MarketSnapshotIntegrityError("D2T set symbols must be canonical and unique")
        if self.interval not in ARCHIVE_FIXED_INTERVALS:
            raise MarketSnapshotIntegrityError("D2T set interval is unsupported")
        _parse_canonical_utc(self.period_start, "period_start")
        _parse_canonical_utc(self.period_end, "period_end")
        _require_hash(self.dataset_set_hash, "dataset_set_hash")
        _require_hash(self.aligned_universe_material_hash, "aligned_universe_material_hash")
        if not self.exact_common_support:
            raise MarketSnapshotGovernanceError("D2T set requires exact common support")
        if self.network_used or self.provider_credentials_used or self.trading_filters_certified:
            raise MarketSnapshotGovernanceError("D2T set cannot imply network/credentials/trading authority")
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
            "snapshot_artifact_hashes": list(self.snapshot_artifact_hashes),
            "symbols": list(self.symbols),
            "interval": self.interval,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "dataset_set_hash": self.dataset_set_hash,
            "aligned_universe_material_hash": self.aligned_universe_material_hash,
            "exact_common_support": self.exact_common_support,
            "network_used": self.network_used,
            "provider_credentials_used": self.provider_credentials_used,
            "trading_filters_certified": self.trading_filters_certified,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


def build_binance_spot_archive_snapshot(
    *,
    descriptor: BinanceSpotArchiveDescriptor,
    archive_bytes: bytes,
    checksum_text: str,
) -> HistoricalMarketSnapshotArtifact:
    """Normalize one already-downloaded Binance Spot public-data archive."""
    if not isinstance(descriptor, BinanceSpotArchiveDescriptor):
        raise TypeError("descriptor must be BinanceSpotArchiveDescriptor")
    if not isinstance(archive_bytes, bytes):
        raise TypeError("archive_bytes must be bytes")
    if not 1 <= len(archive_bytes) <= MAX_ARCHIVE_BYTES:
        raise MarketSnapshotGovernanceError("D2T archive size is outside bound")
    if not isinstance(checksum_text, str) or not checksum_text:
        raise TypeError("checksum_text must be non-empty str")

    archive_sha = sha256(archive_bytes).hexdigest()
    provider_checksum = _parse_provider_checksum(checksum_text, descriptor.archive_filename)
    if archive_sha != provider_checksum:
        raise MarketSnapshotIntegrityError("provider CHECKSUM does not match archive bytes")

    csv_bytes = _read_exact_csv_member(archive_bytes, descriptor.csv_filename)
    csv_sha = sha256(csv_bytes).hexdigest()
    provider_rows = _parse_binance_kline_csv(csv_bytes, descriptor)
    normalized_rows = tuple(_normalized_row(row, descriptor) for row in provider_rows)
    _validate_exact_provider_coverage(provider_rows, descriptor)

    provenance = (
        f"{PROVIDER_ID}:archive={archive_sha}:csv={csv_sha}:descriptor={descriptor.fingerprint}"
    )
    dataset = _dataset_from_normalized_rows(
        instrument=descriptor.instrument,
        rows=normalized_rows,
        source=provenance,
        interval=descriptor.interval,
    )
    _validate_normalized_coverage(
        dataset=dataset,
        start=descriptor.period_start,
        end=descriptor.period_end,
        interval=descriptor.interval,
    )
    manifest = HistoricalMarketSnapshotManifest(
        manifest_version=OSS3D2T_MANIFEST_VERSION,
        producer_code_hash=market_snapshot_producer_semantic_hash(),
        provider_id=PROVIDER_ID,
        provider_base_url=PROVIDER_BASE_URL,
        provider_market=PROVIDER_MARKET,
        provider_data_kind=PROVIDER_DATA_KIND,
        descriptor_fingerprint=descriptor.fingerprint,
        symbol=descriptor.instrument.symbol,
        interval=descriptor.interval,
        granularity=descriptor.granularity,
        period=descriptor.period,
        timestamp_unit_policy=descriptor.timestamp_unit_policy,
        archive_filename=descriptor.archive_filename,
        checksum_filename=descriptor.checksum_filename,
        csv_filename=descriptor.csv_filename,
        relative_archive_path=descriptor.relative_archive_path,
        archive_sha256=archive_sha,
        provider_checksum_sha256=provider_checksum,
        checksum_payload_sha256=sha256(checksum_text.encode("utf-8")).hexdigest(),
        csv_payload_sha256=csv_sha,
        normalized_row_payload_sha256=_normalized_rows_hash(normalized_rows),
        normalized_dataset_hash=dataset.dataset_hash,
        period_start=descriptor.period_start.isoformat(),
        period_end=descriptor.period_end.isoformat(),
        expected_rows=descriptor.expected_rows,
        received_rows=len(normalized_rows),
        coverage_policy=COVERAGE_POLICY,
        zip_member_policy=ZIP_MEMBER_POLICY,
        checksum_policy=CHECKSUM_POLICY,
        normalization_policy=NORMALIZATION_POLICY,
        instrument_metadata_policy=INSTRUMENT_METADATA_POLICY,
        network_used_by_normalizer=False,
        provider_credentials_used=False,
        trading_filters_certified=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )
    artifact_hash = _artifact_hash(
        artifact_version=OSS3D2T_ARTIFACT_VERSION,
        manifest=manifest,
        instrument=descriptor.instrument,
        normalized_rows=normalized_rows,
    )
    return HistoricalMarketSnapshotArtifact(
        artifact_version=OSS3D2T_ARTIFACT_VERSION,
        manifest=manifest,
        instrument=descriptor.instrument,
        normalized_rows=normalized_rows,
        artifact_hash=artifact_hash,
    )


def build_aligned_snapshot_universe(
    artifacts: Iterable[HistoricalMarketSnapshotArtifact],
    *,
    universe_name: str,
) -> tuple[AlignedMarketUniverse, AlignedSnapshotSetEvidence]:
    artifact_tuple = tuple(sorted(tuple(artifacts), key=lambda item: item.instrument.symbol))
    if len(artifact_tuple) < 2:
        raise MarketSnapshotGovernanceError("aligned D2T snapshot set requires at least two artifacts")
    if len({item.instrument.symbol for item in artifact_tuple}) != len(artifact_tuple):
        raise MarketSnapshotIntegrityError("duplicate symbol in D2T aligned snapshot set")
    first = artifact_tuple[0].manifest
    for artifact in artifact_tuple[1:]:
        manifest = artifact.manifest
        for name, expected, actual in (
            ("interval", first.interval, manifest.interval),
            ("period_start", first.period_start, manifest.period_start),
            ("period_end", first.period_end, manifest.period_end),
            ("timestamp_unit_policy", first.timestamp_unit_policy, manifest.timestamp_unit_policy),
            ("granularity", first.granularity, manifest.granularity),
            ("period", first.period, manifest.period),
        ):
            if expected != actual:
                raise MarketSnapshotIntegrityError(f"D2T aligned snapshot {name} mismatch")
    universe = AlignedMarketUniverse.from_datasets(
        datasets=tuple(artifact.dataset for artifact in artifact_tuple),
        universe_name=universe_name,
    )
    dataset_set_hash = _hash(
        [[dataset.instrument.symbol, dataset.dataset_hash] for dataset in universe.datasets]
    )
    evidence = AlignedSnapshotSetEvidence(
        evidence_version=OSS3D2T_SET_EVIDENCE_VERSION,
        snapshot_artifact_hashes=tuple(item.artifact_hash for item in artifact_tuple),
        symbols=universe.symbols,
        interval=first.interval,
        period_start=first.period_start,
        period_end=first.period_end,
        dataset_set_hash=dataset_set_hash,
        aligned_universe_material_hash=universe.universe_hash,
        exact_common_support=True,
        network_used=False,
        provider_credentials_used=False,
        trading_filters_certified=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )
    return universe, evidence


def market_snapshot_producer_semantic_hash() -> str:
    root = Path(__file__).resolve().parents[3]
    payload: list[dict[str, str]] = []
    for relative in SEMANTIC_FILES:
        path = root / relative
        if not path.is_file():
            raise MarketSnapshotIntegrityError(f"D2T semantic file missing: {relative}")
        payload.append({"path": relative, "sha256": sha256(path.read_bytes()).hexdigest()})
    return _hash(payload)


def _parse_provider_checksum(checksum_text: str, expected_filename: str) -> str:
    # The provider file is one SHA-256 + filename line.  Reject comments,
    # multiple lines and path aliases so the checksum cannot be detached from
    # the exact archive descriptor.
    if "\x00" in checksum_text:
        raise MarketSnapshotIntegrityError("D2T CHECKSUM contains NUL")
    lines = checksum_text.splitlines()
    if len(lines) != 1:
        raise MarketSnapshotIntegrityError("D2T CHECKSUM must contain exactly one line")
    match = _CHECKSUM_RE.fullmatch(lines[0])
    if match is None:
        raise MarketSnapshotIntegrityError("D2T CHECKSUM syntax is invalid")
    digest, filename = match.groups()
    if filename != expected_filename:
        raise MarketSnapshotIntegrityError("D2T CHECKSUM filename differs from descriptor")
    return digest.lower()


def _read_exact_csv_member(archive_bytes: bytes, expected_csv_filename: str) -> bytes:
    try:
        with zipfile.ZipFile(BytesIO(archive_bytes), mode="r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise MarketSnapshotIntegrityError("D2T ZIP contains duplicate member names")
            files = [info for info in infos if not info.is_dir()]
            if len(files) != 1 or files[0].filename != expected_csv_filename:
                raise MarketSnapshotIntegrityError("D2T ZIP must contain exactly the expected CSV member")
            info = files[0]
            if info.flag_bits & 0x1:
                raise MarketSnapshotGovernanceError("D2T encrypted ZIP members are forbidden")
            if info.compress_type not in _ALLOWED_ZIP_COMPRESSION:
                raise MarketSnapshotGovernanceError("D2T ZIP compression type is not allowlisted")
            if not 1 <= info.file_size <= MAX_CSV_BYTES:
                raise MarketSnapshotGovernanceError("D2T CSV uncompressed size is outside bound")
            data = archive.read(info)
            if len(data) != info.file_size:
                raise MarketSnapshotIntegrityError("D2T ZIP member size mismatch")
            return data
    except MarketSnapshotError:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise MarketSnapshotIntegrityError("D2T archive is not a valid allowed ZIP") from exc


def _parse_binance_kline_csv(
    csv_bytes: bytes,
    descriptor: BinanceSpotArchiveDescriptor,
) -> tuple[tuple[str, ...], ...]:
    if not csv_bytes or len(csv_bytes) > MAX_CSV_BYTES:
        raise MarketSnapshotGovernanceError("D2T CSV payload size is outside bound")
    try:
        text = csv_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MarketSnapshotIntegrityError("D2T CSV must be UTF-8") from exc
    if "\x00" in text:
        raise MarketSnapshotIntegrityError("D2T CSV contains NUL")
    rows: list[tuple[str, ...]] = []
    try:
        reader = csv.reader(StringIO(text), strict=True)
        for index, row in enumerate(reader):
            if not row:
                raise MarketSnapshotIntegrityError("D2T CSV contains empty row")
            if len(row) != 12:
                raise MarketSnapshotIntegrityError("D2T Binance kline row must contain exactly 12 columns")
            canonical = tuple(value.strip() for value in row)
            if any(value == "" for value in canonical):
                raise MarketSnapshotIntegrityError("D2T Binance kline row contains empty field")
            _validate_provider_row(canonical, descriptor, index)
            rows.append(canonical)
            if len(rows) > MAX_ROWS:
                raise MarketSnapshotGovernanceError("D2T CSV exceeds row bound")
    except MarketSnapshotError:
        raise
    except csv.Error as exc:
        raise MarketSnapshotIntegrityError("D2T CSV syntax is invalid") from exc
    if len(rows) != descriptor.expected_rows:
        raise MarketSnapshotIntegrityError("D2T CSV does not contain exact expected coverage")
    return tuple(rows)


def _validate_provider_row(
    row: tuple[str, ...],
    descriptor: BinanceSpotArchiveDescriptor,
    index: int,
) -> None:
    try:
        open_time = int(row[0])
        close_time = int(row[6])
        prices = tuple(Decimal(row[position]) for position in (1, 2, 3, 4))
        volume = Decimal(row[5])
        quote_volume = Decimal(row[7])
        trades = int(row[8])
        taker_base = Decimal(row[9])
        taker_quote = Decimal(row[10])
        ignore = Decimal(row[11])
    except (ValueError, InvalidOperation) as exc:
        raise MarketSnapshotIntegrityError(f"D2T row {index} contains invalid numeric field") from exc
    if any(not value.is_finite() for value in (*prices, volume, quote_volume, taker_base, taker_quote, ignore)):
        raise MarketSnapshotIntegrityError(f"D2T row {index} contains non-finite numeric field")
    if any(value <= 0 for value in prices):
        raise MarketSnapshotIntegrityError(f"D2T row {index} contains nonpositive OHLC")
    if any(value < 0 for value in (volume, quote_volume, taker_base, taker_quote)) or trades < 0:
        raise MarketSnapshotIntegrityError(f"D2T row {index} contains negative volume/trade field")
    open_price, high, low, close = prices
    if low > high or high < max(open_price, close) or low > min(open_price, close):
        raise MarketSnapshotIntegrityError(f"D2T row {index} violates OHLC geometry")
    unit_multiplier = descriptor.timestamp_unit_multiplier_per_millisecond
    interval_units = FIXED_INTERVAL_MS[descriptor.interval] * unit_multiplier
    expected_open = _epoch_units(descriptor.period_start, descriptor.timestamp_unit_policy) + index * interval_units
    expected_close = expected_open + interval_units - 1
    if open_time != expected_open:
        raise MarketSnapshotIntegrityError(f"D2T row {index} open time differs from exact archive support")
    if close_time != expected_close:
        raise MarketSnapshotIntegrityError(f"D2T row {index} close time differs from fixed kline geometry")


def _validate_exact_provider_coverage(
    rows: Sequence[tuple[str, ...]],
    descriptor: BinanceSpotArchiveDescriptor,
) -> None:
    if len(rows) != descriptor.expected_rows:
        raise MarketSnapshotIntegrityError("D2T provider row count differs from descriptor")
    previous_open: int | None = None
    interval_units = FIXED_INTERVAL_MS[descriptor.interval] * descriptor.timestamp_unit_multiplier_per_millisecond
    for index, row in enumerate(rows):
        current = int(row[0])
        if previous_open is not None and current - previous_open != interval_units:
            raise MarketSnapshotIntegrityError(f"D2T provider rows contain gap/duplicate at index {index}")
        previous_open = current


def _normalized_row(
    row: tuple[str, ...],
    descriptor: BinanceSpotArchiveDescriptor,
) -> tuple[str, str, str, str, str, str]:
    started_at = _datetime_from_units(int(row[0]), descriptor.timestamp_unit_policy)
    return (
        started_at.isoformat(),
        _canonical_decimal(row[1]),
        _canonical_decimal(row[2]),
        _canonical_decimal(row[3]),
        _canonical_decimal(row[4]),
        _canonical_decimal(row[5]),
    )


def _validate_normalized_rows(
    rows: Sequence[tuple[str, str, str, str, str, str]],
) -> tuple[tuple[str, str, str, str, str, str], ...]:
    if not isinstance(rows, (tuple, list)):
        raise TypeError("D2T normalized rows must be sequence")
    converted: list[tuple[str, str, str, str, str, str]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, (tuple, list)) or len(row) != 6:
            raise MarketSnapshotIntegrityError("D2T normalized row must contain six fields")
        current = tuple(str(value) for value in row)
        _parse_canonical_utc(current[0], f"normalized row {index} started_at")
        for value in current[1:]:
            _canonical_decimal(value)
        converted.append(current)  # type: ignore[arg-type]
    return tuple(converted)


def _dataset_from_normalized_rows(
    *,
    instrument: InstrumentMetadata,
    rows: Sequence[tuple[str, str, str, str, str, str]],
    source: str,
    interval: str,
) -> MarketDataset:
    timeframe_seconds = FIXED_INTERVAL_MS[interval] // 1000
    bars = tuple(
        Bar(
            symbol=instrument.symbol,
            started_at=_parse_canonical_utc(row[0], "normalized started_at"),
            timeframe_seconds=timeframe_seconds,
            open=Decimal(row[1]),
            high=Decimal(row[2]),
            low=Decimal(row[3]),
            close=Decimal(row[4]),
            volume=Decimal(row[5]),
        )
        for row in rows
    )
    return MarketDataset(instrument=instrument, bars=bars, source=source)


def _validate_normalized_coverage(
    *,
    dataset: MarketDataset,
    start: datetime,
    end: datetime,
    interval: str,
) -> None:
    interval_seconds = FIXED_INTERVAL_MS[interval] // 1000
    expected = int((end - start).total_seconds()) // interval_seconds
    if len(dataset.bars) != expected:
        raise MarketSnapshotIntegrityError("D2T normalized dataset bar count differs from period")
    for index, bar in enumerate(dataset.bars):
        expected_start = start + timedelta(seconds=index * interval_seconds)
        if bar.started_at != expected_start:
            raise MarketSnapshotIntegrityError("D2T normalized dataset timestamp coverage mismatch")
    if dataset.gap_indexes():
        raise MarketSnapshotIntegrityError("D2T normalized dataset contains gaps")


def _normalized_rows_hash(rows: Sequence[tuple[str, str, str, str, str, str]]) -> str:
    return _hash([list(row) for row in rows])


def _artifact_hash(
    *,
    artifact_version: str,
    manifest: HistoricalMarketSnapshotManifest,
    instrument: InstrumentMetadata,
    normalized_rows: Sequence[tuple[str, str, str, str, str, str]],
) -> str:
    return _hash(
        {
            "artifact_version": artifact_version,
            "manifest": manifest.to_dict(),
            "instrument": _instrument_dict(instrument),
            "normalized_rows": [list(row) for row in normalized_rows],
        }
    )


def _artifact_from_mapping(document: object) -> HistoricalMarketSnapshotArtifact:
    if not isinstance(document, dict):
        raise MarketSnapshotIntegrityError("D2T artifact top level must be object")
    expected_keys = {"artifact_version", "manifest", "instrument", "normalized_rows", "artifact_hash"}
    if set(document) != expected_keys:
        raise MarketSnapshotIntegrityError("D2T artifact top-level schema mismatch")
    manifest_raw = document["manifest"]
    instrument_raw = document["instrument"]
    rows_raw = document["normalized_rows"]
    if not isinstance(manifest_raw, dict) or not isinstance(instrument_raw, dict) or not isinstance(rows_raw, list):
        raise MarketSnapshotIntegrityError("D2T artifact nested schema mismatch")
    manifest_keys = set(HistoricalMarketSnapshotManifest.__dataclass_fields__)
    if set(manifest_raw) != manifest_keys:
        raise MarketSnapshotIntegrityError("D2T manifest schema mismatch")
    try:
        manifest = HistoricalMarketSnapshotManifest(**manifest_raw)
        instrument = InstrumentMetadata(
            symbol=str(instrument_raw["symbol"]),
            venue=str(instrument_raw["venue"]),
            quote_currency=str(instrument_raw["quote_currency"]),
            price_tick=Decimal(str(instrument_raw["price_tick"])),
            quantity_step=Decimal(str(instrument_raw["quantity_step"])),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation, MarketSnapshotError) as exc:
        if isinstance(exc, MarketSnapshotError):
            raise
        raise MarketSnapshotIntegrityError("D2T manifest/instrument fields are invalid") from exc
    if set(instrument_raw) != {"symbol", "venue", "quote_currency", "price_tick", "quantity_step"}:
        raise MarketSnapshotIntegrityError("D2T instrument schema mismatch")
    rows = _validate_normalized_rows(rows_raw)
    try:
        return HistoricalMarketSnapshotArtifact(
            artifact_version=str(document["artifact_version"]),
            manifest=manifest,
            instrument=instrument,
            normalized_rows=rows,
            artifact_hash=str(document["artifact_hash"]),
        )
    except (TypeError, ValueError, MarketSnapshotError) as exc:
        if isinstance(exc, MarketSnapshotError):
            raise
        raise MarketSnapshotIntegrityError("D2T artifact fields are invalid") from exc


def _parse_period(granularity: str, period: str) -> tuple[datetime, datetime]:
    if granularity == "daily":
        match = _PERIOD_DAY_RE.fullmatch(period)
        if match is None:
            raise ValueError("daily D2T period must be YYYY-MM-DD")
        year, month, day = (int(value) for value in match.groups())
        try:
            start = datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValueError("invalid D2T daily period") from exc
        return start, start + timedelta(days=1)
    if granularity == "monthly":
        match = _PERIOD_MONTH_RE.fullmatch(period)
        if match is None:
            raise ValueError("monthly D2T period must be YYYY-MM")
        year, month = (int(value) for value in match.groups())
        if not 1 <= month <= 12:
            raise ValueError("invalid D2T monthly period")
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        return start, end
    raise ValueError("invalid D2T granularity")


def _epoch_units(value: datetime, timestamp_unit_policy: str) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("D2T epoch timestamp must be timezone-aware")
    normalized = value.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = normalized - epoch
    total_us = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    if timestamp_unit_policy == TIMESTAMP_US:
        return total_us
    if timestamp_unit_policy == TIMESTAMP_MS:
        if total_us % 1000:
            raise ValueError("D2T millisecond timestamp is not exactly representable")
        return total_us // 1000
    raise ValueError("invalid D2T timestamp-unit policy")


def _datetime_from_units(value: int, timestamp_unit_policy: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("invalid D2T provider timestamp")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    if timestamp_unit_policy == TIMESTAMP_US:
        return epoch + timedelta(microseconds=value)
    if timestamp_unit_policy == TIMESTAMP_MS:
        return epoch + timedelta(milliseconds=value)
    raise ValueError("invalid D2T timestamp-unit policy")


def _canonical_decimal(value: str) -> str:
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise MarketSnapshotIntegrityError("invalid D2T decimal") from exc
    if not parsed.is_finite():
        raise MarketSnapshotIntegrityError("non-finite D2T decimal")
    # Preserve provider scale where possible while removing syntactic aliases
    # such as leading plus signs/exponents.  `format(..., 'f')` is deterministic.
    return format(parsed, "f")


def _instrument_dict(instrument: InstrumentMetadata) -> dict[str, str]:
    return {
        "symbol": instrument.symbol,
        "venue": instrument.venue,
        "quote_currency": instrument.quote_currency,
        "price_tick": str(instrument.price_tick),
        "quantity_step": str(instrument.quantity_step),
    }


def _parse_canonical_utc(value: str, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must be UTC")
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        raise ValueError(f"{name} must use canonical +00:00 representation")
    return normalized


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{name} must be lowercase sha256")


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MarketSnapshotIntegrityError("D2T value is not canonical JSON") from exc


def _hash(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise MarketSnapshotIntegrityError(f"duplicate D2T JSON key: {key}")
        result[key] = value
    return result


def _deny_authority(
    execution_authorized: bool,
    paper_execution_authorized: bool,
    capital_authority: str,
    live_trading: str,
) -> None:
    if execution_authorized or paper_execution_authorized:
        raise MarketSnapshotGovernanceError("D2T cannot authorize execution")
    if capital_authority != "NONE" or live_trading != "BLOCKED":
        raise MarketSnapshotGovernanceError("D2T cannot grant capital or LIVE authority")
