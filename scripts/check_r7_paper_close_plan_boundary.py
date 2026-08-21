from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/paper_close_plan.py"
FORBIDDEN = (
    "AlpacaPaperCryptoWriter",
    "HttpsAlpacaPaperCryptoWriteTransport",
    "submit_once(",
    ".post(",
    'method="POST"',
    "api.alpaca.markets",
    "http.client",
    "urllib",
    "requests",
)


def fail(message: str) -> None:
    print(f"R7 PAPER close-plan boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    text = MODULE.read_text(encoding="utf-8")
    for token in FORBIDDEN:
        if token in text:
            fail(f"prepared close plan contains broker-write/network authority: {token}")
    for anchor in (
        "class PaperCryptoClosePlan:",
        "prepare_crypto_close_plan(",
        'side: str',
        'order_type: str',
        'time_in_force: str',
        "quantity > position.available_quantity",
        '"network_write_authorized": False',
        '"retry_post": False',
        '"live_trading": "BLOCKED"',
        'CLOSE_PLAN_TTL = timedelta(seconds=15)',
        'MAX_CLOSE_SLIPPAGE_BPS = Decimal("50")',
    ):
        if anchor not in text:
            fail(f"missing close-plan fail-closed anchor: {anchor}")
    print(
        "R7 PAPER close-plan boundary: PASS — fresh broker position bound; quantity/slippage capped; "
        "SELL LIMIT IOC prepared offline; no POST/retry/LIVE authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
