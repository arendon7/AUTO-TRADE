from dataclasses import replace
from decimal import Decimal

import pytest

from autotrade.research.multiple_testing import DeflatedSharpeEvidence, PBOEvidence
from autotrade.research.oss2_robustness import (
    OSS2BootstrapEvidence,
    OSS2CostStressEvidence,
    OSS2LocalNeighbor,
    OSS2LocalSensitivityEvidence,
    OSS2RobustnessEvidence,
    OSS2RobustnessPolicy,
)


D = Decimal
SHA = "a" * 64


def valid_bootstrap():
    return OSS2BootstrapEvidence(
        observations=64,
        iterations=2000,
        block_size=4,
        seed=20260904,
        mean_compounded_return=0.10,
        median_compounded_return=0.09,
        lower_compounded_return=-0.02,
        upper_compounded_return=0.25,
        probability_positive=0.70,
        distribution_hash=SHA,
    )


def valid_cost(multiplier=D("1.5")):
    return OSS2CostStressEvidence(
        multiplier=multiplier,
        total_cost_bps=D("6"),
        config_hash=SHA,
        result_hash=SHA,
        common_window_net_return=0.05,
        common_window_sharpe=1.1,
        common_window_max_drawdown=0.12,
        sharpe_delta_vs_baseline=-0.2,
        net_return_delta_vs_baseline=-0.01,
    )


def valid_neighbors():
    return (
        OSS2LocalNeighbor("n1", 24, 4, 0.9),
        OSS2LocalNeighbor("n2", 48, 4, 1.0),
    )


def valid_local():
    return OSS2LocalSensitivityEvidence(
        selected_lookback_bars=48,
        selected_rebalance_every_bars=4,
        selected_sharpe=1.2,
        neighbors=valid_neighbors(),
        neighbor_median_sharpe=0.95,
        selected_minus_neighbor_median=0.25,
        fraction_selected_at_least_neighbor=1.0,
    )


def valid_pbo(campaign_id="c"):
    return PBOEvidence(
        campaign_id=campaign_id,
        partitions=8,
        combinations_evaluated=70,
        pbo=0.25,
        logits=(0.1,),
        partition_sizes=(8,) * 8,
        balanced_partitions=True,
    )


def valid_dsr(campaign_id="c", selected="winner"):
    return DeflatedSharpeEvidence(
        campaign_id=campaign_id,
        selected_trial_id=selected,
        selected_sharpe=1.2,
        expected_max_sharpe=0.7,
        deflated_sharpe_probability=0.95,
        family_size=12,
        sample_size=64,
    )


def valid_evidence():
    return OSS2RobustnessEvidence(
        campaign_id="c",
        universe_hash=SHA,
        policy_fingerprint=SHA,
        tournament_fingerprint=SHA,
        selected_trial_id="winner",
        selected_common_window_evidence_hash=SHA,
        result_universe_hash=SHA,
        pbo=valid_pbo(),
        deflated_sharpe=valid_dsr(),
        bootstrap=valid_bootstrap(),
        cost_stress=(valid_cost(D("1.5")), valid_cost(D("2.0"))),
        local_sensitivity=valid_local(),
    )


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"pbo_partitions": 3}, "even integer"),
        ({"pbo_partitions": 5}, "even integer"),
        ({"pbo_balanced_partitions": False}, "balanced PBO"),
        ({"bootstrap_iterations": 0}, "bootstrap policy"),
        ({"bootstrap_block_size": 0}, "bootstrap policy"),
        ({"bootstrap_seed": True}, "seed must be int"),
        ({"cost_stress_multipliers": ()}, "cannot be empty"),
        ({"cost_stress_multipliers": (D("1"),)}, "finite Decimal > 1"),
        ({"cost_stress_multipliers": (D("2"), D("1.5"))}, "unique sorted"),
        ({"cost_stress_multipliers": (D("1.5"), D("1.5"))}, "unique sorted"),
    ],
)
def test_policy_invalid_surfaces_fail_closed(kwargs, message):
    with pytest.raises(ValueError, match=message):
        OSS2RobustnessPolicy(**kwargs)


def test_policy_fingerprint_is_deterministic():
    first = OSS2RobustnessPolicy()
    second = OSS2RobustnessPolicy()
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"observations": 1}, "evidence counts"),
        ({"iterations": 0}, "evidence counts"),
        ({"block_size": 0}, "evidence counts"),
        ({"probability_positive": -0.1}, "probability_positive"),
        ({"probability_positive": 1.1}, "probability_positive"),
        ({"mean_compounded_return": float("inf")}, "summary must be finite"),
        ({"distribution_hash": "bad"}, "SHA-256"),
    ],
)
def test_bootstrap_evidence_rejects_invalid_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        replace(valid_bootstrap(), **kwargs)


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"multiplier": D("1")}, "multiplier must exceed"),
        ({"total_cost_bps": D("0")}, "total cost must be positive"),
        ({"config_hash": "bad"}, "SHA-256"),
        ({"result_hash": "bad"}, "SHA-256"),
        ({"common_window_sharpe": float("nan")}, "metrics must be finite"),
        ({"common_window_max_drawdown": -0.1}, "drawdown must be"),
        ({"common_window_max_drawdown": 1.1}, "drawdown must be"),
    ],
)
def test_cost_stress_evidence_rejects_invalid_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        replace(valid_cost(), **kwargs)


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"trial_id": " "}, "trial_id is required"),
        ({"lookback_bars": 0}, "grid coordinates"),
        ({"rebalance_every_bars": 0}, "grid coordinates"),
        ({"common_window_sharpe": float("inf")}, "Sharpe must be finite"),
    ],
)
def test_local_neighbor_rejects_invalid_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        replace(valid_neighbors()[0], **kwargs)


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"selected_lookback_bars": 0}, "grid coordinates"),
        ({"selected_rebalance_every_bars": 0}, "grid coordinates"),
        ({"neighbors": (valid_neighbors()[0],)}, "at least two neighbors"),
        ({"neighbors": tuple(reversed(valid_neighbors()))}, "canonical sorted order"),
        ({"neighbor_median_sharpe": float("nan")}, "metrics must be finite"),
        ({"fraction_selected_at_least_neighbor": -0.1}, "fraction must be"),
        ({"fraction_selected_at_least_neighbor": 1.1}, "fraction must be"),
    ],
)
def test_local_sensitivity_rejects_invalid_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        replace(valid_local(), **kwargs)


def test_robustness_evidence_fingerprint_is_deterministic():
    first = valid_evidence()
    second = valid_evidence()
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"campaign_id": " "}, "campaign_id is required"),
        ({"selected_trial_id": " "}, "selected_trial_id is required"),
        ({"universe_hash": "bad"}, "SHA-256"),
        ({"pbo": valid_pbo("other")}, "PBO campaign identity mismatch"),
        ({"deflated_sharpe": valid_dsr("other")}, "Deflated Sharpe campaign identity mismatch"),
        ({"deflated_sharpe": valid_dsr(selected="other")}, "winner identity mismatch"),
        ({"cost_stress": (valid_cost(D("1.5")),)}, "frozen OSS-2D policy"),
    ],
)
def test_robustness_evidence_identity_guards_fail_closed(kwargs, message):
    with pytest.raises(ValueError, match=message):
        replace(valid_evidence(), **kwargs)
