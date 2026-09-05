from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import e, isfinite, log, sqrt
from statistics import NormalDist, variance
from typing import Mapping, Sequence

from .trials import SQLiteTrialLedger, TrialGovernanceError, TrialStatus


@dataclass(frozen=True, slots=True)
class HolmEvidence:
    campaign_id: str
    family_size: int
    raw_p_values: Mapping[str, float]
    adjusted_p_values: Mapping[str, float]
    failed_trial_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PBOEvidence:
    campaign_id: str
    partitions: int
    combinations_evaluated: int
    pbo: float
    logits: tuple[float, ...]
    partition_sizes: tuple[int, ...] = ()
    balanced_partitions: bool = False


@dataclass(frozen=True, slots=True)
class DeflatedSharpeEvidence:
    campaign_id: str
    selected_trial_id: str
    selected_sharpe: float
    expected_max_sharpe: float
    deflated_sharpe_probability: float
    family_size: int
    sample_size: int


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    if not p_values:
        raise ValueError("p_values cannot be empty")
    for trial_id, value in p_values.items():
        if not trial_id.strip() or not isfinite(value) or value < 0 or value > 1:
            raise ValueError("p_values must bind non-empty ids to finite values in [0,1]")
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    m = len(ordered)
    adjusted_ordered: list[tuple[str, float]] = []
    running = 0.0
    for index, (trial_id, value) in enumerate(ordered):
        candidate = min(1.0, (m - index) * value)
        running = max(running, candidate)
        adjusted_ordered.append((trial_id, running))
    return {trial_id: value for trial_id, value in adjusted_ordered}


def campaign_holm_evidence(ledger: SQLiteTrialLedger, campaign_id: str) -> HolmEvidence:
    accounting = ledger.require_complete_campaign(campaign_id)
    records = {record.spec.trial_id: record for record in ledger.list_trials(campaign_id)}
    raw: dict[str, float] = {}
    for trial_id in accounting.expected_trial_ids:
        record = records[trial_id]
        if record.status is TrialStatus.FAILED:
            raw[trial_id] = 1.0
            continue
        if record.p_value is None:
            raise TrialGovernanceError(
                f"completed trial {trial_id} has no p_value for multiple-testing evidence"
            )
        raw[trial_id] = float(record.p_value)
    return HolmEvidence(
        campaign_id=campaign_id,
        family_size=len(accounting.expected_trial_ids),
        raw_p_values=raw,
        adjusted_p_values=holm_adjust(raw),
        failed_trial_ids=accounting.failed_trial_ids,
    )


def campaign_pbo(
    ledger: SQLiteTrialLedger,
    campaign_id: str,
    returns_by_trial: Mapping[str, Sequence[float]],
    *,
    partitions: int = 8,
    balanced_partitions: bool = False,
) -> PBOEvidence:
    """Estimate PBO with deterministic contiguous CSCV partitions.

    The historical strict mode requires equal-size partitions. When
    ``balanced_partitions`` is true, all observations are retained and assigned
    to contiguous partitions whose sizes differ by at most one row. This avoids
    silently trimming a valid common window solely to satisfy divisibility.
    """
    accounting = ledger.require_complete_campaign(campaign_id)
    if accounting.failed_trial_ids:
        raise TrialGovernanceError(
            "PBO requires terminal return series for every trial; failed trials exist"
        )
    expected = set(accounting.expected_trial_ids)
    if set(returns_by_trial) != expected:
        raise TrialGovernanceError(
            "PBO return universe must exactly match frozen campaign trials"
        )
    if partitions < 4 or partitions % 2:
        raise ValueError("partitions must be an even integer >= 4")
    if not isinstance(balanced_partitions, bool):
        raise TypeError("balanced_partitions must be bool")
    lengths = {len(tuple(values)) for values in returns_by_trial.values()}
    if len(lengths) != 1:
        raise ValueError("all PBO return series must have equal length")
    observations = lengths.pop()
    if observations < partitions * 2:
        raise ValueError("PBO requires at least two observations per partition")
    if not balanced_partitions and observations % partitions:
        raise ValueError(
            "PBO observations must divide evenly into partitions with >=2 rows each"
        )

    if len(expected) < 2:
        raise TrialGovernanceError("PBO requires at least two trials")
    trial_ids = tuple(sorted(expected))
    series = {
        trial_id: tuple(float(value) for value in returns_by_trial[trial_id])
        for trial_id in trial_ids
    }
    if any(not isfinite(value) for values in series.values() for value in values):
        raise ValueError("PBO return values must be finite")

    base_size, remainder = divmod(observations, partitions)
    partition_sizes = tuple(
        base_size + (1 if index < remainder else 0)
        for index in range(partitions)
    )
    if min(partition_sizes) < 2:
        raise ValueError("PBO requires at least two observations per partition")
    if not balanced_partitions and len(set(partition_sizes)) != 1:
        raise ValueError("strict PBO partitions must have equal size")
    partition_indices: list[tuple[int, ...]] = []
    cursor = 0
    for size in partition_sizes:
        partition_indices.append(tuple(range(cursor, cursor + size)))
        cursor += size
    if cursor != observations:
        raise RuntimeError("PBO partition accounting mismatch")

    logits: list[float] = []
    half = partitions // 2
    all_partitions = set(range(partitions))
    for train_parts in combinations(range(partitions), half):
        complement = tuple(sorted(all_partitions - set(train_parts)))
        # CSCV treats every choice of S/2 partitions as an in-sample set;
        # its complement is the corresponding out-of-sample set. The swapped
        # orientation is a distinct CSCV combination and must not be dropped.
        train_idx = tuple(i for part in train_parts for i in partition_indices[part])
        test_idx = tuple(i for part in complement for i in partition_indices[part])
        train_scores = {
            trial_id: _sharpe(tuple(series[trial_id][i] for i in train_idx))
            for trial_id in trial_ids
        }
        selected = max(
            trial_ids, key=lambda trial_id: (train_scores[trial_id], trial_id)
        )
        test_scores = {
            trial_id: _sharpe(tuple(series[trial_id][i] for i in test_idx))
            for trial_id in trial_ids
        }
        ordered = sorted(
            trial_ids, key=lambda trial_id: (test_scores[trial_id], trial_id)
        )
        rank = ordered.index(selected) + 1
        omega = rank / (len(trial_ids) + 1.0)
        logits.append(log(omega / (1.0 - omega)))
    if not logits:
        raise ValueError("PBO produced no CSCV combinations")
    pbo = sum(1 for value in logits if value <= 0.0) / len(logits)
    return PBOEvidence(
        campaign_id=campaign_id,
        partitions=partitions,
        combinations_evaluated=len(logits),
        pbo=pbo,
        logits=tuple(logits),
        partition_sizes=partition_sizes,
        balanced_partitions=balanced_partitions,
    )


