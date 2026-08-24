from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/promotion_fee_accounting.py"
W82_WORKFLOW = ROOT / ".github/workflows/w82-fee-accounting.yml"
CORE_WORKFLOW = ROOT / ".github/workflows/core-tests.yml"
HARDENING_TEST = "tests/test_w82_promotion_fee_accounting_hardening.py"
SCHEDULE_TEST = "tests/test_w82_fee_schedule_attestation.py"

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
    'live_trading": "ENABLED"',
    "preregistered product/venue policy",
)


def main() -> int:
    errors: list[str] = []
    for path in (
        TARGET,
        W82_WORKFLOW,
        CORE_WORKFLOW,
        ROOT / HARDENING_TEST,
        ROOT / SCHEDULE_TEST,
    ):
        if not path.is_file():
            errors.append(f"missing W82 contract file: {path.relative_to(ROOT)}")

    if TARGET.is_file():
        source = TARGET.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(TARGET.relative_to(ROOT)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _forbidden_module(alias.name):
                        errors.append(
                            f"line {node.lineno}: forbidden import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _forbidden_module(module):
                    errors.append(
                        f"line {node.lineno}: forbidden import {module}"
                    )
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in FORBIDDEN_CALLS:
                    errors.append(
                        f"line {node.lineno}: forbidden authority/network call {name}"
                    )
        for marker in FORBIDDEN_TEXT:
            if marker in source:
                errors.append(
                    f"forbidden W82 resolution surface present: {marker}"
                )

        required = (
            'RESOLUTION_CONTRACT_VERSION = "W82_PROMOTION_FEE_ACCOUNTING_RESOLUTION_V3"',
            'STRATEGY_VERSION_BLOCKER = "EXECUTION_STRATEGY_VERSION_UNBOUND"',
            'SHADOW_FORWARD_BLOCKER = "SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED"',
            'MAX_PERCENT_FEE_BPS = Decimal("10000")',
            "FeeLiquidityRole,",
            "fee_schedule_attestation: FeeScheduleAttestation",
            "fee_schedule_attestation.validate_for(",
            "fee_schedule_attestation.required_fee_floor_bps",
            "fee_schedule_attestation.attestation_hash",
            "fee_schedule_attestation.source_checked_at",
            "documented_fee_floor_satisfied",
            '"RESEARCH_FEE_BELOW_DOCUMENTED_BROKER_FLOOR"',
            '"PRODUCT_POLICY_BELOW_DOCUMENTED_BROKER_FLOOR"',
            "product_economics: FeeProductEconomicsEvidence",
            "product_economics.fee_accounting_evidence_hash != fee_evidence.evidence_hash",
            "product_economics.w81_continuity_evidence_hash != w81_resolution.continuity_evidence_hash",
            "product_economics.research_cost_model_hash != fee_evidence.research_cost_model_hash",
            "product_economics.product_id != fee_evidence.product_id",
            "product_economics.asset_class != fee_evidence.asset_class",
            "product_economics.venue != fee_evidence.venue",
            "product_economics.symbol != fee_evidence.symbol",
            "product_economics.side is not fee_evidence.side",
            "product_economics.market_observed_at",
            "is not FeeChargeConvention.RECEIVED_ASSET_PERCENT",
            "Alpaca fee schedule requires received-asset fee convention",
            "product_economics.liquidity_role is not FeeLiquidityRole.WORST_CASE",
            "Alpaca fee schedule requires worst-case liquidity role",
            "percent fee may not exceed 100% at promotion boundary",
            "BUY product fee economics have impossible net direction",
            "SELL product fee economics have impossible net direction",
            "received-asset BUY fee currency binding mismatch",
            "product_economics.status is not FeeProductEconomicsStatus.PASS",
            "not product_economics.fee_schedule_conservative",
            "not product_economics.product_fee_economics_complete",
            '"FEE_SCHEDULE_NOT_CONSERVATIVE"',
            '"PRODUCT_FEE_ECONOMICS_INCOMPLETE"',
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
                errors.append(
                    f"required W82 resolution marker missing: {marker}"
                )

    boundary_marker = "python scripts/check_w82_promotion_fee_accounting_boundary.py"
    for workflow, label in (
        (W82_WORKFLOW, "W82 workflow"),
        (CORE_WORKFLOW, "Core Safety"),
    ):
        if workflow.is_file() and boundary_marker not in workflow.read_text(
            encoding="utf-8"
        ):
            errors.append(
                f"{label}: W82 promotion fee resolution boundary not wired"
            )

    if W82_WORKFLOW.is_file():
        workflow_source = W82_WORKFLOW.read_text(encoding="utf-8")
        for required_path in (HARDENING_TEST, SCHEDULE_TEST):
            if required_path not in workflow_source:
                errors.append(
                    f"W82 workflow: required fee-resolution evidence test not wired: {required_path}"
                )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "W82 PROMOTION FEE RESOLUTION BOUNDARY PASS — fee blocker requires exact W81 candidate + base W82 fee receipt + fresh versioned Alpaca crypto fee schedule attestation + canonical Alpaca venue + RECEIVED_ASSET_PERCENT charge semantics + WORST_CASE liquidity role; caller policy cannot undercut the documented 25 bps conservative floor; final resolution independently revalidates cost/product/asset/venue/symbol/side/market-time identity, rejects semantic drift, >100% percentage fees and impossible net directions; strategy-version and Shadow/Forward remain blocked; broker fee proof and realized profitability remain false; no broker/network/SQLite/OMS/Safety authority; PAPER candidate false; capital NONE; LIVE blocked"
    )
    return 0


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
