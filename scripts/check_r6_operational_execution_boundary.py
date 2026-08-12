from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src/autotrade/brokers/alpaca_paper_operational_execute.py"
LAUNCHER = ROOT / "scripts/r6_execute_paper_canary.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_operational_execution_boundary.py"
SELF_TEST = "tests/test_r6_operational_execution_boundary.py"
FUNCTIONAL_TEST = "tests/test_r6_operational_execute.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "openai",
    "anthropic",
    "autotrade.research",
)
FORBIDDEN_RUNTIME_FRAGMENTS = (
    "PaperCanaryCoordinator",
    "record_operator_approval",
    "attest_account(",
    "attest_flatness(",
    "AlpacaPaperFlatAccountGateway",
    "ALPACA_LIVE_TRADING_HOST",
    "api.alpaca.markets",
)
REQUIRED_RUNTIME = (
    "class PaperOperationalExecutionRuntime:",
    "FLAT_ACCOUNT_MAX_AGE_SECONDS = 30",
    "_require_fresh_clean_flat_account_evidence(",
    "PaperFlatAccountEvidenceStore(workspace).read()",
    "flat.clean_for_first_canary",
    "flat.source_host != ALPACA_PAPER_TRADING_HOST",
    "age_seconds > FLAT_ACCOUNT_MAX_AGE_SECONDS",
    "submission_state.status is not PaperSubmissionStatus.PREPARED",
    "POST replay is forbidden, continue with reconciliation/evidence",
    "PaperOperationalCoreProvenanceReader(self._workspace).verify(",
    "verify_core_provenance_document(",
    "current_order.status is OrderStatus.SUBMITTING",
    "operator_state.status is not PaperOperatorDecisionStatus.CONSUMED",
    "operator_state.consumed_attempt_id != package.attempt_id",
    "_discover_portfolio_health_entity_id(",
    "PaperCanaryExecutionBridge(oms=oms)",
    "bridge.stage_after_operator_decision(",
    "PaperFinalWriteGuard(",
    "self._writer.submit_once(",
    "_NoBrokerExecutionSurface()",
    "PRAGMA query_only=ON",
)
REQUIRED_LAUNCHER = (
    'KEY_ENV = "APCA_API_KEY_ID"',
    'SECRET_ENV = "APCA_API_SECRET_KEY"',
    'WRITE_ENABLE_ENV = "R6_EXTERNAL_PAPER_WRITE"',
    'WRITE_ENABLE_VALUE = "ENABLED"',
    '"--execute-paper-canary"',
    "if not args.execute_paper_canary:",
    "os.environ.get(WRITE_ENABLE_ENV) != WRITE_ENABLE_VALUE",
    "sys.stdin.isatty()",
    "sys.stdout.isatty()",
    "entered = input(",
    "if entered != challenge:",
    "credentials = _credentials_from_environment()",
    "AlpacaPaperWriterConfig(enabled=True)",
    "PaperOperationalExecutionRuntime(",
    "runtime.execute_once(",
    '"capital_authority": "NONE"',
    '"profitability_claim": False',
    '"live_trading": "BLOCKED"',
)


def main() -> int:
    errors: list[str] = []
    for path, label in ((RUNTIME, "runtime"), (LAUNCHER, "launcher")):
        if not path.is_file():
            errors.append(f"R6 operational execution {label} missing: {_relative(path)}")
            continue
        errors.extend(_scan_forbidden_imports(path))

    if RUNTIME.is_file():
        source = RUNTIME.read_text(encoding="utf-8")
        for anchor in REQUIRED_RUNTIME:
            if anchor not in source:
                errors.append(f"operational execution runtime anchor missing: {anchor}")
        for fragment in FORBIDDEN_RUNTIME_FRAGMENTS:
            if fragment in source:
                errors.append(f"operational execution runtime contains forbidden authority: {fragment}")
        if source.count("self._writer.submit_once(") != 1:
            errors.append("operational execution runtime must contain exactly one writer submit surface")
        if source.count("bridge.stage_after_operator_decision(") != 1:
            errors.append("operational execution runtime must contain exactly one execution-stage surface")
        if source.count("_require_fresh_clean_flat_account_evidence(") != 2:
            errors.append(
                "operational execution runtime must contain one flat-account guard call and one helper definition"
            )
        flat_guard = source.index("_require_fresh_clean_flat_account_evidence(")
        first_writable_store = source.index("submission_registry = SQLitePaperSubmissionRegistry(")
        if flat_guard > first_writable_store:
            errors.append(
                "fresh flat-account guard must run before any writable submission/control store is created"
            )
        if source.index("submission_state.status is not PaperSubmissionStatus.PREPARED") > source.index(
            "bridge.stage_after_operator_decision("
        ):
            errors.append("submission PREPARED/no-replay gate must precede execution staging")
        if source.index("verify_core_provenance_document(") > source.index(
            "bridge.stage_after_operator_decision("
        ):
            errors.append("fresh core provenance verification must precede execution staging")
        if flat_guard > source.index("bridge.stage_after_operator_decision("):
            errors.append("fresh flat-account evidence must precede operator consumption/OMS staging")
        if source.index("bridge.stage_after_operator_decision(") > source.index(
            "self._writer.submit_once("
        ):
            errors.append("Execution Bridge staging must precede writer submit")

    if LAUNCHER.is_file():
        source = LAUNCHER.read_text(encoding="utf-8")
        for anchor in REQUIRED_LAUNCHER:
            if anchor not in source:
                errors.append(f"manual PAPER launcher anchor missing: {anchor}")
        for forbidden in (
            "--key-id",
            "--secret",
            "paper-api.alpaca.markets/v2/orders",
            "api.alpaca.markets",
            "record_operator_approval",
            "PaperCanaryCoordinator",
        ):
            if forbidden in source:
                errors.append(f"manual PAPER launcher contains forbidden surface: {forbidden}")
        ordering = (
            "if not args.execute_paper_canary:",
            "os.environ.get(WRITE_ENABLE_ENV) != WRITE_ENABLE_VALUE",
            "if not sys.stdin.isatty() or not sys.stdout.isatty():",
            "entered = input(",
            "if entered != challenge:",
            "credentials = _credentials_from_environment()",
            "AlpacaPaperWriterConfig(enabled=True)",
            "runtime.execute_once(",
        )
        positions = [source.index(anchor) for anchor in ordering]
        if positions != sorted(positions):
            errors.append("manual PAPER launcher authority gates are not in fail-closed order")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: operational execution checker is not wired into permanent CI")
    if R6.is_file():
        r6_source = R6.read_text(encoding="utf-8")
        if SELF_TEST not in r6_source:
            errors.append("R6 Authority: operational execution checker tests are not wired into CI")
        if FUNCTIONAL_TEST not in r6_source:
            errors.append("R6 Authority: operational execution functional tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 operational execution boundary: PASS "
        "(fresh clean flat-account evidence before writable state; triple explicit manual gate; "
        "same-workspace durable control plane; single writer surface; UNKNOWN is never reposted; "
        "LIVE/AI/research denied)"
    )
    return 0


def _scan_forbidden_imports(path: Path) -> list[str]:
    errors: list[str] = []
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{_relative(path)}: syntax error: {exc}"]
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
                    f"{_relative(path)}:{node.lineno}: forbidden execution import {module}"
                )
    return errors


def _relative(path: Path) -> Path:
    try:
        return path.relative_to(ROOT)
    except ValueError:
        return path


if __name__ == "__main__":
    raise SystemExit(main())
