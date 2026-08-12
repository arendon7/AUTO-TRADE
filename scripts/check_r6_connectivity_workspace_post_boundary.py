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


def _class_segment(source: str, tree: ast.AST, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            lines = source.splitlines()
            end = getattr(node, "end_lineno", node.lineno)
            return "\n".join(lines[node.lineno - 1 : end])
    raise SystemExit(f"ERROR: class missing: {name}")


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

    executor = _class_segment(source, tree, "ConnectivityWorkspaceOneShotExecutor")
    reconciler = _class_segment(source, tree, "ConnectivityWorkspaceReconciliationRuntime")

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

    if source.count("self._transport.write(request)") != 1:
        errors.append("executor must contain exactly one transport write call")
    try:
        if source.index("ConnectivityWorkspaceStagingBridge(self._workspace).stage(") > source.index(
            "self._transport.write(request)"
        ):
            errors.append("UNKNOWN staging must textually precede the only transport write")
    except ValueError:
        pass

    executor_tree = ast.parse(executor)
    if any(isinstance(node, (ast.For, ast.While, ast.AsyncFor)) for node in ast.walk(executor_tree)):
        errors.append("one-shot executor may not contain retry/iteration loops")

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
        "exactly one transport write; restart GET-only reconciliation; no blind retry/LIVE)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
