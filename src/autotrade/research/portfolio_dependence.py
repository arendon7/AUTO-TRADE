from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Mapping


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ZERO = Decimal("0")
_ONE = Decimal("1")


class PortfolioDependenceError(RuntimeError):
    pass


class InsufficientDependenceEvidence(PortfolioDependenceError):
    pass


class PortfolioBudgetViolation(PortfolioDependenceError):
    pass


class CalibrationPhase(StrEnum):
    """Phases allowed to calibrate portfolio dependence.

    FINAL_HOLDOUT is deliberately not representable. A protected holdout may
    later evaluate a frozen portfolio policy, but it can never calibrate the
    dependence model that selected/limited that policy.
    """

    TRAIN = "TRAIN"
    DEVELOPMENT = "DEVELOPMENT"


@dataclass(frozen=True, slots=True)
class ReturnObservation:
    occurred_at: datetime
    value: Decimal

    def __post_init__(self) -> None:
        if not _aware(self.occurred_at):
            raise ValueError("return timestamp must be timezone-aware")
        if not _finite(self.value):
            raise ValueError("return value must be finite Decimal")


@dataclass(frozen=True, slots=True)
class StrategyReturnSeries:
    strategy_id: str
    strategy_version: str
    phase: CalibrationPhase
    source_hash: str
    observations: tuple[ReturnObservation, ...]

    def __post_init__(self) -> None:
        _canonical_identity(self.strategy_id, "strategy_id")
        _canonical_identity(self.strategy_version, "strategy_version")
        if not isinstance(self.phase, CalibrationPhase):
            raise ValueError("phase must be TRAIN or DEVELOPMENT")
        if not _SHA256_RE.fullmatch(self.source_hash):
            raise ValueError("source_hash must be lowercase SHA-256 hex")
        if len(self.observations) < 2:
            raise ValueError("strategy return series requires at least two observations")
        previous: datetime | None = None
        for observation in self.observations:
            if not isinstance(observation, ReturnObservation):
                raise ValueError("observations must contain ReturnObservation")
            if previous is not None and observation.occurred_at <= previous:
                raise ValueError("return observations must be strictly increasing and unique")
            previous = observation.occurred_at

    @property
    def strategy_key(self) -> str:
        return f"{self.strategy_id}@{self.strategy_version}"

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version,
                "phase": self.phase.value,
                "source_hash": self.source_hash,
                "observations": [
                    [observation.occurred_at.isoformat(), str(observation.value)]
                    for observation in self.observations
                ],
            }
        )


@dataclass(frozen=True, slots=True)
class DependenceSpec:
    phase: CalibrationPhase
    min_common_observations: int
    cluster_abs_correlation: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.phase, CalibrationPhase):
            raise ValueError("phase must be TRAIN or DEVELOPMENT")
        if (
            isinstance(self.min_common_observations, bool)
            or not isinstance(self.min_common_observations, int)
            or self.min_common_observations < 2
        ):
            raise ValueError("min_common_observations must be integer >= 2")
        if not _finite(self.cluster_abs_correlation):
            raise ValueError("cluster_abs_correlation must be finite Decimal")
        if self.cluster_abs_correlation < _ZERO or self.cluster_abs_correlation > _ONE:
            raise ValueError("cluster_abs_correlation must be between 0 and 1")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "phase": self.phase.value,
                "min_common_observations": self.min_common_observations,
                "cluster_abs_correlation": str(self.cluster_abs_correlation),
            }
        )


@dataclass(frozen=True, slots=True)
class PairCorrelation:
    left_strategy: str
    right_strategy: str
    correlation: Decimal

    def __post_init__(self) -> None:
        _canonical_identity(self.left_strategy, "left_strategy")
        _canonical_identity(self.right_strategy, "right_strategy")
        if self.left_strategy >= self.right_strategy:
            raise ValueError("pair strategy keys must be canonical ascending order")
        if not _finite(self.correlation) or self.correlation < -_ONE or self.correlation > _ONE:
            raise ValueError("correlation must be finite and within [-1,1]")


