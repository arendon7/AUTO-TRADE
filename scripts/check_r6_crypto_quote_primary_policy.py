from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_market_data.py"
SAFETY = ROOT / "src/autotrade/safety.py"


def main() -> int:
    errors: list[str] = []
    if not MARKET.is_file():
        errors.append("missing crypto market-data gateway")
    if not SAFETY.is_file():
        errors.append("missing Capital Safety kernel")

    if MARKET.is_file():
        text = MARKET.read_text(encoding="utf-8")
        required = (
            'LATEST_QUOTE_PATH = "/v1beta3/crypto/us/latest/quotes"',
            'LATEST_TRADE_PATH = "/v1beta3/crypto/us/latest/trades"',
            'quote_age = self._validate_event_time(quote_time, received_at, "crypto latest quote")',
            'trade_age = self._validate_event_time(trade_time, received_at, "crypto latest trade")',
            'if quote_age > fresh_window:',
            '"crypto latest quote is stale for execution: "',
            'if trade_age <= max_reference:',
            '"crypto latest trade deviates from quote midpoint: "',
            'observed_at=received_at',
        )
        for anchor in required:
            if anchor not in text:
                errors.append(f"crypto quote-primary policy anchor missing: {anchor}")

        forbidden = (
            'if trade_age > max_reference:',
            'crypto latest trade reference is too old',
            'min(quote_age, trade_age) > fresh_window',
            '/latest/orderbooks',
        )
        for anchor in forbidden:
            if anchor in text:
                errors.append(f"crypto quote-primary policy regression: {anchor}")

        quote_gate = text.find('if quote_age > fresh_window:')
        spread_gate = text.find('if bid > ask:')
        trade_compare = text.find('if trade_age <= max_reference:')
        if not (0 <= quote_gate < spread_gate < trade_compare):
            errors.append("crypto quote freshness must gate before quote/trade price-coherence checks")

    if SAFETY.is_file():
        text = SAFETY.read_text(encoding="utf-8")
        for anchor in (
            'if not all(_finite_positive(value) for value in (market.bid, market.ask, market.last)):',
            'if intent.order_type is OrderType.LIMIT:',
            'mid = (market.bid + market.ask) / Decimal("2")',
            'deviation_bps = abs(intent.limit_price - mid) / mid * Decimal("10000")',
        ):
            if anchor not in text:
                errors.append(f"Capital Safety quote-price contract anchor missing: {anchor}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "R6 crypto quote-primary market policy: PASS "
        "(fresh bid/ask required for LIMIT execution; old trade is auxiliary provenance; "
        "recent comparable trade still checked; no orderbook proxy; no write authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
