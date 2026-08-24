from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/autotrade"
MEASUREMENT = SRC / "forward_shadow_measurement.py"
PROMOTION = SRC / "promotion_shadow_forward_binding.py"
SOURCE_VERIFICATION = SRC / "promotion_shadow_forward_source_verification.py"
FINAL_VERIFICATION = SRC / "promotion_shadow_forward_final_verification.py"
TEST_MEASUREMENT = ROOT / "tests/test_w84_forward_shadow_measurement.py"
TEST_MEASUREMENT_COVERAGE = ROOT / "tests/test_w84_forward_shadow_measurement_coverage.py"
TEST_PROMOTION = ROOT / "tests/test_w84_shadow_forward_promotion_binding.py"
TEST_PROMOTION_VALIDATION = ROOT / "tests/test_w84_shadow_forward_promotion_validation.py"
TEST_SOURCE_VERIFICATION = ROOT / "tests/test_w84_shadow_forward_source_verification.py"
TEST_FINAL_VERIFICATION = ROOT / "tests/test_w84_shadow_forward_final_verification.py"
W84_WORKFLOW = ROOT / ".github/workflows/w84-shadow-forward-promotion.yml"
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
    "register_config",
    "append_period",
    "register_policy",
    "append_shadow_record",
}
FORBIDDEN_TEXT = (
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "paper-api.alpaca.markets",
    "api.alpaca.markets",
    "OrderIntent(",
    'paper_candidate_authorized": True',
    'external_execution_authorized": True',
    'runtime_execution_authorized": True',
    'live_trading": "ENABLED"',
)

MEASUREMENT_REQUIRED = (
    'FORWARD_MEASUREMENT_PLAN_VERSION = "W84_FORWARD_MEASUREMENT_PLAN_V2"',
    'FORWARD_MEASUREMENT_RUNTIME_VERSION = "W84_FORWARD_MEASUREMENT_RUNTIME_V1"',
    'FORWARD_MEASUREMENT_RECEIPT_VERSION = "W84_FORWARD_MEASUREMENT_RECEIPT_V1"',
    "BacktestEngine().run(",
    "post_freeze_dataset.bars[: post_index + 1]",
    "source_fingerprint=self.measurement_hash",
    '"paper_candidate_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
)

PROMOTION_REQUIRED = (
    'SHADOW_FORWARD_PROMOTION_POLICY_VERSION = "W84_SHADOW_FORWARD_PROMOTION_POLICY_V2"',
    'SHADOW_FORWARD_PROMOTION_EVIDENCE_VERSION = "W84_SHADOW_FORWARD_PROMOTION_EVIDENCE_V2"',
    'PROMOTION_SHADOW_FORWARD_RESOLUTION_VERSION = "W84_PROMOTION_SHADOW_FORWARD_RESOLUTION_V2"',
    '"FORWARD_WINDOW_INCOMPLETE"',
    '"FORWARD_WINDOW_OVERRUN"',
    '"resolved_promotion_blockers": (SHADOW_FORWARD_BLOCKER,)',
    "W84 may resolve only SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED",
    '"paper_candidate_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
)

SOURCE_VERIFICATION_REQUIRED = (
    'SOURCE_VERIFICATION_VERSION = "W84_SHADOW_FORWARD_SOURCE_VERIFICATION_V1"',
    "`PromotionShadowForwardResolution` is an intermediate identity/blocker receipt.",
    "shadow_registry.get_config()",
    "shadow_registry.list_records()",
    "forward_registry.get_policy()",
    "forward_registry.list_records()",
    "verify_shadow_measurement_binding(",
    "measurement_receipts_hash(",
    "source-verified PASS requires exact preregistered Shadow horizon",
    "source-verified PASS requires exact preregistered Forward horizon",
    "cumulative_return, max_drawdown = _forward_metrics(forward_records)",
    "rehash-valid W84 evidence disagrees with durable source truth",
    "R5 source truth changed during final W84 verification",
    '"source_truth_verified": True',
    '"paper_candidate_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
)

FINAL_VERIFICATION_REQUIRED = (
    'FINAL_VERIFICATION_VERSION = "W84_SHADOW_FORWARD_FINAL_VERIFICATION_V1"',
    "There is deliberately no caller-supplied `verified_at`.",
    "observed_now = _now_utc()",
    "verify_promotion_shadow_forward_resolution_sources(",
    "decision_delay > policy.max_assessment_delay_seconds",
    "process-clock freshness budget",
    '"source_truth_verified": True',
    '"process_clock_freshness_verified": True',
    '"resolved_promotion_blockers": (SHADOW_FORWARD_BLOCKER,)',
    '"paper_candidate_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
)


