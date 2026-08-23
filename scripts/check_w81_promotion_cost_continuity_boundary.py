from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/promotion_cost_continuity.py"
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
            errors.append(f"missing W81 resolution contract file: {path.relative_to(ROOT)}")

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
                errors.append(f"forbidden W81 resolution surface present: {marker}")

        required = (
            'RESOLUTION_CONTRACT_VERSION = "W81_PROMOTION_COST_CONTINUITY_RESOLUTION_V1"',
            "continuity.intent_fingerprint != intent_hash",
            "execution_intent.strategy_id != assessment.selected_strategy_id",
            'gate.gate_id == "EXECUTION_SENSITIVITY"',
            "continuity.sensitivity_measurement_hash not in execution_gate.evidence_hashes",
            'reasons.append("W81_MEASUREMENT_NOT_BOUND_TO_W80_EXECUTION_GATE")',
            '"fee_accounting_complete": False',
            '"strategy_version_execution_bound": False',
            '"paper_candidate_authorized": False',
            '"external_execution_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
        )
        for marker in required:
            if marker not in source:
                errors.append(f"required W81 candidate-binding marker missing: {marker}")

    marker = "python scripts/check_w81_promotion_cost_continuity_boundary.py"
    for workflow, label in ((W81_WORKFLOW, "W81 workflow"), (CORE_WORKFLOW, "Core Safety")):
        if workflow.is_file() and marker not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: W81 candidate continuity resolution boundary not wired")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "W81 PROMOTION COST CONTINUITY RESOLUTION BOUNDARY PASS — standalone continuity cannot resolve a candidate; "
        "exact W80 assessment + selected strategy + EXECUTION_SENSITIVITY measurement binding required; "
        "fee and strategy-version blockers remain; no broker/network/SQLite/OMS/Safety authority; "
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
