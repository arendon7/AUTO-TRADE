from __future__ import annotations

from decimal import Decimal
from typing import Mapping

from .domain import PortfolioSnapshot


class PortfolioIntegrityError(RuntimeError):
    pass


def portfolio_snapshot_error(portfolio: PortfolioSnapshot) -> str | None:
    """Return a deterministic semantic-integrity error, or None.

    This validator deliberately contains no trading policy limits. It answers a
    narrower question shared by persistence and Safety: is this snapshot
    internally self-consistent enough to be trusted as state input?
    """

    if not isinstance(portfolio, PortfolioSnapshot):
        return "portfolio must be PortfolioSnapshot"
    if not isinstance(portfolio.snapshot_id, str) or not portfolio.snapshot_id.strip():
        return "snapshot_id is required"
    if portfolio.snapshot_id != portfolio.snapshot_id.strip():
        return "snapshot_id must not contain surrounding whitespace"

    numeric = {
        "equity": portfolio.equity,
        "gross_exposure": portfolio.gross_exposure,
        "net_exposure": portfolio.net_exposure,
        "daily_pnl": portfolio.daily_pnl,
        "drawdown": portfolio.drawdown,
    }
    for name, value in numeric.items():
        if not _finite_decimal(value):
            return f"{name} is not finite"
    if portfolio.equity <= 0:
        return "equity must be > 0"
    if portfolio.gross_exposure < 0:
        return "gross_exposure cannot be negative"
    if portfolio.drawdown < 0:
        return "drawdown cannot be negative"
    if isinstance(portfolio.open_orders, bool) or not isinstance(portfolio.open_orders, int):
        return "open_orders must be integer"
    if portfolio.open_orders < 0:
        return "open_orders cannot be negative"
    if not isinstance(portfolio.reconciliation_ok, bool):
        return "reconciliation_ok must be boolean"
    if not isinstance(portfolio.broker_state_known, bool):
        return "broker_state_known must be boolean"

    aggregate_positions = _mapping(portfolio.signed_position_notional_by_symbol)
    if aggregate_positions is None:
        return "signed_position_notional_by_symbol must be mapping"
    zero = Decimal("0")
    calculated_gross = zero
    calculated_net = zero
    for symbol, value in aggregate_positions.items():
        identity_error = _identity_error(symbol, "position symbol")
        if identity_error:
            return identity_error
        if not _finite_decimal(value):
            return f"position {symbol} is not finite"
        calculated_gross += abs(value)
        calculated_net += value
    if calculated_gross != portfolio.gross_exposure:
        return (
            "gross_exposure does not match position map: "
            f"declared={portfolio.gross_exposure},calculated={calculated_gross}"
        )
    if calculated_net != portfolio.net_exposure:
        return (
            "net_exposure does not match position map: "
            f"declared={portfolio.net_exposure},calculated={calculated_net}"
        )

    strategy_positions = _mapping(portfolio.strategy_signed_position_notional_by_symbol)
    if strategy_positions is None:
        return "strategy_signed_position_notional_by_symbol must be mapping"
    declared_strategy_gross = _mapping(portfolio.strategy_gross_exposure)
    if declared_strategy_gross is None:
        return "strategy_gross_exposure must be mapping"

    aggregate_from_strategies: dict[str, Decimal] = {}
    for strategy, raw_values in strategy_positions.items():
        identity_error = _identity_error(strategy, "strategy id")
        if identity_error:
            return identity_error
        values = _mapping(raw_values)
        if values is None:
            return f"strategy {strategy} position map must be mapping"
        calculated = zero
        for symbol, value in values.items():
            identity_error = _identity_error(symbol, f"strategy {strategy} symbol")
            if identity_error:
                return identity_error
            if not _finite_decimal(value):
                return f"strategy {strategy}/{symbol} position is not finite"
            calculated += abs(value)
            aggregate_from_strategies[symbol] = aggregate_from_strategies.get(symbol, zero) + value
        declared = declared_strategy_gross.get(strategy)
        if declared is None:
            return f"strategy {strategy} is missing gross exposure"
        if not _finite_decimal(declared) or declared < 0:
            return f"strategy {strategy} gross exposure is invalid"
        if declared != calculated:
            return (
                f"strategy {strategy} gross exposure mismatch: "
                f"declared={declared},calculated={calculated}"
            )

    for strategy, declared in declared_strategy_gross.items():
        identity_error = _identity_error(strategy, "strategy id")
        if identity_error:
            return identity_error
        if not _finite_decimal(declared) or declared < 0:
            return f"strategy {strategy} gross exposure is invalid"
        if strategy not in strategy_positions and declared != 0:
            return f"strategy {strategy} gross exposure has no position map"

    aggregate_from_strategies = {
        symbol: value for symbol, value in aggregate_from_strategies.items() if value != zero
    }
    aggregate_nonzero = {
        symbol: value for symbol, value in aggregate_positions.items() if value != zero
    }
    if aggregate_from_strategies != aggregate_nonzero:
        return (
            "aggregate position map does not equal sum of strategy position maps: "
            f"aggregate={aggregate_nonzero},strategies={aggregate_from_strategies}"
        )
    return None


def validate_portfolio_snapshot(portfolio: PortfolioSnapshot) -> None:
    error = portfolio_snapshot_error(portfolio)
    if error is not None:
        raise PortfolioIntegrityError(error)


def _finite_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


def _mapping(value: object) -> Mapping | None:
    return value if isinstance(value, Mapping) else None


def _identity_error(value: object, label: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return f"{label} is empty"
    if value != value.strip():
        return f"{label} must not contain surrounding whitespace"
    return None
