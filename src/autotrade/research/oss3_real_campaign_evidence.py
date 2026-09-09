from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import string

from .oss3_market_collection import canonical_oss3d2u_collection_plan


OSS3D2Y_CONTRACT_VERSION = "OSS3D2Y_DURABLE_REAL_CAMPAIGN_EVIDENCE_SEAL_V1"
D2X_CERTIFIED_COMMIT = "a2abb8fa73ef3b40c4d8523fc326df1e71a06355"
OPERATIONAL_WRAPPER_COMMIT = "1aa46b5664e03f16a7855fa8be896b5f8436674b"
WORKFLOW_RUN_ID = 34309982898
ARTIFACT_ID = 10088057589
ARTIFACT_NAME = "oss3d2x-v2-real-campaign-20260908"
ARTIFACT_SIZE_BYTES = 6_188_378
ARTIFACT_ZIP_SHA256 = "23598e3bd2f42b645095291115451d115dae60455b813c5653f28ebd5a6b7dc7"
ARTIFACT_EXPIRES_AT = "2026-10-09T04:14:03Z"

COLLECTION_ID = "oss3d2u-binance-spot-btc-eth-sol-1h-2023apr-2025-v2"
PLAN_FINGERPRINT = "f43abdcd532f07d37b54f93abafbd3b29a3c3ee07afeedbae95b7aa15153c59d"
DESCRIPTOR_COUNT = 99
CAMPAIGN_SEAL_FINGERPRINT = "4f49d8899e4b11685165cbea948bd09232a3747aef90344606e6a5ac27857f7d"
D2U_PARTITION_MATERIAL_FINGERPRINT = "7cac5172e70ecf6b36c9d51e433ce57184586b08ff525ea94af63ef331a3f873"
TRAINING_UNIVERSE_HASH = "0e998c27a9636f1005b429f052ec902f0b9fb91d66e235dc0c3b2f090fa3cd69"
DEVELOPMENT_UNIVERSE_HASH = "5fb5866a084f97cebff3b75f11be9f9ecf667880fc11584ad52b53cfdd820c9d"

FIRST_RESULT_SHA256 = "1f099595c716db489b5e39eb3a976765f524c7252f276042b438eccdc711bbc0"
OFFLINE_RESULT_SHA256 = "1b64445ac17cd27dcf9093bc14c4c95ab1fae4f698b5754290ec81416e47bc5b"
SOURCE_INVENTORY_SHA256 = "94d47d9bfef334b590a0946fa0094b6534c542f1e5ef41d6a481b893f5a6ce57"
SOURCE_FILE_COUNT = 398
SOURCE_TOTAL_BYTES = 12_733_744
DESCRIPTOR_EVIDENCE_FILE_COUNT = 396
D2U_PLAN_REGISTRY_SHA256 = "dcf7746539618e81cfb5952a9dbdc6bd9133b6cf929f672612e6de8dcdd8ca64"
D2V_CAMPAIGN_REGISTRY_SHA256 = "5dd3598ae1f8306a62933adebeee1d23f70f86423674312ccb937b61aa978857"
EVIDENCE_TAR_SHA256 = "c5d0f694a68f8bf7b67c35774900efaaaad7b425e95735f60660824e07446277"
EVIDENCE_TAR_CHECKSUM_FILE_SHA256 = "95000f9bde4deaa56aefb6fdffeaf513433f86896b27030e775f248ce032e813"
EXPECTED_SEAL_FINGERPRINT = "919009bf61a1a3512e99c3b8edb21017b8d0de4f97722dcc3849f5ba1b5f9beb"


class RealCampaignEvidenceSealError(RuntimeError):
    pass


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in string.hexdigits for character in value)


def _is_git_commit(value: str) -> bool:
    return len(value) == 40 and all(character in string.hexdigits for character in value)


