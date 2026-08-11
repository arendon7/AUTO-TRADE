from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import StrEnum
from hashlib import sha256
import json
from typing import Mapping

from .portfolio_dependence import (
    AllocationBudgetEvidence,
    DependenceEvidence,
    DiversificationBudgetPolicy,
    PortfolioBudgetViolation,
    validate_allocation_budget,
)


_ZERO = Decimal("0")
_ONE = Decimal("1")


class AllocationRobustnessError(RuntimeError):
    pass


class FragileAllocation(AllocationRobustnessError):
    pass


class ScenarioKind(StrEnum):
    PERTURBATION = "PERTURBATION"
    LEAVE_ONE_OUT = "LEAVE_ONE_OUT"


@dataclass(frozen=True, slots=True)
class AllocationRobustnessSpec:
    perturbation_weight: Decimal

    def __post_init__(self) -> None:
        if (
            not _finite(self.perturbation_weight)
            or self.perturbation_weight <= _ZERO
            or self.perturbation_weight >= _ONE
        ):
            raise ValueError("perturbation_weight must be finite Decimal in (0,1)")

    @property
    def fingerprint(self) -> str:
        return _hash({"perturbation_weight": str(self.perturbation_weight)})


@dataclass(frozen=True, slots=True)
class AllocationRobustnessPolicy:
    max_mean_degradation_fraction: Decimal
    max_volatility_increase_fraction: Decimal

    def __post_init__(self) -> None:
        for name, value in (
            ("max_mean_degradation_fraction", self.max_mean_degradation_fraction),
            ("max_volatility_increase_fraction", self.max_volatility_increase_fraction),
        ):
            if not _finite(value) or value < _ZERO or value > _ONE:
                raise ValueError(f"{name} must be finite Decimal in [0,1]")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "max_mean_degradation_fraction": str(self.max_mean_degradation_fraction),
                "max_volatility_increase_fraction": str(self.max_volatility_increase_fraction),
            }
        )


@dataclass(frozen=True, slots=True)
class AllocationScenario:
    scenario_id: str
    kind: ScenarioKind
    weights: tuple[tuple[str, Decimal], ...]
    mean_return: Decimal
    volatility: Decimal
    mean_degradation_fraction: Decimal
    volatility_increase_fraction: Decimal
    passes_policy: bool

    def __post_init__(self) -> None:
        if not self.scenario_id.strip() or self.scenario_id != self.scenario_id.strip():
            raise ValueError("scenario_id must be canonical non-empty text")
        if not isinstance(self.kind, ScenarioKind):
            raise ValueError("kind must be ScenarioKind")
        keys = tuple(key for key, _ in self.weights)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("scenario weights must be unique canonical sorted order")
        if any(not _finite(value) or value < _ZERO for _, value in self.weights):
            raise ValueError("scenario weights must be finite Decimal >= 0")
        total = sum((value for _, value in self.weights), _ZERO)
        if total != _ONE:
            raise ValueError("scenario normalized weights must sum exactly to 1")
        for value in (
            self.mean_return,
            self.volatility,
            self.mean_degradation_fraction,
            self.volatility_increase_fraction,
        ):
            if not _finite(value):
                raise ValueError("scenario metrics must be finite Decimal")
        if self.mean_degradation_fraction < _ZERO:
            raise ValueError("mean_degradation_fraction cannot be negative")
        if self.volatility_increase_fraction < _ZERO:
            raise ValueError("volatility_increase_fraction cannot be negative")
        if not isinstance(self.passes_policy, bool):
            raise ValueError("passes_policy must be boolean")


