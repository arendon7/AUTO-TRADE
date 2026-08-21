from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/brokers/paper_portfolio.py"
FORBIDDEN = (
    "AlpacaPaperCryptoWriter",
    "AlpacaPaperWriter",
    "submit_once(",
    ".post(",
    'method="POST"',
    "method='POST'",
    "api.alpaca.markets/v2/orders",
    "R6_EXTERNAL_PAPER_WRITE",
)
DIRECT_NETWORK = {"http", "socket", "ssl", "requests", "httpx", "aiohttp", "websocket", "websockets"}


def fail(message: str) -> None:
    print(f"R7 PAPER portfolio boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if not MODULE.is_file():
        fail("missing R7 PAPER portfolio module")
    text = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(MODULE))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
    roots = {name.split(".", 1)[0] for name in imports if name}
    bad = roots & DIRECT_NETWORK
    if bad:
        fail(f"portfolio module imports direct network stack: {sorted(bad)}")
    if "urllib" in roots and "urllib.parse" not in imports:
        fail("portfolio may use urllib.parse only; urllib network clients are forbidden")
    for token in FORBIDDEN:
        if token in text:
            fail(f"portfolio module contains broker-write authority: {token}")
    for anchor in (
        "class AlpacaPaperPortfolioGateway:",
        'POSITIONS_PATH = "/v2/positions"',
        'OPEN_ORDERS_QUERY = "status=open&limit=500&direction=asc&nested=true"',
        'if request.method != "GET"',
        "ALPACA_LIVE_TRADING_HOST",
        '"broker_write_performed": False',
        '"credentials_persisted": False',
        '"live_trading": "BLOCKED"',
        "credentials.credential_reference",
        "self._transport.read(request)",
    ):
        if anchor not in text:
            fail(f"missing fail-closed portfolio anchor: {anchor}")
    print(
        "R7 PAPER portfolio boundary: PASS — account + positions + open orders are broker-truth GET-only; "
        "credentials remain ephemeral; no writer/POST/cancel/replace/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
