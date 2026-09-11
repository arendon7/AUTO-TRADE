"""OSS-3D3E restart-safe acquisition of the D3D-frozen holdout archives.

D3E is an acquisition-only boundary.  It proves the exact D3D preregistration
read-only before the first request, acquires only the 18 frozen Binance Spot
monthly archive descriptors, and persists immutable raw + D2T integrity
evidence.

Raw market bytes are necessarily parsed by D2T to prove archive integrity and
coverage.  That is not a scientific holdout evaluation: D3E does not create
features, labels, predictions, metrics, D2J/D2M commitments, D2K/D2N starts,
or holdout permits.  No broker/PAPER/capital/LIVE authority exists here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Mapping

from autotrade.research.external_data import (
    HttpResponse,
    PublicDataPolicy,
    ReadOnlyHttpTransport,
    ReadOnlyRequest,
    UrllibReadOnlyTransport,
)
from autotrade.research.oss3_market_snapshot import (
    MAX_ARCHIVE_BYTES,
    PROVIDER_BASE_URL,
    BinanceSpotArchiveDescriptor,
    HistoricalMarketSnapshotArtifact,
    build_binance_spot_archive_snapshot,
)
from labs.oss3_qlib.dual_holdout_acquisition_preregistration import (
    ECONOMIC_PURPOSE,
    OSS3D3D_REGISTRY_TABLE,
    PREDICTIVE_PURPOSE,
    DualHoldoutAcquisitionPlan,
    canonical_oss3d3d_dual_holdout_plan,
)


OSS3D3E_RECEIPT_VERSION = "OSS3D3E_RAW_HOLDOUT_ARCHIVE_ACQUISITION_RECEIPT_V1"
OSS3D3E_SEAL_VERSION = "OSS3D3E_RAW_HOLDOUT_DESCRIPTOR_SEAL_V1"
OSS3D3E_CAMPAIGN_SEAL_VERSION = "OSS3D3E_COMPLETE_DUAL_HOLDOUT_RAW_ACQUISITION_SEAL_V1"
OSS3D3E_RESULT_VERSION = "OSS3D3E_DUAL_HOLDOUT_ACQUISITION_RESULT_V1"
D3D_PLAN_FINGERPRINT = "d6dc987cc2053a6617796fdb89df9467c0895a090c5745c91b157edc5d7a4af2"
PROVIDER_HOST = "data.binance.vision"
CHECKSUM_MAX_BYTES = 4_096
DEFAULT_TIMEOUT_SECONDS = 20.0
REQUESTS_PER_DESCRIPTOR = 3
EXPECTED_DESCRIPTOR_COUNT = 18
EXPECTED_PREDICTIVE_DESCRIPTORS = 9
EXPECTED_ECONOMIC_DESCRIPTORS = 9
ACQUISITION_ORDER_POLICY = "CHECKSUM_A_THEN_ZIP_THEN_IDENTICAL_CHECKSUM_B_V1"
REQUEST_POLICY = "EXACT_D3D_PREREGISTERED_PATH_GET_ONLY_NO_REDIRECT_NO_RETRY_V1"
PERSISTENCE_POLICY = "ATOMIC_RAW_CHECKSUM_D2T_RECEIPT_EVIDENCE_V1"
RESTART_POLICY = "SEALED_REUSE_ONLY_AFTER_FULL_RAW_RECEIPT_D2T_REVERIFICATION_V1"
STAGING_POLICY = "ATOMIC_PURPOSE_SYMBOL_MONTH_DIRECTORY_RENAME_NO_STALE_STAGE_REUSE_V1"
STORAGE_POLICY = "D3E_EVIDENCE_ROOT_MUST_RESOLVE_OUTSIDE_GIT_REPOSITORY_V1"
NETWORK_POLICY = "D3D_EXACT_18_DESCRIPTOR_PUBLIC_GET_ONLY_V1"
LEDGER_FILENAME = "oss3d3e-dual-holdout-acquisition.sqlite3"
STAGING_DIRNAME = ".oss3d3e-staging"


class DualHoldoutRawAcquisitionError(RuntimeError):
    pass


class DualHoldoutRawAcquisitionIntegrityError(DualHoldoutRawAcquisitionError):
    pass


class DualHoldoutRawAcquisitionGovernanceError(DualHoldoutRawAcquisitionError):
    pass


@dataclass(frozen=True, slots=True)
class D3EArchiveAcquisitionReceipt:
    receipt_version: str
    d3d_plan_fingerprint: str
    purpose: str
    descriptor_fingerprint: str
    symbol: str
    period: str
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
    raw_market_bytes_acquired: bool
    d2t_integrity_normalization_performed: bool
    feature_values_materialized: bool
    label_values_materialized: bool
    prediction_values_materialized: bool
    metrics_computed: bool
    scientific_holdout_evaluation_observed: bool
    d2j_or_d2m_commitment_created: bool
    holdout_permit_issued: bool
    holdout_permit_consumed: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.receipt_version != OSS3D3E_RECEIPT_VERSION:
            raise DualHoldoutRawAcquisitionIntegrityError("noncanonical D3E receipt version")
        if self.d3d_plan_fingerprint != D3D_PLAN_FINGERPRINT:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E D3D plan identity drifted")
        if self.purpose not in {PREDICTIVE_PURPOSE, ECONOMIC_PURPOSE}:
            raise DualHoldoutRawAcquisitionGovernanceError("invalid D3E purpose")
        for name in (
            "descriptor_fingerprint",
            "checksum_a_payload_sha256",
            "archive_payload_sha256",
            "checksum_b_payload_sha256",
            "d2t_snapshot_artifact_hash",
            "d2t_normalized_dataset_hash",
        ):
            _require_hash(getattr(self, name), name)
        if any(status != 200 for status in (self.checksum_a_status, self.archive_status, self.checksum_b_status)):
            raise DualHoldoutRawAcquisitionIntegrityError("D3E requires three HTTP 200 responses")
        if self.acquisition_order_policy != ACQUISITION_ORDER_POLICY:
            raise DualHoldoutRawAcquisitionGovernanceError("D3E acquisition order drifted")
        if self.request_policy != REQUEST_POLICY:
            raise DualHoldoutRawAcquisitionGovernanceError("D3E request policy drifted")
        if not self.checksum_payloads_identical or not self.exact_final_urls:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E checksum/final URL proof failed")
        if self.request_count != REQUESTS_PER_DESCRIPTOR or self.retries_performed != 0:
            raise DualHoldoutRawAcquisitionGovernanceError("D3E is exactly three GETs with zero retries per attempt")
        _parse_utc(self.acquired_at)
        if not self.network_used or self.provider_credentials_used or self.trading_endpoints_used:
            raise DualHoldoutRawAcquisitionGovernanceError("D3E permits only credential-free public archive network use")
        if not self.raw_market_bytes_acquired or not self.d2t_integrity_normalization_performed:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E receipt must acknowledge raw acquisition and D2T integrity normalization")
        if (
            self.feature_values_materialized
            or self.label_values_materialized
            or self.prediction_values_materialized
            or self.metrics_computed
            or self.scientific_holdout_evaluation_observed
            or self.d2j_or_d2m_commitment_created
            or self.holdout_permit_issued
            or self.holdout_permit_consumed
            or self.execution_authorized
            or self.paper_execution_authorized
        ):
            raise DualHoldoutRawAcquisitionGovernanceError("D3E cannot evaluate, commit, permit, or execute holdout evidence")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise DualHoldoutRawAcquisitionGovernanceError("D3E cannot grant capital or LIVE authority")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class D3EDescriptorSeal:
    seal_version: str
    d3d_plan_fingerprint: str
    purpose: str
    descriptor_fingerprint: str
    symbol: str
    period: str
    archive_sha256: str
    checksum_payload_sha256: str
    d2t_snapshot_artifact_hash: str
    d2t_normalized_dataset_hash: str
    acquisition_receipt_fingerprint: str
    evidence_relative_directory: str
    sealed_at: str
    restart_policy: str
    staging_policy: str
    full_reverification_required_on_reuse: bool
    raw_market_bytes_present: bool
    scientific_holdout_evaluation_observed: bool
    feature_values_materialized: bool
    label_values_materialized: bool
    prediction_values_materialized: bool
    metrics_computed: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.seal_version != OSS3D3E_SEAL_VERSION:
            raise DualHoldoutRawAcquisitionIntegrityError("noncanonical D3E descriptor seal version")
        if self.d3d_plan_fingerprint != D3D_PLAN_FINGERPRINT:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E descriptor seal plan drifted")
        if self.purpose not in {PREDICTIVE_PURPOSE, ECONOMIC_PURPOSE}:
            raise DualHoldoutRawAcquisitionGovernanceError("invalid D3E seal purpose")
        for name in (
            "descriptor_fingerprint",
            "archive_sha256",
            "checksum_payload_sha256",
            "d2t_snapshot_artifact_hash",
            "d2t_normalized_dataset_hash",
            "acquisition_receipt_fingerprint",
        ):
            _require_hash(getattr(self, name), name)
        expected_dir = f"{self.purpose}/{self.symbol}/{self.period}"
        if self.evidence_relative_directory != expected_dir:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E evidence directory drifted")
        _parse_utc(self.sealed_at)
        if self.restart_policy != RESTART_POLICY or self.staging_policy != STAGING_POLICY:
            raise DualHoldoutRawAcquisitionGovernanceError("D3E restart/staging policy drifted")
        if not self.full_reverification_required_on_reuse or not self.raw_market_bytes_present:
            raise DualHoldoutRawAcquisitionGovernanceError("D3E sealed evidence must be raw-present and fully reverified on reuse")
        if (
            self.scientific_holdout_evaluation_observed
            or self.feature_values_materialized
            or self.label_values_materialized
            or self.prediction_values_materialized
            or self.metrics_computed
            or self.execution_authorized
            or self.paper_execution_authorized
        ):
            raise DualHoldoutRawAcquisitionGovernanceError("D3E seal exceeds raw acquisition authority")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise DualHoldoutRawAcquisitionGovernanceError("D3E seal cannot grant capital/LIVE authority")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class D3ECampaignSeal:
    seal_version: str
    d3d_plan_fingerprint: str
    descriptor_count: int
    predictive_descriptor_count: int
    economic_descriptor_count: int
    descriptor_seal_fingerprints: tuple[str, ...]
    sealed_at: str
    network_policy: str
    restart_policy: str
    storage_policy: str
    exact_complete_descriptor_family: bool
    predictive_economic_families_disjoint: bool
    all_descriptor_material_reverified: bool
    raw_market_bytes_acquired: bool
    scientific_holdout_evaluation_observed: bool
    feature_values_materialized: bool
    label_values_materialized: bool
    prediction_values_materialized: bool
    metrics_computed: bool
    d2j_or_d2m_commitment_created: bool
    holdout_permit_issued: bool
    holdout_permit_consumed: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.seal_version != OSS3D3E_CAMPAIGN_SEAL_VERSION:
            raise DualHoldoutRawAcquisitionIntegrityError("noncanonical D3E campaign seal version")
        if self.d3d_plan_fingerprint != D3D_PLAN_FINGERPRINT:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E campaign plan drifted")
        if self.descriptor_count != EXPECTED_DESCRIPTOR_COUNT:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E campaign must contain exactly 18 descriptors")
        if self.predictive_descriptor_count != EXPECTED_PREDICTIVE_DESCRIPTORS or self.economic_descriptor_count != EXPECTED_ECONOMIC_DESCRIPTORS:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E campaign must contain exact 9/9 families")
        if len(self.descriptor_seal_fingerprints) != EXPECTED_DESCRIPTOR_COUNT or len(set(self.descriptor_seal_fingerprints)) != EXPECTED_DESCRIPTOR_COUNT:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E campaign seal family is incomplete/duplicated")
        for value in self.descriptor_seal_fingerprints:
            _require_hash(value, "descriptor_seal_fingerprint")
        _parse_utc(self.sealed_at)
        if self.network_policy != NETWORK_POLICY or self.restart_policy != RESTART_POLICY or self.storage_policy != STORAGE_POLICY:
            raise DualHoldoutRawAcquisitionGovernanceError("D3E campaign policy drifted")
        if not (
            self.exact_complete_descriptor_family
            and self.predictive_economic_families_disjoint
            and self.all_descriptor_material_reverified
            and self.raw_market_bytes_acquired
        ):
            raise DualHoldoutRawAcquisitionGovernanceError("D3E complete seal requires exact disjoint fully-reverified raw family")
        if (
            self.scientific_holdout_evaluation_observed
            or self.feature_values_materialized
            or self.label_values_materialized
            or self.prediction_values_materialized
            or self.metrics_computed
            or self.d2j_or_d2m_commitment_created
            or self.holdout_permit_issued
            or self.holdout_permit_consumed
            or self.execution_authorized
            or self.paper_execution_authorized
        ):
            raise DualHoldoutRawAcquisitionGovernanceError("D3E campaign exceeds acquisition-only authority")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise DualHoldoutRawAcquisitionGovernanceError("D3E campaign cannot grant capital/LIVE authority")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        payload = {name: getattr(self, name) for name in self.__dataclass_fields__}
        payload["descriptor_seal_fingerprints"] = list(self.descriptor_seal_fingerprints)
        return payload


@dataclass(frozen=True, slots=True)
class D3ECampaignResult:
    result_version: str
    d3d_plan_fingerprint: str
    descriptor_count: int
    acquired_from_network: int
    reused_after_full_reverification: int
    reconciled_unsealed_final_material: int
    missing_without_network_authority: int
    complete: bool
    campaign_seal_fingerprint: str | None
    network_enabled: bool
    expected_get_count_if_fresh: int
    scientific_holdout_evaluation_observed: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.result_version != OSS3D3E_RESULT_VERSION or self.d3d_plan_fingerprint != D3D_PLAN_FINGERPRINT:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E result identity drifted")
        if self.descriptor_count != EXPECTED_DESCRIPTOR_COUNT:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E result descriptor count drifted")
        counters = (
            self.acquired_from_network,
            self.reused_after_full_reverification,
            self.reconciled_unsealed_final_material,
            self.missing_without_network_authority,
        )
        if any(value < 0 for value in counters) or sum(counters) != EXPECTED_DESCRIPTOR_COUNT:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E result counters are inconsistent")
        if self.complete != (self.missing_without_network_authority == 0):
            raise DualHoldoutRawAcquisitionIntegrityError("D3E completion state is inconsistent")
        if self.complete != (self.campaign_seal_fingerprint is not None):
            raise DualHoldoutRawAcquisitionIntegrityError("D3E campaign seal/completion mismatch")
        if self.campaign_seal_fingerprint is not None:
            _require_hash(self.campaign_seal_fingerprint, "campaign_seal_fingerprint")
        if self.expected_get_count_if_fresh != EXPECTED_DESCRIPTOR_COUNT * REQUESTS_PER_DESCRIPTOR:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E fresh network budget must be exactly 54 GETs")
        if self.scientific_holdout_evaluation_observed or self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise DualHoldoutRawAcquisitionGovernanceError("D3E result exceeds acquisition authority")


class SQLiteD3EAcquisitionLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS oss3d3e_descriptor_seals (
                    descriptor_fingerprint TEXT PRIMARY KEY,
                    purpose TEXT NOT NULL,
                    seal_fingerprint TEXT NOT NULL UNIQUE,
                    seal_json TEXT NOT NULL,
                    sealed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oss3d3e_campaign_seals (
                    d3d_plan_fingerprint TEXT PRIMARY KEY,
                    seal_fingerprint TEXT NOT NULL UNIQUE,
                    seal_json TEXT NOT NULL,
                    sealed_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS oss3d3e_descriptor_no_update
                BEFORE UPDATE ON oss3d3e_descriptor_seals
                BEGIN SELECT RAISE(ABORT, 'OSS3D3E_APPEND_ONLY'); END;
                CREATE TRIGGER IF NOT EXISTS oss3d3e_descriptor_no_delete
                BEFORE DELETE ON oss3d3e_descriptor_seals
                BEGIN SELECT RAISE(ABORT, 'OSS3D3E_APPEND_ONLY'); END;
                CREATE TRIGGER IF NOT EXISTS oss3d3e_campaign_no_update
                BEFORE UPDATE ON oss3d3e_campaign_seals
                BEGIN SELECT RAISE(ABORT, 'OSS3D3E_APPEND_ONLY'); END;
                CREATE TRIGGER IF NOT EXISTS oss3d3e_campaign_no_delete
                BEFORE DELETE ON oss3d3e_campaign_seals
                BEGIN SELECT RAISE(ABORT, 'OSS3D3E_APPEND_ONLY'); END;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_descriptor(self, fingerprint: str) -> D3EDescriptorSeal | None:
        with self._connect() as conn:
            row = conn.execute("SELECT seal_json, seal_fingerprint FROM oss3d3e_descriptor_seals WHERE descriptor_fingerprint = ?", (fingerprint,)).fetchone()
        if row is None:
            return None
        seal = _seal_from_json(str(row["seal_json"]))
        if seal.fingerprint != str(row["seal_fingerprint"]):
            raise DualHoldoutRawAcquisitionIntegrityError("D3E descriptor ledger fingerprint mismatch")
        return seal

    def put_descriptor(self, seal: D3EDescriptorSeal) -> None:
        existing = self.get_descriptor(seal.descriptor_fingerprint)
        if existing is not None:
            if existing != seal:
                raise DualHoldoutRawAcquisitionGovernanceError("D3E descriptor already sealed with different evidence")
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO oss3d3e_descriptor_seals(descriptor_fingerprint,purpose,seal_fingerprint,seal_json,sealed_at) VALUES(?,?,?,?,?)",
                (seal.descriptor_fingerprint, seal.purpose, seal.fingerprint, _canonical_json(seal.to_dict()), seal.sealed_at),
            )

    def descriptor_seals(self) -> tuple[D3EDescriptorSeal, ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT seal_json, seal_fingerprint FROM oss3d3e_descriptor_seals ORDER BY purpose, descriptor_fingerprint").fetchall()
        result = tuple(_seal_from_json(str(row["seal_json"])) for row in rows)
        for seal, row in zip(result, rows, strict=True):
            if seal.fingerprint != str(row["seal_fingerprint"]):
                raise DualHoldoutRawAcquisitionIntegrityError("D3E descriptor ledger fingerprint mismatch")
        return result

    def get_campaign(self) -> D3ECampaignSeal | None:
        with self._connect() as conn:
            row = conn.execute("SELECT seal_json, seal_fingerprint FROM oss3d3e_campaign_seals WHERE d3d_plan_fingerprint = ?", (D3D_PLAN_FINGERPRINT,)).fetchone()
        if row is None:
            return None
        seal = _campaign_seal_from_json(str(row["seal_json"]))
        if seal.fingerprint != str(row["seal_fingerprint"]):
            raise DualHoldoutRawAcquisitionIntegrityError("D3E campaign ledger fingerprint mismatch")
        return seal

    def put_campaign(self, seal: D3ECampaignSeal) -> None:
        existing = self.get_campaign()
        if existing is not None:
            if existing != seal:
                raise DualHoldoutRawAcquisitionGovernanceError("D3E campaign already sealed differently")
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO oss3d3e_campaign_seals(d3d_plan_fingerprint,seal_fingerprint,seal_json,sealed_at) VALUES(?,?,?,?)",
                (seal.d3d_plan_fingerprint, seal.fingerprint, _canonical_json(seal.to_dict()), seal.sealed_at),
            )


def require_exact_d3d_registry_read_only(path: str | Path) -> DualHoldoutAcquisitionPlan:
    plan = canonical_oss3d3d_dual_holdout_plan()
    if plan.fingerprint != D3D_PLAN_FINGERPRINT:
        raise DualHoldoutRawAcquisitionIntegrityError("compiled D3D plan fingerprint drifted")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise DualHoldoutRawAcquisitionGovernanceError("D3D durable preregistration is required before network")
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        row = conn.execute(
            f"SELECT fingerprint, canonical_json FROM {OSS3D3D_REGISTRY_TABLE} WHERE plan_id = ?",
            (plan.plan_id,),
        ).fetchone()
        if row is None:
            raise DualHoldoutRawAcquisitionGovernanceError("canonical D3D plan missing from durable preregistration")
        if str(row["fingerprint"]) != plan.fingerprint or str(row["canonical_json"]) != _canonical_json(plan.to_dict()):
            raise DualHoldoutRawAcquisitionIntegrityError("D3D durable preregistration differs from canonical D3D plan")
    finally:
        conn.close()
    return plan


def descriptor_purpose(plan: DualHoldoutAcquisitionPlan, descriptor: BinanceSpotArchiveDescriptor) -> str:
    fingerprint = descriptor.fingerprint
    predictive = {item.fingerprint for item in plan.predictive.descriptors}
    economic = {item.fingerprint for item in plan.economic.descriptors}
    in_predictive = fingerprint in predictive
    in_economic = fingerprint in economic
    if in_predictive == in_economic:
        raise DualHoldoutRawAcquisitionGovernanceError("descriptor must belong to exactly one D3D holdout family")
    return PREDICTIVE_PURPOSE if in_predictive else ECONOMIC_PURPOSE


def build_d3e_transport(plan: DualHoldoutAcquisitionPlan) -> UrllibReadOnlyTransport:
    descriptors = plan.predictive.descriptors + plan.economic.descriptors
    allowed = frozenset(path for item in descriptors for path in (item.relative_archive_path, item.relative_checksum_path))
    return UrllibReadOnlyTransport(policy=PublicDataPolicy(allowed_host=PROVIDER_HOST, allowed_paths=allowed), max_response_bytes=MAX_ARCHIVE_BYTES)


def acquire_d3d_preregistered_descriptor(
    *,
    d3d_registry_path: str | Path,
    descriptor: BinanceSpotArchiveDescriptor,
    transport: ReadOnlyHttpTransport,
    now: datetime,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[bytes, str, HistoricalMarketSnapshotArtifact, D3EArchiveAcquisitionReceipt]:
    plan = require_exact_d3d_registry_read_only(d3d_registry_path)
    purpose = descriptor_purpose(plan, descriptor)
    _require_aware(now, "now")
    if not 0 < timeout_seconds <= 30:
        raise DualHoldoutRawAcquisitionGovernanceError("D3E timeout must be >0 and <=30 seconds")
    policy = PublicDataPolicy(
        allowed_host=PROVIDER_HOST,
        allowed_paths=frozenset({descriptor.relative_archive_path, descriptor.relative_checksum_path}),
    )
    checksum_request = ReadOnlyRequest(method="GET", url=descriptor.checksum_url, timeout_seconds=timeout_seconds)
    archive_request = ReadOnlyRequest(method="GET", url=descriptor.archive_url, timeout_seconds=timeout_seconds)
    checksum_a = _send_exact(transport, policy, checksum_request, max_bytes=CHECKSUM_MAX_BYTES)
    archive = _send_exact(transport, policy, archive_request, max_bytes=MAX_ARCHIVE_BYTES)
    checksum_b = _send_exact(transport, policy, checksum_request, max_bytes=CHECKSUM_MAX_BYTES)
    if checksum_a.body != checksum_b.body:
        raise DualHoldoutRawAcquisitionIntegrityError("provider CHECKSUM changed during D3E acquisition attempt")
    try:
        checksum_text = checksum_a.body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DualHoldoutRawAcquisitionIntegrityError("D3E CHECKSUM is not UTF-8") from exc
    snapshot = build_binance_spot_archive_snapshot(descriptor=descriptor, archive_bytes=archive.body, checksum_text=checksum_text)
    receipt = D3EArchiveAcquisitionReceipt(
        receipt_version=OSS3D3E_RECEIPT_VERSION,
        d3d_plan_fingerprint=plan.fingerprint,
        purpose=purpose,
        descriptor_fingerprint=descriptor.fingerprint,
        symbol=descriptor.instrument.symbol,
        period=descriptor.period,
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
        raw_market_bytes_acquired=True,
        d2t_integrity_normalization_performed=True,
        feature_values_materialized=False,
        label_values_materialized=False,
        prediction_values_materialized=False,
        metrics_computed=False,
        scientific_holdout_evaluation_observed=False,
        d2j_or_d2m_commitment_created=False,
        holdout_permit_issued=False,
        holdout_permit_consumed=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )
    return archive.body, checksum_text, snapshot, receipt


def run_dual_holdout_acquisition(
    *,
    d3d_registry_path: str | Path,
    evidence_root: str | Path,
    now: datetime,
    allow_network: bool,
    transport: ReadOnlyHttpTransport | None = None,
) -> D3ECampaignResult:
    plan = require_exact_d3d_registry_read_only(d3d_registry_path)
    root = _validated_evidence_root(evidence_root)
    root.mkdir(parents=True, exist_ok=True)
    ledger = SQLiteD3EAcquisitionLedger(root / LEDGER_FILENAME)
    descriptors = tuple((PREDICTIVE_PURPOSE, item) for item in plan.predictive.descriptors) + tuple((ECONOMIC_PURPOSE, item) for item in plan.economic.descriptors)
    if len(descriptors) != EXPECTED_DESCRIPTOR_COUNT:
        raise DualHoldoutRawAcquisitionIntegrityError("D3E canonical family is not exactly 18 descriptors")
    selected_transport = transport
    if allow_network and selected_transport is None:
        selected_transport = build_d3e_transport(plan)

    acquired = reused = reconciled = missing = 0
    verified_seals: list[D3EDescriptorSeal] = []
    for purpose, descriptor in descriptors:
        if descriptor_purpose(plan, descriptor) != purpose:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E purpose/descriptor cross-wire")
        final_dir = root / purpose / descriptor.instrument.symbol / descriptor.period
        stage_dir = root / STAGING_DIRNAME / purpose / descriptor.instrument.symbol / descriptor.period
        existing = ledger.get_descriptor(descriptor.fingerprint)
        if existing is not None:
            verified_seals.append(_reverify_final_material(final_dir, descriptor, purpose, existing))
            reused += 1
            continue
        if stage_dir.exists():
            raise DualHoldoutRawAcquisitionGovernanceError(f"stale D3E staging directory requires operator resolution: {stage_dir}")
        if final_dir.exists():
            seal = _seal_verified_unsealed(final_dir, descriptor, purpose, now)
            ledger.put_descriptor(seal)
            verified_seals.append(seal)
            reconciled += 1
            continue
        if not allow_network:
            missing += 1
            continue
        if selected_transport is None:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E network enabled without transport")
        archive_bytes, checksum_text, snapshot, receipt = acquire_d3d_preregistered_descriptor(
            d3d_registry_path=d3d_registry_path,
            descriptor=descriptor,
            transport=selected_transport,
            now=now,
        )
        stage_dir.mkdir(parents=True, exist_ok=False)
        _write_material(stage_dir, descriptor, archive_bytes, checksum_text, snapshot, receipt)
        _reverify_material(stage_dir, descriptor, purpose)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        if final_dir.exists():
            raise DualHoldoutRawAcquisitionGovernanceError("D3E final evidence directory appeared during acquisition")
        stage_dir.replace(final_dir)
        seal = _seal_verified_unsealed(final_dir, descriptor, purpose, now)
        ledger.put_descriptor(seal)
        verified_seals.append(seal)
        acquired += 1

    campaign_fingerprint: str | None = None
    complete = missing == 0
    if complete:
        if len(verified_seals) != EXPECTED_DESCRIPTOR_COUNT:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E complete run missing verified seals")
        predictive_set = {s.descriptor_fingerprint for s in verified_seals if s.purpose == PREDICTIVE_PURPOSE}
        economic_set = {s.descriptor_fingerprint for s in verified_seals if s.purpose == ECONOMIC_PURPOSE}
        if len(predictive_set) != 9 or len(economic_set) != 9 or predictive_set & economic_set:
            raise DualHoldoutRawAcquisitionIntegrityError("D3E 9/9 family separation failed")
        campaign = D3ECampaignSeal(
            seal_version=OSS3D3E_CAMPAIGN_SEAL_VERSION,
            d3d_plan_fingerprint=plan.fingerprint,
            descriptor_count=18,
            predictive_descriptor_count=9,
            economic_descriptor_count=9,
            descriptor_seal_fingerprints=tuple(sorted(s.fingerprint for s in verified_seals)),
            sealed_at=now.astimezone(timezone.utc).isoformat(),
            network_policy=NETWORK_POLICY,
            restart_policy=RESTART_POLICY,
            storage_policy=STORAGE_POLICY,
            exact_complete_descriptor_family=True,
            predictive_economic_families_disjoint=True,
            all_descriptor_material_reverified=True,
            raw_market_bytes_acquired=True,
            scientific_holdout_evaluation_observed=False,
            feature_values_materialized=False,
            label_values_materialized=False,
            prediction_values_materialized=False,
            metrics_computed=False,
            d2j_or_d2m_commitment_created=False,
            holdout_permit_issued=False,
            holdout_permit_consumed=False,
            execution_authorized=False,
            paper_execution_authorized=False,
            capital_authority="NONE",
            live_trading="BLOCKED",
        )
        ledger.put_campaign(campaign)
        campaign_fingerprint = campaign.fingerprint
    return D3ECampaignResult(
        result_version=OSS3D3E_RESULT_VERSION,
        d3d_plan_fingerprint=plan.fingerprint,
        descriptor_count=18,
        acquired_from_network=acquired,
        reused_after_full_reverification=reused,
        reconciled_unsealed_final_material=reconciled,
        missing_without_network_authority=missing,
        complete=complete,
        campaign_seal_fingerprint=campaign_fingerprint,
        network_enabled=allow_network,
        expected_get_count_if_fresh=54,
        scientific_holdout_evaluation_observed=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )


def _write_material(
    directory: Path,
    descriptor: BinanceSpotArchiveDescriptor,
    archive_bytes: bytes,
    checksum_text: str,
    snapshot: HistoricalMarketSnapshotArtifact,
    receipt: D3EArchiveAcquisitionReceipt,
) -> None:
    _atomic_write(directory / descriptor.archive_filename, archive_bytes)
    _atomic_write(directory / descriptor.checksum_filename, checksum_text.encode("utf-8"))
    snapshot.write(directory / "d2t-snapshot.json")
    _atomic_write(directory / "d3e-acquisition-receipt.json", (_canonical_json(receipt.to_dict()) + "\n").encode("utf-8"))


def _reverify_material(directory: Path, descriptor: BinanceSpotArchiveDescriptor, purpose: str) -> D3EArchiveAcquisitionReceipt:
    archive_path = directory / descriptor.archive_filename
    checksum_path = directory / descriptor.checksum_filename
    snapshot_path = directory / "d2t-snapshot.json"
    receipt_path = directory / "d3e-acquisition-receipt.json"
    if not all(path.is_file() for path in (archive_path, checksum_path, snapshot_path, receipt_path)):
        raise DualHoldoutRawAcquisitionIntegrityError("D3E final material is incomplete")
    archive_bytes = archive_path.read_bytes()
    try:
        checksum_text = checksum_path.read_text(encoding="utf-8")
        raw_receipt = receipt_path.read_bytes()
        document = json.loads(raw_receipt.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DualHoldoutRawAcquisitionIntegrityError("D3E retained text evidence is invalid") from exc
    if raw_receipt != (_canonical_json(document) + "\n").encode("utf-8"):
        raise DualHoldoutRawAcquisitionIntegrityError("D3E receipt serialization is noncanonical")
    receipt = D3EArchiveAcquisitionReceipt(**document)
    if receipt.purpose != purpose or receipt.descriptor_fingerprint != descriptor.fingerprint:
        raise DualHoldoutRawAcquisitionIntegrityError("D3E retained receipt identity differs from planned descriptor")
    if sha256(archive_bytes).hexdigest() != receipt.archive_payload_sha256:
        raise DualHoldoutRawAcquisitionIntegrityError("D3E retained archive bytes drifted")
    if sha256(checksum_text.encode("utf-8")).hexdigest() != receipt.checksum_a_payload_sha256:
        raise DualHoldoutRawAcquisitionIntegrityError("D3E retained checksum drifted")
    snapshot = HistoricalMarketSnapshotArtifact.read(snapshot_path)
    if snapshot.artifact_hash != receipt.d2t_snapshot_artifact_hash:
        raise DualHoldoutRawAcquisitionIntegrityError("D3E retained D2T artifact differs from receipt")
    snapshot.verify_source(descriptor=descriptor, archive_bytes=archive_bytes, checksum_text=checksum_text)
    return receipt


def _seal_verified_unsealed(directory: Path, descriptor: BinanceSpotArchiveDescriptor, purpose: str, now: datetime) -> D3EDescriptorSeal:
    receipt = _reverify_material(directory, descriptor, purpose)
    return D3EDescriptorSeal(
        seal_version=OSS3D3E_SEAL_VERSION,
        d3d_plan_fingerprint=D3D_PLAN_FINGERPRINT,
        purpose=purpose,
        descriptor_fingerprint=descriptor.fingerprint,
        symbol=descriptor.instrument.symbol,
        period=descriptor.period,
        archive_sha256=receipt.archive_payload_sha256,
        checksum_payload_sha256=receipt.checksum_a_payload_sha256,
        d2t_snapshot_artifact_hash=receipt.d2t_snapshot_artifact_hash,
        d2t_normalized_dataset_hash=receipt.d2t_normalized_dataset_hash,
        acquisition_receipt_fingerprint=receipt.fingerprint,
        evidence_relative_directory=f"{purpose}/{descriptor.instrument.symbol}/{descriptor.period}",
        sealed_at=now.astimezone(timezone.utc).isoformat(),
        restart_policy=RESTART_POLICY,
        staging_policy=STAGING_POLICY,
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


def _reverify_final_material(directory: Path, descriptor: BinanceSpotArchiveDescriptor, purpose: str, seal: D3EDescriptorSeal) -> D3EDescriptorSeal:
    receipt = _reverify_material(directory, descriptor, purpose)
    if (
        seal.purpose != purpose
        or seal.descriptor_fingerprint != descriptor.fingerprint
        or seal.archive_sha256 != receipt.archive_payload_sha256
        or seal.checksum_payload_sha256 != receipt.checksum_a_payload_sha256
        or seal.d2t_snapshot_artifact_hash != receipt.d2t_snapshot_artifact_hash
        or seal.d2t_normalized_dataset_hash != receipt.d2t_normalized_dataset_hash
        or seal.acquisition_receipt_fingerprint != receipt.fingerprint
    ):
        raise DualHoldoutRawAcquisitionIntegrityError("D3E sealed evidence no longer matches fully reverified material")
    return seal


def _send_exact(transport: ReadOnlyHttpTransport, policy: PublicDataPolicy, request: ReadOnlyRequest, *, max_bytes: int) -> HttpResponse:
    policy.validate(request)
    try:
        response = transport.send(request)
    except Exception as exc:
        raise DualHoldoutRawAcquisitionError("D3E public archive transport failed") from exc
    policy.validate_final_url(response.final_url)
    if response.final_url != request.url:
        raise DualHoldoutRawAcquisitionGovernanceError("D3E redirects are forbidden")
    if response.status_code != 200:
        raise DualHoldoutRawAcquisitionError(f"D3E public archive response status is {response.status_code}")
    if not isinstance(response.body, bytes) or not 1 <= len(response.body) <= max_bytes:
        raise DualHoldoutRawAcquisitionGovernanceError("D3E public archive response size is outside bound")
    return response


def _validated_evidence_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    repo = Path(__file__).resolve().parents[2]
    if root == repo or repo in root.parents:
        raise DualHoldoutRawAcquisitionGovernanceError("D3E real evidence root must be outside git repository")
    return root


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _seal_from_json(raw: str) -> D3EDescriptorSeal:
    try:
        return D3EDescriptorSeal(**json.loads(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DualHoldoutRawAcquisitionIntegrityError("invalid D3E durable descriptor seal") from exc


def _campaign_seal_from_json(raw: str) -> D3ECampaignSeal:
    try:
        values = json.loads(raw)
        values["descriptor_seal_fingerprints"] = tuple(values["descriptor_seal_fingerprints"])
        return D3ECampaignSeal(**values)
    except (TypeError, ValueError, json.JSONDecodeError, KeyError) as exc:
        raise DualHoldoutRawAcquisitionIntegrityError("invalid D3E durable campaign seal") from exc


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DualHoldoutRawAcquisitionIntegrityError("invalid D3E timestamp") from exc
    _require_aware(parsed, "timestamp")
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        raise DualHoldoutRawAcquisitionIntegrityError("D3E timestamp must be canonical UTC")
    return normalized


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{name} must be lowercase sha256")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()
