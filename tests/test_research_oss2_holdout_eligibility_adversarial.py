from dataclasses import replace

import pytest

from autotrade.research.oss2_holdout_eligibility import (
    OSS2HoldoutEligibilityDecision,
    OSS2HoldoutEligibilityEvidence,
    evaluate_oss2e_holdout_eligibility,
)
from tests.test_research_oss2_holdout_eligibility import passing_robustness


def test_candidate_freeze_changes_if_input_evidence_changes():
    base = passing_robustness()
    first = evaluate_oss2e_holdout_eligibility(base)
    changed = replace(base, pbo=replace(base.pbo, pbo=0.21))
    second = evaluate_oss2e_holdout_eligibility(changed)

    assert first.selected_trial_id == second.selected_trial_id
    assert first.candidate_freeze_fingerprint != second.candidate_freeze_fingerprint
    assert first.fingerprint != second.fingerprint


def test_evidence_constructor_rejects_omitted_failure():
    rejected = evaluate_oss2e_holdout_eligibility(
        replace(
            passing_robustness(),
            pbo=replace(passing_robustness().pbo, pbo=0.90),
        )
    )
    with pytest.raises(ValueError, match="failed gate list"):
        OSS2HoldoutEligibilityEvidence(
            campaign_id=rejected.campaign_id,
            selected_trial_id=rejected.selected_trial_id,
            oss2d_evidence_fingerprint=rejected.oss2d_evidence_fingerprint,
            policy_fingerprint=rejected.policy_fingerprint,
            decision=OSS2HoldoutEligibilityDecision.REJECT,
            gates=rejected.gates,
            failed_gate_ids=(),
        )


def test_evidence_constructor_rejects_manual_eligible_override():
    rejected = evaluate_oss2e_holdout_eligibility(
        replace(
            passing_robustness(),
            pbo=replace(passing_robustness().pbo, pbo=0.90),
        )
    )
    with pytest.raises(ValueError, match="mechanically derived"):
        OSS2HoldoutEligibilityEvidence(
            campaign_id=rejected.campaign_id,
            selected_trial_id=rejected.selected_trial_id,
            oss2d_evidence_fingerprint=rejected.oss2d_evidence_fingerprint,
            policy_fingerprint=rejected.policy_fingerprint,
            decision=OSS2HoldoutEligibilityDecision.HOLDOUT_ELIGIBLE,
            gates=rejected.gates,
            failed_gate_ids=rejected.failed_gate_ids,
        )


def test_exact_thresholds_pass_by_definition():
    base = passing_robustness()
    policy = __import__(
        "autotrade.research.oss2_holdout_eligibility",
        fromlist=["canonical_oss2e_policy"],
    ).canonical_oss2e_policy()
    at_boundary = replace(
        base,
        pbo=replace(base.pbo, pbo=policy.max_pbo),
        deflated_sharpe=replace(
            base.deflated_sharpe,
            deflated_sharpe_probability=policy.min_deflated_sharpe_probability,
        ),
        bootstrap=replace(
            base.bootstrap,
            probability_positive=policy.min_bootstrap_probability_positive,
            median_compounded_return=policy.min_bootstrap_median_compounded_return,
            lower_compounded_return=policy.min_bootstrap_lower_compounded_return,
        ),
        cost_stress=tuple(
            replace(
                item,
                common_window_net_return=policy.min_stressed_net_return,
                common_window_sharpe=policy.min_stressed_sharpe,
                common_window_max_drawdown=policy.max_stressed_drawdown,
            )
            for item in base.cost_stress
        ),
        local_sensitivity=replace(
            base.local_sensitivity,
            neighbor_median_sharpe=policy.min_neighbor_median_sharpe,
            fraction_selected_at_least_neighbor=policy.min_fraction_selected_at_least_neighbor,
            selected_minus_neighbor_median=policy.min_selected_minus_neighbor_median,
        ),
    )
    result = evaluate_oss2e_holdout_eligibility(at_boundary)
    assert result.decision is OSS2HoldoutEligibilityDecision.HOLDOUT_ELIGIBLE
    assert result.failed_gate_ids == ()
