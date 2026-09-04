from __future__ import annotations

import pytest

from autotrade.research.autopilot import StatisticalSelectionPolicy
from autotrade.research.multiple_testing import DeflatedSharpeEvidence, PBOEvidence


def _pbo(value: float = 0.25) -> PBOEvidence:
    return PBOEvidence(
        campaign_id="campaign",
        partitions=8,
        combinations_evaluated=70,
        pbo=value,
        logits=(0.1,),
    )


def _dsr(
    *, trial_id: str = "winner", probability: float = 0.99
) -> DeflatedSharpeEvidence:
    return DeflatedSharpeEvidence(
        campaign_id="campaign",
        selected_trial_id=trial_id,
        selected_sharpe=1.2,
        expected_max_sharpe=0.6,
        deflated_sharpe_probability=probability,
        family_size=20,
        sample_size=500,
    )


def test_statistical_gate_accepts_only_matching_complete_evidence() -> None:
    passed, reasons = StatisticalSelectionPolicy().evaluate(
        selected_trial_id="winner",
        tournament_winner_trial_id="winner",
        pbo_evidence=_pbo(0.25),
        deflated_sharpe_evidence=_dsr(probability=0.99),
    )

    assert passed is True
    assert reasons == ()


def test_statistical_gate_rejects_unavailable_or_weak_evidence() -> None:
    policy = StatisticalSelectionPolicy(
        min_deflated_sharpe_probability=0.95,
        max_pbo=0.50,
    )

    passed, reasons = policy.evaluate(
        selected_trial_id="winner",
        tournament_winner_trial_id="winner",
        pbo_evidence=None,
        deflated_sharpe_evidence=_dsr(probability=0.50),
    )

    assert passed is False
    assert "PBO_UNAVAILABLE" in reasons
    assert "DEFLATED_SHARPE_BELOW_MINIMUM" in reasons


def test_statistical_gate_rejects_robust_winner_that_differs_from_sharpe_winner() -> None:
    passed, reasons = StatisticalSelectionPolicy().evaluate(
        selected_trial_id="robust-winner",
        tournament_winner_trial_id="sharpe-winner",
        pbo_evidence=_pbo(),
        deflated_sharpe_evidence=_dsr(trial_id="sharpe-winner"),
    )

    assert passed is False
    assert "ROBUST_WINNER_NOT_SHARPE_WINNER" in reasons
    assert "DEFLATED_SHARPE_NOT_BOUND_TO_SELECTION" in reasons


def test_statistical_gate_rejects_no_robust_selection() -> None:
    passed, reasons = StatisticalSelectionPolicy().evaluate(
        selected_trial_id="",
        tournament_winner_trial_id="winner",
        pbo_evidence=_pbo(),
        deflated_sharpe_evidence=_dsr(),
    )

    assert passed is False
    assert "NO_ROBUST_SELECTION" in reasons


def test_statistical_gate_can_explicitly_allow_missing_optional_statistics() -> None:
    policy = StatisticalSelectionPolicy(
        require_pbo=False,
        require_deflated_sharpe=False,
    )
    passed, reasons = policy.evaluate(
        selected_trial_id="winner",
        tournament_winner_trial_id="winner",
        pbo_evidence=None,
        deflated_sharpe_evidence=None,
    )

    assert passed is True
    assert reasons == ()


def test_statistical_policy_validates_probability_bounds() -> None:
    with pytest.raises(ValueError, match="min_deflated_sharpe_probability"):
        StatisticalSelectionPolicy(min_deflated_sharpe_probability=1.1)
    with pytest.raises(ValueError, match="max_pbo"):
        StatisticalSelectionPolicy(max_pbo=-0.1)
