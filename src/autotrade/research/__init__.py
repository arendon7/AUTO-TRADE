"""Research-only components.

Nothing in this package is an executable authorization for the live control plane.
"""

from .backtest import BacktestConfig, BacktestEngine, BacktestResult
from .bootstrap import MovingBlockBootstrapConfig, MovingBlockBootstrapResult, moving_block_bootstrap
from .costs import ExecutionCostModel
from .dsl import InvalidStrategySpec, MovingAverageCrossStrategy, StrategySpec
from .gates import (
    RobustnessDecision,
    RobustnessPolicy,
    SampleAdequacyDecision,
    SampleAdequacyPolicy,
    evaluate_sample_adequacy,
    evaluate_walk_forward_robustness,
)
from .market import Bar, InstrumentMetadata, MarketDataset
from .strategy import ResearchSignal, ResearchStrategy
from .validation import SQLiteValidationRegistry, ValidationEvidence, ValidationEvidenceSpec

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "Bar",
    "ExecutionCostModel",
    "InstrumentMetadata",
    "InvalidStrategySpec",
    "MarketDataset",
    "MovingAverageCrossStrategy",
    "MovingBlockBootstrapConfig",
    "MovingBlockBootstrapResult",
    "ResearchSignal",
    "ResearchStrategy",
    "RobustnessDecision",
    "RobustnessPolicy",
    "SampleAdequacyDecision",
    "SampleAdequacyPolicy",
    "SQLiteValidationRegistry",
    "StrategySpec",
    "ValidationEvidence",
    "ValidationEvidenceSpec",
    "evaluate_sample_adequacy",
    "evaluate_walk_forward_robustness",
    "moving_block_bootstrap",
]
