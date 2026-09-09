from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "labs/oss3_qlib/stable_rehydrated_raw_split.py"
TEST = ROOT / "labs/oss3_qlib/tests/test_stable_rehydrated_raw_split.py"
DOC = ROOT / "knowledge/20_RESEARCH/OSS3D3A_STABLE_REHYDRATED_RAW_SPLIT.md"
WORKFLOW = ROOT / ".github/workflows/oss3d3a-stable-rehydrated-raw-split.yml"


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


def function_source(source: str, tree: ast.Module, name: str) -> str:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            return segment or ""
    raise BoundaryFailure(f"missing D3A function: {name}")


def main() -> int:
    for path in (MODULE, TEST, DOC, WORKFLOW):
        require(path.is_file(), f"missing D3A certification file: {path.relative_to(ROOT)}")

    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))

    for marker in (
        "OSS3D3A_STABLE_REHYDRATED_RAW_SPLIT_EVIDENCE_V1",
        "D2Z_STABLE_MATERIAL_PLUS_D2Y_TRAIN_DEVELOPMENT_UNIVERSE_EQUALITY_V1",
        "D2U_REACQUISITION_RECEIPTS_AUDITED_BUT_EXCLUDED_FROM_SCIENTIFIC_IDENTITY_V1",
        "D2U_RECEIPT_BOUND_PARTITION_FINGERPRINT_MAY_CHANGE_BUT_MARKET_UNIVERSES_MUST_NOT_V1",
        "canonical_oss3d2y_real_campaign_evidence_seal",
        "verify_oss3d2y_real_campaign_evidence_seal",
        "load_canonical_oss3d2z_descriptor_material_manifest",
        "verify_durable_descriptor_material_manifest",
        "verify_rehydrated_descriptor_material",
        "assemble_historical_collection",
        "RawTrainingMarketSource.build",
        "RawDevelopmentMarketSource.build",
        "partition_material.training.universe_hash != expected_training_universe_hash",
        "partition_material.development.universe_hash != expected_development_universe_hash",
        "partition_material_fingerprint_reproduction_required=False",
        "receipt_lineage_excluded_from_scientific_identity=True",
        "network_used_by_handoff=False",
        "qlib_runtime_used=False",
        'capital_authority="NONE"',
        'live_trading="BLOCKED"',
    ):
        require(marker in source, f"missing D3A source marker: {marker}")

    scientific = function_source(source, tree, "_scientific_rehydration_fingerprint")
    for required in (
        "d2y_seal_fingerprint",
        "d2z_stable_material_root",
        "training_warmup_universe_hash",
        "training_universe_hash",
        "development_warmup_universe_hash",
        "development_universe_hash",
        "research_universe_identity_hash",
        "raw_training_source_hash",
        "raw_development_source_hash",
    ):
        require(required in scientific, f"D3A scientific fingerprint missing stable component: {required}")
    for forbidden in (
        "reacquisition_lineage_root",
        "acquisition_receipt_hash",
        "reacquired_d2u_partition_material_fingerprint",
        "reacquired_d2u_assembly_evidence_fingerprint",
    ):
        require(forbidden not in scientific, f"D3A scientific fingerprint leaked timestamp-sensitive lineage: {forbidden}")

    forbidden_import_fragments = (
        "qlib",
        "family_runner",
        "dataset_adapter",
        "development_evaluation",
        "final_holdout",
        "broker",
        "oms",
        "safety",
        "order_intent",
        "real_acquisition_campaign",
        "archive_acquisition",
    )
    # The module intentionally lives under labs.oss3_qlib and imports only the
    # raw D2R/D2S contracts in that package; reject actual qlib imports/calls,
    # not its own package path.
    allowed_relative_modules = {"raw_training_bundle_provenance", "raw_development_provenance"}
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
        "acquire_preregistered_archive",
        "build_real_archive_transport",
        "run_restart_safe_campaign",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0].lower()
                require(root not in forbidden_roots, f"D3A may not import network/process root: {alias.name}")
                require(alias.name != "qlib" and not alias.name.startswith("qlib."), f"D3A may not import Qlib: {alias.name}")
                require(
                    not any(fragment in alias.name.lower() for fragment in forbidden_import_fragments[1:]),
                    f"forbidden D3A import: {alias.name}",
                )
        elif isinstance(node, ast.ImportFrom):
            imported = (node.module or "").lower()
            if node.level and imported in allowed_relative_modules:
                continue
            require(imported != "qlib" and not imported.startswith("qlib."), f"D3A may not import Qlib: {imported}")
            require(
                not any(fragment in imported for fragment in forbidden_import_fragments[1:]),
                f"forbidden D3A import: {imported}",
            )
        elif isinstance(node, ast.Call):
            called = dotted_name(node.func)
            require(called not in forbidden_calls, f"D3A may not perform acquisition/network/process call: {called}")

    lowered = source.lower()
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
        require(marker not in lowered, f"forbidden D3A capability marker: {marker}")

    doc = DOC.read_text(encoding="utf-8").lower()
    for marker in (
        "d2z",
        "d2y",
        "receipt",
        "timestamp",
        "scientific fingerprint",
        "audit fingerprint",
        "train",
        "development",
        "d2r",
        "d2s",
        "final_holdout",
        "qlib",
        "capital_authority = none",
        "live_trading = blocked",
    ):
        require(marker in doc, f"missing D3A knowledge marker: {marker}")

    print(
        "AUTO-TRADE OSS-3D3A STABLE REHYDRATED RAW SPLIT BOUNDARY: PASS — "
        "D2U reacquisition receipt lineage remains auditable but is excluded from the scientific fingerprint; "
        "every descriptor is D2Z-gated and TRAIN/DEVELOPMENT must equal D2Y before D2R/D2S raw handoff; "
        "no acquisition/network/Qlib/FINAL_HOLDOUT/PAPER/capital/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
