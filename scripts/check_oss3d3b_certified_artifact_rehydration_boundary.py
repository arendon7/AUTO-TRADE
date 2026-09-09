from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "labs/oss3_qlib/certified_artifact_rehydration.py"
RUNNER = ROOT / "scripts/run_oss3d3b_certified_artifact_rehydration.py"
TEST = ROOT / "labs/oss3_qlib/tests/test_certified_artifact_rehydration.py"
DOC = ROOT / "knowledge/20_RESEARCH/OSS3D3B_CERTIFIED_ARTIFACT_REAL_REHYDRATION.md"
WORKFLOW = ROOT / ".github/workflows/oss3d3b-certified-artifact-rehydration.yml"


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
    for path in (MODULE, RUNNER, TEST, DOC, WORKFLOW):
        require(path.is_file(), f"missing D3B certification file: {path.relative_to(ROOT)}")

    source = MODULE.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))
    runner_tree = ast.parse(runner, filename=str(RUNNER))

    for marker in (
        "OSS3D3B_CERTIFIED_ARTIFACT_REAL_REHYDRATION_EVIDENCE_V1",
        "EXACT_D2Y_ACTIONS_ZIP_THEN_EXACT_INVENTORY_AND_TAR_V1",
        "ORIGINAL_D2Y_DESCRIPTOR_BYTES_AND_RECEIPTS_ONLY_V1",
        "ORIGINAL_D2Y_RECEIPTS_MUST_REPRODUCE_ORIGINAL_PARTITION_FINGERPRINT_V1",
        "canonical_oss3d2y_real_campaign_evidence_seal",
        "verify_oss3d2y_real_campaign_evidence_seal",
        "load_canonical_oss3d2z_descriptor_material_manifest",
        "verify_durable_descriptor_material_manifest",
        "build_canonical_stable_rehydrated_raw_split",
        "sha256(raw_zip).hexdigest() != expectations.artifact_zip_sha256",
        "names != EXPECTED_ARTIFACT_MEMBERS",
        "set(found) != set(inventory)",
        "raw_split.partition_material.fingerprint != d2y.d2u_partition_material_fingerprint",
        "exact_original_partition_fingerprint_reproduced=True",
        "network_used_by_rehydrator=False",
        "provider_network_used_by_rehydrator=False",
        "qlib_runtime_used=False",
        'capital_authority="NONE"',
        'live_trading="BLOCKED"',
    ):
        require(marker in source, f"missing D3B source marker: {marker}")

    for marker in (
        "rehydrate_canonical_d2y_artifact",
        "result_fingerprint",
        "raw_training_source_hash",
        "raw_development_source_hash",
        "training_universe_hash",
        "development_universe_hash",
        "exact_original_partition_fingerprint_reproduced",
    ):
        require(marker in runner, f"missing D3B runner marker: {marker}")

    forbidden_roots = {"socket", "urllib", "requests", "httpx", "aiohttp", "subprocess"}
    forbidden_import_fragments = (
        "qlib",
        "broker",
        "oms",
        "safety",
        "order_intent",
        "archive_acquisition",
        "real_acquisition_campaign",
    )
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
        "acquire_preregistered_archive",
        "build_real_archive_transport",
        "run_restart_safe_campaign",
        "tarfile.extract",
        "tarfile.extractall",
    }
    for parsed in (tree, runner_tree):
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0].lower()
                    require(root not in forbidden_roots, f"D3B may not import network/process root: {alias.name}")
                    require(alias.name != "qlib" and not alias.name.startswith("qlib."), f"D3B may not import Qlib: {alias.name}")
                    require(
                        not any(fragment in alias.name.lower() for fragment in forbidden_import_fragments[1:]),
                        f"forbidden D3B import: {alias.name}",
                    )
            elif isinstance(node, ast.ImportFrom):
                imported = (node.module or "").lower()
                require(imported != "qlib" and not imported.startswith("qlib."), f"D3B may not import Qlib: {imported}")
                require(
                    not any(fragment in imported for fragment in forbidden_import_fragments[1:]),
                    f"forbidden D3B import: {imported}",
                )
            elif isinstance(node, ast.Call):
                called = dotted_name(node.func)
                require(called not in forbidden_calls, f"D3B may not perform acquisition/network/unsafe extraction call: {called}")
                require(not called.endswith(".extractall"), f"D3B may not extract tar/zip trees unsafely: {called}")

    lowered = (source + "\n" + runner).lower()
    for marker in (
        "model.fit",
        "model.predict",
        "holdoutpermit",
        "consume_holdout_permit",
        "promotion_authorized=true",
        "execution_authorized=true",
        "paper_execution_authorized=true",
        'capital_authority="paper"',
        'capital_authority="live"',
        'live_trading="enabled"',
    ):
        require(marker not in lowered, f"forbidden D3B capability marker: {marker}")

    doc = DOC.read_text(encoding="utf-8").lower()
    for marker in (
        "artifact zip",
        "inventory",
        "tar",
        "99",
        "398",
        "d2y",
        "d2z",
        "d3a",
        "original receipt",
        "partition",
        "train",
        "development",
        "final_holdout",
        "qlib",
        "capital_authority = none",
        "live_trading = blocked",
    ):
        require(marker in doc, f"missing D3B knowledge marker: {marker}")

    print(
        "AUTO-TRADE OSS-3D3B CERTIFIED ARTIFACT REAL REHYDRATION BOUNDARY: PASS — "
        "exact D2Y Actions ZIP/inventory/tar/source family is required; original receipts feed certified D3A; "
        "the original D2Y partition fingerprint must reproduce exactly; no provider/network/Qlib/FINAL_HOLDOUT/PAPER/capital/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
