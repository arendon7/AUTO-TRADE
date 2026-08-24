from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/promotion_fee_accounting.py"
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
FORBIDDEN_CALLS = {
    "submit", "submit_order", "place_order", "execute_order", "cancel_order",
    "replace_order", "send_order", "urlopen", "connect",
}
FORBIDDEN_TEXT = (
    "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "paper-api.alpaca.markets",
    "api.alpaca.markets", "INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE",
    "OrderIntent(", 'paper_candidate_authorized": True',
    'external_execution_authorized": True', 'live_trading": "ENABLED"',
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
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in FORBIDDEN_CALLS:
                    errors.append(f"line {node.lineno}: forbidden authority/network call {name}")
        for marker in FORBIDDEN_TEXT:
            if marker in source:
                errors.append(f"forbidden W82 resolution surface present: {marker}")

        required = (
            'RESOLUTION_CONTRACT_VERSION = "W82_PROMOTION_FEE_ACCOUNTING_RESOLUTION_V1"',
            'STRATEGY_VERSION_BLOCKER = "EXECUTION_STRATEGY_VERSION_UNBOUND"',
            'SHADOW_FORWARD_BLOCKER = "SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED"',
            "w81_resolution.status is not PromotionCostContinuityStatus.PASS",
            "fee_evidence.status is not FeeAccountingStatus.COMPLETE",
            "fee_evidence.w81_continuity_evidence_hash != w81_resolution.continuity_evidence_hash",
            "fee_evidence.sensitivity_measurement_hash != w81_resolution.continuity_measurement_hash",
            '"broker_authoritative_fee_proven": False',
            '"realized_profitability_authorized": False',
            '"strategy_version_execution_bound": False',
            '"shadow_forward_promotion_bound": False',
            '"paper_candidate_authorized": False',
            '"external_execution_authorized": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
        )
        for marker in required:
            if marker not in source:
                errors.append(f"required W82 resolution marker missing: {marker}")

    marker = "python scripts/check_w82_promotion_fee_accounting_boundary.py"
    for workflow, label in ((W82_WORKFLOW, "W82 workflow"), (CORE_WORKFLOW, "Core Safety")):
        if workflow.is_file() and marker not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: W82 promotion fee resolution boundary not wired")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "W82 PROMOTION FEE RESOLUTION BOUNDARY PASS — fee completeness resolves only the exact W81 candidate fee blocker; "
        "strategy-version and Shadow/Forward remain blocked; simulated fees are not broker proof or realized profitability; "
        "no broker/network/SQLite/OMS/Safety authority; PAPER candidate false; capital NONE; LIVE blocked"
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
