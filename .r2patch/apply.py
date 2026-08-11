from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]

# Full reviewed replacements staged separately to avoid stale-content writes.
shutil.copy2(ROOT / ".r2patch" / "reconciliation.py", ROOT / "src" / "autotrade" / "reconciliation.py")
shutil.copy2(ROOT / ".r2patch" / "test_r2_risk_matrix.py", ROOT / "tests" / "test_r2_risk_matrix.py")

safety_path = ROOT / "src" / "autotrade" / "safety.py"
safety = safety_path.read_text(encoding="utf-8")
marker = "def _validate_portfolio(portfolio: PortfolioSnapshot) -> str | None:\n"
if marker not in safety:
    raise SystemExit("safety portfolio validation marker not found")
prefix, _ = safety.split(marker, 1)
replacement = '''def _validate_portfolio(portfolio: PortfolioSnapshot) -> str | None:
    numeric = {
        "equity": portfolio.equity,
        "gross_exposure": portfolio.gross_exposure,
        "net_exposure": portfolio.net_exposure,
        "daily_pnl": portfolio.daily_pnl,
        "drawdown": portfolio.drawdown,
    }
    for name, value in numeric.items():
        if not _finite(value):
            return f"{name} is not finite"
    if portfolio.equity <= 0:
        return "equity must be > 0"
    if portfolio.gross_exposure < 0:
        return "gross_exposure cannot be negative"
    if portfolio.drawdown < 0:
        return "drawdown cannot be negative"
    if portfolio.open_orders < 0:
        return "open_orders cannot be negative"

    zero = Decimal("0")
    aggregate_positions = dict(portfolio.signed_position_notional_by_symbol)
    for symbol, value in aggregate_positions.items():
        if not symbol.strip():
            return "position symbol is empty"
        if not _finite(value):
            return f"position {symbol} is not finite"
    calculated_gross = sum((abs(value) for value in aggregate_positions.values()), start=zero)
    calculated_net = sum(aggregate_positions.values(), start=zero)
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

    strategy_positions = portfolio.strategy_signed_position_notional_by_symbol
    for strategy, values in strategy_positions.items():
        if not strategy.strip():
            return "strategy id is empty"
        calculated = zero
        for symbol, value in values.items():
            if not symbol.strip():
                return f"strategy {strategy} contains empty symbol"
            if not _finite(value):
                return f"strategy {strategy}/{symbol} position is not finite"
            calculated += abs(value)
        declared = portfolio.strategy_gross_exposure.get(strategy)
        if declared is None:
            return f"strategy {strategy} is missing gross exposure"
        if not _finite(declared) or declared < 0:
            return f"strategy {strategy} gross exposure is invalid"
        if declared != calculated:
            return (
                f"strategy {strategy} gross exposure mismatch: "
                f"declared={declared},calculated={calculated}"
            )

    for strategy, declared in portfolio.strategy_gross_exposure.items():
        if not _finite(declared) or declared < 0:
            return f"strategy {strategy} gross exposure is invalid"
        if strategy not in strategy_positions and declared != 0:
            return f"strategy {strategy} gross exposure has no position map"
    return None
'''
safety_path.write_text(prefix + replacement, encoding="utf-8")

print("R2 staged patch applied")
