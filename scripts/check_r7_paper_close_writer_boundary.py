from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/paper_close_writer.py"

REQUIRED = (
    "class PaperCloseWriter:",
    "enabled: bool = False",
    'ORDERS_PATH = "/v2/orders"',
    "host: str = ALPACA_PAPER_TRADING_HOST",
    'confirmation != _CONFIRMATION',
    '_CONFIRMATION = "CERRAR PAPER"',
    "lifecycle.mark_submission_unknown(attempt_id, at=instant)",
    "self._transport.post(",
    'raise PaperCloseWriterAmbiguous(',
    'requires GET-only reconciliation',
    "existing SELL order overlaps target close position",
    "FINAL_PORTFOLIO_TTL = timedelta(seconds=5)",
    "credentials.credential_reference != plan.credential_reference",
)
FORBIDDEN = (
    "api.alpaca.markets/v2/orders",
    "retry_post=True",
    "retry_post = True",
    "while True",
    "for retry in",
    "sleep(",
    "OpenAI",
    "Anthropic",
)


def fail(message: str) -> None:
    print(f"R7 PAPER close writer boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def _inside_loop(tree: ast.AST, target: ast.AST) -> bool:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    cursor = target
    while cursor in parents:
        cursor = parents[cursor]
        if isinstance(cursor, (ast.For, ast.AsyncFor, ast.While)):
            return True
    return False


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def main() -> int:
    if not MODULE.is_file():
        fail("missing R7 PAPER close writer")
    text = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(MODULE))

    for anchor in REQUIRED:
        if anchor not in text:
            fail(f"missing authority/safety anchor: {anchor}")
    for token in FORBIDDEN:
        if token in text:
            fail(f"forbidden retry/LIVE/AI authority token: {token}")

    post_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and _call_name(node.func) == "post"]
    transport_posts = [node for node in post_calls if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "_transport"]
    if len(transport_posts) != 1:
        fail(f"writer must have exactly one self._transport.post call, found {len(transport_posts)}")
    if _inside_loop(tree, transport_posts[0]):
        fail("close transport POST may not execute inside any loop")

    unknown_index = text.find("lifecycle.mark_submission_unknown(attempt_id, at=instant)")
    post_index = text.find("self._transport.post(")
    if unknown_index < 0 or post_index < 0 or unknown_index >= post_index:
        fail("durable SUBMISSION_UNKNOWN must occur before the only transport POST")

    request_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and _call_name(node.func) == "request"]
    if len(request_calls) != 1:
        fail(f"HTTPS delegate must contain exactly one request call, found {len(request_calls)}")
    request = request_calls[0]
    if _inside_loop(tree, request):
        fail("HTTPS request may not execute inside any loop")

    if text.count('connection.request("POST", path') != 1:
        fail("exact HTTPS delegate POST call shape is not unique")
    if "ALPACA_LIVE_TRADING_HOST" in text:
        fail("close writer may not import or reference LIVE trading host")

    print(
        "R7 PAPER close writer boundary: PASS — default-disabled, human-bound, fresh broker truth, "
        "UNKNOWN-before-single-POST, no retry loop, exact PAPER host/path, GET-only ambiguity recovery"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
