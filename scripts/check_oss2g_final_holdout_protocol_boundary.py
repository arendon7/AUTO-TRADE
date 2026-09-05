from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "autotrade" / "research" / "oss2_final_holdout_protocol.py"

FORBIDDEN_IMPORT_ROOTS = {
    "requests",
    "httpx",
    "urllib",
    "socket",
    "websockets",
}
FORBIDDEN_IMPORT_FRAGMENTS = (
    ".splits",
    ".registry",
    "broker",
    "oms",
    "safety",
    "order_intent",
    "paper_execution",
)
FORBIDDEN_SYMBOLS = {
    "HoldoutPermit",
    "ProtectedHoldout",
    "MarketDataset",
    "BacktestEngine",
    "OrderIntent",
}
FORBIDDEN_CALLS = {
    "checkout",
    "consume_holdout_permit",
    "post",
    "put",
    "patch",
    "delete",
    "submit",
    "execute_order",
    "send_order",
}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def main() -> None:
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TARGET))

    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    imported_symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
            imported_symbols.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
            imported_symbols.update(alias.asname or alias.name for alias in node.names)
        else:
            modules = []
        for module in modules:
            lowered = module.lower()
            root = lowered.split(".", 1)[0]
            if root in FORBIDDEN_IMPORT_ROOTS:
                raise SystemExit(f"OSS-2G forbidden network import: {module}")
            if any(fragment in lowered for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                raise SystemExit(f"OSS-2G forbidden authority/holdout import: {module}")
        if isinstance(node, ast.Call):
            call_name = _call_name(node)
            if call_name and call_name.lower() in FORBIDDEN_CALLS:
                raise SystemExit(f"OSS-2G forbidden checkout/execution call: {call_name}")

    forbidden_present = FORBIDDEN_SYMBOLS.intersection(imported_symbols)
    if forbidden_present:
        raise SystemExit(
            "OSS-2G may not import holdout/execution symbols: "
            + ",".join(sorted(forbidden_present))
        )

    writer = functions.get("preregister_and_record")
    if writer is None:
        raise SystemExit("OSS-2G canonical protocol writer missing")
    writer_args = {arg.arg.lower() for arg in writer.args.args + writer.args.kwonlyargs}
    if writer_args != {"self", "protocol_id", "freeze"}:
        raise SystemExit(
            "OSS-2G writer may accept only protocol_id + frozen OSS-2F receipt"
        )

    reader = functions.get("read_oss2g_protocol_read_only")
    if reader is None:
        raise SystemExit("OSS-2G independent read-only verifier missing")
    reader_args = {arg.arg.lower() for arg in reader.args.args + reader.args.kwonlyargs}
    if any("dataset" in arg or "holdout" in arg for arg in reader_args):
        raise SystemExit("OSS-2G reader may not accept FINAL_HOLDOUT material")

    required_fragments = (
        'OSS2G_CONTRACT_VERSION = "OSS2G_FINAL_HOLDOUT_PROTOCOL_V1"',
        'min_net_return: float = 0.0',
        'min_sharpe: float = 0.0',
        'max_drawdown: float = 0.35',
        'max_evaluations: int = 1',
        'retuning_allowed: bool = False',
        'reselection_allowed: bool = False',
        'second_attempt_allowed: bool = False',
        'failure_is_terminal: bool = True',
        'split_name: str = _FINAL_HOLDOUT_SPLIT_NAME',
        'permit_purpose: str = _FINAL_VALIDATION_PURPOSE',
        'freeze.decision is not OSS2HoldoutFreezeState.HOLDOUT_ELIGIBLE',
        'final_holdout_observed=False',
        'final_holdout_consumed=False',
        'paper_execution_authorized=False',
        'capital_authority="NONE"',
        'live_trading="BLOCKED"',
        "BEGIN IMMEDIATE",
        "oss2_final_holdout_protocols_no_update",
        "oss2_final_holdout_protocols_no_delete",
        "mode=ro",
        "PRAGMA query_only = ON",
    )
    for fragment in required_fragments:
        if fragment not in source:
            raise SystemExit(f"OSS-2G required fail-closed invariant missing: {fragment}")

    print(
        "AUTO-TRADE OSS-2G FINAL_HOLDOUT protocol boundary: PASS "
        "(eligible OSS-2F only; preregistered 3-gate single-use policy; "
        "append-only protocol receipt; deterministic future authorization identity; "
        "no FINAL_HOLDOUT input/checkout/permit minting, network, broker, OMS, Safety, "
        "execution, capital or LIVE authority)"
    )


if __name__ == "__main__":
    main()
