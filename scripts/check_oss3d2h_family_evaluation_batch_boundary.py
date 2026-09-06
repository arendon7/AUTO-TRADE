from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "labs/oss3_qlib/family_evaluation_batch.py"

FORBIDDEN_IMPORT_ROOTS = {
    "qlib",
    "pandas",
    "numpy",
    "scipy",
    "sklearn",
    "requests",
    "httpx",
    "aiohttp",
    "socket",
    "subprocess",
    "alpaca",
    "ib_insync",
    "ccxt",
}
FORBIDDEN_RELATIVE_MODULES = {"family_runner", "dataset_adapter", "network_guard"}
FORBIDDEN_CALL_NAMES = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_TEXT = (
    "OrderIntent(",
    "BrokerClient(",
    "SafetyEngine(",
    "paper_execution_authorized=True",
    "execution_authorized=True",
    "promotion_authorized=True",
    'capital_authority="LIMITED"',
    'live_trading="ENABLED"',
    "candidate_output_bindings: tuple[dict",
    "from .family_runner import",
    "from .dataset_adapter import",
)
REQUIRED_TEXT = (
    "class FrozenCandidateOutputBinding:",
    "candidate_output_bindings: tuple[FrozenCandidateOutputBinding, ...]",
    "FrozenCandidateOutputBinding.from_output",
    "_verify_frozen_candidate_output",
    "_d2g_evidence_payload",
    "prepare_family_evaluation_preregistration",
    "preregister_family_evaluation",
    "_require_durable_preregistration",
    "evaluate_development_predictions",
    "evaluate_oss3d2e_tournament",
    "final_holdout_observed: bool = False",
    "promotion_authorized: bool = False",
    'capital_authority: str = "NONE"',
    'live_trading: str = "BLOCKED"',
)


def main() -> None:
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET))

    imported_roots: set[str] = set()
    relative_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
            if node.level:
                relative_modules.add(node.module)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALL_NAMES:
                raise SystemExit(f"OSS-3D2H boundary FAIL: forbidden dynamic call {node.func.id}")
            if isinstance(node.func, ast.Attribute):
                root = node.func
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name) and root.id in {"os", "subprocess", "socket"}:
                    raise SystemExit(
                        f"OSS-3D2H boundary FAIL: forbidden operational call through {root.id}"
                    )

    forbidden_imports = sorted(imported_roots & FORBIDDEN_IMPORT_ROOTS)
    if forbidden_imports:
        raise SystemExit(f"OSS-3D2H boundary FAIL: forbidden imports {forbidden_imports}")
    forbidden_relative = sorted(relative_modules & FORBIDDEN_RELATIVE_MODULES)
    if forbidden_relative:
        raise SystemExit(
            f"OSS-3D2H boundary FAIL: runtime-heavy relative imports {forbidden_relative}"
        )
    for marker in FORBIDDEN_TEXT:
        if marker in source:
            raise SystemExit(f"OSS-3D2H boundary FAIL: forbidden authority/mutability marker {marker!r}")
    for marker in REQUIRED_TEXT:
        if marker not in source:
            raise SystemExit(f"OSS-3D2H boundary FAIL: required contract marker missing {marker!r}")

    # Preregistration identity checking must precede D2D value evaluation in the
    # completed batch function. This guards the scientific sequencing at source
    # level in addition to behavioral tests.
    evaluate_start = source.index("def evaluate_preregistered_family(")
    evaluate_body = source[evaluate_start:]
    durable_pos = evaluate_body.index("_require_durable_preregistration")
    metric_pos = evaluate_body.index("evaluate_development_predictions")
    if durable_pos >= metric_pos:
        raise SystemExit("OSS-3D2H boundary FAIL: DEVELOPMENT evaluation precedes durable preregistration")

    helper_start = source.index("def _verify_label_identity_without_values(")
    helper_end = source.index("def _verify_outputs_against_preregistration(")
    helper = source[helper_start:helper_end]
    if "row.value" in helper or ".value)" in helper:
        raise SystemExit("OSS-3D2H boundary FAIL: label-identity helper reads DEVELOPMENT values")

    print(
        "AUTO-TRADE OSS-3D2H family evaluation batch boundary: PASS "
        "(runtime-free immutable six-candidate D2G evidence rebinding -> label-value-free D2E plan "
        "-> durable preregistration -> D2D DEVELOPMENT evaluation -> D2E sign-test/Holm tournament; "
        "Qlib/pandas/family-runner/FINAL_HOLDOUT/promotion/broker/OMS/Safety/PAPER/capital/LIVE denied)"
    )


if __name__ == "__main__":
    main()
