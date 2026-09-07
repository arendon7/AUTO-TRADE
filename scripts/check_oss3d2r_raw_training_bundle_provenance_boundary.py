from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "labs/oss3_qlib/raw_training_bundle_provenance.py"


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
    require(MODULE.is_file(), "D2R module is missing")
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))

    required = (
        "OSS3D2R_RAW_TRAINING_MARKET_SOURCE_V1",
        "OSS3D2R_FORWARD_RETURN_LABEL_FORMULA_V1",
        "OSS3D2R_RAW_TRAINING_BUNDLE_PROVENANCE_V1",
        "OSS3D2R_RESEARCH_UNIVERSE_IDENTITY_V1",
        "close_t_plus_1 / close_t - 1",
        "LABEL_HORIZON_BARS = 1",
        "BAR_ENDED_AT_LE_FEATURE_AS_OF_PREFIX_ONLY_V1",
        "NEXT_BAR_CLOSE_WITHIN_TRAIN_ONLY_V1",
        "EVERY_TRAIN_BAR_CLOSE_EXCEPT_TERMINAL_BAR_V1",
        "LAST_LABEL_AVAILABILITY_PLUS_ONE_MICROSECOND_V1",
        "source_dataset_hash=raw_source.source_hash",
        "source_universe_hash=raw_source.universe_identity_hash",
        "source_hash=canonical_oss3d2q_formula_registry_hash()",
        "source_hash=formula.formula_hash",
        '"feature_future_values_used": False',
        '"label_future_values_used": True',
        '"label_future_values_confined_to_explicit_horizon": True',
        '"label_horizon_crossed_train_boundary": False',
        '"development_values_loaded": False',
        '"final_holdout_values_loaded": False',
        '"economic_holdout_values_loaded": False',
        '"prediction_values_loaded": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    )
    for marker in required:
        require(marker in source, f"missing D2R boundary marker: {marker}")

    forbidden_import_roots = {
        "qlib", "socket", "subprocess", "requests", "urllib", "http", "sqlite3",
    }
    forbidden_project_fragments = (
        ".broker", ".oms", ".safety", ".execution", ".orders", ".order_intent",
        "development_evaluation", "final_holdout_evaluator", "economic_holdout_evaluator",
        "economic_prediction_provenance", "economic_prediction_provenance_admission",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                require(root not in forbidden_import_roots, f"forbidden D2R import: {alias.name}")
                require(
                    not any(fragment in alias.name.lower() for fragment in forbidden_project_fragments),
                    f"forbidden D2R authority import: {alias.name}",
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            require(root not in forbidden_import_roots, f"forbidden D2R import: {module}")
            require(
                not any(fragment in module.lower() for fragment in forbidden_project_fragments),
                f"forbidden D2R authority import: {module}",
            )
        elif isinstance(node, ast.Call):
            called = dotted_name(node.func)
            require(
                called not in {
                    "os.system", "os.popen", "subprocess.run", "subprocess.Popen",
                    "subprocess.call", "subprocess.check_call", "subprocess.check_output",
                },
                f"forbidden D2R process call: {called}",
            )

    forbidden_text = (
        "model.fit", "model.predict", "run_isolated_qlib_family_candidate",
        "paper_execution_authorized=True", "execution_authorized=True",
        'capital_authority="PAPER"', 'capital_authority="LIVE"',
        'live_trading="ENABLED"', "profit_factor", "max_drawdown", "realized_pnl",
        "adaptive_feature_search=True", "adaptive_label_search=True", "adaptive_horizon_search=True",
    )
    for marker in forbidden_text:
        require(marker not in source, f"forbidden D2R state/authority marker: {marker}")

    # D2R is the one stage where future values are scientifically legitimate:
    # only one next-bar TRAIN target. Ensure implementation hard-codes +1 and
    # never iterates a candidate horizon family.
    require("origin_index + 1" in source, "D2R label horizon must be exact +1 bar")
    require("range(training.bar_count - 1)" in source, "terminal TRAIN bar must not be sample origin")
    require("horizon_end >= raw_source.partition_end" in source, "D2R must bound label horizon inside TRAIN")
    require("raw_source.training_universe.dataset(symbol).bars[: origin_index + 1]" in source, "D2R feature path must use causal TRAIN prefix")

    # Stable semantic identity must be visibly independent of dataset hashes.
    universe_function = source[source.index("def research_universe_identity_hash"):source.index("@dataclass", source.index("def research_universe_identity_hash"))]
    require("dataset_hash" not in universe_function, "research universe identity may not contain material dataset hash")
    require("universe_name" not in universe_function, "research universe identity may not depend on partition-specific name")
    for marker in ("symbol", "venue", "quote_currency", "price_tick", "quantity_step", "timeframe_seconds"):
        require(marker in universe_function, f"research universe identity missing semantic field: {marker}")

    semantic_requirements = (
        "raw_training_bundle_provenance.py", "economic_raw_market_feature_provenance.py",
        "market.py", "universe.py", "oss3_factor_matrix_artifact.py",
        "oss3_supervised_label_artifact.py", "oss3_training_bundle.py",
    )
    for marker in semantic_requirements:
        require(marker in source, f"D2R semantic hash omits dependency: {marker}")

    print(
        "AUTO-TRADE OSS-3D2R RAW TRAINING BUNDLE PROVENANCE BOUNDARY: PASS — "
        "stable schema/universe semantics separated from material lineage; exact 20-bar causal TRAIN features + "
        "fixed one-bar forward TRAIN labels -> exact TrainingBundle; DEVELOPMENT/FINAL_HOLDOUT/economic/prediction/"
        "Qlib/network/broker/OMS/Safety/PAPER/capital/LIVE authority denied"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundaryFailure as exc:
        print(f"AUTO-TRADE OSS-3D2R RAW TRAINING BUNDLE PROVENANCE BOUNDARY: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
