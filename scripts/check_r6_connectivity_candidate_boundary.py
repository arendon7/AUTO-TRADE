from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "src/autotrade/connectivity_canary_authority.py"
BUILDER = ROOT / "src/autotrade/brokers/alpaca_paper_connectivity_candidate.py"
CLI = ROOT / "scripts/r6_build_connectivity_candidate.py"
CONSOLE = ROOT / "scripts/mac_safe_console.py"
START = ROOT / "scripts/mac_start.sh"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_connectivity_candidate_boundary.py"
TESTS = (
    "tests/test_connectivity_canary_authority.py",
    "tests/test_r6_connectivity_candidate.py",
    "tests/test_r6_build_connectivity_candidate_cli.py",
)

_FORBIDDEN_IMPORT_PREFIXES = (
    "autotrade.research",
    "autotrade.health_bridge",
    "openai",
    "anthropic",
    "requests",
    "urllib",
    "websockets",
)
_FORBIDDEN_TEXT = (
    "AlpacaPaperCredentials",
    "AlpacaPaperSingleShotWriter",
    "PaperCanaryExecutionBridge",
    "stage_external_submission",
    "submit_once",
    "SQLitePaperSubmissionRegistry",
    "SQLitePaperCanaryPermitRegistry",
    "SQLitePaperOperatorDecisionRegistry",
)


def main() -> int:
    errors: list[str] = []
    for path in (AUTHORITY, BUILDER, CLI):
        if not path.is_file():
            errors.append(f"missing connectivity candidate surface: {path.relative_to(ROOT)}")
            continue
        errors.extend(_scan_imports(path))

    if AUTHORITY.is_file():
        source = AUTHORITY.read_text(encoding="utf-8")
        for anchor in (
            'CONNECTIVITY_CANARY_STRATEGY_ID = "r6-connectivity-canary"',
            'CONNECTIVITY_CANARY = "CONNECTIVITY_CANARY"',
            '"strategy_health_required": False',
            '"strategy_trading_authorized": False',
            '"external_post_authorized": False',
            '"live_trading": "BLOCKED"',
            'self.max_quantity != Decimal("1")',
            'self.max_notional <= Decimal("10")',
            "CONNECTIVITY_CANARY_AUTHORITY_ISSUED",
        ):
            if anchor not in source:
                errors.append(f"connectivity authority anchor missing: {anchor}")
        for forbidden in _FORBIDDEN_TEXT:
            if forbidden in source:
                errors.append(f"connectivity authority contains forbidden surface: {forbidden}")

    if BUILDER.is_file():
        source = BUILDER.read_text(encoding="utf-8")
        for anchor in (
            "PaperAssetEvidenceStore(self._workspace).read()",
            "PaperFlatAccountEvidenceStore(self._workspace).read()",
            "PaperMarketEvidenceStore(self._workspace).read()",
            'MAX_CONNECTIVITY_NOTIONAL = Decimal("10")',
            'MAX_ACCOUNT_FRACTION = Decimal("0.001")',
            'quantity = Decimal("1")',
            'scope": "CONNECTIVITY_SESSION_ONLY"',
            "CapitalSafetyKernel(",
            ").evaluate(",
            "validate_for_external_submission(",
            "SQLiteConnectivityCanaryAuthorityStore(runtime).issue(authority)",
            '"strategy_health_required": False',
            '"strategy_health_created": False',
            '"strategy_trading_authorized": False',
            '"external_post_authorized": False',
            '"capital_authority": "NONE"',
            '"profitability_claim": False',
            '"live_trading": "BLOCKED"',
            '"next_action": "CONNECTIVITY_PREPARATION_BRIDGE_REQUIRED"',
        ):
            if anchor not in source:
                errors.append(f"connectivity builder anchor missing: {anchor}")
        for forbidden in _FORBIDDEN_TEXT:
            if forbidden in source:
                errors.append(f"connectivity builder contains forbidden authority: {forbidden}")
        tree = ast.parse(source, filename=str(BUILDER))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"post", "send", "write", "submit_once", "stage_external_submission"}:
                    errors.append(
                        f"connectivity builder:{node.lineno}: forbidden network/execution call {node.func.attr}"
                    )
                if node.func.attr == "submit" and not _inside_no_broker_method(node, tree):
                    errors.append(
                        f"connectivity builder:{node.lineno}: direct broker submit call is forbidden"
                    )

    if CLI.is_file():
        source = CLI.read_text(encoding="utf-8")
        for anchor in (
            'os.environ.get(_WRITE_ENV) == "ENABLED"',
            "os.environ.get(_KEY_ENV) or os.environ.get(_SECRET_ENV)",
            "PaperConnectivityCandidateBuilder(workspace).build(",
            '"external_post_authorized": False',
            '"operator_authority_created": False',
            '"strategy_health_created": False',
            '"capital_authority": "NONE"',
        ):
            if anchor not in source:
                errors.append(f"connectivity CLI safety anchor missing: {anchor}")
        for forbidden in ("--key", "--secret", "--execute", "submit_once"):
            if forbidden in source:
                errors.append(f"connectivity CLI contains forbidden surface: {forbidden}")

    for path, anchors in (
        (CONSOLE, ("build-connectivity-candidate", "credential_free=True")),
        (START, ("build-connectivity-candidate", "NO external POST authority")),
    ):
        if not path.is_file():
            errors.append(f"missing Mac connectivity surface: {path.relative_to(ROOT)}")
        else:
            source = path.read_text(encoding="utf-8")
            for anchor in anchors:
                if anchor not in source:
                    errors.append(f"Mac connectivity anchor missing in {path.name}: {anchor}")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: connectivity candidate boundary is not wired into CI")
    if R6.is_file():
        source = R6.read_text(encoding="utf-8")
        for test in TESTS:
            if test not in source:
                errors.append(f"R6 Authority: connectivity candidate test not wired into CI: {test}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 connectivity candidate boundary: PASS "
        "(broker-evidence-bound; one-share <=$10/0.1%; real Safety + OMS VALIDATED; "
        "no Strategy Health, credentials, operator authority, submission state or POST authority)"
    )
    return 0


def _scan_imports(path: Path) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
        for module in modules:
            if any(module == prefix or module.startswith(prefix + ".") for prefix in _FORBIDDEN_IMPORT_PREFIXES):
                errors.append(f"{path.relative_to(ROOT)}:{node.lineno}: forbidden import {module}")
    return errors


def _inside_no_broker_method(_node: ast.AST, _tree: ast.AST) -> bool:
    # Calls named submit are forbidden everywhere. The dummy surface defines
    # submit but never calls it, so no AST parent reconstruction is necessary.
    return False


if __name__ == "__main__":
    raise SystemExit(main())
