from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts/r6_external_paper_preflight.py"
OPERATIONAL = ROOT / "src/autotrade/brokers/alpaca_paper_operational.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_operational_lifecycle_boundary.py"
SELF_TEST = "tests/test_r6_operational_lifecycle_boundary.py"

FORBIDDEN_PREFLIGHT_IMPORTS = (
    "alpaca_paper_writer",
    "alpaca_paper_execution_bridge",
    "alpaca_paper_reconciliation_gateway",
    "alpaca_paper_trade_updates_transport",
    "openai",
    "anthropic",
    "autotrade.research",
)
FORBIDDEN_PREFLIGHT_CALLS = {
    "submit_once",
    "stage_external_submission",
    "write",
    "send",
    "connect",
    "record_operator_approval",
}
REQUIRED_PREFLIGHT = (
    'KEY_ENV = "APCA_API_KEY_ID"',
    'SECRET_ENV = "APCA_API_SECRET_KEY"',
    '"--allow-paper-account-read"',
    "AlpacaPaperGatewayConfig(enabled=True)",
    "gateway.attest_account(",
    "workspace.write_account_attestation(attestation)",
    '"network_method": "GET"',
    '"network_path": "/v2/account"',
    '"order_write_authorized": False',
    '"external_order_submitted": False',
    '"live_trading": "BLOCKED"',
)
REQUIRED_OPERATIONAL = (
    "class PaperOperationalWorkspace:",
    "read_prepared_package(",
    "PaperOperatorDecisionContext.from_prepared_package(package)",
    '"network_write_authorized": False',
    '"next_action": "OPERATOR_DECISION_REQUIRED"',
    '"credentials_persisted": False',
    "os.fsync(handle.fileno())",
    "path.chmod(0o600)",
)


def main() -> int:
    errors: list[str] = []
    if not PREFLIGHT.is_file():
        errors.append("R6 external PAPER preflight CLI missing")
    else:
        source = PREFLIGHT.read_text(encoding="utf-8")
        for anchor in REQUIRED_PREFLIGHT:
            if anchor not in source:
                errors.append(f"preflight safety anchor missing: {anchor}")
        errors.extend(_scan_preflight(source, PREFLIGHT))
        if source.count("gateway.attest_account(") != 1:
            errors.append("preflight must contain exactly one account-attestation call")
        for forbidden in ("/v2/orders", "api.alpaca.markets", "--secret", "--key-id"):
            if forbidden in source:
                errors.append(f"preflight contains forbidden surface: {forbidden}")

    if not OPERATIONAL.is_file():
        errors.append("R6 operational workspace module missing")
    else:
        source = OPERATIONAL.read_text(encoding="utf-8")
        for anchor in REQUIRED_OPERATIONAL:
            if anchor not in source:
                errors.append(f"operational workspace anchor missing: {anchor}")
        for forbidden in (
            "alpaca_paper_writer",
            "alpaca_paper_execution_bridge",
            "/v2/orders",
            "APCA-API-SECRET-KEY",
        ):
            if forbidden in source:
                errors.append(f"operational workspace contains forbidden surface: {forbidden}")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: operational lifecycle checker is not wired into CI")
    if not R6.is_file() or SELF_TEST not in R6.read_text(encoding="utf-8"):
        errors.append("R6 Authority: operational lifecycle adversarial tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 operational lifecycle boundary: PASS "
        "(sanitized durable workspace; GET-only preflight; no execution/write authority)"
    )
    return 0


def _scan_preflight(source: str, path: Path) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]
    rel = _relative(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            modules = [base]
            modules.extend(
                f"{base}.{alias.name}" if base else alias.name
                for alias in node.names
            )
        else:
            modules = []
        for module in modules:
            if any(fragment in module for fragment in FORBIDDEN_PREFLIGHT_IMPORTS):
                errors.append(f"{rel}:{node.lineno}: forbidden preflight import {module}")
        if isinstance(node, ast.Call):
            call = _call_name(node.func)
            if call in FORBIDDEN_PREFLIGHT_CALLS:
                errors.append(f"{rel}:{node.lineno}: forbidden preflight call {call}")
    return errors


def _relative(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
