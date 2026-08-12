from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mac_safety_rehearsal.py"
CONSOLE = ROOT / "scripts/mac_safe_console.py"
START = ROOT / "scripts/mac_start.sh"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_mac_safety_rehearsal_boundary.py"
SELF_TEST = "tests/test_mac_safety_rehearsal_boundary.py"
FUNCTIONAL_TEST = "tests/test_mac_safety_rehearsal.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "urllib",
    "http",
    "socket",
    "ssl",
    "requests",
    "websocket",
    "websockets",
    "openai",
    "anthropic",
    "autotrade.oms",
    "autotrade.brokers",
    "autotrade.research",
    "autotrade.persistence",
)
FORBIDDEN_TEXT = (
    "AlpacaPaperCredentials",
    "AlpacaPaperSingleShotWriter",
    "PaperCanaryExecutionBridge",
    "OrderManagementSystem",
    "stage_external_submission",
    "submit_once",
    "record_operator_approval",
    "R6_EXTERNAL_PAPER_WRITE",
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "--max-order-notional",
    "--max-position-notional",
    "--max-strategy-gross-exposure",
    "--max-portfolio-gross-exposure",
    "--max-net-exposure",
    "--max-leverage",
    "--max-daily-loss",
    "--max-drawdown",
)
REQUIRED_SCRIPT = (
    'limits_version="MAC_SAFETY_REHEARSAL_V1"',
    'allowed_symbols=frozenset({"AAPL", "MSFT", "SPY"})',
    'allowed_order_types=frozenset({OrderType.LIMIT})',
    'max_order_notional=Decimal("100")',
    "InMemoryEventLedger()",
    "CapitalSafetyKernel(REHEARSAL_LIMITS, ledger)",
    "decision = safety.evaluate(",
    '"risk_decision_created_by": "CapitalSafetyKernel.evaluate"',
    '"broker_network_used": False',
    '"broker_write_performed": False',
    '"oms_staging_performed": False',
    '"operator_authority_created": False',
    '"external_execution_authorized": False',
    '"capital_authority": "NONE"',
    '"profitability_claim": False',
    '"strategy_promotion_claim": False',
    '"live_trading_status": "BLOCKED"',
)


def main() -> int:
    errors: list[str] = []
    for path, label in ((SCRIPT, "Safety rehearsal"), (CONSOLE, "Safe Console"), (START, "Safe Start")):
        if not path.is_file():
            errors.append(f"missing Mac {label}: {_relative(path)}")

    if SCRIPT.is_file():
        source = SCRIPT.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(SCRIPT))
        except SyntaxError as exc:
            errors.append(f"Safety rehearsal syntax error: {exc}")
            tree = ast.Module(body=[], type_ignores=[])
        for anchor in REQUIRED_SCRIPT:
            if anchor not in source:
                errors.append(f"Safety rehearsal non-authorizing anchor missing: {anchor}")
        for forbidden in FORBIDDEN_TEXT:
            if forbidden in source:
                errors.append(f"Safety rehearsal contains forbidden authority/limit surface: {forbidden}")
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            for module in modules:
                if any(
                    module == prefix or module.startswith(prefix + ".")
                    for prefix in FORBIDDEN_IMPORT_PREFIXES
                ):
                    errors.append(
                        f"Safety rehearsal imports forbidden authority/network module: {module}"
                    )
            if isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in {"Request", "urlopen", "connect", "send", "write"}:
                    errors.append(f"Safety rehearsal contains forbidden I/O call: {name}")
        if source.count("CapitalSafetyKernel(") != 1:
            errors.append("Safety rehearsal must instantiate exactly one CapitalSafetyKernel")
        if source.count("safety.evaluate(") != 1:
            errors.append("Safety rehearsal must create exactly one RiskDecision through safety.evaluate")
        if "RiskDecision(" in source:
            errors.append("Safety rehearsal may not manually construct RiskDecision")

    if CONSOLE.is_file():
        source = CONSOLE.read_text(encoding="utf-8")
        for anchor in (
            '"safety-rehearsal"',
            '"scripts/mac_safety_rehearsal.py"',
            'if args.command == "safety-rehearsal":',
        ):
            if anchor not in source:
                errors.append(f"Safe Console Safety rehearsal anchor missing: {anchor}")
        if "--max-order-notional" in source or "--max-leverage" in source:
            errors.append("Safe Console may not expose rehearsal hard-limit overrides")

    if START.is_file():
        source = START.read_text(encoding="utf-8")
        if "safety-rehearsal)" not in source or "mac_safe_console.py safety-rehearsal" not in source:
            errors.append("Mac Safe Start does not route Safety rehearsal through Safe Console")
        if "mac_safety_rehearsal.py" in source:
            errors.append("Mac Safe Start must not bypass Safe Console to run Safety rehearsal")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: Mac Safety rehearsal checker is not wired into CI")
    if R6.is_file():
        text = R6.read_text(encoding="utf-8")
        if SELF_TEST not in text:
            errors.append("R6 Authority: Safety rehearsal adversarial checker tests are not wired into CI")
        if FUNCTIONAL_TEST not in text:
            errors.append("R6 Authority: Safety rehearsal functional tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE Mac Capital Safety rehearsal boundary: PASS "
        "(fixed rehearsal limits; RiskDecision only from CapitalSafetyKernel.evaluate; no broker/OMS/writer/operator authority)"
    )
    return 0


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _relative(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


if __name__ == "__main__":
    raise SystemExit(main())
