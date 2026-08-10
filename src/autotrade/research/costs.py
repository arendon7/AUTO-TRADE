from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..domain import Side


class InvalidCostModel(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionCostModel:
    fee_bps: Decimal
    half_spread_bps: Decimal
    slippage_bps: Decimal
    allow_zero_total_costs: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("fee_bps", self.fee_bps),
            ("half_spread_bps", self.half_spread_bps),
            ("slippage_bps", self.slippage_bps),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise InvalidCostModel(f"{name} must be finite and >= 0")
        if self.total_bps == 0 and not self.allow_zero_total_costs:
            raise InvalidCostModel(
                "zero-cost backtests require allow_zero_total_costs=True explicitly"
            )

    @property
    def total_bps(self) -> Decimal:
        return self.fee_bps + self.half_spread_bps + self.slippage_bps

    def execution_price(self, *, side: Side, reference_price: Decimal) -> Decimal:
        if not reference_price.is_finite() or reference_price <= 0:
            raise InvalidCostModel("reference_price must be finite and > 0")
        impact_bps = self.half_spread_bps + self.slippage_bps
        multiplier = Decimal("1") + side.sign * impact_bps / Decimal("10000")
        result = reference_price * multiplier
        if not result.is_finite() or result <= 0:
            raise InvalidCostModel("cost model produced invalid execution price")
        return result

    def fee(self, *, quantity: Decimal, execution_price: Decimal) -> Decimal:
        if not quantity.is_finite() or quantity <= 0:
            raise InvalidCostModel("quantity must be finite and > 0")
        if not execution_price.is_finite() or execution_price <= 0:
            raise InvalidCostModel("execution_price must be finite and > 0")
        return quantity * execution_price * self.fee_bps / Decimal("10000")

    def fingerprint_payload(self) -> dict[str, str | bool]:
        return {
            "fee_bps": str(self.fee_bps),
            "half_spread_bps": str(self.half_spread_bps),
            "slippage_bps": str(self.slippage_bps),
            "allow_zero_total_costs": self.allow_zero_total_costs,
        }
