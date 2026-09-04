from __future__ import annotations

import pytest

from autotrade.research.statistical_diagnostics import diagnose_pbo_readiness


def test_pbo_diagnostics_identify_no_activity_and_segment_blockers() -> None:
    active = [0.01, -0.01, 0.02, -0.02] * 4
    inactive = [0.0] * 16

    result = diagnose_pbo_readiness(
        {"active": active, "inactive": inactive}, partitions=4
    )

    assert result.ready is False
    assert result.combinations_evaluated == 6
    assert result.no_activity_trial_ids == ("inactive",)
    assert result.blocking_trial_ids == ("inactive",)
    by_id = {item.trial_id: item for item in result.trials}
    assert by_id["inactive"].zero_variance_full_series is True
    assert by_id["inactive"].zero_variance_train_segments == 6
    assert by_id["inactive"].zero_variance_test_segments == 6
    assert by_id["active"].blocks_pbo is False


def test_pbo_diagnostics_detect_partition_degeneracy_even_when_full_series_varies() -> None:
    # Four partitions of four observations. Trial A varies overall but each half
    # can become constant for at least one CSCV orientation.
    a = [0.01] * 8 + [-0.01] * 8
    b = [0.01, -0.01, 0.02, -0.02] * 4

    result = diagnose_pbo_readiness({"a": a, "b": b}, partitions=4)
    by_id = {item.trial_id: item for item in result.trials}

    assert by_id["a"].zero_variance_full_series is False
    assert by_id["a"].zero_variance_train_segments > 0
    assert by_id["a"].blocks_pbo is True
    assert "a" in result.blocking_trial_ids


def test_pbo_diagnostics_are_deterministic_and_canonical() -> None:
    a = [0.02, -0.01, 0.01, -0.02] * 4
    b = [-0.01, 0.02, -0.02, 0.01] * 4

    first = diagnose_pbo_readiness({"b": b, "a": a}, partitions=4)
    second = diagnose_pbo_readiness({"a": a, "b": b}, partitions=4)

    assert first == second
    assert [item.trial_id for item in first.trials] == ["a", "b"]
    assert first.ready is True


def test_pbo_diagnostics_validate_same_structural_preconditions() -> None:
    with pytest.raises(ValueError, match="at least two"):
        diagnose_pbo_readiness({"a": [0.1] * 16}, partitions=4)
    with pytest.raises(ValueError, match="even integer"):
        diagnose_pbo_readiness(
            {"a": [0.1] * 12, "b": [0.2] * 12}, partitions=3
        )
    with pytest.raises(ValueError, match="equal length"):
        diagnose_pbo_readiness(
            {"a": [0.1] * 16, "b": [0.2] * 15}, partitions=4
        )
    with pytest.raises(ValueError, match="divide evenly"):
        diagnose_pbo_readiness(
            {"a": [0.1] * 10, "b": [0.2] * 10}, partitions=4
        )
    with pytest.raises(ValueError, match="finite"):
        diagnose_pbo_readiness(
            {"a": [0.1] * 16, "b": [float("nan")] * 16}, partitions=4
        )
