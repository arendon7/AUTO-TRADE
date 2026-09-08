"""OSS-3D2U bounded public archive acquisition adapter.

This lab adapter is the only D2U component allowed to perform network I/O.  The
finite collection plan must already exist durably in the append-only D2U SQLite
registry before the first GET.  Each descriptor is acquired exactly once in
this order:

    CHECKSUM A -> ZIP -> CHECKSUM B

The two checksum payloads must be byte-for-byte identical.  Only then are the
ZIP/checksum bytes handed to the offline D2T normalizer.  There is no retry
loop: a caller may begin a distinct acquisition attempt explicitly, but one
attempt never repeats a request after ambiguous I/O.

Research only: public GETs to data.binance.vision; no API keys, trading
endpoints, Qlib, labels, FINAL_HOLDOUT, broker, OMS, Safety, PAPER or LIVE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlsplit

from autotrade.research.external_data import (
    HttpResponse,
    PublicDataPolicy,
    ReadOnlyHttpTransport,
    ReadOnlyRequest,
    UrllibReadOnlyTransport,
)
from autotrade.research.oss3_market_collection import (
    HistoricalCollectionPlan,
    SQLiteHistoricalCollectionPlanRegistry,
)
from autotrade.research.oss3_market_snapshot import (
    MAX_ARCHIVE_BYTES,
    PROVIDER_BASE_URL,
    BinanceSpotArchiveDescriptor,
    HistoricalMarketSnapshotArtifact,
    build_binance_spot_archive_snapshot,
)


OSS3D2U_ACQUISITION_RECEIPT_VERSION = "OSS3D2U_ARCHIVE_ACQUISITION_RECEIPT_V1"
OSS3D2U_ACQUIRED_MATERIAL_VERSION = "OSS3D2U_ACQUIRED_ARCHIVE_MATERIAL_V1"
ACQUISITION_ORDER_POLICY = "CHECKSUM_A_THEN_ZIP_THEN_IDENTICAL_CHECKSUM_B_V1"
REQUEST_POLICY = "EXACT_PREREGISTERED_PATH_GET_ONLY_NO_REDIRECT_NO_RETRY_V1"
PERSISTENCE_POLICY = "ATOMIC_RAW_AND_NORMALIZED_EVIDENCE_FILES_V1"
PROVIDER_HOST = "data.binance.vision"
CHECKSUM_MAX_BYTES = 4_096
DEFAULT_TIMEOUT_SECONDS = 20.0
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class ArchiveAcquisitionError(RuntimeError):
    pass


class ArchiveAcquisitionIntegrityError(ArchiveAcquisitionError):
    pass


class ArchiveAcquisitionGovernanceError(ArchiveAcquisitionError):
    pass


@dataclass(frozen=True, slots=True)
class ArchiveAcquisitionReceipt:
    receipt_version: str
    collection_id: str
    plan_fingerprint: str
    descriptor_fingerprint: str
    archive_url: str
    checksum_url: str
    checksum_a_status: int
    archive_status: int
    checksum_b_status: int
    checksum_a_payload_sha256: str
    archive_payload_sha256: str
    checksum_b_payload_sha256: str
    d2t_snapshot_artifact_hash: str
    d2t_normalized_dataset_hash: str
    acquired_at: str
    acquisition_order_policy: str
    request_policy: str
    checksum_payloads_identical: bool
    exact_final_urls: bool
    request_count: int
    retries_performed: int
    network_used: bool
    provider_credentials_used: bool
    trading_endpoints_used: bool
    final_holdout_values_requested: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.receipt_version != OSS3D2U_ACQUISITION_RECEIPT_VERSION:
            raise ArchiveAcquisitionIntegrityError("noncanonical D2U acquisition receipt version")
        if not self.collection_id.strip():
            raise ArchiveAcquisitionIntegrityError("D2U acquisition collection id is required")
        for name in (
            "plan_fingerprint",
            "descriptor_fingerprint",
            "checksum_a_payload_sha256",
            "archive_payload_sha256",
            "checksum_b_payload_sha256",
            "d2t_snapshot_artifact_hash",
            "d2t_normalized_dataset_hash",
        ):
            _require_hash(getattr(self, name), name)
        if self.archive_url != PROVIDER_BASE_URL + urlsplit(self.archive_url).path:
            raise ArchiveAcquisitionGovernanceError("D2U archive URL is not canonical provider HTTPS URL")
        if self.checksum_url != PROVIDER_BASE_URL + urlsplit(self.checksum_url).path:
            raise ArchiveAcquisitionGovernanceError("D2U checksum URL is not canonical provider HTTPS URL")
        if not self.checksum_url.endswith(".CHECKSUM"):
            raise ArchiveAcquisitionIntegrityError("D2U checksum URL suffix is invalid")
        if any(status != 200 for status in (self.checksum_a_status, self.archive_status, self.checksum_b_status)):
            raise ArchiveAcquisitionIntegrityError("D2U acquisition receipt requires three HTTP 200 responses")
        if self.acquisition_order_policy != ACQUISITION_ORDER_POLICY:
            raise ArchiveAcquisitionGovernanceError("D2U acquisition ordering policy drifted")
        if self.request_policy != REQUEST_POLICY:
            raise ArchiveAcquisitionGovernanceError("D2U request policy drifted")
        if not self.checksum_payloads_identical or not self.exact_final_urls:
            raise ArchiveAcquisitionIntegrityError("D2U acquisition must prove stable checksum and exact final URLs")
        if self.request_count != 3 or self.retries_performed != 0:
            raise ArchiveAcquisitionGovernanceError("D2U one acquisition attempt is exactly three GETs with zero retries")
        _parse_utc(self.acquired_at)
        if not self.network_used:
            raise ArchiveAcquisitionIntegrityError("D2U acquisition receipt must acknowledge public network use")
        if self.provider_credentials_used or self.trading_endpoints_used or self.final_holdout_values_requested:
            raise ArchiveAcquisitionGovernanceError("D2U acquisition may use public archive data only")
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
            "receipt_version": self.receipt_version,
            "collection_id": self.collection_id,
            "plan_fingerprint": self.plan_fingerprint,
            "descriptor_fingerprint": self.descriptor_fingerprint,
            "archive_url": self.archive_url,
            "checksum_url": self.checksum_url,
            "checksum_a_status": self.checksum_a_status,
            "archive_status": self.archive_status,
            "checksum_b_status": self.checksum_b_status,
            "checksum_a_payload_sha256": self.checksum_a_payload_sha256,
            "archive_payload_sha256": self.archive_payload_sha256,
            "checksum_b_payload_sha256": self.checksum_b_payload_sha256,
            "d2t_snapshot_artifact_hash": self.d2t_snapshot_artifact_hash,
            "d2t_normalized_dataset_hash": self.d2t_normalized_dataset_hash,
            "acquired_at": self.acquired_at,
            "acquisition_order_policy": self.acquisition_order_policy,
            "request_policy": self.request_policy,
            "checksum_payloads_identical": self.checksum_payloads_identical,
            "exact_final_urls": self.exact_final_urls,
            "request_count": self.request_count,
            "retries_performed": self.retries_performed,
            "network_used": self.network_used,
            "provider_credentials_used": self.provider_credentials_used,
            "trading_endpoints_used": self.trading_endpoints_used,
            "final_holdout_values_requested": self.final_holdout_values_requested,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


@dataclass(frozen=True, slots=True)
class AcquiredArchiveMaterial:
    material_version: str
    descriptor: BinanceSpotArchiveDescriptor
    archive_bytes: bytes
    checksum_text: str
    snapshot: HistoricalMarketSnapshotArtifact
    receipt: ArchiveAcquisitionReceipt
    persistence_policy: str = PERSISTENCE_POLICY

    def __post_init__(self) -> None:
        if self.material_version != OSS3D2U_ACQUIRED_MATERIAL_VERSION:
            raise ArchiveAcquisitionIntegrityError("noncanonical D2U acquired material version")
        if self.persistence_policy != PERSISTENCE_POLICY:
            raise ArchiveAcquisitionGovernanceError("D2U persistence policy drifted")
        if not isinstance(self.archive_bytes, bytes) or not self.archive_bytes:
            raise ArchiveAcquisitionIntegrityError("D2U acquired archive bytes are missing")
        if not isinstance(self.checksum_text, str) or not self.checksum_text:
            raise ArchiveAcquisitionIntegrityError("D2U acquired checksum text is missing")
        if self.descriptor.fingerprint != self.receipt.descriptor_fingerprint:
            raise ArchiveAcquisitionIntegrityError("D2U descriptor differs from acquisition receipt")
        if self.snapshot.manifest.descriptor_fingerprint != self.descriptor.fingerprint:
            raise ArchiveAcquisitionIntegrityError("D2U D2T snapshot differs from descriptor")
        if self.snapshot.artifact_hash != self.receipt.d2t_snapshot_artifact_hash:
            raise ArchiveAcquisitionIntegrityError("D2U snapshot hash differs from acquisition receipt")
        if self.snapshot.manifest.normalized_dataset_hash != self.receipt.d2t_normalized_dataset_hash:
            raise ArchiveAcquisitionIntegrityError("D2U dataset hash differs from acquisition receipt")
        if sha256(self.archive_bytes).hexdigest() != self.receipt.archive_payload_sha256:
            raise ArchiveAcquisitionIntegrityError("D2U retained archive bytes differ from receipt")
        checksum_hash = sha256(self.checksum_text.encode("utf-8")).hexdigest()
        if checksum_hash != self.receipt.checksum_a_payload_sha256:
            raise ArchiveAcquisitionIntegrityError("D2U retained checksum differs from receipt")
        self.snapshot.verify_source(
            descriptor=self.descriptor,
            archive_bytes=self.archive_bytes,
            checksum_text=self.checksum_text,
        )

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "material_version": self.material_version,
                "descriptor_fingerprint": self.descriptor.fingerprint,
                "snapshot_artifact_hash": self.snapshot.artifact_hash,
                "receipt_hash": self.receipt.fingerprint,
                "archive_payload_sha256": self.receipt.archive_payload_sha256,
                "checksum_payload_sha256": self.receipt.checksum_a_payload_sha256,
                "persistence_policy": self.persistence_policy,
            }
        )

    def write(self, root: str | Path) -> Mapping[str, Path]:
        """Atomically persist raw archive/checksum plus normalized evidence."""
        target = Path(root) / self.descriptor.instrument.symbol / self.descriptor.period
        target.mkdir(parents=True, exist_ok=True)
        paths = {
            "archive": target / self.descriptor.archive_filename,
            "checksum": target / self.descriptor.checksum_filename,
            "snapshot": target / "d2t-snapshot.json",
            "receipt": target / "d2u-acquisition-receipt.json",
        }
        _atomic_write_bytes(paths["archive"], self.archive_bytes)
        _atomic_write_bytes(paths["checksum"], self.checksum_text.encode("utf-8"))
        self.snapshot.write(paths["snapshot"])
        _atomic_write_bytes(
            paths["receipt"],
            (json.dumps(self.receipt.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"),
        )
        return paths


def acquire_preregistered_archive(
    *,
    registry: SQLiteHistoricalCollectionPlanRegistry,
    plan: HistoricalCollectionPlan,
    descriptor: BinanceSpotArchiveDescriptor,
    transport: ReadOnlyHttpTransport,
    now: datetime,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> AcquiredArchiveMaterial:
    """Perform exactly one preregistered CHECKSUM/ZIP/CHECKSUM acquisition attempt."""
    if not isinstance(registry, SQLiteHistoricalCollectionPlanRegistry):
        raise TypeError("registry must be SQLiteHistoricalCollectionPlanRegistry")
    if not isinstance(plan, HistoricalCollectionPlan):
        raise TypeError("plan must be HistoricalCollectionPlan")
    if not isinstance(descriptor, BinanceSpotArchiveDescriptor):
        raise TypeError("descriptor must be BinanceSpotArchiveDescriptor")
    _require_aware(now, "now")
    if not 0 < timeout_seconds <= 30:
        raise ArchiveAcquisitionGovernanceError("D2U acquisition timeout must be >0 and <=30 seconds")

    # CRITICAL ordering gate: no request may happen until the exact finite plan
    # exists durably in the append-only D2U registry.
    registry.require_exact(plan)
    if descriptor.fingerprint not in set(plan.descriptor_fingerprints):
        raise ArchiveAcquisitionGovernanceError("D2U descriptor is not part of preregistered collection plan")

    allowed_paths = frozenset({descriptor.relative_archive_path, descriptor.relative_checksum_path})
    policy = PublicDataPolicy(allowed_host=PROVIDER_HOST, allowed_paths=allowed_paths)
    checksum_request = ReadOnlyRequest(
        method="GET",
        url=descriptor.checksum_url,
        timeout_seconds=timeout_seconds,
    )
    archive_request = ReadOnlyRequest(
        method="GET",
        url=descriptor.archive_url,
        timeout_seconds=timeout_seconds,
    )
    policy.validate(checksum_request)
    policy.validate(archive_request)

    checksum_a = _send_exact(transport, policy, checksum_request, max_bytes=CHECKSUM_MAX_BYTES)
    archive = _send_exact(transport, policy, archive_request, max_bytes=MAX_ARCHIVE_BYTES)
    checksum_b = _send_exact(transport, policy, checksum_request, max_bytes=CHECKSUM_MAX_BYTES)

    if checksum_a.body != checksum_b.body:
        raise ArchiveAcquisitionIntegrityError("D2U provider CHECKSUM changed during acquisition attempt")
    try:
        checksum_text = checksum_a.body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArchiveAcquisitionIntegrityError("D2U provider CHECKSUM is not UTF-8") from exc

    snapshot = build_binance_spot_archive_snapshot(
        descriptor=descriptor,
        archive_bytes=archive.body,
        checksum_text=checksum_text,
    )
    receipt = ArchiveAcquisitionReceipt(
        receipt_version=OSS3D2U_ACQUISITION_RECEIPT_VERSION,
        collection_id=plan.collection_id,
        plan_fingerprint=plan.fingerprint,
        descriptor_fingerprint=descriptor.fingerprint,
        archive_url=descriptor.archive_url,
        checksum_url=descriptor.checksum_url,
        checksum_a_status=checksum_a.status_code,
        archive_status=archive.status_code,
        checksum_b_status=checksum_b.status_code,
        checksum_a_payload_sha256=sha256(checksum_a.body).hexdigest(),
        archive_payload_sha256=sha256(archive.body).hexdigest(),
        checksum_b_payload_sha256=sha256(checksum_b.body).hexdigest(),
        d2t_snapshot_artifact_hash=snapshot.artifact_hash,
        d2t_normalized_dataset_hash=snapshot.manifest.normalized_dataset_hash,
        acquired_at=now.astimezone(timezone.utc).isoformat(),
        acquisition_order_policy=ACQUISITION_ORDER_POLICY,
        request_policy=REQUEST_POLICY,
        checksum_payloads_identical=True,
        exact_final_urls=True,
        request_count=3,
        retries_performed=0,
        network_used=True,
        provider_credentials_used=False,
        trading_endpoints_used=False,
        final_holdout_values_requested=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )
    return AcquiredArchiveMaterial(
        material_version=OSS3D2U_ACQUIRED_MATERIAL_VERSION,
        descriptor=descriptor,
        archive_bytes=archive.body,
        checksum_text=checksum_text,
        snapshot=snapshot,
        receipt=receipt,
    )


def build_real_archive_transport(
    *,
    plan: HistoricalCollectionPlan,
    max_response_bytes: int = MAX_ARCHIVE_BYTES,
) -> UrllibReadOnlyTransport:
    """Build a GET-only transport allowlisted to the frozen D2U collection paths."""
    if max_response_bytes < MAX_ARCHIVE_BYTES:
        raise ArchiveAcquisitionGovernanceError("D2U real transport must permit the D2T archive byte bound")
    allowed = frozenset(
        path
        for descriptor in plan.descriptors
        for path in (descriptor.relative_archive_path, descriptor.relative_checksum_path)
    )
    policy = PublicDataPolicy(allowed_host=PROVIDER_HOST, allowed_paths=allowed)
    return UrllibReadOnlyTransport(policy=policy, max_response_bytes=max_response_bytes)


def _send_exact(
    transport: ReadOnlyHttpTransport,
    policy: PublicDataPolicy,
    request: ReadOnlyRequest,
    *,
    max_bytes: int,
) -> HttpResponse:
    policy.validate(request)
    try:
        response = transport.send(request)
    except ArchiveAcquisitionError:
        raise
    except Exception as exc:
        raise ArchiveAcquisitionError("D2U public archive transport failed") from exc
    policy.validate_final_url(response.final_url)
    if response.final_url != request.url:
        raise ArchiveAcquisitionGovernanceError("D2U redirects are forbidden even between allowlisted paths")
    if response.status_code != 200:
        raise ArchiveAcquisitionError(f"D2U public archive response status is {response.status_code}")
    if not isinstance(response.body, bytes) or not 1 <= len(response.body) <= max_bytes:
        raise ArchiveAcquisitionGovernanceError("D2U public archive response size is outside bound")
    return response


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ArchiveAcquisitionIntegrityError("invalid D2U acquisition timestamp") from exc
    _require_aware(parsed, "acquired_at")
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        raise ArchiveAcquisitionIntegrityError("D2U acquisition timestamp must be canonical UTC")
    return normalized


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase sha256")


def _deny_authority(
    execution_authorized: bool,
    paper_execution_authorized: bool,
    capital_authority: str,
    live_trading: str,
) -> None:
    if execution_authorized or paper_execution_authorized:
        raise ArchiveAcquisitionGovernanceError("D2U execution authority is forbidden")
    if capital_authority != "NONE":
        raise ArchiveAcquisitionGovernanceError("D2U capital authority must be NONE")
    if live_trading != "BLOCKED":
        raise ArchiveAcquisitionGovernanceError("D2U LIVE trading must remain BLOCKED")


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()
