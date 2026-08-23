from decimal import Decimal
from .models import Fill, PaperOrder


class PaperBrokerSimulator:
    """Deterministic paper fill simulator.

    This module intentionally cannot submit external orders.
    """

    def __init__(self, slippage_bps: Decimal = Decimal("5")):
        self.slippage_bps = slippage_bps

    def execute(self, order: PaperOrder, market_price: Decimal) -> Fill:
        order.validate_environment()
        if order.quantity <= 0:
            raise ValueError("quantity must be positive")

        slippage = market_price * self.slippage_bps / Decimal("10000")
        fill_price = market_price + slippage if order.side == "BUY" else market_price - slippage

        return Fill(
            order_id=order.order_id,
            filled_quantity=order.quantity,
            fill_price=fill_price,
        )
