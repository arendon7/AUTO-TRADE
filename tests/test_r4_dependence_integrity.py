from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.portfolio_dependence import (
    CalibrationPhase,
    DependenceSpec,
    PairCorrelation,
    ReturnObservation,
    StrategyReturnSeries,
    build_dependence_evidence,
)


D = Decimal


def series(now, strategy_id, values, source_char):
    return StrategyReturnSeries(
        strategy_id=strategy_id,
        strategy_version="1",
        phase=CalibrationPhase.TRAIN,
        source_hash=source_char * 64,
        observations=tuple(
            ReturnObservation(now + timedelta(minutes=index), D(str(value)))
            for index, value in enumerate(values)
        ),
    )


def evidence(now):
    alpha = series(now, "alpha", [1, -1, 0, 0], "a")
    beta = series(now, "beta", [1, -1, 1, -1], "b")
    gamma = series(now, "gamma", [0, 0, 1, -1], "c")
    return build_dependence_evidence(
        (alpha, beta, gamma),
        DependenceSpec(
            phase=CalibrationPhase.TRAIN,
            min_common_observations=4,
            cluster_abs_correlation=D("0.70"),
        ),
    )


def test_forged_cluster_partition_is_rejected(now):
    original = evidence(now)
    with pytest.raises(ValueError, match="clusters do not match"):
        replace(
            original,
            clusters=(("alpha@1",), ("beta@1",), ("gamma@1",)),
        )


def test_forged_pair_correlation_is_rejected(now):
    original = evidence(now)
    forged_pairs = list(original.pairs)
    forged_pairs[0] = PairCorrelation(
        left_strategy=forged_pairs[0].left_strategy,
        right_strategy=forged_pairs[0].right_strategy,
        correlation=D("0"),
    )
    with pytest.raises(ValueError, match="pair correlations do not match"):
        replace(original, pairs=tuple(forged_pairs))


def test_forged_spec_fingerprint_is_rejected(now):
    original = evidence(now)
    with pytest.raises(ValueError, match="spec fingerprint mismatch"):
        replace(original, spec_fingerprint="0" * 64)


def test_threshold_mutation_without_rebuilding_spec_is_rejected(now):
    original = evidence(now)
    with pytest.raises(ValueError, match="spec fingerprint mismatch"):
        replace(original, cluster_abs_correlation=D("0.99"))


def test_aligned_return_panel_mutation_is_recomputed_and_rejected(now):
    original = evidence(now)
    altered = list(original.aligned_returns)
    key, values = altered[0]
    changed_values = list(values)
    changed_values[-1] = D("99")
    altered[0] = (key, tuple(changed_values))
    with pytest.raises(ValueError, match="pair correlations do not match"):
        replace(original, aligned_returns=tuple(altered))


def test_aligned_return_universe_must_exactly_match_strategy_fingerprints(now):
    original = evidence(now)
    with pytest.raises(ValueError, match="exactly match canonical strategy universe"):
        replace(original, aligned_returns=original.aligned_returns[:-1])


def test_aligned_return_lengths_and_values_are_strict(now):
    original = evidence(now)
    key, values = original.aligned_returns[0]
    bad_length = ((key, values[:-1]),) + original.aligned_returns[1:]
    with pytest.raises(ValueError, match="length must equal"):
        replace(original, aligned_returns=bad_length)

    bad_values = list(values)
    bad_values[0] = D("NaN")
    nonfinite = ((key, tuple(bad_values)),) + original.aligned_returns[1:]
    with pytest.raises(ValueError, match="finite Decimal"):
        replace(original, aligned_returns=nonfinite)


def test_common_timestamp_mutation_cannot_be_silently_accepted(now):
    original = evidence(now)
    with pytest.raises(ValueError, match="sorted"):
        replace(original, common_timestamps=tuple(reversed(original.common_timestamps)))
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(
            original,
            common_timestamps=tuple(value.replace(tzinfo=None) for value in original.common_timestamps),
        )


def test_evidence_payload_commits_aligned_panel_and_policy_parameters(now):
    original = evidence(now)
    payload = original.to_payload()
    assert payload["min_common_observations"] == 4
    assert payload["cluster_abs_correlation"] == "0.70"
    assert len(payload["aligned_returns"]) == 3
    assert payload["fingerprint"] == original.fingerprint
