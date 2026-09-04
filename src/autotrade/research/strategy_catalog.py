"""Research-only strategy catalog with explicit provenance and authority boundaries.

This module intentionally contains no broker, OMS, credential, network or order-writing
surface. Entries describe hypotheses to be expressed through AUTO-TRADE's safe Strategy
DSL and existing preregistration/holdout/Shadow-Forward machinery.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StrategyFamily(str, Enum):
    TREND_FOLLOWING = "trend_following"
    TIME_SERIES_MOMENTUM = "time_series_momentum"
    CROSS_SECTIONAL_MOMENTUM = "cross_sectional_momentum"
    MEAN_REVERSION = "mean_reversion"
    BREAKOUT = "breakout"
    VOLATILITY_REGIME = "volatility_regime"
    STATISTICAL_ARBITRAGE = "statistical_arbitrage"
    MARKET_MAKING = "market_making"
    CARRY_BASIS = "carry_basis"
    ML_RANKING = "ml_ranking"


@dataclass(frozen=True, slots=True)
class StrategyCatalogEntry:
    strategy_id: str
    family: StrategyFamily
    hypothesis: str
    required_features: tuple[str, ...]
    preferred_timeframes: tuple[str, ...]
    primary_market: str
    implementation_mode: str
    source_projects: tuple[str, ...]
    source_licenses: tuple[str, ...]
    execution_authority: bool = False
    live_authority: bool = False


CATALOG: tuple[StrategyCatalogEntry, ...] = (
    StrategyCatalogEntry(
        strategy_id="trend_ema_atr_v1",
        family=StrategyFamily.TREND_FOLLOWING,
        hypothesis="Persistent directional moves can survive conservative costs when trend confirmation is combined with volatility-normalized sizing.",
        required_features=("ema_fast", "ema_slow", "atr", "realized_volatility"),
        preferred_timeframes=("1h", "4h", "1d"),
        primary_market="liquid_spot_crypto_and_equities",
        implementation_mode="native_safe_dsl",
        source_projects=("QuantConnect/Lean", "freqtrade/freqtrade"),
        source_licenses=("Apache-2.0", "GPL-3.0-reference-only"),
    ),
    StrategyCatalogEntry(
        strategy_id="ts_momentum_multi_horizon_v1",
        family=StrategyFamily.TIME_SERIES_MOMENTUM,
        hypothesis="Combining multiple return horizons can reduce dependence on one lookback and improve regime robustness.",
        required_features=("return_1", "return_5", "return_20", "realized_volatility"),
        preferred_timeframes=("1h", "4h", "1d"),
        primary_market="liquid_multi_asset",
        implementation_mode="native_safe_dsl",
        source_projects=("QuantConnect/Lean", "microsoft/qlib"),
        source_licenses=("Apache-2.0", "MIT"),
    ),
    StrategyCatalogEntry(
        strategy_id="cross_sectional_momentum_v1",
        family=StrategyFamily.CROSS_SECTIONAL_MOMENTUM,
        hypothesis="Relative-strength ranking across a liquid universe can diversify single-asset trend risk.",
        required_features=("ranked_returns", "liquidity", "volatility", "turnover"),
        preferred_timeframes=("4h", "1d"),
        primary_market="liquid_crypto_basket_or_equity_universe",
        implementation_mode="research_extension_required",
        source_projects=("microsoft/qlib", "QuantConnect/Lean"),
        source_licenses=("MIT", "Apache-2.0"),
    ),
    StrategyCatalogEntry(
        strategy_id="mean_reversion_zscore_v1",
        family=StrategyFamily.MEAN_REVERSION,
        hypothesis="Short-horizon dislocations may mean-revert when conditioned on volatility and spread/liquidity filters.",
        required_features=("rolling_mean", "rolling_std", "zscore", "atr", "spread"),
        preferred_timeframes=("15m", "1h"),
        primary_market="high_liquidity_spot",
        implementation_mode="native_safe_dsl",
        source_projects=("QuantConnect/Lean", "freqtrade/freqtrade"),
        source_licenses=("Apache-2.0", "GPL-3.0-reference-only"),
    ),
    StrategyCatalogEntry(
        strategy_id="donchian_breakout_atr_v1",
        family=StrategyFamily.BREAKOUT,
        hypothesis="Price-channel breakouts with volatility filters can capture convex moves while limiting churn in quiet regimes.",
        required_features=("rolling_high", "rolling_low", "atr", "volume"),
        preferred_timeframes=("1h", "4h", "1d"),
        primary_market="liquid_spot_crypto_and_futures_research",
        implementation_mode="native_safe_dsl",
        source_projects=("QuantConnect/Lean", "nautechsystems/nautilus_trader"),
        source_licenses=("Apache-2.0", "LGPL-3.0-reference-only"),
    ),
    StrategyCatalogEntry(
        strategy_id="volatility_regime_switch_v1",
        family=StrategyFamily.VOLATILITY_REGIME,
        hypothesis="Strategy weights should change when realized volatility/trend state materially changes rather than forcing one edge across all regimes.",
        required_features=("realized_volatility", "atr", "trend_strength", "drawdown_state"),
        preferred_timeframes=("1h", "4h", "1d"),
        primary_market="multi_asset",
        implementation_mode="native_safe_dsl_single_symbol_regime_filter",
        source_projects=("microsoft/qlib", "QuantConnect/Lean"),
        source_licenses=("MIT", "Apache-2.0"),
    ),
    StrategyCatalogEntry(
        strategy_id="pairs_residual_reversion_v1",
        family=StrategyFamily.STATISTICAL_ARBITRAGE,
        hypothesis="Stable relative-value relationships may support market-neutral residual reversion after strict stationarity and cost checks.",
        required_features=("hedge_ratio", "residual", "zscore", "half_life", "spread"),
        preferred_timeframes=("1h", "4h", "1d"),
        primary_market="highly_liquid_correlated_pairs",
        implementation_mode="research_extension_required",
        source_projects=("microsoft/qlib", "goldmansachs/gs-quant"),
        source_licenses=("MIT", "Apache-2.0"),
    ),
    StrategyCatalogEntry(
        strategy_id="market_making_inventory_aware_v1",
        family=StrategyFamily.MARKET_MAKING,
        hypothesis="Two-sided quoting can be viable only when spread capture exceeds adverse selection, fees and inventory risk.",
        required_features=("orderbook", "microprice", "inventory", "spread", "realized_volatility"),
        preferred_timeframes=("tick", "1s"),
        primary_market="crypto_orderbook_research_only",
        implementation_mode="future_microstructure_lab",
        source_projects=("hummingbot/hummingbot", "nautechsystems/nautilus_trader"),
        source_licenses=("Apache-2.0", "LGPL-3.0-reference-only"),
    ),
    StrategyCatalogEntry(
        strategy_id="carry_basis_v1",
        family=StrategyFamily.CARRY_BASIS,
        hypothesis="Persistent funding/basis premia may offer diversified carry if liquidation, borrow, funding and venue risks are modeled explicitly.",
        required_features=("spot_price", "future_price", "funding_rate", "borrow_cost", "basis"),
        preferred_timeframes=("1h", "4h", "1d"),
        primary_market="crypto_derivatives_research_only",
        implementation_mode="future_derivatives_lab",
        source_projects=("hummingbot/hummingbot", "QuantConnect/Lean"),
        source_licenses=("Apache-2.0", "Apache-2.0"),
    ),
    StrategyCatalogEntry(
        strategy_id="ml_cross_sectional_rank_v1",
        family=StrategyFamily.ML_RANKING,
        hypothesis="Machine-learning models may improve ranking when trained on leakage-safe features and judged only through preregistered out-of-sample evidence.",
        required_features=("returns", "volatility", "volume", "liquidity", "technical_factors"),
        preferred_timeframes=("4h", "1d"),
        primary_market="multi_asset_research",
        implementation_mode="isolated_ml_research_adapter",
        source_projects=("microsoft/qlib", "Numerai"),
        source_licenses=("MIT", "external-platform-reference"),
    ),
)


def get_strategy(strategy_id: str) -> StrategyCatalogEntry:
    for entry in CATALOG:
        if entry.strategy_id == strategy_id:
            return entry
    raise KeyError(strategy_id)


def research_only_catalog() -> tuple[StrategyCatalogEntry, ...]:
    """Return entries only if all authority fields remain explicitly disabled."""
    if any(entry.execution_authority or entry.live_authority for entry in CATALOG):
        raise RuntimeError("strategy catalog authority escalation detected")
    return CATALOG