@dataclass(frozen=True, slots=True)
class DurableRealCampaignEvidenceSeal:
    contract_version: str
    d2x_certified_commit: str
    operational_wrapper_commit: str
    workflow_run_id: int
    artifact_id: int
    artifact_name: str
    artifact_size_bytes: int
    artifact_zip_sha256: str
    artifact_expires_at: str
    collection_id: str
    plan_fingerprint: str
    descriptor_count: int
    campaign_seal_fingerprint: str
    d2u_partition_material_fingerprint: str
    training_universe_hash: str
    development_universe_hash: str
    first_result_sha256: str
    offline_result_sha256: str
    source_inventory_sha256: str
    source_file_count: int
    source_total_bytes: int
    descriptor_evidence_file_count: int
    d2u_plan_registry_sha256: str
    d2v_campaign_registry_sha256: str
    evidence_tar_sha256: str
    evidence_tar_checksum_file_sha256: str
    first_pass_network_enabled: bool
    first_pass_acquired_from_network: int
    first_pass_reused_after_seal_reverification: int
    first_pass_missing_without_network_authority: int
    first_pass_complete: bool
    offline_network_enabled: bool
    offline_acquired_from_network: int
    offline_reused_after_seal_reverification: int
    offline_missing_without_network_authority: int
    offline_complete: bool
    same_campaign_seal: bool
    same_partition_material: bool
    same_training_universe: bool
    same_development_universe: bool
    final_holdout_values_loaded: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.contract_version != OSS3D2Y_CONTRACT_VERSION:
            raise RealCampaignEvidenceSealError("D2Y contract version mismatch")
        if not _is_git_commit(self.d2x_certified_commit):
            raise RealCampaignEvidenceSealError("D2Y certified D2X commit must be a 40-hex Git commit id")
        if not _is_git_commit(self.operational_wrapper_commit):
            raise RealCampaignEvidenceSealError("D2Y operational wrapper commit must be a 40-hex Git commit id")
        if self.descriptor_count != DESCRIPTOR_COUNT:
            raise RealCampaignEvidenceSealError("D2Y descriptor count must remain 99")
        if self.source_file_count != SOURCE_FILE_COUNT:
            raise RealCampaignEvidenceSealError("D2Y source evidence file count must remain 398")
        if self.descriptor_evidence_file_count != DESCRIPTOR_EVIDENCE_FILE_COUNT:
            raise RealCampaignEvidenceSealError("D2Y descriptor evidence file count must remain 396")
        if self.descriptor_evidence_file_count != self.descriptor_count * 4:
            raise RealCampaignEvidenceSealError("D2Y requires four evidence files per descriptor")
        if self.source_file_count != self.descriptor_evidence_file_count + 2:
            raise RealCampaignEvidenceSealError("D2Y requires exactly two registry files beyond descriptor evidence")
        if self.source_total_bytes <= 0 or self.artifact_size_bytes <= 0:
            raise RealCampaignEvidenceSealError("D2Y evidence byte counts must be positive")
        if self.workflow_run_id <= 0 or self.artifact_id <= 0:
            raise RealCampaignEvidenceSealError("D2Y workflow/artifact identifiers must be positive")
        for name, value in self._sha256_fields().items():
            if not _is_sha256(value):
                raise RealCampaignEvidenceSealError(f"D2Y {name} must be a SHA-256 hex digest")

    def _sha256_fields(self) -> dict[str, str]:
        return {
            "artifact_zip_sha256": self.artifact_zip_sha256,
            "plan_fingerprint": self.plan_fingerprint,
            "campaign_seal_fingerprint": self.campaign_seal_fingerprint,
            "d2u_partition_material_fingerprint": self.d2u_partition_material_fingerprint,
            "training_universe_hash": self.training_universe_hash,
            "development_universe_hash": self.development_universe_hash,
            "first_result_sha256": self.first_result_sha256,
            "offline_result_sha256": self.offline_result_sha256,
            "source_inventory_sha256": self.source_inventory_sha256,
            "d2u_plan_registry_sha256": self.d2u_plan_registry_sha256,
            "d2v_campaign_registry_sha256": self.d2v_campaign_registry_sha256,
            "evidence_tar_sha256": self.evidence_tar_sha256,
            "evidence_tar_checksum_file_sha256": self.evidence_tar_checksum_file_sha256,
        }

    def payload(self) -> dict[str, object]:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(self.payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(canonical).hexdigest()


def canonical_oss3d2y_real_campaign_evidence_seal() -> DurableRealCampaignEvidenceSeal:
    return DurableRealCampaignEvidenceSeal(
        contract_version=OSS3D2Y_CONTRACT_VERSION,
        d2x_certified_commit=D2X_CERTIFIED_COMMIT,
        operational_wrapper_commit=OPERATIONAL_WRAPPER_COMMIT,
        workflow_run_id=WORKFLOW_RUN_ID,
        artifact_id=ARTIFACT_ID,
        artifact_name=ARTIFACT_NAME,
        artifact_size_bytes=ARTIFACT_SIZE_BYTES,
        artifact_zip_sha256=ARTIFACT_ZIP_SHA256,
        artifact_expires_at=ARTIFACT_EXPIRES_AT,
        collection_id=COLLECTION_ID,
        plan_fingerprint=PLAN_FINGERPRINT,
        descriptor_count=DESCRIPTOR_COUNT,
        campaign_seal_fingerprint=CAMPAIGN_SEAL_FINGERPRINT,
        d2u_partition_material_fingerprint=D2U_PARTITION_MATERIAL_FINGERPRINT,
        training_universe_hash=TRAINING_UNIVERSE_HASH,
        development_universe_hash=DEVELOPMENT_UNIVERSE_HASH,
        first_result_sha256=FIRST_RESULT_SHA256,
        offline_result_sha256=OFFLINE_RESULT_SHA256,
        source_inventory_sha256=SOURCE_INVENTORY_SHA256,
        source_file_count=SOURCE_FILE_COUNT,
        source_total_bytes=SOURCE_TOTAL_BYTES,
        descriptor_evidence_file_count=DESCRIPTOR_EVIDENCE_FILE_COUNT,
        d2u_plan_registry_sha256=D2U_PLAN_REGISTRY_SHA256,
        d2v_campaign_registry_sha256=D2V_CAMPAIGN_REGISTRY_SHA256,
        evidence_tar_sha256=EVIDENCE_TAR_SHA256,
        evidence_tar_checksum_file_sha256=EVIDENCE_TAR_CHECKSUM_FILE_SHA256,
        first_pass_network_enabled=True,
        first_pass_acquired_from_network=99,
        first_pass_reused_after_seal_reverification=0,
        first_pass_missing_without_network_authority=0,
        first_pass_complete=True,
        offline_network_enabled=False,
        offline_acquired_from_network=0,
        offline_reused_after_seal_reverification=99,
        offline_missing_without_network_authority=0,
        offline_complete=True,
        same_campaign_seal=True,
        same_partition_material=True,
        same_training_universe=True,
        same_development_universe=True,
        final_holdout_values_loaded=False,
        promotion_authorized=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )


def verify_oss3d2y_real_campaign_evidence_seal(seal: DurableRealCampaignEvidenceSeal) -> None:
    expected = canonical_oss3d2y_real_campaign_evidence_seal()
    if seal != expected:
        raise RealCampaignEvidenceSealError("D2Y evidence seal differs from the certified real campaign")
    if seal.fingerprint != EXPECTED_SEAL_FINGERPRINT:
        raise RealCampaignEvidenceSealError("D2Y evidence seal fingerprint drifted")

    plan = canonical_oss3d2u_collection_plan()
    if plan.collection_id != seal.collection_id:
        raise RealCampaignEvidenceSealError("D2Y collection id does not rebind to current D2U V2 plan")
    if plan.fingerprint != seal.plan_fingerprint:
        raise RealCampaignEvidenceSealError("D2Y plan fingerprint does not rebind to current D2U V2 plan")
    if len(plan.descriptors) != seal.descriptor_count:
        raise RealCampaignEvidenceSealError("D2Y descriptor count does not rebind to current D2U V2 plan")

    if not seal.first_pass_network_enabled or seal.first_pass_acquired_from_network != 99:
        raise RealCampaignEvidenceSealError("D2Y first pass must prove 99 real provider acquisitions")
    if seal.first_pass_reused_after_seal_reverification != 0:
        raise RealCampaignEvidenceSealError("D2Y first pass may not claim reused descriptor evidence")
    if seal.first_pass_missing_without_network_authority != 0 or not seal.first_pass_complete:
        raise RealCampaignEvidenceSealError("D2Y first pass must be complete with zero missing descriptors")

    if seal.offline_network_enabled or seal.offline_acquired_from_network != 0:
        raise RealCampaignEvidenceSealError("D2Y second pass must have zero network acquisition authority")
    if seal.offline_reused_after_seal_reverification != 99:
        raise RealCampaignEvidenceSealError("D2Y second pass must reverify all 99 sealed descriptors")
    if seal.offline_missing_without_network_authority != 0 or not seal.offline_complete:
        raise RealCampaignEvidenceSealError("D2Y second pass must be complete with zero missing descriptors")
    if not all(
        (
            seal.same_campaign_seal,
            seal.same_partition_material,
            seal.same_training_universe,
            seal.same_development_universe,
        )
    ):
        raise RealCampaignEvidenceSealError("D2Y offline reverification must reproduce every frozen campaign identity")

    if seal.final_holdout_values_loaded:
        raise RealCampaignEvidenceSealError("D2Y may not load FINAL_HOLDOUT values")
    if seal.promotion_authorized or seal.execution_authorized or seal.paper_execution_authorized:
        raise RealCampaignEvidenceSealError("D2Y may not authorize promotion or execution")
    if seal.capital_authority != "NONE" or seal.live_trading != "BLOCKED":
        raise RealCampaignEvidenceSealError("D2Y has no capital/LIVE authority")
