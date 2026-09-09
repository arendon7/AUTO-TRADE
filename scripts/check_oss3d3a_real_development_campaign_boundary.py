from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autotrade.research.oss3_real_campaign_evidence import (  # noqa: E402
    EXPECTED_SEAL_FINGERPRINT as D2Y_SEAL,
)
from autotrade.research.oss3_real_descriptor_identity import (  # noqa: E402
    STABLE_MATERIAL_ROOT as D2Z_ROOT,
)


REHYDRATION = ROOT / "labs/oss3_market_data/real_campaign_rehydration.py"
CAMPAIGN = ROOT / "labs/oss3_qlib/real_development_campaign.py"
TEST = ROOT / "labs/oss3_qlib/tests/test_real_development_campaign.py"
DOC = ROOT / "knowledge/20_RESEARCH/OSS3D3A_REAL_DEVELOPMENT_CAMPAIGN.md"
REHYDRATION_CLI = ROOT / "scripts/run_oss3d3a_rehydration.py"
CAMPAIGN_CLI = ROOT / "scripts/run_oss3d3a_real_development_campaign.py"


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


def _forbid_runtime_surface(path: Path, *, forbid_network: bool) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    forbidden_fragments = (
        "broker",
        "oms",
        "order_intent",
        "holdout_permit",
        "final_holdout_evaluation",
        "protected_oss2_final_holdout",
        "economic_holdout",
    )
    network_roots = {"socket", "urllib", "requests", "httpx", "aiohttp", "subprocess"}
    network_calls = {
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
                lowered = alias.name.lower()
                require(not any(fragment in lowered for fragment in forbidden_fragments), f"forbidden D3A import: {alias.name}")
                if forbid_network:
                    require(alias.name.split(".", 1)[0].lower() not in network_roots, f"D3A model phase imports network/process root: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            lowered = (node.module or "").lower()
            require(not any(fragment in lowered for fragment in forbidden_fragments), f"forbidden D3A import: {lowered}")
            if forbid_network:
                require(lowered.split(".", 1)[0] not in network_roots, f"D3A model phase imports network/process root: {lowered}")
        elif isinstance(node, ast.Call) and forbid_network:
            called = dotted_name(node.func)
            require(called not in network_calls, f"D3A model phase performs network/process call: {called}")


def main() -> int:
    for path in (REHYDRATION, CAMPAIGN, TEST, DOC, REHYDRATION_CLI, CAMPAIGN_CLI):
        require(path.is_file(), f"missing D3A certification file: {path.relative_to(ROOT)}")

    rehydration = REHYDRATION.read_text(encoding="utf-8")
    campaign = CAMPAIGN.read_text(encoding="utf-8")

    for marker in (
        "OSS3D3A_CERTIFIED_REAL_CAMPAIGN_REHYDRATION_V1",
        "canonical_oss3d2y_real_campaign_evidence_seal",
        "verify_oss3d2y_real_campaign_evidence_seal",
        "load_canonical_oss3d2z_descriptor_material_manifest",
        "verify_rehydrated_descriptor_material",
        "build_canonical_sealed_raw_split_handoff",
        "source_inventory_verified=True",
        "source_tar_verified=True",
        "d2w_offline_reverification_complete=True",
        "network_used_during_rehydration=False",
        "final_holdout_observed=False",
        'capital_authority="NONE"',
        'live_trading="BLOCKED"',
    ):
        require(marker in rehydration, f"missing D3A rehydration marker: {marker}")

    for marker in (
        "OSS3D3A_REAL_DEVELOPMENT_CAMPAIGN_EVIDENCE_V1",
        "PRELABEL_REQUIRE_GLOBAL_AND_EVERY_CROSS_SECTION_SCORE_VARIATION_V1",
        "MIN_EVALUABLE_CANDIDATES = 2",
        "build_concrete_model_request_set",
        "run_isolated_qlib_family_candidate",
        "prepare_raw_development_preregistration",
        "materialize_development_labels_after_preregistration",
        "prepare_d2h_from_d2s_reveal",
        "preregister_family_evaluation",
        "record_oss3d2e_failure",
        "record_oss3d2e_evaluation",
        "evaluate_oss3d2e_tournament",
        "seal_development_winner",
        "verify_development_winner_seal",
        "family_retuned=False",
        "fallback_candidate_used=False",
        "reselection_allowed=False",
        "final_holdout_observed=False",
        "final_holdout_authorized=False",
        "holdout_permit_consumed=False",
        'capital_authority="NONE"',
        'live_trading="BLOCKED"',
    ):
        require(marker in campaign, f"missing D3A campaign marker: {marker}")

    # Static scientific-order proof: the exact-six prediction execution and
    # structural profile must occur before any D2S preregistration/label reveal.
    positions = {
        "run_six": campaign.index("_run_exact_six_outputs("),
        "profile": campaign.index("profiles = tuple(prediction_structural_profile"),
        "minimum_gate": campaign.index("if len(evaluable_ids) < MIN_EVALUABLE_CANDIDATES"),
        "d2s_prereg": campaign.index("d2s = prepare_raw_development_preregistration("),
        "durable_prereg": campaign.index("d2s_registry.preregister("),
        "label_reveal": campaign.index("development_labels, reveal = materialize_development_labels_after_preregistration("),
        "d2h": campaign.index("d2h = prepare_d2h_from_d2s_reveal("),
        "d2e_prereg": campaign.index("preregister_family_evaluation("),
        "evaluation": campaign.index("evaluation = evaluate_development_predictions("),
        "tournament": campaign.index("tournament = evaluate_oss3d2e_tournament("),
        "winner": campaign.index("winner: DevelopmentWinnerSelectionSeal = seal_development_winner("),
    }
    require(
        positions["run_six"] < positions["profile"] < positions["minimum_gate"] < positions["d2s_prereg"]
        < positions["durable_prereg"] < positions["label_reveal"] < positions["d2h"]
        < positions["d2e_prereg"] < positions["evaluation"] < positions["tournament"] < positions["winner"],
        "D3A scientific sequencing drifted",
    )

    expected_ids_marker = "tuple(candidate.candidate_id for candidate in CANONICAL_CANDIDATES)"
    require(campaign.count(expected_ids_marker) >= 2, "D3A must rebind exact canonical six in evidence and runner")

    lowered = campaign.lower()
    for marker in (
        "holdoutpermit",
        "consume_holdout_permit",
        "final_holdout_checkout",
        "evaluate_oss2_final_holdout",
        "submit_order",
        "place_order",
        "orderintent",
        "hyperparameter_optimization=true",
        "adaptive_search=true",
        "execution_authorized=true",
        "paper_execution_authorized=true",
        'capital_authority="paper"',
        'capital_authority="live"',
        'live_trading="enabled"',
    ):
        require(marker not in lowered, f"forbidden D3A capability marker: {marker}")

    _forbid_runtime_surface(REHYDRATION, forbid_network=True)
    _forbid_runtime_surface(CAMPAIGN, forbid_network=True)

    doc = DOC.read_text(encoding="utf-8").lower()
    for marker in (
        D2Y_SEAL,
        D2Z_ROOT,
        "99",
        "six",
        "development",
        "prelabel_blocked",
        "no retuning",
        "final_holdout",
        "capital_authority = none",
        "live_trading = blocked",
    ):
        require(marker.lower() in doc, f"missing D3A knowledge marker: {marker}")

    print(
        "AUTO-TRADE OSS-3D3A REAL DEVELOPMENT CAMPAIGN BOUNDARY: PASS — "
        "certified D2Y/D2Z/D2W real history -> D2R/D2S -> exact six D2G predictions -> "
        "pre-label structural gate -> durable preregistration -> D2E/Holm -> D2I; "
        "no retuning/fallback/FINAL_HOLDOUT/PAPER/capital/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
