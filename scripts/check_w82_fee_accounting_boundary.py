from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/fee_accounting.py"
W82_WORKFLOW = ROOT / ".github/workflows/w82-fee-accounting.yml"
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
    "sqlite3",
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
    for path in (TARGET, W82_WORKFLOW, CORE_WORKFLOW):
        if not path.is_file():
            errors.append(f"missing W82 contract file: {path.relative_to(ROOT)}")

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
                errors.append(f"forbidden W82 surface present: {marker}")

        required = (
            'FEE_ACCOUNTING_CONTRACT_VERSION = "W82_FEE_ACCOUNTING_V1"',
            'FEE_ACCOUNTING_SCOPE = "SIMULATED_QUALIFICATION_ONLY"',
            'SIMULATED_MODEL = "SIMULATED_MODEL"',
            'BROKER_AUTHORITATIVE = "BROKER_AUTHORITATIVE"',
            'FILLED_NOTIONAL_QUOTE = "FILLED_NOTIONAL_QUOTE"',
            "qualification.research_fee_bps != cost_model.fee_bps",
            "continuity.status is not ExecutionCostContinuityStatus.PASS",
            "continuity.sensitivity_measurement_hash != sensitivity_report.measurement_report_hash",
            "continuity_observation.outcome_hash != outcome.outcome_hash",
            "non_fee_components_counted_as_fee",
            '"broker_authoritative": False',
            '"broker_authoritative_fee_proven": False',
            '"realized_profitability_authorized": False',
            '"paper_candidate_authorized": False',
            '"external_execution_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
            "BROKER_AUTHORITATIVE fee accounting is unsupported",
        )
        for marker in required:
            if marker not in source:
                errors.append(f"required W82 fail-closed marker missing: {marker}")

    marker = "python scripts/check_w82_fee_accounting_boundary.py"
    for workflow, label in ((W82_WORKFLOW, "W82 workflow"), (CORE_WORKFLOW, "Core Safety")):
        if workflow.is_file() and marker not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: W82 fee accounting boundary not wired")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "W82 FEE ACCOUNTING BOUNDARY PASS — Research fee schedule + W78 measurement + W81 continuity are hash-bound; "
        "simulated filled-notional quote-currency fees are separate from spread/slippage; gross/net position deltas are not broker fee proof; "
        "no broker/network/SQLite/OMS/Safety/writer authority; realized-profitability unauthorized; PAPER candidate false; capital NONE; LIVE blocked"
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
