from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "autotrade" / "strategy_lab_promotion.py"

FORBIDDEN_MODULE_PREFIXES = (
    "autotrade.brokers",
    "autotrade.engine",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.paper_close",
    "requests",
    "httpx",
    "urllib",
    "socket",
    "websockets",
)
FORBIDDEN_NAMES = {
    "TradingPipeline",
    "CapitalSafetyKernel",
    "OrderManagementSystem",
    "ExecutionBroker",
    "run_paper_execution_sensitivity",
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
)


def main() -> int:
    if not TARGET.exists():
        print("ERROR: W79 strategy promotion module is missing", file=sys.stderr)
        return 1

    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET.relative_to(ROOT)))
    errors: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_module(alias.name):
                    errors.append(f"line {node.lineno}: forbidden import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_module(module):
                errors.append(f"line {node.lineno}: forbidden import {module}")
            if module == "autotrade.paper_execution_lab":
                names = {alias.name for alias in node.names}
                if names != {"PaperExecutionSensitivityReport"}:
                    errors.append(
                        f"line {node.lineno}: W79 may import only PaperExecutionSensitivityReport from W78 lab"
                    )
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    errors.append(f"line {node.lineno}: forbidden execution symbol {alias.name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            errors.append(f"line {node.lineno}: forbidden execution symbol {node.id}")
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS:
                errors.append(f"line {node.lineno}: forbidden execution/network call {name}")

    for marker in FORBIDDEN_TEXT:
        if marker in source:
            errors.append(f"forbidden credential/network marker present: {marker}")

    required_markers = (
        '"paper_candidate_authorized": False',
        '"external_execution_authorized": False',
        '"live_trading": "BLOCKED"',
        "PERMANENT_W79_PROMOTION_BLOCKERS",
        "EXECUTION_STRATEGY_VERSION_UNBOUND",
        "SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED",
        "TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN",
        "FEE_ACCOUNTING_INCOMPLETE",
    )
    for marker in required_markers:
        if marker not in source:
            errors.append(f"required fail-closed marker missing: {marker}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "W79 STRATEGY PROMOTION BOUNDARY PASS — frozen evidence only; "
        "no broker/network/Safety/OMS authority; PAPER candidate false; LIVE blocked"
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
