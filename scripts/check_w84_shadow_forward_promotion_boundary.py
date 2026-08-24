from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/autotrade"
MEASUREMENT = SRC / "forward_shadow_measurement.py"
PROMOTION = SRC / "promotion_shadow_forward_binding.py"
SOURCE_VERIFICATION = SRC / "promotion_shadow_forward_source_verification.py"
TEST_MEASUREMENT = ROOT / "tests/test_w84_forward_shadow_measurement.py"
TEST_MEASUREMENT_COVERAGE = ROOT / "tests/test_w84_forward_shadow_measurement_coverage.py"
TEST_PROMOTION = ROOT / "tests/test_w84_shadow_forward_promotion_binding.py"
TEST_PROMOTION_VALIDATION = ROOT / "tests/test_w84_shadow_forward_promotion_validation.py"
TEST_SOURCE_VERIFICATION = ROOT / "tests/test_w84_shadow_forward_source_verification.py"
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
    # R5 registries remain the persistence authority. W84 production may read
    # their verified chains but may never create or append durable evidence.
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
    "build_safe_dsl_runtime_identity()",
    "BacktestEngine().run(",
    '"autotrade/research/backtest.py"',
    '"autotrade/research/costs.py"',
    '"autotrade/domain.py"',
    '"history_dataset_hash"',
    '"backtest_config_hash"',
    '"measurement_runtime_hash"',
    "history dataset must end exactly at measurement plan freeze",
    "measurement plan freeze must strictly predate forward activation",
    "post_freeze_dataset.bars[: post_index + 1]",
    '"previous_measurement_hash": previous_measurement_hash',
    "source_fingerprint=self.measurement_hash",
    "receipt.previous_measurement_hash != previous_hash",
    "shadow observation is not the exact deterministic W84 measurement",
    "forward measurements cannot be captured before dataset end",
    "current_w83_runtime.identity_hash != w83_resolution.loaded_runtime_code_hash",
    '"paper_candidate_authorized": False',
    '"external_execution_authorized": False',
    '"runtime_execution_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
)

PROMOTION_REQUIRED = (
    'SHADOW_FORWARD_PROMOTION_POLICY_VERSION = "W84_SHADOW_FORWARD_PROMOTION_POLICY_V2"',
    'SHADOW_FORWARD_PROMOTION_EVIDENCE_VERSION = "W84_SHADOW_FORWARD_PROMOTION_EVIDENCE_V2"',
    'PROMOTION_SHADOW_FORWARD_RESOLUTION_VERSION = "W84_PROMOTION_SHADOW_FORWARD_RESOLUTION_V2"',
    "self.max_capture_lag_seconds + self.max_assessment_delay_seconds",
    ">= self.timeframe_seconds",
    "minimum forward duration cannot exceed fixed qualification horizon",
    "build_forward_shadow_measurements(",
    "verify_shadow_measurement_binding(",
    "measurement_receipts_hash(",
    'source_code_hash=policy.measurement_runtime_hash',
    'strategy_weights={w83_resolution.selected_strategy_id: Decimal("1")}',
    '"FORWARD_WINDOW_INCOMPLETE"',
    '"FORWARD_WINDOW_OVERRUN"',
    '"per_observation_measurement_bound": True',
    '"prefix_only_measurement_bound": True',
    '"measurement_freshness_bound": True',
    '"full_observed_forward_tail_bound": True',
    '"fixed_forward_window_bound": True',
    '"resolved_promotion_blockers": (SHADOW_FORWARD_BLOCKER,)',
    "W84 may resolve only SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED",
    '"paper_candidate_authorized": False',
    '"external_execution_authorized": False',
    '"runtime_execution_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
)

