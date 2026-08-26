from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/paper_runtime_read_only_pipeline.py"
TEST = "tests/test_w86_paper_runtime_read_only_pipeline.py"
HARDENING_TEST = "tests/test_w86_paper_runtime_read_only_pipeline_hardening.py"
DEDICATED = ROOT / ".github/workflows/w86-paper-runtime-read-only-pipeline.yml"
CORE = ROOT / ".github/workflows/core-tests.yml"
SELF_COMMAND = "python scripts/check_w86_paper_runtime_read_only_pipeline_boundary.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "requests",
    "httpx",
    "socket",
    "urllib",
    "sqlite3",
    "websocket",
    "websockets",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.health_bridge",
    "autotrade.execution",
    "autotrade.paper_close",
    "autotrade.research",
)
FORBIDDEN_CALL_NAMES = {
    "post",
    "put",
    "patch",
    "delete",
    "submit",
    "submit_once",
    "submit_order",
    "place_order",
    "cancel_order",
    "replace_order",
    "reserve",
    "reserve_capital",
    "stage_external_submission",
    "stage_external_handoff",
    "create_order_intent",
}


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        print("ERROR: missing W86 read-only runtime pipeline", file=sys.stderr)
        return 1

    source = TARGET.read_text(encoding="utf-8")
    required = (
        'PAPER_RUNTIME_READ_ONLY_PIPELINE_VERSION = "W86_PAPER_RUNTIME_READ_ONLY_PIPELINE_V1"',
        "class PaperRuntimeReadOnlyPipelineReceipt:",
        "class PaperRuntimeReadOnlyPipelineResult:",
        "def collect_paper_runtime_readiness(",
        "started_at = _clock(None)",
        "_preflight_source_candidate(",
        "observed_at=started_at",
        "source_snapshot.current_state is not PaperCandidateEligibilityState.ACTIVE",
        "source_snapshot.reproved_at",
        "source_snapshot.admission_valid_until",
        '"W86 source admission expired before network read"',
        "AlpacaPaperAccountGateway(",
        ").attest_account(",
        "attest_active_crypto_account(",
        "AlpacaPaperFlatAccountGateway(",
        ").attest_flatness(",
        "bind_paper_runtime_broker_truth(",
        "read_and_bind_paper_runtime_asset_truth(",
        "read_and_bind_paper_runtime_market_truth(",
        "PaperRuntimeSafetyHealthTruthReader(core_path).verify_current(",
        "finalize_paper_runtime_readiness(",
        "bind_paper_runtime_funding_capacity(",
        "account_attestation=account",
        '"paper_runtime_ready": funding_capacity.paper_runtime_ready',
        '"internal_process_clock": True',
        '"read_only_collection": True',
        '"network_reads_performed": True',
        '"network_write_performed": False',
        '"separate_execution_approval_required": True',
        '"paper_execution_authorized": False',
        '"external_execution_authorized": False',
        '"runtime_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        "pipeline readiness must be the funding-capacity readiness",
    )
    for marker in required:
        if marker not in source:
            errors.append(f"missing W86 pipeline boundary marker: {marker}")

    for forbidden in (
        "OrderIntent(",
        "CapitalSafetyKernel(",
        "SQLiteRuntime(",
        "UrllibAlpacaPaperReadTransport(",
        "UrllibAlpacaPaperMarketDataTransport(",
        "CREATE TABLE",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        'method="POST"',
        'method="PUT"',
        'method="PATCH"',
        'method="DELETE"',
        "api.alpaca.markets",
        "submit_order",
        "place_order",
        "cancel_order",
        "replace_order",
        "reserve_capital",
    ):
        if forbidden in source:
            errors.append(f"W86 pipeline contains forbidden authority/I/O surface: {forbidden}")

    try:
        tree = ast.parse(source, filename=str(TARGET))
    except SyntaxError as exc:
        errors.append(f"W86 pipeline syntax error: {exc}")
        tree = None

    if tree is not None:
        entry = next(
            (
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "collect_paper_runtime_readiness"
            ),
            None,
        )
        if entry is None:
            errors.append("W86 pipeline entrypoint missing")
        else:
            argument_names = {
                arg.arg
                for arg in (
                    list(entry.args.posonlyargs)
                    + list(entry.args.args)
                    + list(entry.args.kwonlyargs)
                )
            }
            forbidden_clock_args = {"now", "observed_at", "received_at", "attested_at"}
            leaked = sorted(argument_names & forbidden_clock_args)
            if leaked:
                errors.append(
                    "W86 pipeline public entrypoint accepts caller clock fields: "
                    + ",".join(leaked)
                )

            calls = list(_calls(entry))
            first_clock = _first_call_line(calls, "_clock")
            preflight = _first_call_line(calls, "_preflight_source_candidate")
            account_gateway = _first_call_line(calls, "AlpacaPaperAccountGateway")
            if None in (first_clock, preflight, account_gateway):
                errors.append(
                    "W86 pipeline must contain internal clock -> preflight -> account gateway sequence"
                )
            elif not (first_clock < preflight < account_gateway):
                errors.append(
                    "W86 pipeline must validate internal time/source before constructing first network gateway"
                )

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
                        f"W86 pipeline imports forbidden writer/raw-I/O surface at line {node.lineno}: {module}"
                    )
            if isinstance(node, ast.Call):
                name = _call_name(node.func).lower()
                if name in FORBIDDEN_CALL_NAMES:
                    errors.append(
                        f"W86 pipeline contains forbidden mutating call at line {node.lineno}: {name}"
                    )

    for workflow, label in ((DEDICATED, "Dedicated W86 Pipeline"), (CORE, "Core Safety")):
        if not workflow.is_file():
            errors.append(f"{label}: workflow missing")
            continue
        workflow_source = workflow.read_text(encoding="utf-8")
        if SELF_COMMAND not in workflow_source:
            errors.append(f"{label}: W86 pipeline boundary not wired into CI")
        if TEST not in workflow_source:
            errors.append(f"{label}: W86 pipeline tests not wired into CI")

    if DEDICATED.is_file():
        dedicated_source = DEDICATED.read_text(encoding="utf-8")
        if HARDENING_TEST not in dedicated_source:
            errors.append("Dedicated W86 Pipeline: pre-network hardening tests missing")
        for reproved in (
            "python scripts/check_w86_paper_runtime_funding_capacity_boundary.py",
            "python scripts/check_w86_paper_runtime_final_readiness_boundary.py",
            "python scripts/check_w86_paper_runtime_broker_truth_boundary.py",
            "python scripts/check_w86_paper_runtime_asset_truth_boundary.py",
            "python scripts/check_w86_paper_runtime_market_truth_boundary.py",
            "python scripts/check_w86_paper_runtime_safety_health_truth_boundary.py",
            "python scripts/check_w85_paper_candidate_admission_boundary.py",
            "python scripts/check_r6_authority.py",
            "python scripts/check_r6_live_deny_boundary.py",
        ):
            if reproved not in dedicated_source:
                errors.append(
                    f"Dedicated W86 Pipeline does not re-prove required boundary: {reproved}"
                )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "AUTO-TRADE W86 read-only runtime pipeline boundary: PASS "
        "(internal monotonic process clock; ACTIVE/non-expired W85 preflight before first GET; "
        "exact retained account receipt through funding; GET/read-only broker+asset+market; "
        "read-only Safety/Health; final readiness plus funding capacity; no OrderIntent, OMS, "
        "capital reservation, broker write, execution authority or LIVE authority)"
    )
    return 0


def _calls(node: ast.AST):
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            yield child


def _first_call_line(calls: list[ast.Call], name: str) -> int | None:
    lines = [node.lineno for node in calls if _call_name(node.func) == name]
    return min(lines) if lines else None


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