@dataclass(frozen=True, slots=True)
class DependenceEvidence:
    phase: CalibrationPhase
    min_common_observations: int
    cluster_abs_correlation: Decimal
    spec_fingerprint: str
    strategy_fingerprints: tuple[tuple[str, str], ...]
    common_timestamps: tuple[datetime, ...]
    aligned_returns: tuple[tuple[str, tuple[Decimal, ...]], ...]
    pairs: tuple[PairCorrelation, ...]
    clusters: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.phase, CalibrationPhase):
            raise ValueError("phase must be TRAIN or DEVELOPMENT")
        if (
            isinstance(self.min_common_observations, bool)
            or not isinstance(self.min_common_observations, int)
            or self.min_common_observations < 2
        ):
            raise ValueError("min_common_observations must be integer >= 2")
        if (
            not _finite(self.cluster_abs_correlation)
            or self.cluster_abs_correlation < _ZERO
            or self.cluster_abs_correlation > _ONE
        ):
            raise ValueError("cluster_abs_correlation must be within [0,1]")
        expected_spec_fingerprint = DependenceSpec(
            phase=self.phase,
            min_common_observations=self.min_common_observations,
            cluster_abs_correlation=self.cluster_abs_correlation,
        ).fingerprint
        if self.spec_fingerprint != expected_spec_fingerprint:
            raise ValueError("dependence spec fingerprint mismatch")

        if not self.strategy_fingerprints:
            raise ValueError("strategy_fingerprints cannot be empty")
        keys = tuple(key for key, _ in self.strategy_fingerprints)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("strategy_fingerprints must use unique canonical sorted keys")
        if any(not _SHA256_RE.fullmatch(value) for _, value in self.strategy_fingerprints):
            raise ValueError("strategy fingerprint must be SHA-256 hex")

        if not self.common_timestamps:
            raise ValueError("common_timestamps cannot be empty")
        if self.common_timestamps != tuple(sorted(self.common_timestamps)):
            raise ValueError("common_timestamps must be sorted")
        if len(set(self.common_timestamps)) != len(self.common_timestamps):
            raise ValueError("common_timestamps must be unique")
        if any(not _aware(value) for value in self.common_timestamps):
            raise ValueError("common_timestamps must be timezone-aware")
        if len(self.common_timestamps) < self.min_common_observations:
            raise InsufficientDependenceEvidence(
                "evidence common timestamps are below min_common_observations"
            )

        aligned_keys = tuple(key for key, _ in self.aligned_returns)
        if aligned_keys != keys:
            raise ValueError("aligned_returns must exactly match canonical strategy universe")
        aligned_map: dict[str, tuple[Decimal, ...]] = {}
        for key, values in self.aligned_returns:
            if len(values) != len(self.common_timestamps):
                raise ValueError("aligned return length must equal common timestamp length")
            if any(not _finite(value) for value in values):
                raise ValueError("aligned returns must be finite Decimal values")
            aligned_map[key] = values

        expected_pairs: list[PairCorrelation] = []
        for left_index, left in enumerate(keys):
            for right in keys[left_index + 1 :]:
                expected_pairs.append(
                    PairCorrelation(
                        left_strategy=left,
                        right_strategy=right,
                        correlation=_pearson(aligned_map[left], aligned_map[right]),
                    )
                )
        if self.pairs != tuple(expected_pairs):
            raise ValueError("pair correlations do not match aligned return evidence")

        expected_clusters = _clusters(
            keys,
            tuple(expected_pairs),
            self.cluster_abs_correlation,
        )
        if self.clusters != expected_clusters:
            raise ValueError("clusters do not match correlation evidence and threshold")

    @property
    def strategy_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _ in self.strategy_fingerprints)

    @property
    def common_observation_count(self) -> int:
        return len(self.common_timestamps)

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_payload(include_fingerprint=False))

    def correlation(self, left: str, right: str) -> Decimal:
        if left == right:
            if left not in self.strategy_keys:
                raise KeyError(left)
            return _ONE
        a, b = sorted((left, right))
        for pair in self.pairs:
            if pair.left_strategy == a and pair.right_strategy == b:
                return pair.correlation
        raise KeyError((left, right))

    def to_payload(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "phase": self.phase.value,
            "min_common_observations": self.min_common_observations,
            "cluster_abs_correlation": str(self.cluster_abs_correlation),
            "spec_fingerprint": self.spec_fingerprint,
            "strategy_fingerprints": [list(item) for item in self.strategy_fingerprints],
            "common_timestamps": [value.isoformat() for value in self.common_timestamps],
            "aligned_returns": [
                [key, [str(value) for value in values]]
                for key, values in self.aligned_returns
            ],
            "pairs": [
                {
                    "left_strategy": pair.left_strategy,
                    "right_strategy": pair.right_strategy,
                    "correlation": str(pair.correlation),
                }
                for pair in self.pairs
            ],
            "clusters": [list(cluster) for cluster in self.clusters],
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload


@dataclass(frozen=True, slots=True)
class DiversificationBudgetPolicy:
    max_strategy_weight: Decimal
    max_cluster_weight: Decimal
    max_total_weight: Decimal

    def __post_init__(self) -> None:
        for name, value in (
            ("max_strategy_weight", self.max_strategy_weight),
            ("max_cluster_weight", self.max_cluster_weight),
            ("max_total_weight", self.max_total_weight),
        ):
            if not _finite(value) or value <= _ZERO or value > _ONE:
                raise ValueError(f"{name} must be finite Decimal in (0,1]")
        if self.max_strategy_weight > self.max_cluster_weight:
            raise ValueError("max_strategy_weight cannot exceed max_cluster_weight")
        if self.max_cluster_weight > self.max_total_weight:
            raise ValueError("max_cluster_weight cannot exceed max_total_weight")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "max_strategy_weight": str(self.max_strategy_weight),
                "max_cluster_weight": str(self.max_cluster_weight),
                "max_total_weight": str(self.max_total_weight),
            }
        )


