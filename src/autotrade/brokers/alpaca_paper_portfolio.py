from __future__ import annotations

# Compatibility-only import surface. The actual R7 PAPER portfolio network
# authority lives in paper_portfolio.py and is certified by the R7 GET-only
# boundary. This shim contains no endpoint, transport, POST, cancel or replace
# authority and exists only so early R7 imports remain stable during the stack.
from .paper_portfolio import (
    AlpacaPaperPortfolioGateway,
    OPEN_ORDERS_QUERY,
    ORDERS_PATH,
    POSITIONS_PATH,
    PaperPortfolioDisabled,
    PaperPortfolioError,
    PaperPortfolioIntegrityError,
    PaperPortfolioOpenOrder,
    PaperPortfolioPosition,
    PaperPortfolioReadPolicy,
    PaperPortfolioSnapshot,
)

__all__ = [
    "AlpacaPaperPortfolioGateway",
    "OPEN_ORDERS_QUERY",
    "ORDERS_PATH",
    "POSITIONS_PATH",
    "PaperPortfolioDisabled",
    "PaperPortfolioError",
    "PaperPortfolioIntegrityError",
    "PaperPortfolioOpenOrder",
    "PaperPortfolioPosition",
    "PaperPortfolioReadPolicy",
    "PaperPortfolioSnapshot",
]
