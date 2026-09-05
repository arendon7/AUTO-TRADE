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
from .cross_sectional_backtest import (
    CrossSectionalBacktestConfig,
    CrossSectionalBacktestEngine,
    CrossSectionalBacktestMetrics,
    CrossSectionalBacktestResult,
    CrossSectionalEquityPoint,
    CrossSectionalResearchFill,
    InvalidCrossSectionalBacktestConfig,
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
from .oss2_campaign import (
    CommonWindowMetricsEvidence,
    OSS2CampaignPlan,
    backtest_config_from_oss2_trial,
    build_oss2_development_campaign,
    evaluate_oss2_common_window,
    oss2_candidate_count,
)
from .oss2_holdout_eligibility import (
    OSS2HoldoutEligibilityDecision,
    OSS2HoldoutEligibilityEvidence,
    OSS2HoldoutEligibilityGate,
    OSS2HoldoutEligibilityGovernanceError,
    OSS2HoldoutEligibilityPolicy,
    canonical_oss2e_policy,
    evaluate_oss2e_holdout_eligibility,
)
from .oss2_holdout_freeze import (
    OSS2F_CONTRACT_VERSION,
    OSS2HoldoutFreezeConflict,
    OSS2HoldoutFreezeError,
    OSS2HoldoutFreezeIntegrityError,
    OSS2HoldoutFreezeReceipt,
    OSS2HoldoutFreezeState,
    SQLiteOSS2HoldoutFreezeRegistry,
    read_oss2f_freeze_read_only,
)
from .oss2_robustness import (
    OSS2BootstrapEvidence,
    OSS2CostStressEvidence,
    OSS2LocalNeighbor,
    OSS2LocalSensitivityEvidence,
    OSS2RobustnessEvidence,
    OSS2RobustnessGovernanceError,
    OSS2RobustnessPolicy,
    canonical_oss2d_policy,
    run_oss2d_robustness,
)
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
    "CommonWindowMetricsEvidence",
    "CrossSectionalBacktestConfig",
    "CrossSectionalBacktestEngine",
    "CrossSectionalBacktestMetrics",
    "CrossSectionalBacktestResult",
    "CrossSectionalEquityPoint",
    "CrossSectionalMomentumConfig",
    "CrossSectionalRankingEvidence",
    "CrossSectionalResearchError",
    "CrossSectionalResearchFill",
    "ExecutionCostModel",
    "InstrumentMetadata",
    "InvalidAlignedUniverse",
    "InvalidCrossSectionalBacktestConfig",
    "InvalidStrategySpec",
    "MarketDataset",
    "MovingAverageCrossStrategy",
    "MovingBlockBootstrapConfig",
    "MovingBlockBootstrapResult",
    "OSS2BootstrapEvidence",
    "OSS2CampaignPlan",
    "OSS2CostStressEvidence",
    "OSS2F_CONTRACT_VERSION",
    "OSS2HoldoutEligibilityDecision",
    "OSS2HoldoutEligibilityEvidence",
    "OSS2HoldoutEligibilityGate",
    "OSS2HoldoutEligibilityGovernanceError",
    "OSS2HoldoutEligibilityPolicy",
    "OSS2HoldoutFreezeConflict",
    "OSS2HoldoutFreezeError",
    "OSS2HoldoutFreezeIntegrityError",
    "OSS2HoldoutFreezeReceipt",
    "OSS2HoldoutFreezeState",
    "OSS2LocalNeighbor",
    "OSS2LocalSensitivityEvidence",
    "OSS2RobustnessEvidence",
    "OSS2RobustnessGovernanceError",
    "OSS2RobustnessPolicy",
    "OSSCampaignPlan",
    "ResearchSignal",
    "ResearchStrategy",
    "RobustnessDecision",
    "RobustnessPolicy",
    "SafeDeclarativeStrategy",
    "SampleAdequacyDecision",
    "SampleAdequacyPolicy",
    "SQLiteOSS2HoldoutFreezeRegistry",
    "SQLiteValidationRegistry",
    "StrategySpec",
    "ValidationEvidence",
    "ValidationEvidenceSpec",
    "backtest_config_from_oss2_trial",
    "build_oss1_development_campaign",
    "build_oss2_development_campaign",
    "canonical_oss2d_policy",
    "canonical_oss2e_policy",
    "evaluate_oss2_common_window",
    "evaluate_oss2e_holdout_eligibility",
    "evaluate_sample_adequacy",
    "evaluate_walk_forward_robustness",
    "moving_block_bootstrap",
    "oss1_candidate_count",
    "oss2_candidate_count",
    "rank_cross_sectional_momentum",
    "read_oss2f_freeze_read_only",
    "run_oss2d_robustness",
]