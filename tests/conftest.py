from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest

from autotrade.domain import MarketSnapshot, OrderIntent, OrderType, PortfolioSnapshot, Side
from autotrade.safety import SafetyLimits


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


_W86_SAFETY_HEALTH_TEST = "test_w86_paper_runtime_safety_health_truth.py"


def pytest_collection_modifyitems(items) -> None:
    """Attach nested-workspace setup only to the W86 Safety/Health test module."""
    for item in items:
        path = Path(str(item.path))
        if path.name == _W86_SAFETY_HEALTH_TEST:
            item.add_marker(pytest.mark.usefixtures("_w86_safety_health_nested_workspaces"))


@pytest.fixture
def _w86_safety_health_nested_workspaces(tmp_path: Path) -> None:
    """Precreate the two explicitly nested SQLite workspaces used by W86 tests."""
    (tmp_path / "second").mkdir(parents=True, exist_ok=True)
    (tmp_path / "good").mkdir(parents=True, exist_ok=True)


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 10, 22, 30, tzinfo=timezone.utc)


@pytest.fixture
def limits() -> SafetyLimits:
    return SafetyLimits(
        limits_version="test-v1",
        allowed_symbols=frozenset({"TEST-USD"}),
        allowed_order_types=frozenset({OrderType.MARKET, OrderType.LIMIT}),
        max_order_notional=Decimal("10000"),
        max_position_notional=Decimal("20000"),
        max_strategy_gross_exposure=Decimal("25000"),
        max_portfolio_gross_exposure=Decimal("50000"),
        max_net_exposure=Decimal("30000"),
        max_leverage=Decimal("2"),
        max_daily_loss=Decimal("1000"),
        max_drawdown=Decimal("0.10"),
        max_open_orders=10,
        stale_market_data_ms=1000,
        price_deviation_bps=Decimal("100"),
        decision_ttl_ms=500,
    )


@pytest.fixture
def market(now: datetime) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="TEST-USD",
        bid=Decimal("99"),
        ask=Decimal("101"),
        last=Decimal("100"),
        observed_at=now,
    )


@pytest.fixture
def empty_portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id="portfolio-v1",
        equity=Decimal("100000"),
        gross_exposure=Decimal("0"),
        net_exposure=Decimal("0"),
        daily_pnl=Decimal("0"),
        drawdown=Decimal("0"),
        open_orders=0,
        signed_position_notional_by_symbol={},
        strategy_gross_exposure={},
        strategy_signed_position_notional_by_symbol={},
        reconciliation_ok=True,
        broker_state_known=True,
    )


@pytest.fixture
def market_buy_intent(now: datetime) -> OrderIntent:
    return OrderIntent(
        intent_id="intent-1",
        idempotency_key="idem-1",
        strategy_id="strategy-a",
        symbol="TEST-USD",
        side=Side.BUY,
        quantity=Decimal("10"),
        order_type=OrderType.MARKET,
        created_at=now,
    )
