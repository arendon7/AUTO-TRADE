from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autotrade.research.oss3_real_campaign_evidence import (  # noqa: E402
    ARTIFACT_ID,
    ARTIFACT_ZIP_SHA256,
    CAMPAIGN_SEAL_FINGERPRINT,
    DESCRIPTOR_COUNT,
    D2U_PARTITION_MATERIAL_FINGERPRINT,
    D2X_CERTIFIED_COMMIT,
    EXPECTED_SEAL_FINGERPRINT,
    PLAN_FINGERPRINT,
    SOURCE_FILE_COUNT,
    SOURCE_INVENTORY_SHA256,
    TRAINING_UNIVERSE_HASH,
    DEVELOPMENT_UNIVERSE_HASH,
    WORKFLOW_RUN_ID,
    canonical_oss3d2y_real_campaign_evidence_seal,
    verify_oss3d2y_real_campaign_evidence_seal,
)


MODULE = ROOT / "src/autotrade/research/oss3_real_campaign_evidence.py"
TEST = ROOT / "tests/test_research_oss3_real_campaign_evidence.py"
DOC = ROOT / "knowledge/20_RESEARCH/OSS3D2Y_DURABLE_REAL_CAMPAIGN_EVIDENCE_SEAL.md"


class BoundaryFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BoundaryFailure(message)


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def main() -> int:
    require(MODULE.is_file(), "D2Y evidence module is missing")
    require(TEST.is_file(), "D2Y adversarial test suite is missing")
    require(DOC.is_file(), "D2Y knowledge contract is missing")

    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))

    seal = canonical_oss3d2y_real_campaign_evidence_seal()
    verify_oss3d2y_real_campaign_evidence_seal(seal)

    require(seal.d2x_certified_commit == D2X_CERTIFIED_COMMIT, "D2Y certified D2X commit drifted")
    require(seal.workflow_run_id == WORKFLOW_RUN_ID, "D2Y workflow run id drifted")
    require(seal.artifact_id == ARTIFACT_ID, "D2Y artifact id drifted")
    require(seal.artifact_zip_sha256 == ARTIFACT_ZIP_SHA256, "D2Y artifact digest drifted")
    require(seal.plan_fingerprint == PLAN_FINGERPRINT, "D2Y plan fingerprint drifted")
    require(seal.descriptor_count == DESCRIPTOR_COUNT == 99, "D2Y descriptor count must remain 99")
    require(seal.campaign_seal_fingerprint == CAMPAIGN_SEAL_FINGERPRINT, "D2Y campaign seal drifted")
    require(seal.d2u_partition_material_fingerprint == D2U_PARTITION_MATERIAL_FINGERPRINT, "D2Y D2U partition material drifted")
    require(seal.training_universe_hash == TRAINING_UNIVERSE_HASH, "D2Y TRAIN universe identity drifted")
    require(seal.development_universe_hash == DEVELOPMENT_UNIVERSE_HASH, "D2Y DEVELOPMENT universe identity drifted")
    require(seal.source_inventory_sha256 == SOURCE_INVENTORY_SHA256, "D2Y inventory commitment drifted")
    require(seal.source_file_count == SOURCE_FILE_COUNT == 398, "D2Y source file count must remain 398")
    require(seal.fingerprint == EXPECTED_SEAL_FINGERPRINT, "D2Y seal fingerprint drifted")

    require(seal.first_pass_network_enabled is True, "D2Y first pass must represent real provider acquisition")
    require(seal.first_pass_acquired_from_network == 99, "D2Y first pass must bind 99 provider acquisitions")
    require(seal.first_pass_complete is True, "D2Y first pass must be complete")
    require(seal.offline_network_enabled is False, "D2Y second pass must have no network authority")
    require(seal.offline_acquired_from_network == 0, "D2Y second pass must acquire zero files")
    require(seal.offline_reused_after_seal_reverification == 99, "D2Y second pass must reverify 99 sealed descriptors")
    require(seal.offline_complete is True, "D2Y offline reverification must be complete")
    require(seal.same_campaign_seal and seal.same_partition_material, "D2Y offline pass must reproduce campaign/material identity")
    require(seal.same_training_universe and seal.same_development_universe, "D2Y offline pass must reproduce TRAIN/DEVELOPMENT universes")

    require(seal.final_holdout_values_loaded is False, "D2Y may not load FINAL_HOLDOUT values")
    require(seal.promotion_authorized is False, "D2Y may not authorize promotion")
    require(seal.execution_authorized is False, "D2Y may not authorize execution")
    require(seal.paper_execution_authorized is False, "D2Y may not authorize PAPER execution")
    require(seal.capital_authority == "NONE", "D2Y capital authority must remain NONE")
    require(seal.live_trading == "BLOCKED", "D2Y LIVE trading must remain BLOCKED")

    for marker in (
        "OSS3D2Y_DURABLE_REAL_CAMPAIGN_EVIDENCE_SEAL_V1",
        D2X_CERTIFIED_COMMIT,
        str(WORKFLOW_RUN_ID),
        str(ARTIFACT_ID),
        ARTIFACT_ZIP_SHA256,
        PLAN_FINGERPRINT,
        CAMPAIGN_SEAL_FINGERPRINT,
        D2U_PARTITION_MATERIAL_FINGERPRINT,
        TRAINING_UNIVERSE_HASH,
        DEVELOPMENT_UNIVERSE_HASH,
        SOURCE_INVENTORY_SHA256,
        EXPECTED_SEAL_FINGERPRINT,
        "first_pass_acquired_from_network=99",
        "offline_acquired_from_network=0",
        "offline_reused_after_seal_reverification=99",
        "final_holdout_values_loaded=False",
        'capital_authority="NONE"',
        'live_trading="BLOCKED"',
    ):
        require(marker in source, f"missing D2Y source boundary marker: {marker}")

    forbidden_import_fragments = (
        "qlib",
        "broker",
        "oms",
        "safety",
        "order_intent",
        "final_holdout",
        "development_evaluation",
        "family_runner",
        "dataset_adapter",
    )
    forbidden_roots = {"socket", "urllib", "requests", "httpx", "aiohttp", "subprocess"}
    forbidden_calls = {
        "urlopen",
        "urllib.request.urlopen",
        "requests.get",
        "requests.request",
        "httpx.get",
        "httpx.request",
        "socket.socket",
        "subprocess.run",
        "subprocess.Popen",
        "os.system",
        "os.popen",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0].lower()
                require(root not in forbidden_roots, f"D2Y evidence module may not import network/process root: {alias.name}")
                require(not any(fragment in alias.name.lower() for fragment in forbidden_import_fragments), f"forbidden D2Y import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            require(not any(fragment in module for fragment in forbidden_import_fragments), f"forbidden D2Y import: {module}")
        elif isinstance(node, ast.Call):
            called = dotted_name(node.func)
            require(called not in forbidden_calls, f"D2Y evidence module may not perform network/process call: {called}")

    lowered = source.lower()
    for marker in (
        "model.fit",
        "model.predict",
        "holdoutpermit",
        "consume_holdout_permit",
        "evaluate_oss",
        "execution_authorized=true",
        "paper_execution_authorized=true",
        'capital_authority="paper"',
        'capital_authority="live"',
        'live_trading="enabled"',
    ):
        require(marker not in lowered, f"forbidden D2Y capability/state: {marker}")

    doc = DOC.read_text(encoding="utf-8")
    for marker in (
        D2X_CERTIFIED_COMMIT,
        str(WORKFLOW_RUN_ID),
        str(ARTIFACT_ID),
        CAMPAIGN_SEAL_FINGERPRINT,
        D2U_PARTITION_MATERIAL_FINGERPRINT,
        TRAINING_UNIVERSE_HASH,
        DEVELOPMENT_UNIVERSE_HASH,
        SOURCE_INVENTORY_SHA256,
        EXPECTED_SEAL_FINGERPRINT,
        "99/99",
        "0 GET",
        "artifact expiry does not invalidate",
        "FINAL_HOLDOUT",
        "NONE",
        "BLOCKED",
    ):
        require(marker in doc, f"missing D2Y knowledge marker: {marker}")

    print(
        "AUTO-TRADE OSS-3D2Y DURABLE REAL CAMPAIGN EVIDENCE BOUNDARY: PASS — "
        "certified D2X real campaign 99/99 and zero-network 99/99 reverification are frozen by durable cryptographic commitments; "
        "TRAIN/DEVELOPMENT identities rebind to D2U V2; artifact expiry is non-authoritative; FINAL_HOLDOUT/PAPER/capital/LIVE denied"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
