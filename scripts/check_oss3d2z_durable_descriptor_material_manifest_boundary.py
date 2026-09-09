from __future__ import annotations

import ast
from hashlib import sha256
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autotrade.research.oss3_real_campaign_evidence import (  # noqa: E402
    EXPECTED_SEAL_FINGERPRINT as D2Y_SEAL_FINGERPRINT,
)
from autotrade.research.oss3_real_descriptor_identity import (  # noqa: E402
    DESCRIPTOR_COUNT,
    IDENTITY_POLICY,
    MATERIAL_MANIFEST_FILE_SHA256,
    MATERIAL_MANIFEST_PATH,
    STABLE_MATERIAL_ROOT,
    load_canonical_oss3d2z_descriptor_material_manifest,
)


MODULE = ROOT / "src/autotrade/research/oss3_real_descriptor_identity.py"
TEST = ROOT / "tests/test_research_oss3_real_descriptor_identity.py"
DOC = ROOT / "knowledge/20_RESEARCH/OSS3D2Z_DURABLE_DESCRIPTOR_MATERIAL_MANIFEST.md"


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
    for path in (MODULE, TEST, DOC, MATERIAL_MANIFEST_PATH):
        require(path.is_file(), f"missing D2Z certification file: {path.relative_to(ROOT)}")

    require(
        sha256(MATERIAL_MANIFEST_PATH.read_bytes()).hexdigest() == MATERIAL_MANIFEST_FILE_SHA256,
        "D2Z durable material manifest bytes drifted",
    )
    manifest = load_canonical_oss3d2z_descriptor_material_manifest()
    require(manifest.descriptor_count == DESCRIPTOR_COUNT == 99, "D2Z descriptor family must remain exactly 99")
    require(manifest.stable_material_root == STABLE_MATERIAL_ROOT, "D2Z stable material root drifted")
    require(manifest.d2y_seal_fingerprint == D2Y_SEAL_FINGERPRINT, "D2Z D2Y provenance drifted")

    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))
    for marker in (
        "OSS3D2Z_DURABLE_DESCRIPTOR_MATERIAL_MANIFEST_V1",
        "OSS3D2Z_REAL_DESCRIPTOR_MATERIAL_MANIFEST_V1",
        IDENTITY_POLICY,
        MATERIAL_MANIFEST_FILE_SHA256,
        STABLE_MATERIAL_ROOT,
        D2Y_SEAL_FINGERPRINT,
        "verify_rehydrated_descriptor_material",
        "stable_identity_vector",
    ):
        require(marker in source, f"missing D2Z source marker: {marker}")

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
                require(root not in forbidden_roots, f"D2Z may not import network/process root: {alias.name}")
                require(
                    not any(fragment in alias.name.lower() for fragment in forbidden_import_fragments),
                    f"forbidden D2Z import: {alias.name}",
                )
        elif isinstance(node, ast.ImportFrom):
            imported = (node.module or "").lower()
            require(
                not any(fragment in imported for fragment in forbidden_import_fragments),
                f"forbidden D2Z import: {imported}",
            )
        elif isinstance(node, ast.Call):
            called = dotted_name(node.func)
            require(called not in forbidden_calls, f"D2Z may not perform network/process call: {called}")

    lowered = source.lower()
    for marker in (
        "acquired_at",
        "sealed_at",
        "acquisition_receipt_hash",
        "source_receipt_file_sha256",
        "model.fit",
        "model.predict",
        "holdoutpermit",
        "consume_holdout_permit",
        "execution_authorized=true",
        "paper_execution_authorized=true",
        'capital_authority="paper"',
        'capital_authority="live"',
        'live_trading="enabled"',
    ):
        require(marker not in lowered, f"forbidden D2Z time-dependent/capability marker: {marker}")

    doc = DOC.read_text(encoding="utf-8").lower()
    for marker in (
        D2Y_SEAL_FINGERPRINT,
        MATERIAL_MANIFEST_FILE_SHA256,
        STABLE_MATERIAL_ROOT,
        "99",
        "398",
        "acquired_at",
        "sealed_at",
        "receipt timestamp",
        "final_holdout",
        "qlib",
        "capital_authority = none",
        "live_trading = blocked",
    ):
        require(marker.lower() in doc, f"missing D2Z knowledge marker: {marker}")

    print(
        "AUTO-TRADE OSS-3D2Z DURABLE DESCRIPTOR MATERIAL MANIFEST BOUNDARY: PASS — "
        "99 canonical D2U descriptors are durably committed to exact provider archive/CHECKSUM and D2T output identities; "
        "receipt/acquisition timestamps are excluded from stable identity; no network/Qlib/FINAL_HOLDOUT/PAPER/capital/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
