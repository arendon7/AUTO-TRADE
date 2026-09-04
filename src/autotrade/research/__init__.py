"""Research-only components.

Nothing in this package is an executable authorization for the live control plane.
"""

from .backtest import BacktestConfig, BacktestEngine, BacktestResult
from .bootstrap import MovingBlockBootstrapConfig, MovingBlockBootstrapResult, moving_block_bootstrap
from .costs import ExecutionCostModel
from .cross_sectional import (
    AssetRanking,
    CrossSectionalMomentumConfig,
    CrossSectionalRankingEvidence,
    CrossSectionalResearchError,
    rank_cross_sectional_momentum,
)
from .dsl import (
    InvalidStrategySpec,
    MovingAverageCrossStrategy,
    SafeDeclarativeStrategy,
    StrategySpec,
)
from .gates import (
    RobustnessDecision,
    RobustnessPolicy,
    SampleAdequacyDecision,
    SampleAdequacyPolicy,
    evaluate_sample_adequacy,
    evaluate_walk_forward_robustness,
)
from .market import Bar, InstrumentMetadata, MarketDataset
from .oss_campaign import OSSCampaignPlan, build_oss1_development_campaign, oss1_candidate_count
from .strategy import ResearchSignal, ResearchStrategy
from .universe import AlignedMarketUniverse, InvalidAlignedUniverse
from .validation import SQLiteValidationRegistry, ValidationEvidence, ValidationEvidenceSpec

__all__ = [
    "AlignedMarketUniverse",
    "AssetRanking",
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "Bar",
    "CrossSectionalMomentumConfig",
    "CrossSectionalRankingEvidence",
    "CrossSectionalResearchError",
    "ExecutionCostModel",
    "InstrumentMetadata",
    "InvalidAlignedUniverse",
    "InvalidStrategySpec",
    "MarketDataset",
    "MovingAverageCrossStrategy",
    "MovingBlockBootstrapConfig",
    "MovingBlockBootstrapResult",
    "OSSCampaignPlan",
    "ResearchSignal",
    "ResearchStrategy",
    "RobustnessDecision",
    "RobustnessPolicy",
    "SafeDeclarativeStrategy",
    "SampleAdequacyDecision",
    "SampleAdequacyPolicy",
    "SQLiteValidationRegistry",
    "StrategySpec",
    "ValidationEvidence",
    "ValidationEvidenceSpec",
    "build_oss1_development_campaign",
    "evaluate_sample_adequacy",
    "evaluate_walk_forward_robustness",
    "moving_block_bootstrap",
    "oss1_candidate_count",
    "rank_cross_sectional_momentum",
]
