from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import isfinite
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class TrialReturnDiagnostics:
    trial_id: str
    observations: int
    nonzero_observations: int
    zero_variance_full_series: bool
    zero_variance_train_segments: int
    zero_variance_test_segments: int

    @property
    def blocks_pbo(self) -> bool:
        return (
            self.zero_variance_full_series
            or self.zero_variance_train_segments > 0
            or self.zero_variance_test_segments > 0
        )


@dataclass(frozen=True, slots=True)
class PBOReadinessDiagnostics:
    partitions: int
    observations: int
    combinations_evaluated: int
    trial_count: int
    blocking_trial_ids: tuple[str, ...]
    no_activity_trial_ids: tuple[str, ...]
    trials: tuple[TrialReturnDiagnostics, ...]

    @property
    def ready(self) -> bool:
        return not self.blocking_trial_ids


def diagnose_pbo_readiness(
    returns_by_trial: Mapping[str, Sequence[float]],
    *,
    partitions: int = 8,
) -> PBOReadinessDiagnostics:
    """Explain PBO/CSCV zero-variance precondition failures deterministically.

    This function is diagnostics only. It does not assign a substitute Sharpe,
    drop trials, change the frozen family or modify `campaign_pbo` semantics.
    """

    if len(returns_by_trial) < 2:
        raise ValueError("PBO diagnostics require at least two trials")
    if partitions < 4 or partitions % 2:
        raise ValueError("partitions must be an even integer >= 4")
    if any(not trial_id.strip() for trial_id in returns_by_trial):
        raise ValueError("trial ids cannot be blank")

    normalized = {
        trial_id: tuple(float(value) for value in values)
        for trial_id, values in returns_by_trial.items()
    }
    lengths = {len(values) for values in normalized.values()}
    if len(lengths) != 1:
        raise ValueError("all PBO diagnostic return series must have equal length")
    observations = lengths.pop()
    if observations < partitions * 2 or observations % partitions:
        raise ValueError(
            "PBO diagnostic observations must divide evenly into partitions with >=2 rows each"
        )
    if any(not isfinite(value) for values in normalized.values() for value in values):
        raise ValueError("PBO diagnostic return values must be finite")

    block = observations // partitions
    partition_indices = tuple(
        tuple(range(index * block, (index + 1) * block)) for index in range(partitions)
    )
    half = partitions // 2
    all_partitions = set(range(partitions))
    combinations_list = tuple(combinations(range(partitions), half))

    trial_diagnostics: list[TrialReturnDiagnostics] = []
    for trial_id in sorted(normalized):
        values = normalized[trial_id]
        zero_train = 0
        zero_test = 0
        for train_parts in combinations_list:
            test_parts = tuple(sorted(all_partitions - set(train_parts)))
            train_values = tuple(
                values[index]
                for part in train_parts
                for index in partition_indices[part]
            )
            test_values = tuple(
                values[index]
                for part in test_parts
                for index in partition_indices[part]
            )
            if _zero_variance(train_values):
                zero_train += 1
            if _zero_variance(test_values):
                zero_test += 1
        trial_diagnostics.append(
            TrialReturnDiagnostics(
                trial_id=trial_id,
                observations=observations,
                nonzero_observations=sum(1 for value in values if value != 0.0),
                zero_variance_full_series=_zero_variance(values),
                zero_variance_train_segments=zero_train,
                zero_variance_test_segments=zero_test,
            )
        )

    trials = tuple(trial_diagnostics)
    return PBOReadinessDiagnostics(
        partitions=partitions,
        observations=observations,
        combinations_evaluated=len(combinations_list),
        trial_count=len(trials),
        blocking_trial_ids=tuple(item.trial_id for item in trials if item.blocks_pbo),
        no_activity_trial_ids=tuple(
            item.trial_id for item in trials if item.nonzero_observations == 0
        ),
        trials=trials,
    )


def _zero_variance(values: Sequence[float]) -> bool:
    if not values:
        raise ValueError("variance diagnostics require observations")
    first = values[0]
    return all(value == first for value in values[1:])


__all__ = [
    "PBOReadinessDiagnostics",
    "TrialReturnDiagnostics",
    "diagnose_pbo_readiness",
]
