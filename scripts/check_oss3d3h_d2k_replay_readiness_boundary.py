from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "labs/oss3_qlib/d2k_replay_readiness.py"
RUNNER = ROOT / "scripts/run_oss3d3h_d2k_replay_readiness.py"


def main() -> int:
    sources = {"module": MODULE.read_text(encoding="utf-8"), "runner": RUNNER.read_text(encoding="utf-8")}
    forbidden = (
        "ProtectedOSS3FinalHoldout", "HoldoutPermit", "SQLiteOSS3FinalHoldoutEvaluationRegistry",
        "FinalHoldoutFeatureRow", "FinalHoldoutLabelRow", "materialize_protected_dual_holdouts",
        "_consume_and_record_start", "model.fit(", "model.predict(", "LinearModel",
        "submit_order", "place_order", "OrderIntent",
    )
    for label, source in sources.items():
        for marker in forbidden:
            if marker in source:
                raise SystemExit(f"D3H boundary violation in {label}: {marker}")
    module = sources["module"]
    required = (
        "build_canonical_sealed_raw_split_handoff", "derive_raw_training_bundle",
        "derive_raw_development_features", "build_concrete_model_request_set",
        "read_oss3d2j_protocol_read_only", "collect_candidate_environment_attestation",
        "evaluator_semantic_hash", "final_holdout_private_material_loaded: bool = False",
        "holdout_permit_issued: bool = False", "holdout_permit_consumed: bool = False",
        "final_holdout_checkout_authorized: bool = False", "capital_authority: str = \"NONE\"",
        "live_trading: str = \"BLOCKED\"",
    )
    for marker in required:
        if marker not in module:
            raise SystemExit(f"D3H required boundary marker missing: {marker}")
    print("OSS-3D3H D2K replay-readiness boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
