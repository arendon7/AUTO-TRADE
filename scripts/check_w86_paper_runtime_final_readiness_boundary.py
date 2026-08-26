from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/autotrade/paper_runtime_final_readiness.py"
TEST = "tests/test_w86_paper_runtime_final_readiness.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
DEDICATED = ROOT / ".github/workflows/w86-paper-runtime-final-readiness.yml"
SELF_COMMAND = "python scripts/check_w86_paper_runtime_final_readiness_boundary.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "requests", "httpx", "socket", "urllib", "websocket", "websockets", "sqlite3",
    "autotrade.brokers", "autotrade.oms", "autotrade.safety", "autotrade.health_bridge",
    "autotrade.execution", "autotrade.research",
)
FORBIDDEN_CALLS = {
    "post", "put", "patch", "delete", "submit", "submit_once", "place_order",
    "cancel_order", "replace_order", "reserve", "reserve_capital",
    "stage_external_submission", "evaluate_order",
}


def main() -> int:
    errors: list[str] = []
    if not TARGET.is_file():
        print("ERROR: missing W86 final PAPER runtime readiness module", file=sys.stderr)
        return 1
    source = TARGET.read_text(encoding="utf-8")
    required = (
        'PAPER_RUNTIME_FINAL_READINESS_VERSION = "W86_PAPER_RUNTIME_FINAL_READINESS_V1"',
        'W85_PROBATION_NOTIONAL_MAX_USD = Decimal("5")',
        "W85_PROBATION_ORDER_MAX = 1",
        "class PaperRuntimeFinalReadinessPolicy:",
        "class PaperRuntimeFinalReadinessReceipt:",
        "class PaperRuntimeReadinessStatus(StrEnum):",
        'READY = "READY"',
        'BLOCKED = "BLOCKED"',
        "class PaperRuntimeReadinessBlocker(StrEnum):",
        'MINIMUM_NOTIONAL_EXCEEDS_PROBATION_CAP = "MINIMUM_NOTIONAL_EXCEEDS_PROBATION_CAP"',
        "def finalize_paper_runtime_readiness(",
        "source_module._proof_payload(source, include_hash=False)",
        "candidate_module._payload(candidate, include_hash=False)",
        "broker_module._proof_payload(broker, include_hash=False)",
        "asset_module._proof_payload(asset, include_hash=False)",
        "market_module._proof_payload(market, include_hash=False)",
        "safety_module._proof_payload(safety, include_hash=False)",
        "candidate.w85_source_snapshot_hash != source.proof_hash",
        "asset.broker_truth_hash, broker.proof_hash",
        "market.broker_truth_hash, broker.proof_hash",
        "market.asset_truth_hash, asset.proof_hash",
        "market.asset_attestation_fingerprint != asset.asset_attestation_fingerprint",
        "source_snapshot.probation_notional_cap_usd > W85_PROBATION_NOTIONAL_MAX_USD",
        "source_snapshot.probation_order_cap != W85_PROBATION_ORDER_MAX",
        "minimum_quantity = _ceil(asset_truth.min_order_size, asset_truth.min_trade_increment)",
        "asset_truth.price_increment",
        'market_truth.ask_price if candidate_identity.side == "BUY" else market_truth.bid_price',
        "ROUND_CEILING",
        "now = _utc(_now_utc())",
        "blocker_codes = tuple(blockers)",
        "ready = not blocker_codes",
        '"separate_execution_approval_required": True',
        '"order_intent_created": False',
        '"oms_handoff_performed": False',
        '"capital_reserved": False',
        '"broker_write_performed": False',
        '"paper_runtime_ready": ready',
        '"paper_execution_authorized": False',
        '"external_execution_authorized": False',
        '"runtime_execution_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        "valid_until = min(",
    )
    for anchor in required:
        if anchor not in source:
            errors.append(f"W86 final readiness contract missing: {anchor}")

    for forbidden in (
        "OrderIntent(", "AlpacaPaperCredentials", "AlpacaPaperGateway", "SQLite",
        "CREATE TABLE", "INSERT INTO", "UPDATE ", "DELETE FROM",
        'method="POST"', 'method="PUT"', 'method="PATCH"', 'method="DELETE"',
        "paper-api.alpaca.markets",
    ):
        if forbidden in source:
            errors.append(f"W86 final readiness contains forbidden surface: {forbidden}")

    try:
        tree = ast.parse(source, filename=str(TARGET))
    except SyntaxError as exc:
        errors.append(f"W86 final readiness syntax error: {exc}")
        tree = None

    if tree is not None:
        finalize = next(
            (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "finalize_paper_runtime_readiness"),
            None,
        )
        if finalize is None:
            errors.append("W86 final readiness function missing")
        else:
            argument_names = {
                arg.arg
                for arg in (
                    list(finalize.args.posonlyargs) + list(finalize.args.args) + list(finalize.args.kwonlyargs)
                )
            }
            if "observed_at" in argument_names or "now" in argument_names:
                errors.append("W86 final readiness process clock must be internal, not caller supplied")

        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            for module in modules:
                if any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES):
                    errors.append(
                        f"W86 final readiness imports forbidden authority/I/O surface at line {node.lineno}: {module}"
                    )
            if isinstance(node, ast.Call):
                name = _call_name(node.func).lower()
                if name in FORBIDDEN_CALLS:
                    errors.append(
                        f"W86 final readiness contains forbidden mutating call at line {node.lineno}: {name}"
                    )

    for workflow, label in ((CORE, "Core Safety"), (DEDICATED, "W86 Final Readiness")):
        if not workflow.is_file():
            errors.append(f"{label}: workflow missing")
            continue
        workflow_source = workflow.read_text(encoding="utf-8")
        if SELF_COMMAND not in workflow_source:
            errors.append(f"{label}: final readiness boundary is not wired into CI")
        if TEST not in workflow_source:
            errors.append(f"{label}: final readiness adversarial tests are not wired into CI")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE W86 final PAPER runtime readiness boundary: PASS "
        "(six exact read-only proofs; internal finite clock; USD 5/one-order probation ceiling; "
        "conservative broker-minimum sizing; READY/BLOCKED reason codes; no OrderIntent, OMS, "
        "capital reservation, broker write, execution or LIVE authority)"
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
