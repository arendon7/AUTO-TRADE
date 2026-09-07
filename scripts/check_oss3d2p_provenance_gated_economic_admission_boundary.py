from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "labs/oss3_qlib/economic_prediction_provenance_admission.py"


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
    require(MODULE.is_file(), "D2P module is missing")
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE))

    required_markers = (
        "OSS3D2P_ECONOMIC_PREDICTION_PROVENANCE_ADMISSION_V1",
        "OSS3D2P_D2O_PRECOMMIT_D2K_D2N_SHARED_SQLITE_ORDERING_V1",
        "INDEPENDENT_D2O_EXACT_PREDICTION_REPLAY_V1",
        "run_economic_prediction_provenance",
        "read_oss3d2n_prediction_precommit_read_only",
        "_require_no_d2k_state",
        "BEGIN IMMEDIATE",
        "oss3_economic_prediction_provenance_admissions",
        "oss3_d2p_d2n_start_requires_provenance_admission",
        "replay-verified prediction provenance admission required",
        "prediction_precommit_proven",
        "independent_d2o_replay_performed",
        "replay_prediction_exact_match",
        "provenance_receipt_recomputed",
        "frozen_before_d2k_start",
        "d2k_start_absent_at_commit",
        "d2k_permit_unconsumed_at_commit",
        "economic_labels_loaded",
        "economic_outcomes_loaded",
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    )
    for marker in required_markers:
        require(marker in source, f"missing D2P contract marker: {marker}")

    forbidden_literal_authority = (
        "execution_authorized=True",
        "paper_execution_authorized=True",
        "profitability_claim_authorized=True",
        "promotion_authorized=True",
        'capital_authority="PAPER"',
        'capital_authority="LIVE"',
        'live_trading="ENABLED"',
    )
    for marker in forbidden_literal_authority:
        require(marker not in source, f"forbidden D2P authority marker: {marker}")

    forbidden_import_roots = {
        "socket",
        "subprocess",
        "requests",
        "urllib",
        "http",
    }
    forbidden_project_fragments = (
        ".broker",
        ".oms",
        ".safety",
        ".execution",
        ".orders",
        ".order_intent",
    )
    direct_qlib_imports = []
    direct_d2k_or_d2n_calls = []
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
                    direct_qlib_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            require(root not in forbidden_import_roots, f"forbidden import: {module}")
            require(
                not any(fragment in module.lower() for fragment in forbidden_project_fragments),
                f"forbidden authority import: {module}",
            )
            if root == "qlib":
                direct_qlib_imports.append(module)
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
            if "FinalHoldoutEvaluationRegistry" in called or "EconomicHoldoutEvaluationRegistry" in called:
                direct_d2k_or_d2n_calls.append(called)

    require(not direct_qlib_imports, "D2P must delegate Qlib execution only to certified D2O")
    require(
        not direct_d2k_or_d2n_calls,
        "D2P admission must not itself execute D2K or D2N",
    )

    # D2P may inspect the point-in-time D2O feature artifact because it must
    # independently replay inference. It must not import raw economic market
    # bars or economic evaluation/outcome types.
    for forbidden in (
        "AlignedMarketUniverse",
        "MarketDataset",
        "EconomicHoldoutMaterial",
        "ProtectedEconomicHoldout",
        "OSS3EconomicDecision",
        "profit_factor",
        "max_drawdown",
        "net_return",
        "realized_pnl",
        "failed_gate_ids",
    ):
        require(forbidden not in source, f"D2P may not consume economic outcome surface: {forbidden}")

    # Replay must happen before the candidate is built and before the durable
    # admission transaction. Exact equality against precommit must occur before
    # any INSERT.
    replay_pos = source.find("replay_prediction, replay_attestation, d2o_receipt =")
    compare_pos = source.find("_verify_replay_matches_precommit(")
    candidate_pos = source.find("candidate = _build_admission_receipt(")
    transaction_pos = source.find('conn.execute("BEGIN IMMEDIATE")')
    insert_pos = source.find("INSERT INTO oss3_economic_prediction_provenance_admissions")
    require(-1 not in (replay_pos, compare_pos, candidate_pos, transaction_pos, insert_pos), "D2P ordering surface incomplete")
    require(
        replay_pos < compare_pos < candidate_pos < transaction_pos < insert_pos,
        "D2P replay/compare/durable ordering drifted",
    )

    semantic_requirements = (
        "economic_prediction_provenance_admission.py",
        "economic_prediction_provenance.py",
        "economic_prediction_precommit.py",
        "economic_holdout_evaluator.py",
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
        require(marker in source, f"D2P semantic hash omits dependency: {marker}")

    print(
        "AUTO-TRADE OSS-3D2P PROVENANCE-GATED ECONOMIC ADMISSION BOUNDARY: PASS — "
        "exact durable D2N precommit -> independent certified D2O replay -> exact artifact/payload/support equality -> "
        "append-only pre-D2K admission -> DB-enforced D2N start gate; economic outcomes and broker/OMS/Safety/OrderIntent/"
        "PAPER/capital/LIVE authority denied"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundaryFailure as exc:
        print(f"AUTO-TRADE OSS-3D2P PROVENANCE-GATED ECONOMIC ADMISSION BOUNDARY: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