@dataclass(frozen=True, slots=True)
class AllocationBudgetEvidence:
    dependence_fingerprint: str
    policy_fingerprint: str
    strategy_weights: tuple[tuple[str, Decimal], ...]
    cluster_weights: tuple[tuple[tuple[str, ...], Decimal], ...]
    total_weight: Decimal

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "dependence_fingerprint": self.dependence_fingerprint,
                "policy_fingerprint": self.policy_fingerprint,
                "strategy_weights": [[key, str(value)] for key, value in self.strategy_weights],
                "cluster_weights": [
                    [list(cluster), str(value)] for cluster, value in self.cluster_weights
                ],
                "total_weight": str(self.total_weight),
            }
        )


def build_dependence_evidence(
    series: tuple[StrategyReturnSeries, ...],
    spec: DependenceSpec,
) -> DependenceEvidence:
    if not isinstance(spec, DependenceSpec):
        raise TypeError("spec must be DependenceSpec")
    if len(series) < 2:
        raise InsufficientDependenceEvidence("at least two strategy series are required")

    by_key: dict[str, StrategyReturnSeries] = {}
    for item in series:
        if not isinstance(item, StrategyReturnSeries):
            raise ValueError("series must contain StrategyReturnSeries")
        if item.phase is not spec.phase:
            raise PortfolioDependenceError(
                f"strategy {item.strategy_key} phase {item.phase.value} does not match calibration phase {spec.phase.value}"
            )
        if item.strategy_key in by_key:
            raise PortfolioDependenceError(f"duplicate strategy key: {item.strategy_key}")
        by_key[item.strategy_key] = item

    keys = tuple(sorted(by_key))
    timestamp_maps = {
        key: {observation.occurred_at: observation.value for observation in by_key[key].observations}
        for key in keys
    }
    common = set(timestamp_maps[keys[0]])
    for key in keys[1:]:
        common.intersection_update(timestamp_maps[key])
    common_timestamps = tuple(sorted(common))
    if len(common_timestamps) < spec.min_common_observations:
        raise InsufficientDependenceEvidence(
            f"common observation count {len(common_timestamps)} is below required {spec.min_common_observations}"
        )

    aligned = {
        key: tuple(timestamp_maps[key][timestamp] for timestamp in common_timestamps)
        for key in keys
    }
    pairs: list[PairCorrelation] = []
    for left_index, left in enumerate(keys):
        for right in keys[left_index + 1 :]:
            pairs.append(
                PairCorrelation(
                    left_strategy=left,
                    right_strategy=right,
                    correlation=_pearson(aligned[left], aligned[right]),
                )
            )
    clusters = _clusters(keys, tuple(pairs), spec.cluster_abs_correlation)
    return DependenceEvidence(
        phase=spec.phase,
        min_common_observations=spec.min_common_observations,
        cluster_abs_correlation=spec.cluster_abs_correlation,
        spec_fingerprint=spec.fingerprint,
        strategy_fingerprints=tuple((key, by_key[key].fingerprint) for key in keys),
        common_timestamps=common_timestamps,
        aligned_returns=tuple((key, aligned[key]) for key in keys),
        pairs=tuple(pairs),
        clusters=clusters,
    )