def main() -> int:
    errors: list[str] = []
    required_files = (
        MEASUREMENT,
        PROMOTION,
        SOURCE_VERIFICATION,
        FINAL_VERIFICATION,
        TEST_MEASUREMENT,
        TEST_MEASUREMENT_COVERAGE,
        TEST_PROMOTION,
        TEST_PROMOTION_VALIDATION,
        TEST_SOURCE_VERIFICATION,
        TEST_FINAL_VERIFICATION,
        W84_WORKFLOW,
        CORE_WORKFLOW,
    )
    for path in required_files:
        if not path.is_file():
            errors.append(f"missing W84 contract file: {path.relative_to(ROOT)}")

    for path in (MEASUREMENT, PROMOTION, SOURCE_VERIFICATION, FINAL_VERIFICATION):
        if path.is_file():
            errors.extend(_scan_authority(path))

    for path, markers, label in (
        (MEASUREMENT, MEASUREMENT_REQUIRED, "measurement"),
        (PROMOTION, PROMOTION_REQUIRED, "promotion"),
        (SOURCE_VERIFICATION, SOURCE_VERIFICATION_REQUIRED, "source-verification"),
        (FINAL_VERIFICATION, FINAL_VERIFICATION_REQUIRED, "final-verification"),
    ):
        if path.is_file():
            source = path.read_text(encoding="utf-8")
            for marker in markers:
                if marker not in source:
                    errors.append(f"W84 {label} marker missing: {marker}")

    intermediate_resolver = "resolve_promotion_shadow_forward_binding"
    source_verifier = "verify_promotion_shadow_forward_resolution_sources"
    for path in SRC.glob("*.py"):
        if path not in {PROMOTION, SOURCE_VERIFICATION, FINAL_VERIFICATION}:
            source = path.read_text(encoding="utf-8")
            if intermediate_resolver in source:
                errors.append(
                    f"{path.name}: consumes intermediate W84 resolver without final verification"
                )
            if source_verifier in source:
                errors.append(
                    f"{path.name}: consumes intermediate W84 source verifier without process-clock finalization"
                )

    boundary_marker = "python scripts/check_w84_shadow_forward_promotion_boundary.py"
    for workflow, label in (
        (W84_WORKFLOW, "W84 workflow"),
        (CORE_WORKFLOW, "Core Safety"),
    ):
        if workflow.is_file() and boundary_marker not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: W84 boundary not wired")

    if W84_WORKFLOW.is_file():
        workflow_source = W84_WORKFLOW.read_text(encoding="utf-8")
        for path in (
            "tests/test_w84_forward_shadow_measurement.py",
            "tests/test_w84_forward_shadow_measurement_coverage.py",
            "tests/test_w84_shadow_forward_promotion_binding.py",
            "tests/test_w84_shadow_forward_promotion_validation.py",
            "tests/test_w84_shadow_forward_source_verification.py",
            "tests/test_w84_shadow_forward_final_verification.py",
        ):
            if path not in workflow_source:
                errors.append(f"W84 workflow: required test not wired: {path}")
        for inherited in (
            "check_w83_strategy_version_binding_boundary.py",
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
                errors.append(f"W84 workflow: inherited proof not re-run: {inherited}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "W84 SHADOW/FORWARD PROMOTION BOUNDARY PASS — exact W83 candidate and "
        "prefix-only deterministic measurements bind R5 Shadow/Forward truth; "
        "the V2 blocker receipt and source verifier are intermediate only; final "
        "W84 certification re-reads durable sources and uses an internal process "
        "clock to enforce the frozen decision-lag budget; R5 mutation remains "
        "outside W84; no broker/network/OMS/Safety/SQLite authority; PAPER "
        "candidate false; capital NONE; LIVE blocked"
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
                    errors.append(f"{path.name}:{node.lineno}: forbidden import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_module(module):
                errors.append(f"{path.name}:{node.lineno}: forbidden import {module}")
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS:
                errors.append(
                    f"{path.name}:{node.lineno}: forbidden authority/mutation/network call {name}"
                )
    for marker in FORBIDDEN_TEXT:
        if marker in source:
            errors.append(f"{path.name}: forbidden W84 surface present: {marker}")
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
