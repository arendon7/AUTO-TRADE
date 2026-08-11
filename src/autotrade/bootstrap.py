from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .brokers.durable_paper import DurablePaperBroker
from .domain import PortfolioSnapshot
from .engine import DurableTradingPipeline
from .execution_state import SQLiteFillAwarePortfolioStore, SQLiteFillStore
from .oms import OrderManagementSystem
from .persistence import (
    SQLiteEventLedger,
    SQLiteOrderStore,
    SQLiteReservationStore,
    SQLiteRuntime,
)
from .reconciliation import ReconciliationEngine, ReconciliationResult
from .risk_state import SQLiteR2SafetyStateStore, SQLiteRiskTelemetryStore
from .safety import CapitalSafetyKernel, SafetyLimits


@dataclass(frozen=True, slots=True)
class DurablePaperCore:
    runtime: SQLiteRuntime
    ledger: SQLiteEventLedger
    broker: DurablePaperBroker
    safety: CapitalSafetyKernel
    oms: OrderManagementSystem
    portfolio_store: SQLiteFillAwarePortfolioStore
    fill_store: SQLiteFillStore
    reservation_store: SQLiteReservationStore
    risk_telemetry: SQLiteRiskTelemetryStore
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
    portfolio_store = SQLiteFillAwarePortfolioStore(runtime)
    portfolio_store.initialize(initial_portfolio, now=now)
    safety_state_store = SQLiteR2SafetyStateStore(runtime)
    risk_telemetry = SQLiteRiskTelemetryStore(
        runtime,
        max_daily_loss=limits.max_daily_loss,
        max_drawdown=limits.max_drawdown,
    )
    risk_telemetry.initialize(equity=initial_portfolio.equity, now=now)
    reservation_store = SQLiteReservationStore(runtime)
    order_store = SQLiteOrderStore(runtime)
    fill_store = SQLiteFillStore(runtime)
    broker = DurablePaperBroker(runtime)
    safety = CapitalSafetyKernel(limits, ledger, state_store=safety_state_store)
    oms = OrderManagementSystem(
        broker=broker,
        ledger=ledger,
        order_store=order_store,
        safety_state_store=safety_state_store,
        fill_store=fill_store,
    )
    pipeline = DurableTradingPipeline(
        safety=safety,
        oms=oms,
        portfolio_store=portfolio_store,
        reservation_store=reservation_store,
        risk_telemetry_store=risk_telemetry,
    )
    reconciliation = ReconciliationEngine(
        broker=broker,
        oms=oms,
        portfolio_store=portfolio_store,
        reservation_store=reservation_store,
        ledger=ledger,
    )

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
        fill_store=fill_store,
        reservation_store=reservation_store,
        risk_telemetry=risk_telemetry,
        pipeline=pipeline,
        reconciliation=reconciliation,
        startup_reconciliation=startup_reconciliation,
    )
