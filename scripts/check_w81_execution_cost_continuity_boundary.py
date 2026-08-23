from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/execution_cost_continuity.py"
W81_WORKFLOW = ROOT / ".github/workflows/w81-execution-cost-continuity.yml"
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
    "SQLiteRuntime",
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
    "connect",
}
FORBIDDEN_TEXT = (
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "api.alpaca.markets",
    "paper-api.alpaca.markets",
    "Authorization: Bearer",
    "INSERT INTO",
    "UPDATE ",
    "DELETE FROM",
    "CREATE TABLE",
    "OrderIntent(",
    'paper_candidate_authorized": True',
    'external_execution_authorized": True',
    'live_trading": "ENABLED"',
)


def main() -> int:
    errors: list[str] = []
    for path in (TARGET, W81_WORKFLOW, CORE_WORKFLOW):
        if not path.is_file():
            errors.append(f"missing W81 contract file: {path.relative_to(ROOT)}")

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
                errors.append(f"forbidden W81 surface present: {marker}")

        required = (
            'CONTINUITY_CONTRACT_VERSION = "W81_EXECUTION_COST_CONTINUITY_V1"',
            'CONTINUITY_BLOCKER = "TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN"',
            'FEE_ACCOUNTING_BLOCKER = "FEE_ACCOUNTING_INCOMPLETE"',
            'FEE_ACCOUNTING_STATE = "INCOMPLETE_NOT_ASSESSED_BY_W81"',
            "qualification.research_cost_model_hash != cost_hash",
            "qualification.scenario_matrix_hash != matrix.matrix_hash",
            "sensitivity_report.qualification_contract_hash != qualification.contract_hash",
            "sensitivity_report.scenario_matrix_hash != matrix.matrix_hash",
            "sensitivity_report.intent_fingerprint != intent_hash",
            "sensitivity_report.market_fingerprint != market_hash",
            "effective_non_fee_impact_bps",
            "research_non_fee_impact_bps",
            "continuity_margin_bps",
            '"fee_accounting_complete": False',
            '"paper_candidate_authorized": False',
            '"external_execution_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
        )
        for marker in required:
            if marker not in source:
                errors.append(f"required W81 fail-closed marker missing: {marker}")

    marker = "python scripts/check_w81_execution_cost_continuity_boundary.py"
    for workflow, label in ((W81_WORKFLOW, "W81 workflow"), (CORE_WORKFLOW, "Core Safety")):
        if workflow.is_file() and marker not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: W81 execution cost continuity boundary not wired")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "W81 EXECUTION COST CONTINUITY BOUNDARY PASS — Research cost hash + W78 qualification/matrix/report + "
        "intent/market fingerprints bound; midpoint non-fee impact cannot silently weaken preregistered friction; "
        "fee accounting remains incomplete; no broker/network/SQLite/OMS/Safety/OrderIntent authority; "
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
