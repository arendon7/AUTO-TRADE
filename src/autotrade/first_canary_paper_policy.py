from __future__ import annotations

from decimal import Decimal


# Isolated operator policy for the first real Alpaca PAPER BTC/USD canary.
# Alpaca's current crypto/USD contract requires an approximately USD 10
# minimum order cost basis. Keep a small deterministic margin above that
# broker floor while remaining tightly bounded and PAPER-only.
FIRST_CANARY_PAPER_SYMBOL = "BTC/USD"
FIRST_CANARY_PAPER_MIN_NOTIONAL = Decimal("10")
FIRST_CANARY_PAPER_TARGET_NOTIONAL = Decimal("10.50")
FIRST_CANARY_PAPER_MAX_NOTIONAL = Decimal("12")
FIRST_CANARY_PAPER_MAX_ACCOUNT_FRACTION = Decimal("0.001")


def validate_first_canary_notional(notional: Decimal) -> None:
    if not isinstance(notional, Decimal) or not notional.is_finite():
        raise ValueError("first-canary PAPER notional must be a finite Decimal")
    if not FIRST_CANARY_PAPER_MIN_NOTIONAL <= notional <= FIRST_CANARY_PAPER_MAX_NOTIONAL:
        raise ValueError("first-canary PAPER notional must remain within USD 10-12")


__all__ = [
    "FIRST_CANARY_PAPER_SYMBOL",
    "FIRST_CANARY_PAPER_MIN_NOTIONAL",
    "FIRST_CANARY_PAPER_TARGET_NOTIONAL",
    "FIRST_CANARY_PAPER_MAX_NOTIONAL",
    "FIRST_CANARY_PAPER_MAX_ACCOUNT_FRACTION",
    "validate_first_canary_notional",
]
