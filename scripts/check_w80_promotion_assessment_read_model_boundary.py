from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/strategy_promotion_assessment_read_model.py"
W80_WORKFLOW = ROOT / ".github/workflows/w80-promotion-assessment.yml"
CORE_WORKFLOW = ROOT / ".github/workflows/core-tests.yml"

FORBIDDEN_MODULE_PREFIXES = (
    "autotrade.brokers",
    "autotrade.engine",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.connectivity",
    "autotrade.paper_close",
    "autotrade.strategy_promotion_assessment",
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
    "SQLiteRuntime",
    "SQLiteTrialLedger",
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
    "INSERT INTO strategy_promotion_assessments",
    "UPDATE strategy_promotion_assessments",
    "DELETE FROM strategy_promotion_assessments",
    "CREATE TABLE",
    "ALTER TABLE",
    "DROP TABLE",
)


def main() -> int:
    errors: list[str] = []
    for path in (TARGET, W80_WORKFLOW, CORE_WORKFLOW):
        if not path.is_file():
            errors.append(f"missing W80 reader contract file: {path.relative_to(ROOT)}")

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
                errors.append(f"forbidden W80 reader surface present: {marker}")

        required = (
            'ASSESSMENT_CONTRACT_VERSION = "W80_PROMOTION_ASSESSMENT_V1"',
            'ZERO_ASSESSMENT_HASH = "0" * 64',
            'sqlite3.connect(f"file:{encoded}?mode=ro"',
            'conn.execute("PRAGMA query_only=ON")',
            "assessment receipt hash mismatch",
            "assessment SQLite column mismatch",
            "assessment predecessor hash discontinuity",
            "assessment evidence regressed",
            "assessment gate regressed to MISSING",
            '"paper_candidate_authorized": False',
            '"external_execution_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
            '"broker_network_used": False',
            '"broker_write_performed": False',
            '"credentials_used": False',
        )
        for marker in required:
            if marker not in source:
                errors.append(f"required W80 reader fail-closed marker missing: {marker}")

    marker = "python scripts/check_w80_promotion_assessment_read_model_boundary.py"
    for workflow, label in ((W80_WORKFLOW, "W80 workflow"), (CORE_WORKFLOW, "Core Safety")):
        if workflow.is_file() and marker not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: W80 reader boundary not wired")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "W80 PROMOTION ASSESSMENT READ MODEL BOUNDARY PASS — independent mode=ro/query_only verification; "
        "writer import denied; receipt/side-column/hash-chain integrity revalidated; no broker/network/OMS/Safety authority; "
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
