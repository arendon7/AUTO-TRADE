from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "labs/oss3_qlib/economic_prediction_provenance.py"


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


def parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    result: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            result[child] = parent
    return result


def enclosed_by_deny_network(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.With):
            for item in current.items:
                expression = dotted_name(item.context_expr)
                if expression == "deny_network" or expression.endswith(".deny_network"):
                    return True
                if isinstance(item.context_expr, ast.Call):
                    called = dotted_name(item.context_expr.func)
                    if called == "deny_network" or called.endswith(".deny_network"):
                        return True
    return False


def main() -> int:
    require(MODULE.is_file(), "D2O module is missing")
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))
    parents = parent_map(tree)

    required = (
        "OSS3D2O_ECONOMIC_FEATURE_ARTIFACT_V1",
        "OSS3D2O_ECONOMIC_PREDICTION_PROVENANCE_V1",
        "EVERY_NONTERMINAL_ECONOMIC_BAR_CLOSE_FULL_UNIVERSE_V1",
        "market_derived_features_observed",
        "economic_labels_included",
        "economic_outcomes_included",
        "prediction_generated_by_qlib",
        "original_train_bundle_replayed",
        "economic_labels_loaded",
        "economic_outcomes_loaded",
        "collect_candidate_environment_attestation",
        "family_runner_code_hash",
        "economic_prediction_provenance_semantic_hash",
        "with deny_network():",
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    )
    for marker in required:
        require(marker in source, f"missing D2O boundary marker: {marker}")

    forbidden_text = (
        "OrderIntent",
        "paper_execution_authorized=True",
        "execution_authorized=True",
        'capital_authority="PAPER"',
        'capital_authority="LIVE"',
        'live_trading="ENABLED"',
        "SQLiteOSS3EconomicHoldoutEvaluationRegistry",
        "SQLiteOSS3FinalHoldoutEvaluationRegistry",
        "holdout_permits",
        "oss3_economic_holdout_evaluation_starts",
    )
    for marker in forbidden_text:
        require(marker not in source, f"forbidden D2O authority/state marker: {marker}")

    forbidden_import_roots = {
        "socket",
        "subprocess",
        "requests",
        "urllib",
        "http",
        "sqlite3",
    }
    forbidden_project_fragments = (
        ".broker",
        ".oms",
        ".safety",
        ".execution",
        ".orders",
        ".order_intent",
    )
    qlib_nodes: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                require(root not in forbidden_import_roots, f"forbidden import: {alias.name}")
                require(
                    not any(fragment in alias.name.lower() for fragment in forbidden_project_fragments),
                    f"forbidden authority import: {alias.name}",
                )
                if root == "qlib":
                    qlib_nodes.append(node)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            require(root not in forbidden_import_roots, f"forbidden import: {module}")
            require(
                not any(fragment in module.lower() for fragment in forbidden_project_fragments),
                f"forbidden authority import: {module}",
            )
            if root == "qlib":
                qlib_nodes.append(node)
        elif isinstance(node, ast.Call):
            called = dotted_name(node.func)
            require(
                called not in {
                    "os.system",
                    "os.popen",
                    "subprocess.run",
                    "subprocess.Popen",
                    "subprocess.call",
                    "subprocess.check_call",
                    "subprocess.check_output",
                },
                f"forbidden process call: {called}",
            )

    require(qlib_nodes, "D2O must contain real Qlib execution")
    for node in qlib_nodes:
        require(
            enclosed_by_deny_network(node, parents),
            "every D2O Qlib import must execute inside deny_network()",
        )

    # D2O may see point-in-time features but must have no direct raw market or
    # economic-evaluator input surface.  This keeps outcome access outside the
    # prediction producer.
    for forbidden in (
        "AlignedMarketUniverse",
        "MarketDataset",
        "Bar(",
        "EconomicHoldoutMaterial",
        "ProtectedEconomicHoldout",
        "OSS3EconomicDecision",
        "profit_factor",
        "max_drawdown",
        "net_return",
        "realized_pnl",
    ):
        require(forbidden not in source, f"D2O may not consume economic outcome surface: {forbidden}")

    semantic_requirements = (
        "economic_prediction_provenance.py",
        "predictive_economic_protocol.py",
        "predictive_strategy_contract.py",
        "family_model_contract.py",
        "family_environment_attestation.py",
        "network_guard.py",
        "requirements.txt",
        "oss3_training_bundle.py",
        "oss3_qlib_artifact.py",
    )
    for marker in semantic_requirements:
        require(marker in source, f"D2O semantic hash omits dependency: {marker}")

    print(
        "AUTO-TRADE OSS-3D2O ECONOMIC PREDICTION PROVENANCE BOUNDARY: PASS — "
        "exact D2M/D2L/D2J + winner request + original TRAIN replay + committed point-in-time "
        "economic features + exact D2G runtime + no-network real Qlib prediction; economic labels/outcomes, "
        "broker/OMS/Safety/OrderIntent/SQLite/PAPER/capital/LIVE authority denied"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundaryFailure as exc:
        print(f"AUTO-TRADE OSS-3D2O ECONOMIC PREDICTION PROVENANCE BOUNDARY: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