@dataclass(frozen=True, slots=True)
class AllocationRobustnessEvidence:
    dependence_fingerprint: str
    budget_evidence_fingerprint: str
    spec_fingerprint: str
    policy_fingerprint: str
    baseline_normalized_weights: tuple[tuple[str, Decimal], ...]
    baseline_mean_return: Decimal
    baseline_volatility: Decimal
    scenarios: tuple[AllocationScenario, ...]

    def __post_init__(self) -> None:
        for name, value in (
            ("dependence_fingerprint", self.dependence_fingerprint),
            ("budget_evidence_fingerprint", self.budget_evidence_fingerprint),
            ("spec_fingerprint", self.spec_fingerprint),
            ("policy_fingerprint", self.policy_fingerprint),
        ):
            if not _sha256(value):
                raise ValueError(f"{name} must be SHA-256 hex")
        keys = tuple(key for key, _ in self.baseline_normalized_weights)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("baseline weights must be canonical unique sorted order")
        if sum((value for _, value in self.baseline_normalized_weights), _ZERO) != _ONE:
            raise ValueError("baseline normalized weights must sum exactly to 1")
        if self.baseline_mean_return <= _ZERO or not _finite(self.baseline_mean_return):
            raise AllocationRobustnessError("baseline mean return must be finite and > 0")
        if self.baseline_volatility <= _ZERO or not _finite(self.baseline_volatility):
            raise AllocationRobustnessError("baseline volatility must be finite and > 0")
        scenario_ids = tuple(scenario.scenario_id for scenario in self.scenarios)
        if scenario_ids != tuple(sorted(scenario_ids)) or len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("scenarios must use unique canonical sorted ids")
        if not self.scenarios:
            raise AllocationRobustnessError("robustness evidence requires scenarios")

    @property
    def robust(self) -> bool:
        return all(scenario.passes_policy for scenario in self.scenarios)

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_payload(include_fingerprint=False))

    def to_payload(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "dependence_fingerprint": self.dependence_fingerprint,
            "budget_evidence_fingerprint": self.budget_evidence_fingerprint,
            "spec_fingerprint": self.spec_fingerprint,
            "policy_fingerprint": self.policy_fingerprint,
            "baseline_normalized_weights": [
                [key, str(value)] for key, value in self.baseline_normalized_weights
            ],
            "baseline_mean_return": str(self.baseline_mean_return),
            "baseline_volatility": str(self.baseline_volatility),
            "scenarios": [
                {
                    "scenario_id": scenario.scenario_id,
                    "kind": scenario.kind.value,
                    "weights": [[key, str(value)] for key, value in scenario.weights],
                    "mean_return": str(scenario.mean_return),
                    "volatility": str(scenario.volatility),
                    "mean_degradation_fraction": str(scenario.mean_degradation_fraction),
                    "volatility_increase_fraction": str(scenario.volatility_increase_fraction),
                    "passes_policy": scenario.passes_policy,
                }
                for scenario in self.scenarios
            ],
        }
        if include_fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload


def evaluate_allocation_robustness(
    dependence: DependenceEvidence,
    budget_policy: DiversificationBudgetPolicy,
    strategy_weights: Mapping[str, Decimal],
    spec: AllocationRobustnessSpec,
    policy: AllocationRobustnessPolicy,
) -> AllocationRobustnessEvidence:
    """Evaluate composition robustness without optimizing or submitting capital.

    The proposed *absolute* weights must first pass the certified diversification
    budgets. Performance scenarios then operate on normalized composition so
    leave-one-out does not look fragile merely because it intentionally lowers
    total exposure. No scenario is used to choose a new live allocation here.
    """

    budget_evidence = validate_allocation_budget(
        dependence,
        budget_policy,
        strategy_weights,
    )
    positive = tuple((key, value) for key, value in budget_evidence.strategy_weights if value > _ZERO)
    if len(positive) < 2:
        raise AllocationRobustnessError("robustness requires at least two positive-weight strategies")
    normalized = _normalize_exact(positive)

    aligned = dict(dependence.aligned_returns)
    baseline_returns = _portfolio_returns(normalized, aligned)
    baseline_mean, baseline_volatility = _mean_volatility(baseline_returns)
    if baseline_mean <= _ZERO:
        raise AllocationRobustnessError("baseline mean return must be > 0 for degradation analysis")
    if baseline_volatility <= _ZERO:
        raise AllocationRobustnessError("baseline volatility must be > 0 for robustness analysis")

    scenarios: list[AllocationScenario] = []
    baseline_map = dict(normalized)
    keys = tuple(key for key, _ in normalized)

    for removed in keys:
        remaining = tuple(
            (key, baseline_map[key])
            for key in keys
            if key != removed and baseline_map[key] > _ZERO
        )
        if not remaining:
            raise AllocationRobustnessError("leave-one-out requires positive remaining allocation")
        remaining_normalized = dict(_normalize_exact(remaining))
        weights = tuple(
            (key, (_ZERO if key == removed else remaining_normalized[key]))
            for key in keys
        )
        scenarios.append(
            _scenario(
                scenario_id=f"loo:{removed}",
                kind=ScenarioKind.LEAVE_ONE_OUT,
                weights=weights,
                aligned=aligned,
                baseline_mean=baseline_mean,
                baseline_volatility=baseline_volatility,
                policy=policy,
            )
        )

    for donor in keys:
        for receiver in keys:
            if donor == receiver:
                continue
            delta = min(spec.perturbation_weight, baseline_map[donor])
            if delta <= _ZERO:
                continue
            changed = dict(baseline_map)
            changed[donor] -= delta
            changed[receiver] += delta
            weights = _normalize_exact(tuple((key, changed[key]) for key in keys))
            scenarios.append(
                _scenario(
                    scenario_id=f"shift:{donor}->{receiver}:{delta}",
                    kind=ScenarioKind.PERTURBATION,
                    weights=weights,
                    aligned=aligned,
                    baseline_mean=baseline_mean,
                    baseline_volatility=baseline_volatility,
                    policy=policy,
                )
            )

    scenarios.sort(key=lambda value: value.scenario_id)
    return AllocationRobustnessEvidence(
        dependence_fingerprint=dependence.fingerprint,
        budget_evidence_fingerprint=budget_evidence.fingerprint,
        spec_fingerprint=spec.fingerprint,
        policy_fingerprint=policy.fingerprint,
        baseline_normalized_weights=normalized,
        baseline_mean_return=baseline_mean,
        baseline_volatility=baseline_volatility,
        scenarios=tuple(scenarios),
    )


