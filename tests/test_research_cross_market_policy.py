from __future__ import annotations

from dataclasses import replace

import pytest

from autotrade.research.cross_market_policy import (
    CrossMarketBreadthPolicy,
    CrossMarketObservation,
    DEFAULT_EXPERIMENT_C_BREADTH_POLICY,
    evaluate_cross_market_breadth,
)


MARKETS = (
    ("BTCUSDT", "1h"),
    ("BTCUSDT", "4h"),
    ("ETHUSDT", "1h"),
    ("ETHUSDT", "4h"),
    ("SOLUSDT", "1h"),
    ("SOLUSDT", "4h"),
)


def _observations() -> tuple[CrossMarketObservation, ...]:
    values = (
        (0.03, 1.2, 0.04, True, True),
        (0.02, 0.8, 0.05, True, False),
        (0.01, 0.6, 0.03, True, True),
        (0.005, 0.4, 0.02, False, False),
        (-0.01, -0.2, 0.06, False, False),
        (-0.02, -0.3, 0.07, False, False),
    )
    return tuple(
        CrossMarketObservation(
            market=f"{symbol}:{interval}",
            symbol=symbol,
            interval=interval,
            net_return=net_return,
            sharpe=sharpe,
            max_drawdown=drawdown,
            policy_eligible=eligible,
            robustness_passed=robust,
        )
        for (symbol, interval), (net_return, sharpe, drawdown, eligible, robust) in zip(
            MARKETS, values, strict=True
        )
    )


def test_default_policy_is_frozen_and_reasonable() -> None:
    policy = DEFAULT_EXPERIMENT_C_BREADTH_POLICY
    assert policy.min_positive_return_markets == 4
    assert policy.min_positive_sharpe_markets == 4
    assert policy.min_policy_eligible_markets == 3
    assert policy.min_robust_markets == 2
    assert policy.min_distinct_robust_symbols == 2
    assert policy.min_median_sharpe == 0.25
    assert policy.min_worst_net_return == -0.05
    assert policy.max_drawdown_across_markets == 0.25
    assert len(policy.fingerprint) == 64


def test_breadth_policy_passes_only_full_six_market_matrix() -> None:
    evidence = evaluate_cross_market_breadth(
        hypothesis_id="c-test",
        observations=_observations(),
        policy=DEFAULT_EXPERIMENT_C_BREADTH_POLICY,
    )
    assert evidence.passed is True
    assert evidence.reasons == ()
    assert evidence.positive_return_markets == 4
    assert evidence.positive_sharpe_markets == 4
    assert evidence.policy_eligible_markets == 3
    assert evidence.robust_markets == 2
    assert evidence.distinct_robust_symbols == 2
    assert evidence.median_sharpe == pytest.approx(0.5)
    assert evidence.worst_net_return == pytest.approx(-0.02)


def test_breadth_policy_rejects_single_asset_robustness() -> None:
    observations = list(_observations())
    observations[2] = replace(observations[2], robustness_passed=False)
    observations[1] = replace(observations[1], robustness_passed=True)
    evidence = evaluate_cross_market_breadth(
        hypothesis_id="c-single-asset",
        observations=tuple(observations),
        policy=DEFAULT_EXPERIMENT_C_BREADTH_POLICY,
    )
    assert evidence.passed is False
    assert evidence.robust_markets == 2
    assert evidence.distinct_robust_symbols == 1
    assert "INSUFFICIENT_DISTINCT_ROBUST_SYMBOLS" in evidence.reasons


def test_breadth_policy_rejects_narrow_positive_performance() -> None:
    observations = tuple(
        replace(item, net_return=-0.01, sharpe=-0.1)
        if item.symbol != "BTCUSDT"
        else item
        for item in _observations()
    )
    evidence = evaluate_cross_market_breadth(
        hypothesis_id="c-narrow",
        observations=observations,
        policy=DEFAULT_EXPERIMENT_C_BREADTH_POLICY,
    )
    assert evidence.passed is False
    assert "INSUFFICIENT_POSITIVE_RETURN_MARKETS" in evidence.reasons
    assert "INSUFFICIENT_POSITIVE_SHARPE_MARKETS" in evidence.reasons
    assert "MEDIAN_SHARPE_BELOW_MINIMUM" in evidence.reasons


def test_breadth_policy_rejects_large_cross_market_loss() -> None:
    observations = list(_observations())
    observations[-1] = replace(observations[-1], net_return=-0.08)
    evidence = evaluate_cross_market_breadth(
        hypothesis_id="c-tail-loss",
        observations=tuple(observations),
        policy=DEFAULT_EXPERIMENT_C_BREADTH_POLICY,
    )
    assert evidence.passed is False
    assert "WORST_NET_RETURN_BELOW_MINIMUM" in evidence.reasons


def test_breadth_policy_requires_exact_frozen_matrix() -> None:
    observations = _observations()
    with pytest.raises(ValueError, match="exactly six"):
        evaluate_cross_market_breadth(
            hypothesis_id="c-missing",
            observations=observations[:-1],
            policy=DEFAULT_EXPERIMENT_C_BREADTH_POLICY,
        )

    duplicate = observations[:-1] + (observations[0],)
    with pytest.raises(ValueError, match="frozen 3x2"):
        evaluate_cross_market_breadth(
            hypothesis_id="c-duplicate",
            observations=duplicate,
            policy=DEFAULT_EXPERIMENT_C_BREADTH_POLICY,
        )


def test_policy_validation_is_fail_closed() -> None:
    with pytest.raises(ValueError, match="min_distinct_robust_symbols"):
        CrossMarketBreadthPolicy(min_distinct_robust_symbols=4)
    with pytest.raises(ValueError, match="max_drawdown"):
        CrossMarketBreadthPolicy(max_drawdown_across_markets=2.0)
