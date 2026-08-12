from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts/r6_external_paper_preflight.py"
OPERATIONAL = ROOT / "src/autotrade/brokers/alpaca_paper_operational.py"
PREPARER = ROOT / "src/autotrade/brokers/alpaca_paper_operational_prepare.py"
SNAPSHOT = ROOT / "src/autotrade/brokers/alpaca_paper_preparation_snapshot.py"
EVIDENCE = ROOT / "src/autotrade/brokers/alpaca_paper_operational_evidence.py"
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
FORBIDDEN_EXECUTION_CALLS = {
    "submit_once",
    "stage_external_submission",
    "write",
    "send",
    "connect",
    "record_operator_approval",
}
FORBIDDEN_PREPARER_IMPORTS = (
    "alpaca_paper_writer",
    "alpaca_paper_execution_bridge",
    "alpaca_paper_reconciliation_gateway",
    "alpaca_paper_trade_updates_transport",
    "openai",
    "anthropic",
    "autotrade.research",
)
FORBIDDEN_EVIDENCE_IMPORTS = (
    "alpaca_paper_writer",
    "alpaca_paper_execution_bridge",
    "alpaca_paper_canary_permit",
    "alpaca_paper_canary_coordinator",
    "autotrade.oms",
    "autotrade.safety",
    "openai",
    "anthropic",
    "autotrade.research",
)
REQUIRED_PREFLIGHT = (
    'KEY_ENV = "APCA_API_KEY_ID"',
    'SECRET_ENV = "APCA_API_SECRET_KEY"',
    '"--allow-paper-account-read"',
    "AlpacaPaperGatewayConfig(enabled=True)",
    "gateway.attest_account(",
    "operational.write_account_attestation(attestation)",
    '"network_method": "GET"',
    '"network_path": "/v2/account"',
    '"order_write_authorized": False',
    '"external_order_submitted": False',
    '"live_trading": "BLOCKED"',
)
REQUIRED_OPERATIONAL = (
    "class PaperOperationalWorkspace:",
    "read_prepared_package(",
    "read_expected_bracket(",
    "read_bracket_attestation(",
    "PaperOperatorDecisionContext.from_prepared_package(package)",
    '"network_write_authorized": False',
    '"next_action": "OPERATOR_DECISION_REQUIRED"',
    '"credentials_persisted": False',
    "os.fsync(sync_fd)",
    "path.chmod(0o600)",
    "temp_path.write_bytes(raw)",
    '"exact PAPER account attestation must be persisted before canary package"',
    '"prepared package account attestation does not match workspace evidence"',
    '"capital_authority": "NONE"',
    '"profitability_claim": False',
    '"live_trading": "BLOCKED"',
)
REQUIRED_PREPARER = (
    "class PaperOperationalCanaryPreparer:",
    "coordinator: PaperCanaryCoordinator",
    "self._workspace.write_account_attestation(account_attestation)",
    "result = self._coordinator.prepare(",
    "self._workspace.write_prepared_canary(",
    "write_preparation_snapshot(",
    "read_preparation_snapshot(",
    "result.package,",
    "result.bracket,",
    "persisted = read_prepared_package(package_path)",
    "persisted_bracket = read_expected_bracket(self._workspace.expected_bracket_path)",
    '"operational preparation cannot authorize network write"',
    '"operational preparation must stop at operator decision"',
)
REQUIRED_SNAPSHOT = (
    'SNAPSHOT_NAME = "preparation_snapshot.json"',
    "preparation_snapshot_payload(",
    "write_preparation_snapshot(",
    "read_preparation_snapshot(",
    '"credentials_persisted": False',
    '"network_write_authorized": False',
    '"next_action": "OPERATOR_DECISION_REQUIRED"',
    '"live_trading": "BLOCKED"',
    'raise PaperOperationalIntegrityError("preparation snapshot hash mismatch")',
    "os.fsync(sync_fd)",
)
REQUIRED_EVIDENCE = (
    "class PaperOperationalEvidenceCollector:",
    "self._reconciler.reconcile(",
    "self._reconciler.recover_acknowledged_attestation(",
    "self._trade_updates_transport.connect_and_listen(credentials=credentials)",
    "session.receive(timeout_seconds=float(timeout_seconds))",
    "self._parser.parse(frame, scope=scope)",
    "ledger.append(event)",
    "self._qualifier.qualify(",
    "self._workspace.write_qualification_report_payload(report.to_dict())",
    "self._workspace.write_evidence_manifest(",
    '"broker reconciliation evidence requires UNKNOWN or ACKNOWLEDGED submission state"',
    '"trade_updates capture requires persisted reconciled bracket attestation"',
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
        errors.extend(_scan_no_execution_surface(source, PREFLIGHT, "preflight", FORBIDDEN_PREFLIGHT_IMPORTS))
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
        errors.extend(_scan_operational_workspace(source, OPERATIONAL))

    if not PREPARER.is_file():
        errors.append("R6 operational canary preparer missing")
    else:
        source = PREPARER.read_text(encoding="utf-8")
        for anchor in REQUIRED_PREPARER:
            if anchor not in source:
                errors.append(f"operational preparer anchor missing: {anchor}")
        errors.extend(_scan_no_execution_surface(source, PREPARER, "preparer", FORBIDDEN_PREPARER_IMPORTS))
        if source.count("self._coordinator.prepare(") != 1:
            errors.append("operational preparer must call coordinator.prepare exactly once")

    if not SNAPSHOT.is_file():
        errors.append("R6 restart-safe preparation snapshot module missing")
    else:
        source = SNAPSHOT.read_text(encoding="utf-8")
        for anchor in REQUIRED_SNAPSHOT:
            if anchor not in source:
                errors.append(f"preparation snapshot anchor missing: {anchor}")
        errors.extend(_scan_no_execution_surface(source, SNAPSHOT, "snapshot", FORBIDDEN_PREPARER_IMPORTS))
        for forbidden in ("/v2/orders", "api.alpaca.markets", "APCA-API-SECRET-KEY"):
            if forbidden in source:
                errors.append(f"preparation snapshot contains forbidden surface: {forbidden}")

    if not EVIDENCE.is_file():
        errors.append("R6 operational evidence collector missing")
    else:
        source = EVIDENCE.read_text(encoding="utf-8")
        for anchor in REQUIRED_EVIDENCE:
            if anchor not in source:
                errors.append(f"operational evidence anchor missing: {anchor}")
        errors.extend(_scan_evidence_collector(source, EVIDENCE))
        if source.count("self._reconciler.reconcile(") != 1:
            errors.append("evidence collector must contain exactly one UNKNOWN reconciliation call")
        if source.count("self._trade_updates_transport.connect_and_listen(") != 1:
            errors.append("evidence collector must contain exactly one trade_updates connect call")
        if source.count("session.receive(") != 1:
            errors.append("evidence collector must contain exactly one receive surface")
        if source.count("self._qualifier.qualify(") != 1:
            errors.append("evidence collector must contain exactly one offline qualification call")
        for forbidden in ("/v2/orders", "api.alpaca.markets", "paper-api.alpaca.markets/v2/orders"):
            if forbidden in source:
                errors.append(f"operational evidence contains direct endpoint surface: {forbidden}")

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
        "(sanitized workspace; GET-only preflight; restart-safe offline preparation; "
        "GET/receive-only evidence collector; no execution/write authority)"
    )
    return 0


