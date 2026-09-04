from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite


_EXPECTED_MARKETS = frozenset(
    {
        "BTCUSDT:1h",
        "BTCUSDT:4h",
        "ETHUSDT:1h",
        "ETHUSDT:4h",
        "SOLUSDT:1h",
        "SOLUSDT:4h",
    }
)


def _hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class CrossMarketObservation:
    market: str
    symbol: str
    interval: str
    net_return: float
    sharpe: float
    max_drawdown: float
    policy_eligible: bool
    robustness_passed: bool

    def __post_init__(self) -> None:
        if not self.market.strip() or not self.symbol.strip() or not self.interval.strip():
            raise ValueError("market identity is required")
        if self.market != f"{self.symbol}:{self.interval}":
            raise ValueError("market must equal '<symbol>:<interval>'")
        if self.market not in _EXPECTED_MARKETS:
            raise ValueError(f"market is outside frozen Experiment C matrix: {self.market}")
        for name, value in (
            ("net_return", self.net_return),
            ("sharpe", self.sharpe),
            ("max_drawdown", self.max_drawdown),
        ):
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.max_drawdown < 0 or self.max_drawdown > 1:
            raise ValueError("max_drawdown must be in [0,1]")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "market": self.market,
            "symbol": self.symbol,
            "interval": self.interval,
            "net_return": self.net_return,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "policy_eligible": self.policy_eligible,
            "robustness_passed": self.robustness_passed,
        }


@dataclass(frozen=True, slots=True)
class CrossMarketBreadthPolicy:
    """Prospective Experiment C breadth gate frozen before C market results exist.

    This is a research prerequisite only. Passing it cannot authorize HOLDOUT,
    PAPER or LIVE. A concrete market candidate must still independently satisfy
    the existing per-campaign robustness, statistical and regime gates.
    """

    min_positive_return_markets: int = 4
    min_positive_sharpe_markets: int = 4
    min_policy_eligible_markets: int = 3
    min_robust_markets: int = 2
    min_distinct_robust_symbols: int = 2
    min_median_sharpe: float = 0.25
    min_worst_net_return: float = -0.05
    max_drawdown_across_markets: float = 0.25

    def __post_init__(self) -> None:
        for name, value in (
            ("min_positive_return_markets", self.min_positive_return_markets),
            ("min_positive_sharpe_markets", self.min_positive_sharpe_markets),
            ("min_policy_eligible_markets", self.min_policy_eligible_markets),
            ("min_robust_markets", self.min_robust_markets),
            ("min_distinct_robust_symbols", self.min_distinct_robust_symbols),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 6:
                raise ValueError(f"{name} must be an integer in [0,6]")
        if self.min_distinct_robust_symbols > 3:
            raise ValueError("min_distinct_robust_symbols cannot exceed 3")
        for name, value in (
            ("min_median_sharpe", self.min_median_sharpe),
            ("min_worst_net_return", self.min_worst_net_return),
            ("max_drawdown_across_markets", self.max_drawdown_across_markets),
        ):
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0 <= self.max_drawdown_across_markets <= 1:
            raise ValueError("max_drawdown_across_markets must be in [0,1]")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "min_positive_return_markets": self.min_positive_return_markets,
            "min_positive_sharpe_markets": self.min_positive_sharpe_markets,
            "min_policy_eligible_markets": self.min_policy_eligible_markets,
            "min_robust_markets": self.min_robust_markets,
            "min_distinct_robust_symbols": self.min_distinct_robust_symbols,
            "min_median_sharpe": self.min_median_sharpe,
            "min_worst_net_return": self.min_worst_net_return,
            "max_drawdown_across_markets": self.max_drawdown_across_markets,
        }

    @property
    def fingerprint(self) -> str:
        return _hash(self.payload)


