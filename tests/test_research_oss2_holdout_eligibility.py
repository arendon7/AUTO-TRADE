from dataclasses import replace
from decimal import Decimal
from inspect import signature

import pytest

from autotrade.research.multiple_testing import DeflatedSharpeEvidence, PBOEvidence
from autotrade.research.oss2_holdout_eligibility import (
    OSS2HoldoutEligibilityDecision,
    OSS2HoldoutEligibilityGate,
    OSS2HoldoutEligibilityGovernanceError,
    canonical_oss2e_policy,
    evaluate_oss2e_holdout_eligibility,
)
from autotrade.research.oss2_robustness import (
    OSS2BootstrapEvidence,
    OSS2CostStressEvidence,
    OSS2LocalNeighbor,
    OSS2LocalSensitivityEvidence,
    OSS2RobustnessEvidence,
    canonical_oss2d_policy,
)


D = Decimal
H = "a" * 64
H2 = "b" * 64
H3 = "c" * 64
H4 = "d" * 64
H5 = "e" * 64


def passing_robustness() -> OSS2RobustnessEvidence:
    return OSS2RobustnessEvidence(
        campaign_id="oss2-campaign",
        universe_hash=H,
        policy_fingerprint=canonical_oss2d_policy().fingerprint,
        tournament_fingerprint=H2,
        selected_trial_id="trial-winner",
        selected_common_window_evidence_hash=H3,
        result_universe_hash=H4,
        pbo=PBOEvidence(
            campaign_id="oss2-campaign",
            partitions=8,
            combinations_evaluated=70,
            pbo=0.20,
            logits=(-1.0, 0.4),
            partition_sizes=(8, 8, 8, 8, 8, 8, 8, 8),
            balanced_partitions=True,
        ),
        deflated_sharpe=DeflatedSharpeEvidence(
            campaign_id="oss2-campaign",
            selected_trial_id="trial-winner",
            selected_sharpe=0.10,
            expected_max_sharpe=0.04,
            deflated_sharpe_probability=0.92,
            family_size=12,
            sample_size=64,
            metric_name="common_window_sharpe",
            metric_scale=1 / (365**0.5),
        ),
        bootstrap=OSS2BootstrapEvidence(
            observations=64,
            iterations=2000,
            block_size=4,
            seed=20260904,
            mean_compounded_return=0.13,
            median_compounded_return=0.12,
            lower_compounded_return=-0.05,
            upper_compounded_return=0.31,
            probability_positive=0.75,
            distribution_hash=H5,
        ),
        cost_stress=(
            OSS2CostStressEvidence(
                multiplier=D("1.5"),
                total_cost_bps=D("6"),
                config_hash=H,
                result_hash=H2,
                common_window_net_return=0.08,
                common_window_sharpe=0.70,
                common_window_max_drawdown=0.12,
                sharpe_delta_vs_baseline=-0.12,
                net_return_delta_vs_baseline=-0.03,
            ),
            OSS2CostStressEvidence(
                multiplier=D("2.0"),
                total_cost_bps=D("8"),
                config_hash=H3,
                result_hash=H4,
                common_window_net_return=0.03,
                common_window_sharpe=0.30,
                common_window_max_drawdown=0.18,
                sharpe_delta_vs_baseline=-0.52,
                net_return_delta_vs_baseline=-0.08,
            ),
        ),
        local_sensitivity=OSS2LocalSensitivityEvidence(
            selected_lookback_bars=30,
            selected_rebalance_every_bars=5,
            selected_sharpe=0.90,
            neighbors=(
                OSS2LocalNeighbor("trial-a", 20, 5, 0.50),
                OSS2LocalNeighbor("trial-b", 40, 5, 0.70),
                OSS2LocalNeighbor("trial-c", 30, 10, 0.60),
            ),
            neighbor_median_sharpe=0.60,
            selected_minus_neighbor_median=0.30,
            fraction_selected_at_least_neighbor=1.0,
        ),
    )


def test_oss2e_eligible_decision_freezes_exact_candidate():
    robustness = passing_robustness()
    evidence = evaluate_oss2e_holdout_eligibility(robustness)

    assert evidence.decision is OSS2HoldoutEligibilityDecision.HOLDOUT_ELIGIBLE
    assert evidence.failed_gate_ids == ()
    assert all(gate.passed for gate in evidence.gates)
    assert evidence.selected_trial_id == robustness.selected_trial_id
    assert evidence.oss2d_evidence_fingerprint == robustness.fingerprint
    assert evidence.policy_fingerprint == canonical_oss2e_policy().fingerprint
    assert len(evidence.candidate_freeze_fingerprint) == 64
    assert len(evidence.fingerprint) == 64


