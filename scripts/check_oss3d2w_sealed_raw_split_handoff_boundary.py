from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "labs/oss3_qlib/sealed_raw_split_handoff.py"


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
    require(MODULE.is_file(), "D2W module is missing")
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))

    for marker in (
        "OSS3D2W_SEALED_RAW_SPLIT_HANDOFF_EVIDENCE_V1",
        "OSS3D2W_SEALED_RAW_SPLIT_HANDOFF_MATERIAL_V1",
        "ONE_D2V_SEAL_TO_EXACT_D2R_TRAIN_AND_D2S_DEVELOPMENT_RAW_SOURCES_V1",
        "PREEXISTING_D2V_COMPLETE_SEAL_THEN_FULL_OFFLINE_REVERIFICATION_V1",
        "D2R_TRAIN_AND_D2S_PRELABEL_DEVELOPMENT_ONLY_V1",
        "preexisting_complete_d2v_seal_required=True",
        "d2v_offline_reverification_complete=True",
        "network_used_during_handoff=False",
        "train_label_artifact_materialized=False",
        "development_label_artifact_materialized=False",
        "prediction_values_loaded=False",
        "development_metrics_computed=False",
        "final_holdout_values_loaded=False",
        "qlib_runtime_used=False",
        "promotion_authorized=False",
        "execution_authorized=False",
        "paper_execution_authorized=False",
        'capital_authority="NONE"',
        'live_trading="BLOCKED"',
    ):
        require(marker in source, f"missing D2W boundary marker: {marker}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                require(root not in {"socket", "urllib", "requests", "httpx", "aiohttp", "subprocess"}, f"D2W forbidden network/process import: {alias.name}")
                require(root != "qlib", "D2W cannot import Qlib")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            require("external_data" not in module, f"D2W cannot import network transport: {module}")
            require("archive_acquisition" not in module, f"D2W cannot import acquisition adapter: {module}")
            require("family_runner" not in module, f"D2W cannot import Qlib runner: {module}")
            require("supervised_label_artifact" not in module, f"D2W cannot import label artifact: {module}")
            require("development_evaluation" not in module, f"D2W cannot import DEVELOPMENT evaluator: {module}")
            require("final_holdout" not in module, f"D2W cannot import FINAL_HOLDOUT: {module}")
            require(not any(fragment in module for fragment in ("broker", "oms", "safety", "order_intent")), f"D2W forbidden authority import: {module}")
        elif isinstance(node, ast.Call):
            called = dotted_name(node.func)
            require(
                called not in {
                    "urlopen", "urllib.request.urlopen", "requests.get", "requests.request",
                    "httpx.get", "httpx.request", "socket.socket", "subprocess.run",
                    "subprocess.Popen", "os.system", "os.popen",
                },
                f"D2W forbidden network/process call: {called}",
            )

    build_phase = function_source(source, "build_sealed_raw_split_handoff", "build_canonical_sealed_raw_split_handoff")
    seal_lookup_index = build_phase.index("preexisting_seal = ledger.get_campaign_seal")
    seal_required_index = build_phase.index("if preexisting_seal is None")
    plan_binding_index = build_phase.index("_verify_seal_plan_binding(preexisting_seal, plan)")
    d2v_reverify_index = build_phase.index("run_restart_safe_campaign(")
    offline_index = build_phase.index("allow_network=False", d2v_reverify_index)
    same_seal_index = build_phase.index("result.campaign_seal_fingerprint != preexisting_seal.fingerprint")
    partition_binding_index = build_phase.index("_verify_partition_material_against_seal")
    train_build_index = build_phase.index("RawTrainingMarketSource.build")
    development_build_index = build_phase.index("RawDevelopmentMarketSource.build")
    split_hash_index = build_phase.index("research_split_hash = _research_split_hash")
    require(
        seal_lookup_index < seal_required_index < plan_binding_index < d2v_reverify_index
        < offline_index < same_seal_index < partition_binding_index < train_build_index
        < development_build_index < split_hash_index,
        "D2W preexisting-seal/offline-reverify/source-build ordering drifted",
    )
    require("result.acquired_from_network != 0" in build_phase, "D2W must reject any network acquisition during handoff")
    require("training_identity != development_identity" in build_phase, "D2W must require one TRAIN/DEVELOPMENT universe identity")
    require("training_source.universe_identity_hash != development_source.universe_identity_hash" in build_phase, "D2W must recheck downstream source universe identity")

    split_phase = function_source(source, "_research_split_hash", "_require_hash")
    for marker in (
        '"d2v_campaign_seal_fingerprint": seal.fingerprint',
        '"d2u_plan_fingerprint": plan.fingerprint',
        '"d2u_partition_material_fingerprint": material.fingerprint',
        '"research_universe_identity_hash": research_universe_identity_hash_value',
        '"training_warmup_universe_hash": material.training_warmup.universe_hash',
        '"training_universe_hash": material.training.universe_hash',
        '"development_warmup_universe_hash": material.development_warmup.universe_hash',
        '"development_universe_hash": material.development.universe_hash',
        '"train_start": plan.train_start',
        '"development_start": plan.development_start',
        '"development_end": plan.development_end',
    ):
        require(marker in split_phase, f"D2W research split hash missing binding: {marker}")

    forbidden_text = (
        "derive_raw_training_bundle(",
        "derive_development",
        "SupervisedLabelArtifact",
        "build_concrete_model_request_set",
        "run_isolated_qlib_family_candidate",
        "prepare_family_evaluation_preregistration",
        "evaluate_development_predictions",
        "model.fit",
        "model.predict",
        "execution_authorized=True",
        "paper_execution_authorized=True",
        'capital_authority="PAPER"',
        'capital_authority="LIVE"',
        'live_trading="ENABLED"',
    )
    for marker in forbidden_text:
        require(marker not in source, f"forbidden D2W capability/state: {marker}")

    print(
        "AUTO-TRADE OSS-3D2W SEALED RAW SPLIT HANDOFF BOUNDARY: PASS — "
        "a pre-existing complete D2V campaign seal is required before full offline reverification; the exact D2U material "
        "is rebound to that same seal and converted only into D2R TRAIN and D2S pre-label DEVELOPMENT raw sources sharing "
        "one hash-bound research split/universe identity; network/labels/predictions/metrics/Qlib/FINAL_HOLDOUT/promotion/"
        "broker/OMS/Safety/PAPER/capital/LIVE denied"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundaryFailure as exc:
        print(f"AUTO-TRADE OSS-3D2W SEALED RAW SPLIT HANDOFF BOUNDARY: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
