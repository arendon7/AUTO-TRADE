from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/paper_close_reconciliation.py"
NETWORK_ROOTS = {"http", "httpx", "requests", "socket", "ssl", "urllib", "websocket", "websockets"}
FORBIDDEN = (
    ".post(",
    "submit_once(",
    "PaperCloseWriter",
    "HttpsPaperCloseWriteTransport",
    "retry_post=True",
    "retry_post = True",
    "api.alpaca.markets",
    "OpenAI",
    "Anthropic",
)
REQUIRED = (
    "class AlpacaPaperCloseReconciliationGateway:",
    'if request.method != "GET"',
    'ORDER_BY_CLIENT_PATH = "/v2/orders:by_client_order_id"',
    "portfolio_gateway.snapshot(",
    "close reconciliation requires exactly one burned POST attempt",
    "attempt remains burned and GET-only reconciliation must continue",
    '"retry_post": False',
    '"live_trading": "BLOCKED"',
    "lifecycle.reconcile(",
)


def fail(message: str) -> None:
    print(f"R7 PAPER close reconciliation boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if not MODULE.is_file():
        fail("missing R7 close reconciliation module")
    text = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(MODULE))
    imports: set[str] = set()
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)
            elif isinstance(node.func, ast.Name):
                calls.append(node.func.id)
    direct_network = {name.split(".", 1)[0] for name in imports if name} & NETWORK_ROOTS
    if direct_network:
        fail(f"GET-only reconciliation imports direct network stack: {sorted(direct_network)}")
    if "post" in calls or "submit_once" in calls:
        fail("GET-only reconciliation contains broker write call")
    for token in FORBIDDEN:
        if token in text:
            fail(f"GET-only reconciliation contains forbidden authority token: {token}")
    for anchor in REQUIRED:
        if anchor not in text:
            fail(f"missing GET-only/reconciliation anchor: {anchor}")
    print(
        "R7 PAPER close reconciliation boundary: PASS — exact order GET + broker Portfolio GET truth, "
        "burned attempt required, no POST/retry/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
