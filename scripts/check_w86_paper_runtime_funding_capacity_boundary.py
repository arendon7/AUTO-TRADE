from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/paper_runtime_funding_capacity.py"
TEST = "tests/test_w86_paper_runtime_funding_capacity.py"
DEDICATED = ROOT / ".github/workflows/w86-paper-runtime-funding-capacity.yml"
SELF_COMMAND = "python scripts/check_w86_paper_runtime_funding_capacity_boundary.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "requests",
    "httpx",
    "socket",
    "urllib",
    "websocket",
    "websockets",
    "sqlite3",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.health_bridge",
    "autotrade.execution",
    "autotrade.research",
)
FORBIDDEN_CALLS = {
    "post",
    "put",
    "patch",
    "delete",
    "submit",
    "submit_once",
    "place_order",
    "cancel_order",
    "replace_order",
    "reserve",
    "reserve_capital",
    "stage_external_submission",
    "stage_external_handoff",
    "evaluate_order",
    "read",
    "attest_account",
}


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        print("ERROR: missing W86 PAPER funding-capacity module", file=sys.stderr)
        return 1

    source = TARGET.read_text(encoding="utf-8")
    required = (
        'PAPER_RUNTIME_FUNDING_CAPACITY_VERSION = "W86_PAPER_RUNTIME_FUNDING_CAPACITY_V1"',
        "class PaperRuntimeFundingCapacityPolicy:",
        "max_account_age_seconds: int = 5",
        "ready_ttl_seconds: int = 2",
        "class PaperRuntimeFundingCapacityProof:",
        "class PaperRuntimeFundingCapacityStatus(StrEnum):",
        'READY = "READY"',
        'BLOCKED = "BLOCKED"',
        "class PaperRuntimeFundingCapacityBlocker(StrEnum):",
        'INSUFFICIENT_BUYING_POWER = "INSUFFICIENT_BUYING_POWER"',
        'ACCOUNT_ATTESTATION_STALE = "ACCOUNT_ATTESTATION_STALE"',
        'FINAL_RUNTIME_RECEIPT_EXPIRED = "FINAL_RUNTIME_RECEIPT_EXPIRED"',
        "def bind_paper_runtime_funding_capacity(",
        "final_module._payload(final_readiness, include_hash=False)",
        "broker_module._proof_payload(broker_truth, include_hash=False)",
        "final_readiness.broker_truth_hash != broker_truth.proof_hash",
        "account.fingerprint != broker.account_attestation_fingerprint",
        "account.account_id != broker.account_id",
        "account.credential_reference != broker.credential_reference",
        "account.request_id != broker.account_request_id",
        "account.status != \"ACTIVE\"",
        "account.currency != \"USD\"",
        "account.source_host != ALPACA_PAPER_TRADING_HOST",
        "account.source_path != ALPACA_PAPER_ACCOUNT_PATH",
        "now = _utc(_now_utc())",
        "buying_power_sufficient = buying_power >= minimum_notional",
        "PaperRuntimeFundingCapacityBlocker.INSUFFICIENT_BUYING_POWER",
        '"separate_execution_approval_required": True',
        '"capital_reserved": False',
        '"broker_write_performed": False',
        '"paper_execution_authorized": False',
        '"external_execution_authorized": False',
        '"runtime_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        "valid_until = min(",
    )
    for marker in required:
        if marker not in source:
            errors.append(f"missing W86 funding-capacity boundary marker: {marker}")

    for forbidden in (
        "OrderIntent(",
        "AlpacaPaperAccountGateway(",
        "AlpacaPaperCredentials",
        "CapitalSafetyKernel",
        "SQLiteRuntime(",
        "sqlite3.connect",
        "CREATE TABLE",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        'method="POST"',
        'method="PUT"',
        'method="PATCH"',
        'method="DELETE"',
        "paper-api.alpaca.markets",
        "api.alpaca.markets",
        "submit_order",
        "place_order",
        "cancel_order",
        "replace_order",
        "reserve_capital",
    ):
        if forbidden in source:
            errors.append(f"W86 funding-capacity contains forbidden surface: {forbidden}")

    try:
        tree = ast.parse(source, filename=str(TARGET))
    except SyntaxError as exc:
        errors.append(f"W86 funding-capacity syntax error: {exc}")
        tree = None

    if tree is not None:
        bind = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "bind_paper_runtime_funding_capacity"
            ),
            None,
        )
        if bind is None:
            errors.append("W86 funding-capacity bind function missing")
        else:
            argument_names = {
                arg.arg
                for arg in (
                    list(bind.args.posonlyargs)
                    + list(bind.args.args)
                    + list(bind.args.kwonlyargs)
                )
            }
            if "observed_at" in argument_names or "now" in argument_names:
                errors.append(
                    "W86 funding-capacity process clock must be internal, not caller supplied"
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
                        f"W86 funding-capacity imports forbidden authority/I/O surface at line {node.lineno}: {module}"
                    )
            if isinstance(node, ast.Call):
                name = _call_name(node.func).lower()
                if name in FORBIDDEN_CALLS:
                    errors.append(
                        f"W86 funding-capacity contains forbidden mutating/network call at line {node.lineno}: {name}"
                    )

    if not DEDICATED.is_file():
        errors.append("W86 Funding Capacity: dedicated workflow missing")
    else:
        workflow_source = DEDICATED.read_text(encoding="utf-8")
        if SELF_COMMAND not in workflow_source:
            errors.append("W86 Funding Capacity: boundary is not wired into dedicated CI")
        if TEST not in workflow_source:
            errors.append("W86 Funding Capacity: adversarial tests are not wired into dedicated CI")
        for reproved in (
            "python scripts/check_w86_paper_runtime_final_readiness_boundary.py",
            "python scripts/check_w86_paper_runtime_broker_truth_boundary.py",
            "python scripts/check_r6_authority.py",
            "python scripts/check_r6_live_deny_boundary.py",
        ):
            if reproved not in workflow_source:
                errors.append(
                    f"W86 Funding Capacity: dedicated CI does not re-prove {reproved}"
                )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "AUTO-TRADE W86 PAPER funding-capacity boundary: PASS "
        "(exact broker-bound account attestation; internal <=5s account freshness; "
        "buying power must cover conservative minimum executable notional; <=2s READY TTL; "
        "no network read in overlay, no OrderIntent, OMS, reservation, broker write, execution or LIVE authority)"
    )
    return 0


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