def test_oss2e_reject_is_mechanical_and_lists_failed_gates():
    robustness = passing_robustness()
    robustness = replace(robustness, pbo=replace(robustness.pbo, pbo=0.60))
    evidence = evaluate_oss2e_holdout_eligibility(robustness)

    assert evidence.decision is OSS2HoldoutEligibilityDecision.REJECT
    assert evidence.failed_gate_ids == ("PBO_MAX",)
    assert next(g for g in evidence.gates if g.gate_id == "PBO_MAX").passed is False


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("max_pbo", -0.1),
        ("min_deflated_sharpe_probability", 1.1),
        ("min_bootstrap_probability_positive", float("nan")),
        ("max_stressed_drawdown", 1.01),
        ("min_stressed_sharpe", float("inf")),
    ),
)
def test_oss2e_policy_rejects_invalid_thresholds(field, value):
    with pytest.raises(ValueError):
        replace(canonical_oss2e_policy(), **{field: value})


def test_oss2e_policy_is_frozen_and_has_no_execution_or_holdout_data_surface():
    policy = canonical_oss2e_policy()
    assert policy.max_pbo == 0.35
    assert policy.min_deflated_sharpe_probability == 0.80
    assert policy.min_bootstrap_probability_positive == 0.60
    assert policy.min_bootstrap_median_compounded_return == 0.0
    assert policy.min_bootstrap_lower_compounded_return == -0.10
    assert policy.min_stressed_net_return == 0.0
    assert policy.min_stressed_sharpe == 0.0
    assert policy.max_stressed_drawdown == 0.35
    assert policy.min_neighbor_median_sharpe == 0.0
    assert policy.min_fraction_selected_at_least_neighbor == 0.50
    assert policy.min_selected_minus_neighbor_median == -0.25

    forbidden = {
        "holdout",
        "final_holdout",
        "broker",
        "network",
        "credentials",
        "oms",
        "order_intent",
        "capital",
        "paper",
        "live",
    }
    assert forbidden.isdisjoint(policy.__dataclass_fields__)
    assert tuple(signature(evaluate_oss2e_holdout_eligibility).parameters) == ("robustness",)


def test_oss2e_gate_evidence_cannot_lie_about_comparison():
    with pytest.raises(ValueError, match="does not match"):
        OSS2HoldoutEligibilityGate(
            gate_id="X",
            passed=True,
            observed=0.8,
            comparison="<=",
            threshold=0.2,
        )


@pytest.mark.parametrize(
    "tamper",
    (
        lambda r: replace(r, policy_fingerprint=H),
        lambda r: replace(r, pbo=replace(r.pbo, partitions=6)),
        lambda r: replace(r, pbo=replace(r.pbo, combinations_evaluated=69)),
        lambda r: replace(
            r,
            deflated_sharpe=replace(r.deflated_sharpe, family_size=11),
        ),
        lambda r: replace(
            r,
            deflated_sharpe=replace(r.deflated_sharpe, metric_name="sharpe"),
        ),
        lambda r: replace(r, bootstrap=replace(r.bootstrap, seed=1)),
        lambda r: replace(r, bootstrap=replace(r.bootstrap, observations=63)),
    ),
)
def test_oss2e_rejects_noncanonical_oss2d_contract(tamper):
    with pytest.raises(OSS2HoldoutEligibilityGovernanceError):
        evaluate_oss2e_holdout_eligibility(tamper(passing_robustness()))


def test_oss2e_all_hard_gates_are_fail_closed():
    base = passing_robustness()
    variants = (
        replace(base, pbo=replace(base.pbo, pbo=0.350001)),
        replace(
            base,
            deflated_sharpe=replace(
                base.deflated_sharpe,
                deflated_sharpe_probability=0.799999,
            ),
        ),
        replace(base, bootstrap=replace(base.bootstrap, probability_positive=0.599999)),
        replace(base, bootstrap=replace(base.bootstrap, median_compounded_return=-0.000001)),
        replace(base, bootstrap=replace(base.bootstrap, lower_compounded_return=-0.100001)),
        replace(
            base,
            cost_stress=(
                replace(base.cost_stress[0], common_window_net_return=-0.000001),
                base.cost_stress[1],
            ),
        ),
        replace(
            base,
            cost_stress=(
                base.cost_stress[0],
                replace(base.cost_stress[1], common_window_sharpe=-0.000001),
            ),
        ),
        replace(
            base,
            cost_stress=(
                base.cost_stress[0],
                replace(base.cost_stress[1], common_window_max_drawdown=0.350001),
            ),
        ),
        replace(
            base,
            local_sensitivity=replace(base.local_sensitivity, neighbor_median_sharpe=-0.000001),
        ),
        replace(
            base,
            local_sensitivity=replace(
                base.local_sensitivity,
                fraction_selected_at_least_neighbor=0.499999,
            ),
        ),
        replace(
            base,
            local_sensitivity=replace(
                base.local_sensitivity,
                selected_minus_neighbor_median=-0.250001,
            ),
        ),
    )

    for robustness in variants:
        evidence = evaluate_oss2e_holdout_eligibility(robustness)
        assert evidence.decision is OSS2HoldoutEligibilityDecision.REJECT
        assert evidence.failed_gate_ids