@dataclass(frozen=True, slots=True)
class CrossMarketBreadthEvidence:
    hypothesis_id: str
    policy_fingerprint: str
    positive_return_markets: int
    positive_sharpe_markets: int
    policy_eligible_markets: int
    robust_markets: int
    distinct_robust_symbols: int
    median_sharpe: float
    worst_net_return: float
    max_drawdown_across_markets: float
    passed: bool
    reasons: tuple[str, ...]
    observations: tuple[CrossMarketObservation, ...]

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "hypothesis_id": self.hypothesis_id,
                "policy_fingerprint": self.policy_fingerprint,
                "positive_return_markets": self.positive_return_markets,
                "positive_sharpe_markets": self.positive_sharpe_markets,
                "policy_eligible_markets": self.policy_eligible_markets,
                "robust_markets": self.robust_markets,
                "distinct_robust_symbols": self.distinct_robust_symbols,
                "median_sharpe": self.median_sharpe,
                "worst_net_return": self.worst_net_return,
                "max_drawdown_across_markets": self.max_drawdown_across_markets,
                "passed": self.passed,
                "reasons": list(self.reasons),
                "observations": [item.payload for item in self.observations],
            }
        )


def evaluate_cross_market_breadth(
    *,
    hypothesis_id: str,
    observations: tuple[CrossMarketObservation, ...],
    policy: CrossMarketBreadthPolicy,
) -> CrossMarketBreadthEvidence:
    if not hypothesis_id.strip():
        raise ValueError("hypothesis_id is required")
    if len(observations) != 6:
        raise ValueError("Experiment C breadth evidence requires exactly six markets")
    by_market = {item.market: item for item in observations}
    if set(by_market) != _EXPECTED_MARKETS or len(by_market) != 6:
        raise ValueError("Experiment C breadth evidence requires the frozen 3x2 matrix")

    ordered = tuple(by_market[key] for key in sorted(by_market))
    returns = sorted(item.net_return for item in ordered)
    sharpes = sorted(item.sharpe for item in ordered)
    median_sharpe = (sharpes[2] + sharpes[3]) / 2
    positive_return_markets = sum(item.net_return > 0 for item in ordered)
    positive_sharpe_markets = sum(item.sharpe > 0 for item in ordered)
    policy_eligible_markets = sum(item.policy_eligible for item in ordered)
    robust = tuple(item for item in ordered if item.robustness_passed)
    robust_markets = len(robust)
    distinct_robust_symbols = len({item.symbol for item in robust})
    worst_net_return = returns[0]
    max_drawdown = max(item.max_drawdown for item in ordered)

    reasons: list[str] = []
    if positive_return_markets < policy.min_positive_return_markets:
        reasons.append("INSUFFICIENT_POSITIVE_RETURN_MARKETS")
    if positive_sharpe_markets < policy.min_positive_sharpe_markets:
        reasons.append("INSUFFICIENT_POSITIVE_SHARPE_MARKETS")
    if policy_eligible_markets < policy.min_policy_eligible_markets:
        reasons.append("INSUFFICIENT_POLICY_ELIGIBLE_MARKETS")
    if robust_markets < policy.min_robust_markets:
        reasons.append("INSUFFICIENT_ROBUST_MARKETS")
    if distinct_robust_symbols < policy.min_distinct_robust_symbols:
        reasons.append("INSUFFICIENT_DISTINCT_ROBUST_SYMBOLS")
    if median_sharpe < policy.min_median_sharpe:
        reasons.append("MEDIAN_SHARPE_BELOW_MINIMUM")
    if worst_net_return < policy.min_worst_net_return:
        reasons.append("WORST_NET_RETURN_BELOW_MINIMUM")
    if max_drawdown > policy.max_drawdown_across_markets:
        reasons.append("CROSS_MARKET_DRAWDOWN_ABOVE_MAXIMUM")

    return CrossMarketBreadthEvidence(
        hypothesis_id=hypothesis_id,
        policy_fingerprint=policy.fingerprint,
        positive_return_markets=positive_return_markets,
        positive_sharpe_markets=positive_sharpe_markets,
        policy_eligible_markets=policy_eligible_markets,
        robust_markets=robust_markets,
        distinct_robust_symbols=distinct_robust_symbols,
        median_sharpe=median_sharpe,
        worst_net_return=worst_net_return,
        max_drawdown_across_markets=max_drawdown,
        passed=not reasons,
        reasons=tuple(reasons),
        observations=ordered,
    )


DEFAULT_EXPERIMENT_C_BREADTH_POLICY = CrossMarketBreadthPolicy()


__all__ = [
    "CrossMarketBreadthEvidence",
    "CrossMarketBreadthPolicy",
    "CrossMarketObservation",
    "DEFAULT_EXPERIMENT_C_BREADTH_POLICY",
    "evaluate_cross_market_breadth",
]
