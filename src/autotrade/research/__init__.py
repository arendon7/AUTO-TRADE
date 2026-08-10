"""Research-only backtesting primitives.

Nothing in this package is an executable authorization for the live control plane.
"""

from .backtest import BacktestConfig, BacktestEngine, BacktestResult
from .costs import ExecutionCostModel
from .market import Bar, InstrumentMetadata, MarketDataset
from .strategy import ResearchSignal, ResearchStrategy

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "ExecutionCostModel",
    "Bar",
    "InstrumentMetadata",
    "MarketDataset",
    "ResearchSignal",
    "ResearchStrategy",
]
