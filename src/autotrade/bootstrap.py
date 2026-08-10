from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .brokers.durable_paper import DurablePaperBroker
from .domain import PortfolioSnapshot
from .engine import DurableTradingPipeline
from .oms import OrderManagementSystem
from .persistence import (
    SQLiteEventLedger,
    SQLiteOrderStore,
    SQLitePortfolioStore,
    SQLiteReservationStore,
    SQLiteRuntime,
    SQLiteSafetyStateStore,
)
from .reconciliation import ReconciliationEngine, ReconciliationResult
from .safety import CapitalSafetyKernel, SafetyLimits


@dataclass(frozen=True, slots=True)
class DurablePaperCore:
    runtime: SQLiteRuntime
    ledger: SQLiteEventLedger
    broker: DurablePaperBroker
    safety: CapitalSafetyKernel
    oms: OrderManagementSystem
    portfolio_store: SQLitePortfolioStore
    reservation_store: SQLiteReservationStore
    pipeline: DurableTradingPipeline
    reconciliation: ReconciliationEngine
    startup_reconciliation: ReconciliationResult


def build_durable_paper_core(
    *,
    db_path: str | Path,
    limits: SafetyLimits,
    initial_portfolio: PortfolioSnapshot,
    now: datetime,
) -> DurablePaperCore:
    runtime = SQLiteRuntime(db_path)
    ledger = SQLiteEventLedger(runtime)
    portfolio_store = SQLitePortfolioStore(runtime)
    portfolio_store.initialize(initial_portfolio, now=now)
    safety_state_store = SQLiteSafetyStateStore(runtime)
    reservation_store = SQLiteReservationStore(runtime)
    order_store = SQLiteOrderStore(runtime)
    broker = DurablePaperBroker(runtime)
    safety = CapitalSafetyKernel(limits, ledger, state_store=safety_state_store)
    oms = OrderManagementSystem(
        broker=broker,
        ledger=ledger,
        order_store=order_store,
        safety_state_store=safety_state_store,
    )
    pipeline = DurableTradingPipeline(
        safety=safety,
        oms=oms,
        portfolio_store=portfolio_store,
        reservation_store=reservation_store,
    )
    reconciliation = ReconciliationEngine(
        broker=broker,
        oms=oms,
        portfolio_store=portfolio_store,
        reservation_store=reservation_store,
        ledger=ledger,
    )

    # Every process start is fail-closed until the durable local state and the
    # broker-side simulator agree. A future real broker adapter must preserve
    # this startup contract.
    portfolio_store.set_reconciliation_status(
        reconciliation_ok=False,
        broker_state_known=False,
        now=now,
    )
    startup_reconciliation = reconciliation.reconcile(now=now)

    return DurablePaperCore(
        runtime=runtime,
        ledger=ledger,
        broker=broker,
        safety=safety,
        oms=oms,
        portfolio_store=portfolio_store,
        reservation_store=reservation_store,
        pipeline=pipeline,
        reconciliation=reconciliation,
        startup_reconciliation=startup_reconciliation,
    )
