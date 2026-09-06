from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "labs" / "oss3_qlib" / "final_holdout_protocol.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "qlib",
    "pandas",
    "numpy",
    "scipy",
    "sklearn",
    "socket",
    "subprocess",
    "requests",
    "aiohttp",
    "httpx",
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.engine",
    "autotrade.research.registry",
    "autotrade.research.splits",
    "autotrade.research.oss2_final_holdout_evaluation",
    "autotrade.research.oss2_final_holdout_protocol",
)
FORBIDDEN_CALL_NAMES = {
    "eval",
    "exec",
    "__import__",
    "import_module",
    "submit_order",
    "place_order",
    "send_order",
    "execute_order",
    "consume_holdout_permit",
    "checkout",
}
FORBIDDEN_AUTHORITY_NAMES = {
    "OrderIntent",
    "RiskDecision",
    "CapitalSafetyKernel",
    "HoldoutPermit",
    "ProtectedHoldout",
    "ProtectedOSS2FinalHoldout",
}


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        return _finish(["missing OSS-3D2J module"])

    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET.relative_to(ROOT)))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_module(alias.name):
                    errors.append(f"forbidden import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_module(module):
                errors.append(f"forbidden import: {module}")
            for alias in node.names:
                if alias.name in FORBIDDEN_AUTHORITY_NAMES:
                    errors.append(f"forbidden authority symbol: {alias.name}")
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALL_NAMES:
                errors.append(f"forbidden call: {name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_AUTHORITY_NAMES:
            errors.append(f"forbidden authority symbol: {node.id}")

    required = (
        'OSS3D2J_CONTRACT_VERSION = "OSS3D2J_FINAL_HOLDOUT_PROTOCOL_V1"',
        'OSS3D2J_WINNER_BINDING_VERSION = "OSS3D2J_WINNER_LINEAGE_BINDING_V1"',
        'OSS3D2J_COMMITMENT_VERSION = "OSS3D2J_PROTECTED_FINAL_HOLDOUT_COMMITMENT_V1"',
        'FINAL_HOLDOUT_SPLIT = "FINAL_HOLDOUT"',
        'FINAL_VALIDATION_PURPOSE = "final_validation"',
        "MIN_MEAN_CROSS_SECTIONAL_RANK_IC = 0.02",
        "MAX_ONE_SIDED_SIGN_TEST_P_VALUE = 0.05",
        "MIN_HOLDOUT_CROSS_SECTIONS = 30",
        "MIN_HOLDOUT_TOTAL_OBSERVATIONS = 90",
        "MIN_CROSS_SECTION_OBSERVATIONS = 3",
        "MIN_NONZERO_RANK_IC_CROSS_SECTIONS = 20",
        "MAX_EVALUATIONS = 1",
        "class OSS3D2JWinnerLineageBinding:",
        "class OSS3ProtectedFinalHoldoutCommitment:",
        "class OSS3FinalHoldoutProtocolPolicy:",
        "class OSS3FinalHoldoutProtocolReceipt:",
        "class SQLiteOSS3FinalHoldoutProtocolRegistry:",
        "verify_development_winner_seal(",
        "holdout_commitment_fingerprint TEXT NOT NULL UNIQUE",
        "source_d2i_seal_fingerprint TEXT NOT NULL UNIQUE",
        "OSS-3D2J registry is append-only",
        "label_values_exposed: bool = False",
        "final_holdout_observed: bool = False",
        '"final_holdout_observed": False',
        '"final_holdout_consumed": False',
        '"holdout_permit_issued": False',
        '"holdout_permit_consumed": False',
        '"final_holdout_checkout_authorized": False',
        '"predictive_validation_passed": False',
        '"profitability_claim_authorized": False',
        '"promotion_authorized": False',
        '"execution_authorized": False',
        '"paper_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        "D2J holdout chronological boundary overlaps DEVELOPMENT",
        "D2J holdout label artifact cannot reuse DEVELOPMENT labels",
        "D2J holdout keyset cannot reuse DEVELOPMENT support",
    )
    for snippet in required:
        if snippet not in source:
            errors.append(f"missing required D2J binding: {snippet}")

    forbidden_text = (
        "HoldoutPermit",
        "consume_holdout_permit",
        "ProtectedHoldout",
        "ProtectedOSS2FinalHoldout",
        "SQLiteOSS2FinalHoldoutEvaluationRegistry",
        "paper_execution_authorized=True",
        "execution_authorized=True",
        "promotion_authorized=True",
        "holdout_permit_issued=True",
        "holdout_permit_consumed=True",
        "final_holdout_checkout_authorized=True",
    )
    for snippet in forbidden_text:
        if snippet in source:
            errors.append(f"forbidden D2J authority surface: {snippet}")

    return _finish(errors)


def _finish(errors: list[str]) -> int:
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE OSS-3D2J FINAL_HOLDOUT protocol boundary: PASS "
        "(deep immutable D2I/D2H winner binding + value-opaque exact holdout commitment + "
        "fixed predictive gates; no holdout values/evaluator/permit/checkout/promotion/broker/OMS/"
        "Safety/PAPER/capital/LIVE)"
    )
    return 0


def _forbidden_module(module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in FORBIDDEN_IMPORT_PREFIXES
    )


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
