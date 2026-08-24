from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BINDING = ROOT / "src/autotrade/strategy_execution_binding.py"
RESOLUTION = ROOT / "src/autotrade/promotion_strategy_version_binding.py"
TEST_BINDING = ROOT / "tests/test_w83_execution_strategy_binding.py"
TEST_RESOLUTION = ROOT / "tests/test_w83_promotion_strategy_version_resolution.py"
W83_WORKFLOW = ROOT / ".github/workflows/w83-strategy-version-binding.yml"
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
    "paper-api.alpaca.markets",
    "api.alpaca.markets",
    "INSERT INTO",
    "UPDATE ",
    "DELETE FROM",
    "CREATE TABLE",
    "OrderIntent(",
    'paper_candidate_authorized": True',
    'external_execution_authorized": True',
    'runtime_execution_authorized": True',
    'live_trading": "ENABLED"',
)

BINDING_REQUIRED = (
    'EXECUTION_STRATEGY_BINDING_VERSION = "W83_EXECUTION_STRATEGY_BINDING_V1"',
    'RUNTIME_PROJECTION_CONTRACT_VERSION = "W83_DSL_SIGNAL_TO_EXISTING_MARKET_INTENT_V1"',
    'selected_trial.parameters.get("spec_hash") != strategy_spec.canonical_hash',
    "dict(selected_trial.parameters) != runtime_parameters",
    "fee_product_economics.evidence_hash != w82_resolution.fee_product_economics_hash",
    "fee_product_economics.venue != dataset.instrument.venue",
    "fee_product_economics.quote_currency != dataset.instrument.quote_currency",
    "full_intent_hash != w82_resolution.intent_fingerprint",
    "execution_intent.order_type is not OrderType.MARKET",
    "execution intent quantity differs from deterministic signal delta",
    '"strategy_version_binding_proven": True',
    '"shadow_forward_promotion_bound": False',
    '"paper_candidate_authorized": False',
    '"external_execution_authorized": False',
    '"runtime_execution_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
)

RESOLUTION_REQUIRED = (
    '"W83_PROMOTION_STRATEGY_VERSION_RESOLUTION_V1"',
    '"W83_SAFE_DSL_RUNTIME_CODE_IDENTITY_V2"',
    "class SafeDslRuntimeIdentity:",
    "research_dsl.StrategySpec",
    "research_strategy.StrategyContext",
    "research_market.Bar",
    "sys.version_info.micro",
    '"runtime_dsl_source_hash"',
    '"runtime_strategy_source_hash"',
    '"runtime_market_source_hash"',
    "Path(source_path).read_bytes()",
    "selected_trial.code_version != loaded_runtime_hash",
    'selected_trial.parameters.get("spec_hash")',
    "binding_evidence.fee_product_economics_hash",
    "w82_resolution.fee_product_economics_hash",
    "w82_resolution.strategy_version_execution_bound is not False",
    "STRATEGY_VERSION_BLOCKER",
    "SHADOW_FORWARD_BLOCKER",
    '"resolved_promotion_blockers": (STRATEGY_VERSION_BLOCKER,)',
    '"strategy_version_execution_bound": True',
    '"shadow_forward_promotion_bound": False',
    '"paper_candidate_authorized": False',
    '"external_execution_authorized": False',
    '"runtime_execution_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
)


def main() -> int:
    errors: list[str] = []
    required_files = (
        BINDING,
        RESOLUTION,
        TEST_BINDING,
        TEST_RESOLUTION,
        W83_WORKFLOW,
        CORE_WORKFLOW,
    )
    for path in required_files:
        if not path.is_file():
            errors.append(f"missing W83 contract file: {path.relative_to(ROOT)}")

    for path in (BINDING, RESOLUTION):
        if path.is_file():
            errors.extend(_scan_authority(path))

    if BINDING.is_file():
        source = BINDING.read_text(encoding="utf-8")
        for marker in BINDING_REQUIRED:
            if marker not in source:
                errors.append(f"W83 binding marker missing: {marker}")

    if RESOLUTION.is_file():
        source = RESOLUTION.read_text(encoding="utf-8")
        for marker in RESOLUTION_REQUIRED:
            if marker not in source:
                errors.append(f"W83 resolution marker missing: {marker}")

    boundary_marker = "python scripts/check_w83_strategy_version_binding_boundary.py"
    for workflow, label in (
        (W83_WORKFLOW, "W83 workflow"),
        (CORE_WORKFLOW, "Core Safety"),
    ):
        if workflow.is_file() and boundary_marker not in workflow.read_text(
            encoding="utf-8"
        ):
            errors.append(f"{label}: W83 boundary not wired")

    if W83_WORKFLOW.is_file():
        workflow_source = W83_WORKFLOW.read_text(encoding="utf-8")
        for path in (
            "tests/test_w83_execution_strategy_binding.py",
            "tests/test_w83_promotion_strategy_version_resolution.py",
        ):
            if path not in workflow_source:
                errors.append(f"W83 workflow: required test not wired: {path}")
        for inherited in (
            "check_w82_fee_accounting_boundary.py",
            "check_w82_promotion_fee_accounting_boundary.py",
            "check_w81_execution_cost_continuity_boundary.py",
            "check_w81_promotion_cost_continuity_boundary.py",
            "check_w80_promotion_assessment_boundary.py",
            "check_w79_strategy_promotion_boundary.py",
            "check_w78_paper_execution_boundary.py",
            "check_research_authority.py",
        ):
            if inherited not in workflow_source:
                errors.append(
                    f"W83 workflow: inherited proof not re-run: {inherited}"
                )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "W83 STRATEGY VERSION BINDING BOUNDARY PASS — exact W79 selected trial "
        "freezes StrategySpec canonical hash + dataset; exact W82 product/venue/"
        "currency/intent provenance is retained; loaded safe-DSL semantic source "
        "set (dsl.py + strategy.py + market.py) plus exact Python implementation/"
        "patch must equal preregistered trial code_version; only "
        "EXECUTION_STRATEGY_VERSION_UNBOUND may be resolved; Shadow/Forward "
        "remains blocked; no broker/network/SQLite/OMS/Safety/OrderIntent "
        "construction authority; PAPER candidate false; capital NONE; LIVE blocked"
    )
    return 0


def _scan_authority(path: Path) -> list[str]:
    errors: list[str] = []
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path.relative_to(ROOT)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_module(alias.name):
                    errors.append(
                        f"{path.name}:{node.lineno}: forbidden import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_module(module):
                errors.append(
                    f"{path.name}:{node.lineno}: forbidden import {module}"
                )
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS:
                errors.append(
                    f"{path.name}:{node.lineno}: forbidden authority/network call {name}"
                )
    for marker in FORBIDDEN_TEXT:
        if marker in source:
            errors.append(f"{path.name}: forbidden W83 surface present: {marker}")
    return errors


def _forbidden_module(module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in FORBIDDEN_MODULE_PREFIXES
    )


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