SOURCE_VERIFICATION_REQUIRED = (
    'SOURCE_VERIFICATION_VERSION = "W84_SHADOW_FORWARD_SOURCE_VERIFICATION_V1"',
    "`PromotionShadowForwardResolution` is an intermediate identity/blocker receipt.",
    "shadow_registry.get_config()",
    "shadow_registry.list_records()",
    "shadow_registry.control_state()",
    "forward_registry.get_policy()",
    "forward_registry.list_records()",
    "forward_registry.control_state()",
    "verify_shadow_measurement_binding(",
    "measurement_receipts_hash(",
    "source-verified PASS requires exact preregistered Shadow horizon",
    "source-verified PASS requires exact preregistered Forward horizon",
    "cumulative_return, max_drawdown = _forward_metrics(forward_records)",
    "rehash-valid W84 evidence disagrees with durable source truth",
    "R5 source truth changed during final W84 verification",
    '"source_truth_verified": True',
    '"resolved_promotion_blockers": (SHADOW_FORWARD_BLOCKER,)',
    '"paper_candidate_authorized": False',
    '"external_execution_authorized": False',
    '"runtime_execution_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
)


def main() -> int:
    errors: list[str] = []
    required_files = (
        MEASUREMENT,
        PROMOTION,
        SOURCE_VERIFICATION,
        TEST_MEASUREMENT,
        TEST_MEASUREMENT_COVERAGE,
        TEST_PROMOTION,
        TEST_PROMOTION_VALIDATION,
        TEST_SOURCE_VERIFICATION,
        W84_WORKFLOW,
        CORE_WORKFLOW,
    )
    for path in required_files:
        if not path.is_file():
            errors.append(f"missing W84 contract file: {path.relative_to(ROOT)}")

    for path in (MEASUREMENT, PROMOTION, SOURCE_VERIFICATION):
        if path.is_file():
            errors.extend(_scan_authority(path))

    if MEASUREMENT.is_file():
        source = MEASUREMENT.read_text(encoding="utf-8")
        for marker in MEASUREMENT_REQUIRED:
            if marker not in source:
                errors.append(f"W84 measurement marker missing: {marker}")

    if PROMOTION.is_file():
        source = PROMOTION.read_text(encoding="utf-8")
        for marker in PROMOTION_REQUIRED:
            if marker not in source:
                errors.append(f"W84 promotion marker missing: {marker}")

    if SOURCE_VERIFICATION.is_file():
        source = SOURCE_VERIFICATION.read_text(encoding="utf-8")
        for marker in SOURCE_VERIFICATION_REQUIRED:
            if marker not in source:
                errors.append(f"W84 source-verification marker missing: {marker}")

    # The V2 resolver is intentionally an intermediate receipt. No production
    # layer outside W84 source verification may consume it as final authority.
    intermediate_name = "resolve_promotion_shadow_forward_binding"
    for path in SRC.glob("*.py"):
        if path in {PROMOTION, SOURCE_VERIFICATION}:
            continue
        if intermediate_name in path.read_text(encoding="utf-8"):
            errors.append(
                f"{path.name}: consumes intermediate W84 resolver without source verification"
            )

    boundary_marker = "python scripts/check_w84_shadow_forward_promotion_boundary.py"
    for workflow, label in (
        (W84_WORKFLOW, "W84 workflow"),
        (CORE_WORKFLOW, "Core Safety"),
    ):
        if workflow.is_file() and boundary_marker not in workflow.read_text(
            encoding="utf-8"
        ):
            errors.append(f"{label}: W84 boundary not wired")

    if W84_WORKFLOW.is_file():
        workflow_source = W84_WORKFLOW.read_text(encoding="utf-8")
        for path in (
            "tests/test_w84_forward_shadow_measurement.py",
            "tests/test_w84_forward_shadow_measurement_coverage.py",
            "tests/test_w84_shadow_forward_promotion_binding.py",
            "tests/test_w84_shadow_forward_promotion_validation.py",
            "tests/test_w84_shadow_forward_source_verification.py",
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
                errors.append(
                    f"W84 workflow: inherited proof not re-run: {inherited}"
                )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "W84 SHADOW/FORWARD PROMOTION BOUNDARY PASS — exact W83 candidate, "
        "StrategySpec and runtime remain bound; deterministic prefix-only W84 "
        "measurements bind every R5 Shadow observation; the V2 blocker receipt is "
        "intermediate only and final W84 certification re-reads durable R5 Shadow/" 
        "Forward truth plus measurement receipts, recomputes metrics/freshness and "
        "rejects rehash-valid lies; R5 mutation stays outside W84; only the Shadow/" 
        "Forward promotion blocker may be removed; no broker/network/OMS/Safety/" 
        "SQLite authority; PAPER candidate false; capital NONE; LIVE blocked"
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
