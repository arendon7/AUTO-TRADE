from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "labs/oss3_qlib/raw_development_provenance.py"


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


def function_source(source: str, name: str, next_name: str) -> str:
    start = source.index(f"def {name}")
    end = source.index(f"def {next_name}", start)
    return source[start:end]


def main() -> int:
    require(MODULE.is_file(), "D2S module is missing")
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))

    required = (
        "OSS3D2S_RAW_DEVELOPMENT_MARKET_SOURCE_V1",
        "OSS3D2S_RAW_DEVELOPMENT_PREREGISTRATION_V1",
        "OSS3D2S_DEVELOPMENT_LABEL_REVEAL_V1",
        "MATERIALIZE_ONLY_AFTER_DURABLE_D2S_PREREGISTRATION_V1",
        "FULL_RAW_PATH_HASHED_BEFORE_LABEL_MATERIALIZATION_V1",
        "D2E_PRIMARY_METRIC_AND_MULTIPLE_TESTING_FROZEN_PRE_LABEL_V1",
        "full_development_path_loaded=True",
        "supervised_label_artifact_materialized=False",
        "label_artifact_materialized: bool = False",
        "label_values_used: bool = False",
        "development_metrics_computed: bool = False",
        "predictions_frozen_before_label_materialization",
        "label_values_materialized_after_durable_preregistration",
        "registry.require_exact(preregistration)",
        "prepare_family_evaluation_preregistration",
        "source_dataset_hash=raw_source.source_hash",
        "source_universe_hash=raw_source.universe_identity_hash",
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    )
    for marker in required:
        require(marker in source, f"missing D2S boundary marker: {marker}")

    forbidden_import_roots = {"qlib", "socket", "subprocess", "requests", "urllib", "http"}
    forbidden_project_fragments = (
        ".broker", ".oms", ".safety", ".execution", ".orders", ".order_intent",
        "final_holdout_evaluator", "economic_holdout_evaluator",
        "economic_prediction_provenance_admission",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                require(root not in forbidden_import_roots, f"forbidden D2S import: {alias.name}")
                require(
                    not any(fragment in alias.name.lower() for fragment in forbidden_project_fragments),
                    f"forbidden D2S authority import: {alias.name}",
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            require(root not in forbidden_import_roots, f"forbidden D2S import: {module}")
            require(
                not any(fragment in module.lower() for fragment in forbidden_project_fragments),
                f"forbidden D2S authority import: {module}",
            )
        elif isinstance(node, ast.Call):
            called = dotted_name(node.func)
            require(
                called not in {
                    "os.system", "os.popen", "subprocess.run", "subprocess.Popen",
                    "subprocess.call", "subprocess.check_call", "subprocess.check_output",
                },
                f"forbidden D2S process call: {called}",
            )

    forbidden_text = (
        "model.fit", "model.predict", "run_isolated_qlib_family_candidate",
        "paper_execution_authorized=True", "execution_authorized=True",
        'capital_authority="PAPER"', 'capital_authority="LIVE"',
        'live_trading="ENABLED"', "realized_pnl", "profit_factor", "max_drawdown",
        "adaptive_feature_search=True", "adaptive_label_search=True", "adaptive_horizon_search=True",
    )
    for marker in forbidden_text:
        require(marker not in source, f"forbidden D2S state/authority marker: {marker}")

    feature_phase = function_source(
        source,
        "derive_raw_development_features",
        "verify_raw_development_features",
    )
    require("_derive_development_labels" not in feature_phase, "D2S feature phase may not materialize labels")
    require("SupervisedLabelArtifact.build" not in feature_phase, "D2S feature phase may not build label artifact")

    prereg_phase = function_source(
        source,
        "prepare_raw_development_preregistration",
        "materialize_development_labels_after_preregistration",
    )
    require("_derive_development_labels" not in prereg_phase, "D2S preregistration may not derive labels")
    require("SupervisedLabelArtifact.build" not in prereg_phase, "D2S preregistration may not build labels")
    require(".value" not in prereg_phase, "D2S preregistration may not read label values")
    require("development_label_artifact_hash" not in prereg_phase, "D2S preregistration may not bind a label artifact hash")

    prereg_class_start = source.index("class OSS3RawDevelopmentPreregistration")
    prereg_class_end = source.index("class OSS3DevelopmentLabelRevealReceipt", prereg_class_start)
    prereg_class = source[prereg_class_start:prereg_class_end]
    require("development_label_artifact_hash" not in prereg_class, "D2S preregistration schema exposes D2H label hash too early")
    require("label_artifact_hash" not in prereg_class, "D2S preregistration schema exposes label artifact hash too early")

    reveal_phase = function_source(
        source,
        "materialize_development_labels_after_preregistration",
        "prepare_d2h_from_d2s_reveal",
    )
    durable_index = reveal_phase.index("registry.require_exact(preregistration)")
    derive_index = reveal_phase.index("_derive_development_labels(")
    require(durable_index < derive_index, "D2S must prove durable preregistration before deriving labels")
    require("family_evaluation_code_hash()" in reveal_phase, "D2S reveal must reject D2H/D2E semantic drift")

    label_phase_start = source.index("def _derive_development_labels")
    label_phase_end = source.index("def _causal_history", label_phase_start)
    label_phase = source[label_phase_start:label_phase_end]
    require("origin_index + 1" in label_phase, "D2S label horizon must be exact +1 bar")
    require("range(raw_source.development_universe.bar_count - 1)" in label_phase, "terminal DEVELOPMENT bar must not be label origin")
    require("LabelPartition.DEVELOPMENT" in label_phase, "D2S may materialize DEVELOPMENT labels only")

    causal_phase_start = source.index("def _causal_history")
    causal_phase_end = source.index("def _compute_formula", causal_phase_start)
    causal_phase = source[causal_phase_start:causal_phase_end]
    require("[: signal_index + 1]" in causal_phase, "D2S feature history must use causal DEVELOPMENT prefix")
    require("> as_of" in causal_phase, "D2S feature path must reject future bars")

    semantic_requirements = (
        "raw_development_provenance.py", "raw_training_bundle_provenance.py",
        "economic_raw_market_feature_provenance.py", "family_evaluation_batch.py",
        "oss3_development_inference.py", "oss3_development_evaluation.py",
        "oss3_development_model_tournament.py", "oss3_factor_matrix_artifact.py",
        "oss3_supervised_label_artifact.py", "market.py", "universe.py",
    )
    for marker in semantic_requirements:
        require(marker in source, f"D2S semantic hash omits dependency: {marker}")

    print(
        "AUTO-TRADE OSS-3D2S RAW DEVELOPMENT PROVENANCE BOUNDARY: PASS — "
        "raw DEVELOPMENT commitment + causal features + six frozen predictions/statistical policy are durable before "
        "any label artifact exists; labels reveal only after append-only preregistration and ordinary D2H/D2E remains "
        "the metric/tournament authority; FINAL_HOLDOUT/broker/OMS/Safety/PAPER/capital/LIVE denied"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundaryFailure as exc:
        print(f"AUTO-TRADE OSS-3D2S RAW DEVELOPMENT PROVENANCE BOUNDARY: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
