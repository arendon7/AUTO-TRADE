from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
POSTCHECK = ROOT / "src/autotrade/paper_runtime_source_postcheck.py"
SEAL = ROOT / "src/autotrade/paper_runtime_readiness_seal.py"
TEST = "tests/test_w86_paper_runtime_readiness_seal.py"
DEDICATED = ROOT / ".github/workflows/w86-paper-runtime-readiness-seal.yml"
CORE = ROOT / ".github/workflows/core-tests.yml"
SELF_COMMAND = "python scripts/check_w86_paper_runtime_readiness_seal_boundary.py"


FORBIDDEN_SEAL_IMPORT_PREFIXES = (
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
    "autotrade.brokers",
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
    for path, label in ((POSTCHECK, "source postcheck"), (SEAL, "readiness seal")):
        if not path.is_file():
            errors.append(f"missing W86 {label} module")

    post_source = POSTCHECK.read_text(encoding="utf-8") if POSTCHECK.is_file() else ""
    seal_source = SEAL.read_text(encoding="utf-8") if SEAL.is_file() else ""

    for marker in (
        'PAPER_RUNTIME_SOURCE_POSTCHECK_VERSION = "W86_PAPER_RUNTIME_SOURCE_POSTCHECK_V1"',
        "class PaperRuntimeSourcePostcheckProof:",
        "class PaperRuntimeSourcePostcheckReader:",
        "def verify_after_collection(",
        'f"file:{self._core_path}?mode=ro"',
        "PRAGMA query_only=ON",
        "source_v1._validate_full_admission_chain",
        "source_v1._read_and_validate_lifecycle",
        "current_head == source_snapshot.lifecycle_head_hash",
        "current_count == source_snapshot.lifecycle_events_count",
        '"post_collection_source_verified": verified',
        '"paper_execution_authorized": False',
        '"external_execution_authorized": False',
        '"runtime_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    ):
        if marker not in post_source:
            errors.append(f"source postcheck missing boundary marker: {marker}")

    for forbidden in (
        "CREATE TABLE",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        'method="POST"',
        'method="PUT"',
        'method="PATCH"',
        'method="DELETE"',
        "OrderIntent(",
        "CapitalSafetyKernel(",
        "SQLiteRuntime(",
        "submit_order",
        "place_order",
        "cancel_order",
        "replace_order",
        "reserve_capital",
    ):
        if forbidden in post_source:
            errors.append(f"source postcheck contains forbidden authority/write surface: {forbidden}")

    for marker in (
        'PAPER_RUNTIME_READINESS_SEAL_VERSION = "W86_PAPER_RUNTIME_READINESS_SEAL_V1"',
        "READINESS_SEAL_TTL_SECONDS = 1",
        "class PaperRuntimeReadinessSealReceipt:",
        "class PaperRuntimeReadinessSealedResult:",
        "def seal_paper_runtime_readiness_after_collection(",
        "PaperRuntimeSourcePostcheckReader(core_path).verify_after_collection(",
        "post.__post_init__()",
        "def _expected_blockers(",
        "if upstream_runtime_ready is not True:",
        "if observed_at > upstream_funding_valid_until:",
        "if source_unchanged is not True:",
        "if source_current_state is not PaperCandidateEligibilityState.ACTIVE:",
        "if observed_at > source_admission_valid_until:",
        "seal blockers are not the exact fail-closed projection",
        "post-collection source verification flag is not exact projection",
        "upstream_runtime_ready",
        "upstream_funding_valid_until",
        "source_current_state",
        "source_admission_valid_until",
        "pipeline_result.account_attestation.__post_init__()",
        "pipeline_result.broker_truth.__post_init__()",
        "pipeline_result.asset_truth.__post_init__()",
        "pipeline_result.market_truth.__post_init__()",
        "pipeline_result.safety_health_truth.__post_init__()",
        "pipeline_result.final_readiness.__post_init__()",
        "pipeline_result.funding_capacity.__post_init__()",
        "PaperRuntimeReadinessSealBlocker.W85_SOURCE_CHANGED",
        "PaperRuntimeReadinessSealBlocker.UPSTREAM_RUNTIME_EXPIRED",
        '"separate_execution_approval_required": True',
        '"broker_write_performed": False',
        '"capital_reserved": False',
        '"paper_execution_authorized": False',
        '"external_execution_authorized": False',
        '"runtime_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    ):
        if marker not in seal_source:
            errors.append(f"readiness seal missing boundary marker: {marker}")

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
        "submit_order",
        "place_order",
        "cancel_order",
        "replace_order",
        "reserve_capital",
    ):
        if forbidden in seal_source:
            errors.append(f"readiness seal contains forbidden authority/I/O surface: {forbidden}")

    _check_ast(post_source, POSTCHECK, errors, allow_sqlite=True)
    _check_ast(seal_source, SEAL, errors, allow_sqlite=False)

    for workflow, label in ((DEDICATED, "Dedicated W86 Seal"), (CORE, "Core Safety")):
        if not workflow.is_file():
            errors.append(f"{label}: workflow missing")
            continue
        workflow_source = workflow.read_text(encoding="utf-8")
        if SELF_COMMAND not in workflow_source:
            errors.append(f"{label}: W86 seal boundary not wired into CI")
        if TEST not in workflow_source:
            errors.append(f"{label}: W86 seal tests not wired into CI")

    if DEDICATED.is_file():
        workflow_source = DEDICATED.read_text(encoding="utf-8")
        for required in (
            "python scripts/check_w86_paper_runtime_read_only_pipeline_boundary.py",
            "python scripts/check_w86_paper_runtime_funding_capacity_boundary.py",
            "python scripts/check_w86_paper_runtime_final_readiness_boundary.py",
            "python scripts/check_w86_paper_runtime_readiness_snapshot_boundary.py",
            "python scripts/check_w85_paper_candidate_admission_boundary.py",
            "python scripts/check_r6_authority.py",
            "python scripts/check_r6_live_deny_boundary.py",
        ):
            if required not in workflow_source:
                errors.append(
                    f"Dedicated W86 Seal does not re-prove required boundary: {required}"
                )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "AUTO-TRADE W86 post-collection readiness seal boundary: PASS "
        "(second exact W85 lifecycle read after network collection; exact self-verifying "
        "blocker projection; SUSPEND/REVOKE/REINSTATE/expiry fail closed; immutable "
        "admission+policy chain revalidated; one-second finite seal; no OrderIntent, OMS, "
        "capital reservation, broker write, execution authority or LIVE authority)"
    )
    return 0


def _check_ast(source: str, path: Path, errors: list[str], *, allow_sqlite: bool) -> None:
    if not source:
        return
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        errors.append(f"{path.name}: syntax error: {exc}")
        return
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
        for module in modules:
            if not allow_sqlite and module == "sqlite3":
                errors.append(f"{path.name}: readiness seal may not import raw SQLite")
            if path == SEAL and any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in FORBIDDEN_SEAL_IMPORT_PREFIXES
            ):
                errors.append(
                    f"{path.name}: imports forbidden writer/raw-I/O surface at line {node.lineno}: {module}"
                )
        if isinstance(node, ast.Call):
            name = _call_name(node.func).lower()
            if name in FORBIDDEN_CALL_NAMES:
                errors.append(
                    f"{path.name}: forbidden mutating call at line {node.lineno}: {name}"
                )


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
