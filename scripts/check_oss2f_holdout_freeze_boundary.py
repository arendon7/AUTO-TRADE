from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "autotrade" / "research" / "oss2_holdout_freeze.py"

FORBIDDEN_IMPORT_ROOTS = {
    "requests",
    "httpx",
    "urllib",
    "socket",
    "websockets",
}
FORBIDDEN_AUTHORITY_FRAGMENTS = (
    "broker",
    "oms",
    "safety",
    "order_intent",
    "paper_execution",
)
FORBIDDEN_CALLS = {
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
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []
        for name in names:
            lowered = name.lower()
            root = lowered.split(".", 1)[0]
            if root in FORBIDDEN_IMPORT_ROOTS:
                raise SystemExit(f"OSS-2F forbidden network import: {name}")
            if any(fragment in lowered for fragment in FORBIDDEN_AUTHORITY_FRAGMENTS):
                raise SystemExit(f"OSS-2F forbidden authority import: {name}")
        if isinstance(node, ast.Call):
            call_name = _call_name(node)
            if call_name and call_name.lower() in FORBIDDEN_CALLS:
                raise SystemExit(f"OSS-2F forbidden execution/network call: {call_name}")

    freeze = functions.get("freeze_and_record")
    if freeze is None:
        raise SystemExit("OSS-2F canonical freeze writer missing")
    freeze_args = {arg.arg.lower() for arg in freeze.args.args + freeze.args.kwonlyargs}
    if any("holdout" in arg and arg != "robustness" for arg in freeze_args):
        raise SystemExit("OSS-2F writer may not accept FINAL_HOLDOUT material")

    reader = functions.get("read_oss2f_freeze_read_only")
    if reader is None:
        raise SystemExit("OSS-2F independent read-only verifier missing")
    reader_args = {arg.arg.lower() for arg in reader.args.args + reader.args.kwonlyargs}
    if any("holdout" in arg for arg in reader_args):
        raise SystemExit("OSS-2F reader may not accept FINAL_HOLDOUT material")

    required_fragments = (
        "evaluate_oss2e_holdout_eligibility(robustness)",
        "BEGIN IMMEDIATE",
        "oss2_holdout_freezes_no_update",
        "oss2_holdout_freezes_no_delete",
        "mode=ro",
        "PRAGMA query_only = ON",
        'final_holdout_observed=False',
        'paper_execution_authorized=False',
        'capital_authority="NONE"',
        'live_trading="BLOCKED"',
    )
    for fragment in required_fragments:
        if fragment not in source:
            raise SystemExit(f"OSS-2F required fail-closed invariant missing: {fragment}")

    print(
        "AUTO-TRADE OSS-2F pre-holdout freeze boundary: PASS "
        "(one durable append-only freeze per campaign; read-only verifier; "
        "no FINAL_HOLDOUT input, network, broker, OMS, Safety, execution, capital or LIVE authority)"
    )


if __name__ == "__main__":
    main()
