from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/brokers/alpaca_paper_operator_decision.py"
ISSUER = ROOT / "scripts/r6_issue_operator_decision.py"
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"

FORBIDDEN_IMPORT_PREFIXES = (
    "urllib",
    "http",
    "socket",
    "websockets",
    "requests",
    "openai",
    "anthropic",
    "autotrade.research",
)
FORBIDDEN_DECISION_IMPORT_FRAGMENTS = (
    "alpaca_paper_writer",
    "OrderManagementSystem",
    "OrderStore",
)
SELF_COMMAND = "python scripts/check_r6_operator_decision_boundary.py"
SELF_TEST = "tests/test_r6_operator_decision_boundary.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"


def main() -> int:
    errors: list[str] = []
    for path in (MODULE, ISSUER):
        if not path.is_file():
            errors.append(f"required operator-decision surface missing: {path.relative_to(ROOT)}")
            continue
        errors.extend(_scan_forbidden_authority(path))

    if ISSUER.is_file():
        source = ISSUER.read_text(encoding="utf-8")
        for anchor in (
            "sys.stdin.isatty()",
            "sys.stdout.isatty()",
            "operator_confirmation_challenge(context)",
            "entered = input(",
            "registry.record_operator_approval(",
            '"external_order_submitted": False',
            '"live_trading": "BLOCKED"',
        ):
            if anchor not in source:
                errors.append(f"interactive operator issuer anchor missing: {anchor}")

    allowed_caller = ISSUER.resolve()
    for root in (SRC, SCRIPTS):
        for path in sorted(root.rglob("*.py")):
            if path.resolve() == MODULE.resolve():
                continue
            for lineno, call in _named_calls(path, "record_operator_approval"):
                if path.resolve() != allowed_caller:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{lineno}: production operator authority may only be minted by the interactive issuer ({call})"
                    )

    if MODULE.is_file():
        source = MODULE.read_text(encoding="utf-8")
        for anchor in (
            'source != _OPERATOR_SOURCE',
            'action != _OPERATOR_ACTION',
            '_OPERATOR_SOURCE = "HUMAN_OPERATOR"',
            '_OPERATOR_ACTION = "APPROVE_SINGLE_PAPER_CANARY"',
            '_MAX_DECISION_TTL = timedelta(minutes=2)',
            'if self.environment != "PAPER"',
            "PreparedPaperCanaryPackage",
            "network_write_authorized is not False",
            'next_action != "OPERATOR_DECISION_REQUIRED"',
            'order_status != "VALIDATED"',
        ):
            if anchor not in source:
                errors.append(f"operator decision fail-closed anchor missing: {anchor}")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: operator decision checker is not wired into permanent CI")
    if not R6.is_file() or SELF_TEST not in R6.read_text(encoding="utf-8"):
        errors.append("R6 Authority: operator decision adversarial checker tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 human operator decision boundary: PASS "
        "(interactive issuer only; no network/OMS/AI authority; package-bound PAPER one-shot)"
    )
    return 0


def _scan_forbidden_authority(path: Path) -> list[str]:
    errors: list[str] = []
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    rel = _relative(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden_import(alias.name):
                    errors.append(f"{rel}:{node.lineno}: forbidden authority/network import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _forbidden_import(module):
                errors.append(f"{rel}:{node.lineno}: forbidden authority/network import: {module}")
        elif isinstance(node, ast.Call):
            call = _call_name(node.func)
            if call in {"urlopen", "Request", "send"}:
                errors.append(f"{rel}:{node.lineno}: network call forbidden in operator decision surface: {call}")
    if path.resolve() == MODULE.resolve():
        for fragment in FORBIDDEN_DECISION_IMPORT_FRAGMENTS:
            if fragment in source:
                errors.append(f"{rel}: operator decision registry must not depend on {fragment}")
    return errors


def _forbidden_import(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES)


def _named_calls(path: Path, name: str) -> list[tuple[int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError):
        return []
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node.func) == name:
            found.append((node.lineno, name))
    return found


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
