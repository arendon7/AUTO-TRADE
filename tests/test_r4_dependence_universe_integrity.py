from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.portfolio_dependence import (
    CalibrationPhase,
    DependenceEvidence,
    DependenceSpec,
    InsufficientDependenceEvidence,
    ReturnObservation,
    StrategyReturnSeries,
    build_dependence_evidence,
)


def _series(now, strategy_id, source_char):
    return StrategyReturnSeries(
        strategy_id=strategy_id,
        strategy_version="1",
        phase=CalibrationPhase.TRAIN,
        source_hash=source_char * 64,
        observations=tuple(
            ReturnObservation(
                occurred_at=now + timedelta(minutes=index),
                value=Decimal(str(value)),
            )
            for index, value in enumerate((1, 2, 4))
        ),
    )


def _evidence(now):
    return build_dependence_evidence(
        (_series(now, "alpha", "a"), _series(now, "beta", "b")),
        DependenceSpec(
            phase=CalibrationPhase.TRAIN,
            min_common_observations=3,
            cluster_abs_correlation=Decimal("0.8"),
        ),
    )


def test_dependence_evidence_cannot_be_downgraded_to_one_strategy(now):
    original = _evidence(now)
    key, fingerprint = original.strategy_fingerprints[0]
    _, values = original.aligned_returns[0]
    with pytest.raises(InsufficientDependenceEvidence, match="at least two strategies"):
        DependenceEvidence(
            phase=original.phase,
            min_common_observations=original.min_common_observations,
            cluster_abs_correlation=original.cluster_abs_correlation,
            spec_fingerprint=original.spec_fingerprint,
            strategy_fingerprints=((key, fingerprint),),
            common_timestamps=original.common_timestamps,
            aligned_returns=((key, values),),
            pairs=(),
            clusters=((key,),),
        )


def test_dependence_evidence_rejects_noncanonical_strategy_key(now):
    original = _evidence(now)
    fingerprints = list(original.strategy_fingerprints)
    key, fingerprint = fingerprints[0]
    bad_key = f" {key}"
    fingerprints[0] = (bad_key, fingerprint)
    aligned = list(original.aligned_returns)
    aligned[0] = (bad_key, aligned[0][1])
    with pytest.raises(ValueError, match="surrounding whitespace"):
        replace(
            original,
            strategy_fingerprints=tuple(fingerprints),
            aligned_returns=tuple(aligned),
        )


def test_dependence_evidence_requires_strategy_id_version_key_shape(now):
    original = _evidence(now)
    fingerprints = list(original.strategy_fingerprints)
    _, fingerprint = fingerprints[0]
    fingerprints[0] = ("alpha", fingerprint)
    aligned = list(original.aligned_returns)
    aligned[0] = ("alpha", aligned[0][1])
    with pytest.raises(ValueError, match="strategy_id@strategy_version"):
        replace(
            original,
            strategy_fingerprints=tuple(fingerprints),
            aligned_returns=tuple(aligned),
        )
