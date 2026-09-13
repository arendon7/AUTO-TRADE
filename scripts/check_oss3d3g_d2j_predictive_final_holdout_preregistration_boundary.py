from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    ROOT / "labs/oss3_qlib/d2j_predictive_final_holdout_preregistration.py",
    ROOT / "scripts/run_oss3d3g_d2j_predictive_final_holdout_preregistration.py",
)
FORBIDDEN = (
    "import qlib",
    "from qlib",
    "one_shot_final_holdout_evaluator",
    "run_oss3d2k",
    "consume_holdout_permit",
    "materialize_protected_dual_holdouts",
    "build_predictive_final_holdout_material",
    "run_dual_holdout_acquisition",
    "load_and_reverify_material",
    "requests.get",
    "urllib.request",
    "submit_order",
    "place_order",
    "OrderIntent",
)


def main() -> int:
    for path in SOURCES:
        if not path.is_file():
            raise SystemExit(f"missing D3G source: {path.relative_to(ROOT)}")
        source = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in source:
                raise SystemExit(f"D3G boundary violation in {path.name}: {token}")
    module = SOURCES[0].read_text(encoding="utf-8")
    required = (
        "read_durable_development_outcome",
        "read_public_d3f_evidence",
        "seal_development_winner",
        "SQLiteOSS3FinalHoldoutProtocolRegistry",
        "read_oss3d2j_protocol_read_only",
        "final_holdout_evaluations_performed=0",
        'capital_authority != "NONE"',
        'live_trading != "BLOCKED"',
    )
    for token in required:
        if token not in module:
            raise SystemExit(f"D3G boundary missing required guard: {token}")
    print("OSS-3D3G D2J preregistration authority boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
