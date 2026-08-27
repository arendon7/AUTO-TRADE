from __future__ import annotations

import autotrade.brokers.alpaca_paper_crypto_canary_coordinator as r6_coordinator
from autotrade.paper_execution_admission import PaperExecutionAdmissionReceipt
from autotrade.paper_execution_canary_preparation import (
    PaperExecutionCanaryPreparationBlocked,
    PaperExecutionCanaryPreparationResult,
    prepare_paper_execution_canary,
)
from autotrade.paper_execution_risk_contract import PaperExecutionRiskContractResult
from autotrade.paper_runtime_readiness_seal import PaperRuntimeReadinessSealedResult
from autotrade.persistence import SQLiteRuntime
from autotrade.portfolio_integrity import portfolio_snapshot_error
from autotrade.safety import CapitalSafetyKernel
from autotrade.state import VersionedPortfolioSnapshot
from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import CryptoPaperCanaryCoordinator


class PaperExecutionCanaryPreparationGuardBlocked(PaperExecutionCanaryPreparationBlocked):
    pass


def prepare_guarded_paper_execution_canary(
    *,
    bridge_id: str,
    admission: PaperExecutionAdmissionReceipt,
    sealed_result: PaperRuntimeReadinessSealedResult,
    risk_result: PaperExecutionRiskContractResult,
    safety: CapitalSafetyKernel,
    portfolio_store,
    coordinator: CryptoPaperCanaryCoordinator,
    runtime: SQLiteRuntime,
) -> PaperExecutionCanaryPreparationResult:
    """Last local-only W87 guard before the certified R6 preparation coordinator.

    The exact Safety control-plane version and exact durable portfolio snapshot
    used by W87-B must still be current immediately before preparation and must
    remain unchanged until the R6 coordinator has stopped at OMS VALIDATED /
    ENTRY_PREPARED. The exact W87 notional must also fit the already-certified
    R6 first-canary account cap *before* OMS/lifecycle mutation. A race may leave
    only local PREPARED evidence; it never grants POST, capital, operator approval
    or LIVE authority.
    """

    if not isinstance(safety, CapitalSafetyKernel):
        raise TypeError("safety must be authoritative CapitalSafetyKernel")
    if not hasattr(portfolio_store, "get") or not callable(portfolio_store.get):
        raise TypeError("portfolio_store must expose get()")

    before_safety = safety.state_store.get()
    before_portfolio = portfolio_store.get()
    _validate_guard_state(
        safety_state=before_safety,
        portfolio=before_portfolio,
        risk_result=risk_result,
    )
    _require_r6_first_canary_capacity(
        admission=admission,
        sealed_result=sealed_result,
        risk_result=risk_result,
    )

    result = prepare_paper_execution_canary(
        bridge_id=bridge_id,
        admission=admission,
        sealed_result=sealed_result,
        risk_result=risk_result,
        coordinator=coordinator,
        runtime=runtime,
    )

    after_safety = safety.state_store.get()
    after_portfolio = portfolio_store.get()
    if after_safety != before_safety:
        raise PaperExecutionCanaryPreparationGuardBlocked(
            "Safety control state changed during W87 canary preparation"
        )
    if after_portfolio != before_portfolio:
        raise PaperExecutionCanaryPreparationGuardBlocked(
            "portfolio state changed during W87 canary preparation"
        )
    _validate_guard_state(
        safety_state=after_safety,
        portfolio=after_portfolio,
        risk_result=risk_result,
    )
    return result


def _require_r6_first_canary_capacity(
    *,
    admission: PaperExecutionAdmissionReceipt,
    sealed_result: PaperRuntimeReadinessSealedResult,
    risk_result: PaperExecutionRiskContractResult,
) -> None:
    account = sealed_result.pipeline.account_attestation
    approved = risk_result.receipt.approved_notional_usd
    if approved != admission.canary_notional_usd:
        raise PaperExecutionCanaryPreparationGuardBlocked(
            "W87 admitted and Safety-approved notionals differ before R6 preparation"
        )
    effective_cap = min(
        r6_coordinator.FIRST_CANARY_MAX_NOTIONAL,
        account.portfolio_value * r6_coordinator.FIRST_CANARY_MAX_ACCOUNT_FRACTION,
        account.buying_power,
    )
    if effective_cap <= 0 or approved > effective_cap:
        raise PaperExecutionCanaryPreparationGuardBlocked(
            "W87 canary exceeds existing R6 first-canary conservative cap before OMS/lifecycle"
        )


def _validate_guard_state(*, safety_state, portfolio, risk_result: PaperExecutionRiskContractResult) -> None:
    receipt = risk_result.receipt
    if (
        safety_state.version != receipt.safety_state_version
        or safety_state.kill_switch_active is not False
        or safety_state.circuit_active is not False
    ):
        raise PaperExecutionCanaryPreparationGuardBlocked(
            "authoritative Safety state no longer matches W87 risk contract"
        )
    if not isinstance(portfolio, VersionedPortfolioSnapshot):
        raise PaperExecutionCanaryPreparationGuardBlocked(
            "authoritative portfolio reader returned non-versioned state"
        )
    if (
        portfolio.version != receipt.portfolio_version
        or portfolio.snapshot.snapshot_id != receipt.portfolio_snapshot_id
    ):
        raise PaperExecutionCanaryPreparationGuardBlocked(
            "authoritative portfolio no longer matches W87 risk contract"
        )
    error = portfolio_snapshot_error(portfolio.snapshot)
    if error is not None:
        raise PaperExecutionCanaryPreparationGuardBlocked(
            f"authoritative portfolio integrity failed: {error}"
        )
    snapshot = portfolio.snapshot
    if (
        snapshot.reconciliation_ok is not True
        or snapshot.broker_state_known is not True
        or snapshot.open_orders != 0
        or snapshot.gross_exposure != 0
        or snapshot.net_exposure != 0
        or any(value != 0 for value in snapshot.signed_position_notional_by_symbol.values())
        or any(value != 0 for value in snapshot.strategy_gross_exposure.values())
        or any(
            value != 0
            for values in snapshot.strategy_signed_position_notional_by_symbol.values()
            for value in values.values()
        )
    ):
        raise PaperExecutionCanaryPreparationGuardBlocked(
            "first W87 canary requires exact flat reconciled durable portfolio"
        )


__all__ = [
    "PaperExecutionCanaryPreparationGuardBlocked",
    "prepare_guarded_paper_execution_canary",
]
