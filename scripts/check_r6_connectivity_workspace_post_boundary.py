from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/autotrade/connectivity_workspace_post.py"

FORBIDDEN_IMPORTS = (
    "requests",
    "httpx",
    "urllib",
    "http.client",
    "socket",
    "ssl",
    "websockets",
    "openai",
    "anthropic",
)
FORBIDDEN_TEXT = (
    "api.alpaca.markets/v2/orders",
    "submit_once(",
    "mark_submit_attempt_unknown(",
    "while True",
    "health_bypass",
)


def _class_node(tree: ast.AST, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise SystemExit(f"ERROR: class missing: {name}")


def _class_segment(source: str, node: ast.ClassDef) -> str:
    lines = source.splitlines()
    end = getattr(node, "end_lineno", node.lineno)
    return "\n".join(lines[node.lineno - 1 : end])


def _is_transport_write(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "write"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_transport"
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
    )


def main() -> int:
    if not SOURCE.is_file():
        print("ERROR: missing connectivity one-shot POST module", file=sys.stderr)
        return 1
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SOURCE))
    errors: list[str] = []

    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
        for module in modules:
            if any(module == p or module.startswith(p + ".") for p in FORBIDDEN_IMPORTS):
                errors.append(f"forbidden direct network/runtime import: {module}")

    executor_node = _class_node(tree, "ConnectivityWorkspaceOneShotExecutor")
    reconciler_node = _class_node(tree, "ConnectivityWorkspaceReconciliationRuntime")
    reconciler = _class_segment(source, reconciler_node)

    required = (
        "ConnectivityWorkspaceStagingBridge(self._workspace).stage(",
        "self._transport.write(request)",
        "_validate_writer_base_url(self._config.base_url)",
        "_validate_write_request(request)",
        "_validate_final_url(response.final_url)",
        "_verify_final_safety(self._workspace, bound_result)",
        "_verify_durable_unknown(self._workspace, bound_result)",
        "PaperSubmissionStatus.UNKNOWN",
        '"reconciliation_required": True',
        '"blind_retry_allowed": False',
        '"live_trading": "BLOCKED"',
        '"next_action": "GET_ONLY_RECONCILIATION_REQUIRED"',
        "AlpacaPaperBracketReconciler",
    )
    for anchor in required:
        if anchor not in source:
            errors.append(f"required one-shot/reconciliation anchor missing: {anchor}")

    writes = [node for node in ast.walk(executor_node) if _is_transport_write(node)]
    if len(writes) != 1:
        errors.append("executor must contain exactly one self._transport.write call")
    else:
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(executor_node):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        cursor = parents.get(writes[0])
        while cursor is not None:
            if isinstance(cursor, (ast.For, ast.While, ast.AsyncFor)):
                errors.append("transport write may not be nested under any retry/iteration loop")
                break
            cursor = parents.get(cursor)

    try:
        if source.index("ConnectivityWorkspaceStagingBridge(self._workspace).stage(") > source.index(
            "self._transport.write(request)"
        ):
            errors.append("UNKNOWN staging must textually precede the only transport write")
    except ValueError:
        pass

    if "AlpacaPaperWriteTransport" in reconciler or ".write(" in reconciler:
        errors.append("restart reconciliation class may not expose a write transport/call")
    if ".reconcile(" not in reconciler:
        errors.append("restart reconciliation must delegate to GET-only bracket reconciler")

    for forbidden in FORBIDDEN_TEXT:
        if forbidden in source:
            errors.append(f"forbidden one-shot authority/retry surface: {forbidden}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 connectivity one-shot POST boundary: PASS "
        "(same-process UNKNOWN-before-POST; final Safety + durable UNKNOWN recheck; "
        "exactly one non-loop transport write; restart GET-only reconciliation; no blind retry/LIVE)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