def campaign_deflated_sharpe(
    ledger: SQLiteTrialLedger,
    campaign_id: str,
    *,
    selected_trial_id: str,
    sample_size: int,
    skewness: float,
    kurtosis: float,
    metric_name: str = "sharpe",
) -> DeflatedSharpeEvidence:
    """Compute Deflated Sharpe against a frozen campaign metric family.

    ``metric_name="sharpe"`` preserves the historical contract. Research layers
    that preregister a different Sharpe field (for example a common-window
    Sharpe) may bind that exact field without copying the DSR implementation.
    """
    if not metric_name.strip():
        raise ValueError("metric_name is required")
    accounting = ledger.require_complete_campaign(campaign_id)
    if accounting.failed_trial_ids:
        raise TrialGovernanceError(
            "Deflated Sharpe requires metric evidence for every trial"
        )
    if selected_trial_id not in accounting.expected_trial_ids:
        raise TrialGovernanceError("selected trial is outside frozen campaign")
    if sample_size < 3:
        raise ValueError("sample_size must be >= 3")
    if not isfinite(skewness) or not isfinite(kurtosis) or kurtosis < 1:
        raise ValueError("skewness/kurtosis preconditions are invalid")

    records = {record.spec.trial_id: record for record in ledger.list_trials(campaign_id)}
    sharpes: dict[str, float] = {}
    for trial_id in accounting.expected_trial_ids:
        raw = records[trial_id].metrics.get(metric_name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise TrialGovernanceError(
                f"trial {trial_id} has no numeric {metric_name} metric for Deflated Sharpe"
            )
        value = float(raw)
        if not isfinite(value):
            raise TrialGovernanceError(
                f"trial {trial_id} {metric_name} is not finite"
            )
        sharpes[trial_id] = value
    if len(sharpes) < 2:
        raise TrialGovernanceError("Deflated Sharpe requires at least two trials")
    sharpe_variance = variance(sharpes.values())
    if sharpe_variance <= 0:
        raise TrialGovernanceError(
            "Deflated Sharpe requires non-zero trial Sharpe variance"
        )

    n = len(sharpes)
    normal = NormalDist()
    gamma = 0.5772156649015329
    selected_best = max(sharpes.values())
    if sharpes[selected_trial_id] != selected_best:
        message = (
            "Deflated Sharpe selected_trial_id must be a maximum-Sharpe trial"
            if metric_name == "sharpe"
            else "Deflated Sharpe selected_trial_id must maximize the bound metric"
        )
        raise TrialGovernanceError(message)
    expected_max = sqrt(sharpe_variance) * (
        (1.0 - gamma) * normal.inv_cdf(1.0 - 1.0 / n)
        + gamma * normal.inv_cdf(1.0 - 1.0 / (n * e))
    )
    selected = sharpes[selected_trial_id]
    denominator_term = (
        1.0
        - skewness * selected
        + ((kurtosis - 1.0) / 4.0) * selected * selected
    )
    if denominator_term <= 0 or not isfinite(denominator_term):
        raise TrialGovernanceError("Deflated Sharpe denominator precondition failed")
    z = (selected - expected_max) * sqrt(sample_size - 1.0) / sqrt(denominator_term)
    probability = normal.cdf(z)
    return DeflatedSharpeEvidence(
        campaign_id=campaign_id,
        selected_trial_id=selected_trial_id,
        selected_sharpe=selected,
        expected_max_sharpe=expected_max,
        deflated_sharpe_probability=probability,
        family_size=n,
        sample_size=sample_size,
    )


def _sharpe(values: Sequence[float]) -> float:
    if len(values) < 2:
        raise ValueError("Sharpe requires at least two observations")
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    if variance == 0:
        raise ValueError("Sharpe is undefined for a zero-variance return segment")
    return mean / sqrt(variance)