def validate_allocation_budget(
    evidence: DependenceEvidence,
    policy: DiversificationBudgetPolicy,
    strategy_weights: Mapping[str, Decimal],
) -> AllocationBudgetEvidence:
    if not isinstance(evidence, DependenceEvidence):
        raise TypeError("evidence must be DependenceEvidence")
    if not isinstance(policy, DiversificationBudgetPolicy):
        raise TypeError("policy must be DiversificationBudgetPolicy")
    if not isinstance(strategy_weights, Mapping):
        raise TypeError("strategy_weights must be a mapping")
    expected = set(evidence.strategy_keys)
    actual = set(strategy_weights)
    if actual != expected:
        raise PortfolioBudgetViolation(
            f"allocation universe mismatch; missing={sorted(expected-actual)} extra={sorted(actual-expected)}"
        )

    canonical: list[tuple[str, Decimal]] = []
    for key in evidence.strategy_keys:
        value = strategy_weights[key]
        if not _finite(value) or value < _ZERO:
            raise PortfolioBudgetViolation(f"weight for {key} must be finite Decimal >= 0")
        if value > policy.max_strategy_weight:
            raise PortfolioBudgetViolation(f"weight for {key} exceeds max_strategy_weight")
        canonical.append((key, value))

    total = sum((value for _, value in canonical), _ZERO)
    if total > policy.max_total_weight:
        raise PortfolioBudgetViolation("allocation exceeds max_total_weight")

    weight_map = dict(canonical)
    cluster_weights: list[tuple[tuple[str, ...], Decimal]] = []
    for cluster in evidence.clusters:
        cluster_weight = sum((weight_map[key] for key in cluster), _ZERO)
        if cluster_weight > policy.max_cluster_weight:
            raise PortfolioBudgetViolation(
                f"cluster {cluster} exceeds max_cluster_weight"
            )
        cluster_weights.append((cluster, cluster_weight))

    return AllocationBudgetEvidence(
        dependence_fingerprint=evidence.fingerprint,
        policy_fingerprint=policy.fingerprint,
        strategy_weights=tuple(canonical),
        cluster_weights=tuple(cluster_weights),
        total_weight=total,
    )


def _pearson(left: tuple[Decimal, ...], right: tuple[Decimal, ...]) -> Decimal:
    if len(left) != len(right) or len(left) < 2:
        raise InsufficientDependenceEvidence("Pearson correlation requires equal samples >= 2")
    with localcontext() as context:
        context.prec = 50
        count = Decimal(len(left))
        left_mean = sum(left, _ZERO) / count
        right_mean = sum(right, _ZERO) / count
        left_deltas = tuple(value - left_mean for value in left)
        right_deltas = tuple(value - right_mean for value in right)
        numerator = sum((a * b for a, b in zip(left_deltas, right_deltas)), _ZERO)
        left_ss = sum((value * value for value in left_deltas), _ZERO)
        right_ss = sum((value * value for value in right_deltas), _ZERO)
        if left_ss == _ZERO or right_ss == _ZERO:
            raise InsufficientDependenceEvidence("zero-variance series cannot calibrate correlation")
        denominator = (left_ss * right_ss).sqrt()
        result = numerator / denominator
        # Decimal rounding can only move a mathematically bounded Pearson value
        # by tiny representational epsilon; clamp solely to its mathematical domain.
        if result > _ONE:
            result = _ONE
        elif result < -_ONE:
            result = -_ONE
        return +result


def _clusters(
    keys: tuple[str, ...],
    pairs: tuple[PairCorrelation, ...],
    threshold: Decimal,
) -> tuple[tuple[str, ...], ...]:
    adjacency = {key: set() for key in keys}
    for pair in pairs:
        if abs(pair.correlation) >= threshold:
            adjacency[pair.left_strategy].add(pair.right_strategy)
            adjacency[pair.right_strategy].add(pair.left_strategy)

    remaining = set(keys)
    clusters: list[tuple[str, ...]] = []
    while remaining:
        root = min(remaining)
        stack = [root]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(sorted(adjacency[current] - component, reverse=True))
        remaining.difference_update(component)
        clusters.append(tuple(sorted(component)))
    return tuple(sorted(clusters))


def _canonical_identity(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    if value != value.strip():
        raise ValueError(f"{name} must not contain surrounding whitespace")


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _finite(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


def _hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()
