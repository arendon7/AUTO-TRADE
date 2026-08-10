from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock

from .domain import MarketSnapshot, OrderIntent, OrderRecord, PortfolioSnapshot, RiskDecision, RiskDecisionStatus
from .oms import OrderManagementSystem
from .safety import CapitalSafetyKernel


@dataclass(frozen=True, slots=True)
class PipelineResult:
    decision: RiskDecision
    order: OrderRecord | None


class TradingPipeline:
    """Single guarded path from intent to broker-facing OMS.

    The process lock serializes evaluate+submit inside one process. Cross-process
    reservations and durable portfolio versioning are intentionally deferred;
    live trading remains blocked until those are implemented and certified.
    """

    def __init__(self, *, safety: CapitalSafetyKernel, oms: OrderManagementSystem) -> None:
        self._safety = safety
        self._oms = oms
        self._lock = RLock()

    def process_intent(
        self,
        *,
        intent: OrderIntent,
        market: MarketSnapshot,
        portfolio: PortfolioSnapshot,
        now: datetime,
    ) -> PipelineResult:
        with self._lock:
            decision = self._safety.evaluate(intent=intent, market=market, portfolio=portfolio, now=now)
            if decision.status is RiskDecisionStatus.REJECTED:
                return PipelineResult(decision=decision, order=None)
            order = self._oms.submit(intent=intent, decision=decision, market=market, now=now)
            return PipelineResult(decision=decision, order=order)
