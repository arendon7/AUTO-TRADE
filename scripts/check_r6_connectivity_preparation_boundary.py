from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "src/autotrade/brokers/alpaca_paper_connectivity_gate.py"
BINDING = ROOT / "src/autotrade/connectivity_preparation_binding.py"
PREPARE = ROOT / "src/autotrade/brokers/alpaca_paper_connectivity_prepare.py"
CLI = ROOT / "scripts/r6_prepare_connectivity_candidate.py"
CONSOLE = ROOT / "scripts/mac_safe_console.py"
START = ROOT / "scripts/mac_start.sh"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_connectivity_preparation_boundary.py"
TESTS = ("tests/test_r6_connectivity_prepare.py", "tests/test_r6_prepare_connectivity_candidate_cli.py")
FORBIDDEN_IMPORTS = ("autotrade.research", "autotrade.health_bridge", "openai", "anthropic", "requests", "urllib", "websockets")
FORBIDDEN_TEXT = ("AlpacaPaperCredentials", "AlpacaPaperSingleShotWriter", "PaperCanaryExecutionBridge", "stage_external_submission", "submit_once", "record_operator_approval")


def main() -> int:
    errors: list[str] = []
    for path in (GATE, BINDING, PREPARE, CLI):
        if not path.is_file():
            errors.append(f"missing connectivity preparation surface: {path.relative_to(ROOT)}")
            continue
        errors.extend(_scan_imports(path))
    if GATE.is_file():
        source = GATE.read_text(encoding="utf-8")
        for anchor in ("health_allows_new_exposure is not False", "must not be represented as Strategy Health approval", "CONNECTIVITY_CANARY_STRATEGY_ID", 'order.intent.quantity != Decimal("1")', 'order.intent.side.value != "BUY"', 'order.intent.order_type.value != "LIMIT"'):
            if anchor not in source: errors.append(f"connectivity gate anchor missing: {anchor}")
        for forbidden in FORBIDDEN_TEXT:
            if forbidden in source: errors.append(f"connectivity gate contains forbidden execution surface: {forbidden}")
    if BINDING.is_file():
        source = BINDING.read_text(encoding="utf-8")
        for anchor in ('"purpose": "CONNECTIVITY_CANARY"', '"strategy_health_required": False', '"strategy_trading_authorized": False', '"operator_authority_created": False', '"external_post_authorized": False', '"live_trading": "BLOCKED"', "CONNECTIVITY_CANARY_PREPARED"):
            if anchor not in source: errors.append(f"connectivity preparation binding anchor missing: {anchor}")
    if PREPARE.is_file():
        source = PREPARE.read_text(encoding="utf-8")
        for anchor in ("ConnectivityCanaryGate(authority)", "health_allows_new_exposure=False", "SQLitePaperSubmissionRegistry", "SQLitePaperCanaryPermitRegistry", "SQLiteConnectivityPreparationBindingStore(runtime).record(binding)", "self._require_normal_operator_artifacts_absent()", 'CONNECTIVITY_PREP_ARTIFACT = "connectivity_preparation.json"', '"operator_context_created": False', '"strategy_health_required": False', '"operator_authority_created": False', '"external_post_authorized": False', '"next_action": "CONNECTIVITY_OPERATOR_BRIDGE_REQUIRED"'):
            if anchor not in source: errors.append(f"connectivity preparation anchor missing: {anchor}")
        for forbidden in FORBIDDEN_TEXT:
            if forbidden in source: errors.append(f"connectivity preparation contains forbidden authority: {forbidden}")
        tree = ast.parse(source, filename=str(PREPARE))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"post", "send", "write", "submit_once", "stage_external_submission"}:
                errors.append(f"connectivity preparation:{node.lineno}: forbidden network/execution call {node.func.attr}")
    if CLI.is_file():
        source = CLI.read_text(encoding="utf-8")
        for anchor in ('os.environ.get(_WRITE_ENV) == "ENABLED"', "os.environ.get(_KEY_ENV) or os.environ.get(_SECRET_ENV)", "PaperConnectivityPreparationBridge", '"external_post_authorized": False', '"operator_authority_created": False', '"capital_authority": "NONE"'):
            if anchor not in source: errors.append(f"connectivity preparation CLI anchor missing: {anchor}")
    for path, anchors in ((CONSOLE, ("prepare-connectivity-candidate", "credential_free=True")), (START, ("prepare-connectivity-candidate", "CONNECTIVITY_OPERATOR_BRIDGE_REQUIRED"))):
        if not path.is_file(): errors.append(f"missing Mac connectivity preparation surface: {path.relative_to(ROOT)}")
        else:
            source = path.read_text(encoding="utf-8")
            for anchor in anchors:
                if anchor not in source: errors.append(f"Mac connectivity preparation anchor missing in {path.name}: {anchor}")
    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: connectivity preparation boundary is not wired into CI")
    if R6.is_file():
        source = R6.read_text(encoding="utf-8")
        for test in TESTS:
            if test not in source: errors.append(f"R6 Authority: connectivity preparation test not wired into CI: {test}")
    if errors:
        for error in errors: print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("AUTO-TRADE R6 connectivity preparation boundary: PASS (health=false purpose gate; standard PREPARED+permit reuse; connectivity ledger binding; normal operator artifacts absent; no credentials/network/operator/POST authority)")
    return 0


def _scan_imports(path: Path) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import): modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom): modules.append(node.module or "")
        for module in modules:
            if any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORTS):
                errors.append(f"{path.relative_to(ROOT)}:{node.lineno}: forbidden import {module}")
    return errors


if __name__ == "__main__": raise SystemExit(main())
