from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/brokers/alpaca_paper_flat_account.py"
EVIDENCE = ROOT / "src/autotrade/brokers/alpaca_paper_flat_account_evidence.py"
CLI = ROOT / "scripts/r6_external_paper_flat_account_preflight.py"
READINESS = ROOT / "src/autotrade/brokers/alpaca_paper_market_readiness.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_flat_account_boundary.py"
FUNCTIONAL_TEST = "tests/test_r6_flat_account_preflight.py"

FORBIDDEN_IMPORT_FRAGMENTS = (
    "alpaca_paper_writer",
    "alpaca_paper_execution_bridge",
    "autotrade.research",
    "openai",
    "anthropic",
)
FORBIDDEN_CALLS = {
    "submit_once",
    "stage_external_submission",
    "record_operator_approval",
    "consume",
    "cancel",
    "cancel_all",
    "close_position",
    "close_all_positions",
    "delete",
    "patch",
}


def main() -> int:
    errors: list[str] = []
    for path in (MODULE, EVIDENCE, CLI, READINESS):
        if not path.is_file():
            errors.append(f"required flat-account artifact missing: {path.relative_to(ROOT)}")

    if MODULE.is_file():
        source = MODULE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODULE))
        for anchor in (
            'POSITIONS_PATH = "/v2/positions"',
            'ORDERS_PATH = "/v2/orders"',
            'ORDERS_QUERY = "status=open&limit=500&direction=asc&nested=true"',
            'method="GET"',
            'position_count=len(position_payload)',
            'open_order_count=len(order_payload)',
            'return self.position_count == 0 and self.open_order_count == 0',
            'raise PaperFlatAccountDisabled(',
            'credentials.credential_reference != expected_credential_reference',
        ):
            if anchor not in source:
                errors.append(f"flat-account module safety anchor missing: {anchor}")
        if source.count("self._read(") != 2:
            errors.append("flat-account gateway must perform exactly two audited reads")
        errors.extend(_scan_ast(tree, MODULE))

    if EVIDENCE.is_file():
        source = EVIDENCE.read_text(encoding="utf-8")
        for anchor in (
            'ARTIFACT_NAME = "flat_account_attestation.json"',
            '"credentials_persisted": False',
            '"broker_mutation_performed": False',
            '"execution_authorized": False',
            '"capital_authority": "NONE"',
            '"production_status": "PAPER_ONLY_LIVE_BLOCKED"',
        ):
            if anchor not in source:
                errors.append(f"flat-account evidence safety anchor missing: {anchor}")
        errors.extend(_scan_ast(ast.parse(source, filename=str(EVIDENCE)), EVIDENCE))

    if CLI.is_file():
        source = CLI.read_text(encoding="utf-8")
        for anchor in (
            "--allow-paper-flat-account-read",
            'os.environ.get(WRITE_ENV) == WRITE_ENABLED',
            "AlpacaPaperFlatAccountGateway",
            "PaperFlatAccountEvidenceStore",
            '"broker_mutation_performed": False',
            '"execution_authorized": False',
            '"STOP_AND_REVIEW_EXISTING_PAPER_EXPOSURE_MANUALLY"',
        ):
            if anchor not in source:
                errors.append(f"flat-account CLI safety anchor missing: {anchor}")
        if "--key" in source or "--secret" in source:
            errors.append("flat-account CLI may not accept PAPER credentials as arguments")
        errors.extend(_scan_ast(ast.parse(source, filename=str(CLI)), CLI))

    if READINESS.is_file():
        source = READINESS.read_text(encoding="utf-8")
        for anchor in (
            'FLAT_ACCOUNT_PREFLIGHT_REQUIRED = "FLAT_ACCOUNT_PREFLIGHT_REQUIRED"',
            'BLOCKED_EXISTING_PAPER_EXPOSURE = "BLOCKED_EXISTING_PAPER_EXPOSURE"',
            '"STOP_AND_REVIEW_EXISTING_PAPER_EXPOSURE_MANUALLY"',
            "flat.clean_for_first_canary",
            "flat.account_attestation_fingerprint != account.get",
        ):
            if anchor not in source:
                errors.append(f"readiness flat-account anchor missing: {anchor}")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: flat-account checker is not wired into CI")
    if R6.is_file() and FUNCTIONAL_TEST not in R6.read_text(encoding="utf-8"):
        errors.append("R6 Authority: flat-account functional tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 flat-account boundary: PASS "
        "(exact two GETs; zero broker mutation; empty positions+orders required for first canary)"
    )
    return 0


def _scan_ast(tree: ast.AST, path: Path) -> list[str]:
    errors: list[str] = []
    rel = path.relative_to(ROOT)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []
        for name in names:
            if any(fragment in name for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                errors.append(f"{rel}:{getattr(node, 'lineno', '?')}: forbidden authority import: {name}")
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS:
                errors.append(f"{rel}:{node.lineno}: forbidden mutation/authority call: {name}")
            if name == "Request":
                errors.append(f"{rel}:{node.lineno}: flat-account modules may not construct raw HTTP Request objects")
    return errors


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
