from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "labs/oss3_qlib/economic_raw_market_feature_provenance.py"


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
    require(MODULE.is_file(), "D2Q module is missing")
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))

    required = (
        "OSS3D2Q_RAW_MARKET_FEATURE_SOURCE_V1",
        "OSS3D2Q_CANONICAL_FACTOR_FORMULA_V1",
        "OSS3D2Q_RAW_MARKET_FEATURE_PROVENANCE_V1",
        "BAR_ENDED_AT_LE_AS_OF_PREFIX_ONLY_V1",
        "DECIMAL_PRECISION_50_TO_FLOAT64_V1",
        "EXACT_PREPARTITION_LOOKBACK_BARS_V1",
        "close_t / close_t_minus_20 - 1",
        "POPULATION_STANDARD_DEVIATION_20_RETURNS",
        "CANONICAL_LOOKBACK_BARS = 20",
        "economic_prefix = raw_source.economic_universe.dataset(symbol).bars[: economic_signal_index + 1]",
        "future market bar entered D2Q feature row",
        '"future_market_values_used_per_row": False',
        '"training_feature_values_rederived": False',
        '"economic_labels_loaded": False',
        '"prediction_values_loaded": False',
        '"economic_metrics_loaded": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    )
    for marker in required:
        require(marker in source, f"missing D2Q boundary marker: {marker}")

    forbidden_import_roots = {
        "qlib",
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
        "supervised_label",
        "development_evaluation",
        "economic_holdout_evaluator",
        "economic_prediction_provenance_admission",
    )
    forbidden_imported_names = {
        "QlibPredictionArtifact",
        "QlibPredictionRow",
        "ProtectedEconomicHoldout",
        "OSS3EconomicMetrics",
        "OSS3EconomicDecision",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                require(root not in forbidden_import_roots, f"forbidden D2Q import: {alias.name}")
                require(
                    not any(fragment in alias.name.lower() for fragment in forbidden_project_fragments),
                    f"forbidden D2Q project import: {alias.name}",
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            require(root not in forbidden_import_roots, f"forbidden D2Q import: {module}")
            require(
                not any(fragment in module.lower() for fragment in forbidden_project_fragments),
                f"forbidden D2Q project import: {module}",
            )
            imported = {alias.name for alias in node.names}
            require(
                not imported.intersection(forbidden_imported_names),
                f"forbidden D2Q imported type: {sorted(imported.intersection(forbidden_imported_names))}",
            )
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

    forbidden_text = (
        "paper_execution_authorized=True",
        "execution_authorized=True",
        'capital_authority="PAPER"',
        'capital_authority="LIVE"',
        'live_trading="ENABLED"',
        "profit_factor",
        "max_drawdown",
        "realized_pnl",
        "prediction.score",
        "row.score",
        "model.fit",
        "model.predict",
    )
    for marker in forbidden_text:
        require(marker not in source, f"forbidden D2Q state/authority marker: {marker}")

    # The only import from D2O is its feature-row/artifact contract. D2Q must
    # not execute inference or inspect prediction values.
    d2o_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").endswith("economic_prediction_provenance")
    ]
    require(len(d2o_imports) == 1, "D2Q must have one narrow D2O contract import")
    require(
        {alias.name for alias in d2o_imports[0].names}
        == {"EconomicFeatureRow", "EconomicPredictionFeatureArtifact"},
        "D2Q D2O import surface exceeds feature artifact contract",
    )

    semantic_requirements = (
        "economic_raw_market_feature_provenance.py",
        "economic_prediction_provenance.py",
        "predictive_economic_protocol.py",
        "predictive_strategy_contract.py",
        "market.py",
        "universe.py",
        "oss3_factor_matrix_artifact.py",
    )
    for marker in semantic_requirements:
        require(marker in source, f"D2Q semantic hash omits dependency: {marker}")

    print(
        "AUTO-TRADE OSS-3D2Q RAW-MARKET FEATURE PROVENANCE BOUNDARY: PASS — "
        "exact D2M market identity + exact canonical TRAIN formula declarations + 20-bar warmup + "
        "causal close-only prefix evaluation -> D2O feature artifact; full offline path acknowledged, "
        "future-per-row use/labels/predictions/economic metrics/network/broker/OMS/Safety/PAPER/capital/LIVE denied"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundaryFailure as exc:
        print(
            f"AUTO-TRADE OSS-3D2Q RAW-MARKET FEATURE PROVENANCE BOUNDARY: FAIL — {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
