from decimal import Decimal
from .models import PaperOrder, PaperPosition
from .simulator import PaperBrokerSimulator


class PaperExecutionEngine:
    MAX_ORDER_VALUE = Decimal('12')

    def __init__(self):
        self.simulator = PaperBrokerSimulator()
        self.positions = {}

    def execute(self, order: PaperOrder, market_price: Decimal):
        order.validate_environment()
        if order.quantity * market_price > self.MAX_ORDER_VALUE:
            raise ValueError('ORDER_BLOCKED')
        fill = self.simulator.execute(order, market_price)
        position = PaperPosition(
            asset=order.asset,
            quantity=fill.filled_quantity,
            entry_price=fill.fill_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
        )
        self.positions[order.asset] = position
        return position
