from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/paper_close_lifecycle.py"
NETWORK_ROOTS = {"http", "httpx", "requests", "socket", "ssl", "urllib", "websocket", "websockets"}
FORBIDDEN_TOKENS = (
    "AlpacaPaperCredentials",
    "secret_key",
    "key_id",
    ".post(",
    "submit_once(",
    "api.alpaca.markets",
)
REQUIRED = (
    'SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"',
    'return "RECONCILE_ONLY"',
    'def retry_post(self) -> bool:',
    "return False",
    '"retry_post": False',
    '"live_trading": "BLOCKED"',
    "submission_attempt_count=1",
    "only PREPARED close attempt may cross SUBMISSION_UNKNOWN",
    "close reconciliation requires exactly one burned POST attempt",
    "class SQLitePaperCloseLifecycle:",
)


def fail(message: str) -> None:
    print(f"R7 PAPER close lifecycle boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if not MODULE.is_file():
        fail("missing R7 PAPER close lifecycle")
    text = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(MODULE))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
    roots = {item.split(".", 1)[0] for item in imports if item}
    bad = roots & NETWORK_ROOTS
    if bad:
        fail(f"durable lifecycle imports network stack: {sorted(bad)}")
    for token in FORBIDDEN_TOKENS:
        if token in text:
            fail(f"durable lifecycle contains broker/credential authority: {token}")
    for anchor in REQUIRED:
        if anchor not in text:
            fail(f"missing one-shot/reconciliation anchor: {anchor}")
    print(
        "R7 PAPER close lifecycle boundary: PASS — durable UNKNOWN-before-future-POST state, "
        "exactly one attempt, restart RECONCILE_ONLY, no credentials/network/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