def _scan_no_execution_surface(
    source: str,
    path: Path,
    label: str,
    forbidden_imports: tuple[str, ...],
) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]
    rel = _relative(path)
    for node in ast.walk(tree):
        modules = _import_modules(node)
        for module in modules:
            if any(fragment in module for fragment in forbidden_imports):
                errors.append(f"{rel}:{node.lineno}: forbidden {label} import {module}")
        if isinstance(node, ast.Call):
            call = _call_name(node.func)
            if call in FORBIDDEN_EXECUTION_CALLS:
                errors.append(f"{rel}:{node.lineno}: forbidden {label} call {call}")
    return errors


def _scan_preflight(source: str, path: Path) -> list[str]:
    return _scan_no_execution_surface(
        source, path, "preflight", FORBIDDEN_PREFLIGHT_IMPORTS
    )


def _scan_evidence_collector(source: str, path: Path) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]
    rel = _relative(path)
    forbidden_calls = {
        "submit",
        "submit_once",
        "stage_external_submission",
        "record_operator_approval",
        "consume",
        "mark_submit_attempt_unknown",
    }
    for node in ast.walk(tree):
        for module in _import_modules(node):
            if any(fragment in module for fragment in FORBIDDEN_EVIDENCE_IMPORTS):
                errors.append(f"{rel}:{node.lineno}: forbidden evidence import {module}")
        if isinstance(node, ast.Call):
            call = _call_name(node.func)
            if call in forbidden_calls:
                errors.append(f"{rel}:{node.lineno}: forbidden evidence call {call}")
    return errors


def _scan_operational_workspace(source: str, path: Path) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error: {exc}"]
    rel = _relative(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call = _call_name(node.func)
            if call in {"submit_once", "stage_external_submission", "record_operator_approval"}:
                errors.append(f"{rel}:{node.lineno}: operational workspace cannot call {call}")
            if call == "write":
                errors.append(f"{rel}:{node.lineno}: operational workspace cannot own transport-style write authority")
    return errors


def _import_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        base = node.module or ""
        modules = [base]
        modules.extend(
            f"{base}.{alias.name}" if base else alias.name
            for alias in node.names
        )
        return modules
    return []


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
