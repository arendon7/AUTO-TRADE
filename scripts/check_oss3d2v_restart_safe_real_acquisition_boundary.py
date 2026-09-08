from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "labs/oss3_market_data/real_acquisition_campaign.py"
CLI = ROOT / "scripts/run_oss3d2v_real_acquisition.py"
GITIGNORE = ROOT / ".gitignore"


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
    for path in (RUNNER, CLI, GITIGNORE):
        require(path.is_file(), f"missing D2V boundary file: {path.relative_to(ROOT)}")
    runner = RUNNER.read_text(encoding="utf-8")
    cli = CLI.read_text(encoding="utf-8")
    gitignore = GITIGNORE.read_text(encoding="utf-8")
    runner_tree = ast.parse(runner, filename=str(RUNNER))
    cli_tree = ast.parse(cli, filename=str(CLI))

    for marker in (
        "OSS3D2V_DESCRIPTOR_EVIDENCE_SEAL_V1",
        "OSS3D2V_COMPLETE_COLLECTION_SEAL_V1",
        "REUSE_ONLY_AFTER_FULL_RAW_RECEIPT_SNAPSHOT_REVERIFICATION_V1",
        "ATOMIC_DIRECTORY_RENAME_NO_STALE_STAGE_REUSE_V1",
        "REAL_EVIDENCE_ROOT_MUST_RESOLVE_OUTSIDE_GIT_REPOSITORY_V1",
        "EXECUTE_EXACT_CERTIFIED_D2U_PLAN_WITHOUT_FAMILY_OR_PARTITION_DRIFT_V1",
        "PUBLIC_GET_ONLY_THROUGH_D2U_TRIPLE_CHECKSUM_ADAPTER_V1",
        "oss3d2v_descriptor_seals",
        "oss3d2v_campaign_seals",
        "OSS3D2V_APPEND_ONLY",
        "full_reverification_required_on_reuse=True",
        "final_holdout_values_requested=False",
        "final_holdout_values_loaded=False",
        "execution_authorized=False",
        "paper_execution_authorized=False",
        'capital_authority="NONE"',
        'live_trading="BLOCKED"',
    ):
        require(marker in runner, f"missing D2V boundary marker: {marker}")

    # D2V must consume the already-frozen D2U plan, not define another data family.
    require("canonical_oss3d2u_collection_plan" in runner, "D2V must reuse canonical D2U plan")
    for forbidden in (
        "BinanceSpotArchiveDescriptor.monthly(",
        "CANONICAL_SYMBOLS =",
        "CANONICAL_FIRST_MONTH =",
        "CANONICAL_LAST_MONTH =",
        "development_start=",
        "train_start=",
    ):
        require(forbidden not in runner, f"D2V may not redefine D2U family/partition: {forbidden}")

    allowed_network_imports = {
        "ReadOnlyHttpTransport",
    }
    for node in ast.walk(runner_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                require(root not in {"requests", "httpx", "aiohttp", "socket", "subprocess"}, f"D2V bypass network/process import: {alias.name}")
                require(root != "qlib", "D2V cannot import Qlib")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            lowered = module.lower()
            if module == "autotrade.research.external_data":
                require({alias.name for alias in node.names} == allowed_network_imports, "D2V external_data import surface drifted")
            require("qlib" not in lowered, f"D2V cannot import Qlib: {module}")
            require("supervised_label" not in lowered, f"D2V cannot import labels: {module}")
            require("development_evaluation" not in lowered, f"D2V cannot import evaluation: {module}")
            require("final_holdout" not in lowered, f"D2V cannot import holdout material: {module}")
            require(not any(fragment in lowered for fragment in ("broker", "oms", "safety", "order_intent")), f"D2V forbidden authority import: {module}")
        elif isinstance(node, ast.Call):
            called = dotted_name(node.func)
            require(
                called not in {
                    "urlopen", "urllib.request.urlopen", "requests.get", "requests.request",
                    "httpx.get", "httpx.request", "socket.socket", "subprocess.run",
                    "subprocess.Popen", "os.system", "os.popen",
                },
                f"D2V runner bypasses D2U acquisition adapter: {called}",
            )

    for required_import in (
        "acquire_preregistered_archive",
        "build_real_archive_transport",
    ):
        require(required_import in runner, f"D2V must reuse D2U acquisition authority: {required_import}")

    run_phase = function_source(runner, "run_restart_safe_campaign", "run_canonical_campaign")
    plan_registry_index = run_phase.index("d2u_registry.preregister(plan")
    exact_plan_index = run_phase.index("d2u_registry.require_exact(plan)")
    transport_index = run_phase.index("build_real_archive_transport(plan=plan)")
    loop_index = run_phase.index("for descriptor in plan.descriptors")
    existing_seal_index = run_phase.index("if existing_seal is not None")
    local_final_index = run_phase.index("if final_directory.exists()")
    no_network_index = run_phase.index("if not allow_network:", local_final_index)
    acquire_index = run_phase.index("acquire_preregistered_archive(")
    persist_index = run_phase.index("persist_material_with_atomic_directory_commit(")
    seal_index = run_phase.index("ledger.put_descriptor_seal(seal)", persist_index)
    assemble_index = run_phase.index("assemble_historical_collection(")
    campaign_seal_index = run_phase.index("CompleteCollectionSeal(", assemble_index)
    require(
        plan_registry_index < exact_plan_index < transport_index < loop_index
        < existing_seal_index < local_final_index < no_network_index < acquire_index
        < persist_index < seal_index < assemble_index < campaign_seal_index,
        "D2V preregister/reuse/acquire/persist/seal/assemble ordering drifted",
    )
    require("load_and_reverify_material" in run_phase, "D2V restart must fully reload material")
    require("existing_seal" in run_phase and "_verify_descriptor_seal" in run_phase, "D2V sealed reuse must reverify seal")
    require("ledger.descriptor_seal_count() != len(plan.descriptors)" in run_phase, "D2V final seal requires exact descriptor count")

    persistence_phase = function_source(runner, "persist_material_with_atomic_directory_commit", "load_and_reverify_material")
    require("os.replace(staged_directory, final_directory)" in persistence_phase, "D2V final evidence commit must be atomic directory rename")
    require("refuses to overwrite existing final descriptor evidence" in persistence_phase, "D2V must deny final evidence overwrite")
    require("stale staging evidence requires explicit operator review" in persistence_phase, "D2V must fail on stale staging")
    require("_load_material_from_directory" in persistence_phase, "D2V must reverify staged material before rename")

    load_phase = function_source(runner, "load_and_reverify_material", "_load_material_from_directory")
    require("descriptor.fingerprint not in set(plan.descriptor_fingerprints)" in load_phase, "D2V loader must bind frozen D2U plan")
    raw_load_phase = function_source(runner, "_load_material_from_directory", "_read_receipt")
    for marker in (
        "HistoricalMarketSnapshotArtifact.read",
        "_read_receipt(",
        "AcquiredArchiveMaterial(",
        "actual_names != expected_names",
    ):
        require(marker in raw_load_phase, f"D2V full persisted evidence reverification missing: {marker}")

    storage_phase = function_source(runner, "validate_external_evidence_root", "persist_material_with_atomic_directory_commit")
    require("repo in resolved.parents" in storage_phase, "D2V evidence root must reject repository descendants")
    require("root.is_symlink()" in storage_phase, "D2V evidence root must reject symlink root")

    for marker in (
        "--evidence-root",
        "--execute-public-get",
        'action="store_true"',
        "allow_network=bool(args.execute_public_get)",
    ):
        require(marker in cli, f"D2V CLI fail-closed network gate missing: {marker}")

    for tree, label in ((runner_tree, "runner"), (cli_tree, "CLI")):
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value.lower()
                require("/api/v3/order" not in value, f"D2V {label} contains trading endpoint")
                require("x-mbx-apikey" not in value, f"D2V {label} contains credential header")

    for marker in (
        "model.fit",
        "model.predict",
        "SupervisedLabelArtifact",
        "evaluate_development_predictions",
        "execution_authorized=True",
        "paper_execution_authorized=True",
        'capital_authority="PAPER"',
        'capital_authority="LIVE"',
        'live_trading="ENABLED"',
    ):
        require(marker not in runner, f"forbidden D2V capability/state: {marker}")

    require(".oss3d2v-evidence/" in gitignore and "oss3d2v-evidence/" in gitignore, "D2V gitignore defense is missing")

    print(
        "AUTO-TRADE OSS-3D2V RESTART-SAFE REAL ACQUISITION BOUNDARY: PASS — "
        "D2V reuses the exact certified D2U plan; durable plan precedes transport creation; sealed/local material is fully "
        "reverified before reuse; missing material alone may use D2U GET-only triple-checksum acquisition; final evidence is "
        "atomic and non-overwriting; descriptor/campaign seals are append-only; complete seal requires exact D2U assembly; "
        "FINAL_HOLDOUT/Qlib/labels/models/broker/OMS/Safety/PAPER/capital/LIVE denied"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundaryFailure as exc:
        print(f"AUTO-TRADE OSS-3D2V RESTART-SAFE REAL ACQUISITION BOUNDARY: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
