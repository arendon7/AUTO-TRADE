from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/strategy_promotion_assessment.py"
W80_WORKFLOW = ROOT / ".github/workflows/w80-promotion-assessment.yml"
CORE_WORKFLOW = ROOT / ".github/workflows/core-tests.yml"

FORBIDDEN_MODULE_PREFIXES = (
    "autotrade.brokers",
    "autotrade.engine",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.connectivity",
    "autotrade.paper_close",
    "requests",
    "httpx",
    "urllib.request",
    "socket",
    "websockets",
)
FORBIDDEN_NAMES = {
    "TradingPipeline",
    "CapitalSafetyKernel",
    "OrderManagementSystem",
    "ExecutionBroker",
}
FORBIDDEN_CALLS = {
    "submit",
    "submit_order",
    "place_order",
    "execute_order",
    "cancel_order",
    "replace_order",
    "send_order",
    "urlopen",
}
FORBIDDEN_TEXT = (
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "api.alpaca.markets",
    "paper-api.alpaca.markets",
    "Authorization: Bearer",
    "UPDATE strategy_promotion_assessments",
    "DELETE FROM strategy_promotion_assessments",
    "INSERT OR REPLACE INTO strategy_promotion_assessments",
    "REPLACE INTO strategy_promotion_assessments",
)


def main() -> int:
    errors: list[str] = []
    for path in (TARGET, W80_WORKFLOW, CORE_WORKFLOW):
        if not path.is_file():
            errors.append(f"missing W80 contract file: {path.relative_to(ROOT)}")

    if TARGET.is_file():
        source = TARGET.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(TARGET.relative_to(ROOT)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _forbidden_module(alias.name):
                        errors.append(f"line {node.lineno}: forbidden import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _forbidden_module(module):
                    errors.append(f"line {node.lineno}: forbidden import {module}")
                for alias in node.names:
                    if alias.name in FORBIDDEN_NAMES:
                        errors.append(f"line {node.lineno}: forbidden authority symbol {alias.name}")
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                errors.append(f"line {node.lineno}: forbidden authority symbol {node.id}")
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in FORBIDDEN_CALLS:
                    errors.append(f"line {node.lineno}: forbidden authority/network call {name}")

        for marker in FORBIDDEN_TEXT:
            if marker in source:
                errors.append(f"forbidden W80 surface present: {marker}")

        required = (
            'ASSESSMENT_CONTRACT_VERSION = "W80_PROMOTION_ASSESSMENT_V1"',
            'ZERO_ASSESSMENT_HASH = "0" * 64',
            "CREATE TABLE IF NOT EXISTS strategy_promotion_assessments",
            "INSERT INTO strategy_promotion_assessments(",
            'conn.execute("BEGIN IMMEDIATE")',
            "evaluate_strategy_promotion(",
            "previous_assessment_hash",
            "assessment evidence hashes may not regress",
            "assessment gate may not regress to MISSING",
            '"paper_candidate_authorized": False',
            '"external_execution_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
            "must share one authoritative SQLite runtime",
        )
        for marker in required:
            if marker not in source:
                errors.append(f"required W80 fail-closed marker missing: {marker}")

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                if any(
                    isinstance(arg.annotation, ast.Name)
                    and arg.annotation.id == "StrategyPromotionEvidenceView"
                    for arg in node.args.args
                    if arg.annotation is not None
                ):
                    errors.append(
                        f"public function {node.name} may not accept an arbitrary prebuilt StrategyPromotionEvidenceView"
                    )
            if isinstance(node, ast.ClassDef) and node.name == "SQLiteStrategyPromotionAssessmentRegistry":
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith("_"):
                        annotations = [arg.annotation for arg in child.args.args if arg.annotation is not None]
                        if any(isinstance(annotation, ast.Name) and annotation.id == "StrategyPromotionEvidenceView" for annotation in annotations):
                            errors.append(
                                f"public assessment registry method {child.name} may not accept arbitrary prebuilt promotion views"
                            )

    marker = "python scripts/check_w80_promotion_assessment_boundary.py"
    for workflow, label in ((W80_WORKFLOW, "W80 workflow"), (CORE_WORKFLOW, "Core Safety")):
        if workflow.is_file() and marker not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: W80 durable assessment boundary not wired")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "W80 PROMOTION ASSESSMENT BOUNDARY PASS — append-only hash-chained scientific assessments only; "
        "internal W79 evaluation; no arbitrary public view ingestion; no broker/network/OMS/Safety authority; "
        "PAPER candidate false; capital NONE; LIVE blocked"
    )
    return 0


def _forbidden_module(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_MODULE_PREFIXES)


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())