def require_robust_allocation(
    dependence: DependenceEvidence,
    budget_policy: DiversificationBudgetPolicy,
    strategy_weights: Mapping[str, Decimal],
    spec: AllocationRobustnessSpec,
    policy: AllocationRobustnessPolicy,
) -> AllocationRobustnessEvidence:
    """Recompute the complete robustness universe before granting a PASS.

    A serialized or manually constructed AllocationRobustnessEvidence is audit
    evidence, never a self-authorizing token. Consumers that need a gate must
    provide the original self-validating dependence evidence and frozen policy
    inputs so the scenarios are rebuilt deterministically.
    """

    evidence = evaluate_allocation_robustness(
        dependence,
        budget_policy,
        strategy_weights,
        spec,
        policy,
    )
    failed = tuple(
        scenario.scenario_id for scenario in evidence.scenarios if not scenario.passes_policy
    )
    if failed:
        raise FragileAllocation(f"allocation failed robustness scenarios: {failed}")
    return evidence


def _scenario(
    *,
    scenario_id: str,
    kind: ScenarioKind,
    weights: tuple[tuple[str, Decimal], ...],
    aligned: Mapping[str, tuple[Decimal, ...]],
    baseline_mean: Decimal,
    baseline_volatility: Decimal,
    policy: AllocationRobustnessPolicy,
) -> AllocationScenario:
    returns = _portfolio_returns(weights, aligned)
    mean_return, volatility = _mean_volatility(returns)
    degradation = max(_ZERO, (baseline_mean - mean_return) / abs(baseline_mean))
    volatility_increase = max(
        _ZERO,
        (volatility - baseline_volatility) / baseline_volatility,
    )
    passes = (
        degradation <= policy.max_mean_degradation_fraction
        and volatility_increase <= policy.max_volatility_increase_fraction
    )
    return AllocationScenario(
        scenario_id=scenario_id,
        kind=kind,
        weights=weights,
        mean_return=mean_return,
        volatility=volatility,
        mean_degradation_fraction=degradation,
        volatility_increase_fraction=volatility_increase,
        passes_policy=passes,
    )


def _normalize_exact(
    weights: tuple[tuple[str, Decimal], ...],
) -> tuple[tuple[str, Decimal], ...]:
    """Normalize canonical weights while preserving an exact Decimal sum of 1.

    Decimal division of repeating ratios cannot represent every fraction exactly.
    Dividing each component independently can therefore create a vector whose
    arithmetic sum is one representational ulp away from 1. We preserve the
    exact-sum contract by computing all but the final canonical component and
    assigning that final component the exact remainder. This is deterministic,
    does not change the universe/order, and never relaxes the invariant checked
    by AllocationScenario.
    """

    if not weights:
        raise AllocationRobustnessError("weights cannot be empty")
    keys = tuple(key for key, _ in weights)
    if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
        raise AllocationRobustnessError("weights must be canonical unique sorted order")
    if any(not _finite(value) or value < _ZERO for _, value in weights):
        raise AllocationRobustnessError("weights must be finite Decimal >= 0")
    total = sum((value for _, value in weights), _ZERO)
    if total <= _ZERO:
        raise AllocationRobustnessError("weights must contain positive allocation")

    normalized: list[tuple[str, Decimal]] = []
    running = _ZERO
    for index, (key, value) in enumerate(weights):
        if index == len(weights) - 1:
            normalized_value = _ONE - running
        else:
            normalized_value = value / total
            running += normalized_value
        if normalized_value < _ZERO:
            raise AllocationRobustnessError("normalization produced negative weight")
        normalized.append((key, normalized_value))
    result = tuple(normalized)
    if sum((value for _, value in result), _ZERO) != _ONE:
        raise AllocationRobustnessError("exact normalization failed to sum to 1")
    return result


def _portfolio_returns(
    weights: tuple[tuple[str, Decimal], ...],
    aligned: Mapping[str, tuple[Decimal, ...]],
) -> tuple[Decimal, ...]:
    if not weights:
        raise AllocationRobustnessError("weights cannot be empty")
    length: int | None = None
    for key, _ in weights:
        values = aligned.get(key)
        if values is None:
            raise AllocationRobustnessError(f"missing aligned returns for {key}")
        if length is None:
            length = len(values)
        elif len(values) != length:
            raise AllocationRobustnessError("aligned return lengths differ")
    assert length is not None
    return tuple(
        sum((weight * aligned[key][index] for key, weight in weights), _ZERO)
        for index in range(length)
    )


def _mean_volatility(values: tuple[Decimal, ...]) -> tuple[Decimal, Decimal]:
    if len(values) < 2:
        raise AllocationRobustnessError("at least two observations are required")
    with localcontext() as context:
        context.prec = 50
        count = Decimal(len(values))
        mean = sum(values, _ZERO) / count
        squared = sum(((value - mean) ** 2 for value in values), _ZERO)
        variance = squared / count
        return +mean, +variance.sqrt()


def _finite(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


def _sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()
