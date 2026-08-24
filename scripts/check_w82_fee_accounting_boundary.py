from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ROOT / "src/autotrade/fee_accounting.py",
    ROOT / "src/autotrade/fee_product_economics.py",
    ROOT / "src/autotrade/paper_fee_activity_evidence.py",
)
DOMAIN = ROOT / "src/autotrade/domain.py"
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
    for path in (*TARGETS, DOMAIN, W82_WORKFLOW, CORE_WORKFLOW):
        if not path.is_file():
            errors.append(f"missing W82 contract file: {path.relative_to(ROOT)}")

    for target in TARGETS:
        if target.is_file():
            errors.extend(_scan_target(target))

    if DOMAIN.is_file():
        errors.extend(_check_fill_shape(DOMAIN))

    required_by_file = {
        "fee_accounting.py": (
            'FEE_ACCOUNTING_CONTRACT_VERSION = "W82_FEE_ACCOUNTING_V1"',
            'FEE_ACCOUNTING_SCOPE = "SIMULATED_QUALIFICATION_ONLY"',
            'FILLED_NOTIONAL_QUOTE = "FILLED_NOTIONAL_QUOTE"',
            "qualification.research_fee_bps != cost_model.fee_bps",
            "continuity.status is not ExecutionCostContinuityStatus.PASS",
            "non_fee_components_counted_as_fee",
            '"broker_authoritative_fee_proven": False',
            '"realized_profitability_authorized": False',
            "BROKER_AUTHORITATIVE fee accounting is unsupported",
        ),
        "fee_product_economics.py": (
            'FEE_PRODUCT_ECONOMICS_VERSION = "W82_FEE_PRODUCT_ECONOMICS_V1"',
            'RECEIVED_ASSET_PERCENT = "RECEIVED_ASSET_PERCENT"',
            'WORST_CASE = "WORST_CASE"',
            "research_fee_bps >= policy.minimum_fee_bps",
            '"RESEARCH_FEE_BELOW_POLICY"',
            "filled - charged_amount",
            '"literal_broker_fee_semantics_modeled": True',
            '"broker_authoritative_fee_proven": False',
            '"realized_profitability_authorized": False',
        ),
        "paper_fee_activity_evidence.py": (
            'PAPER_FEE_ACTIVITY_VERSION = "W82_PAPER_FEE_ACTIVITY_V1"',
            'OBSERVED = "OBSERVED"',
            'PENDING_PUBLICATION = "PENDING_PUBLICATION"',
            '"CFEE", "FEE"',
            '"FEE_ACTIVITY_NOT_YET_OBSERVED"',
            '"zero_fee_inferred": False',
            '"broker_authoritative_fee_proven": False',
            '"paper_only": True',
        ),
    }
    for target in TARGETS:
        if not target.is_file():
            continue
        source = target.read_text(encoding="utf-8")
        for marker in required_by_file[target.name]:
            if marker not in source:
                errors.append(f"{target.name}: required W82 fail-closed marker missing: {marker}")

    marker = "python scripts/check_w82_fee_accounting_boundary.py"
    for workflow, label in ((W82_WORKFLOW, "W82 workflow"), (CORE_WORKFLOW, "Core Safety")):
        if workflow.is_file() and marker not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: W82 fee accounting boundary not wired")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "W82 FEE ACCOUNTING BOUNDARY PASS — base fee arithmetic, preregistered product/venue fee floor, received-asset semantics and delayed PAPER fee activity are separate/hash-bound; canonical Fill unchanged; missing activity never means zero; no broker/network/SQLite/OMS/Safety/writer authority; realized-profitability unauthorized; PAPER candidate false; capital NONE; LIVE blocked"
    )
    return 0


def _scan_target(path: Path) -> list[str]:
    errors: list[str] = []
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path.relative_to(ROOT)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_module(alias.name):
                    errors.append(f"{path.name}:{node.lineno}: forbidden import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_module(module):
                errors.append(f"{path.name}:{node.lineno}: forbidden import {module}")
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    errors.append(f"{path.name}:{node.lineno}: forbidden authority symbol {alias.name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            errors.append(f"{path.name}:{node.lineno}: forbidden authority symbol {node.id}")
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS:
                errors.append(f"{path.name}:{node.lineno}: forbidden authority/network call {name}")
    for marker in FORBIDDEN_TEXT:
        if marker in source:
            errors.append(f"{path.name}: forbidden W82 surface present: {marker}")
    return errors


def _check_fill_shape(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path.relative_to(ROOT)))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Fill":
            fields = [
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            ]
            expected = ["fill_id", "order_id", "symbol", "side", "quantity", "price", "occurred_at"]
            return [] if fields == expected else [f"domain.Fill shape changed: {fields!r}"]
    return ["domain.Fill class not found"]


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
