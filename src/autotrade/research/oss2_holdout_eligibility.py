"""OSS-2E preregistered gate for one FINAL_HOLDOUT evaluation.

OSS-2E consumes only the immutable OSS-2D DEVELOPMENT robustness package. It
never reads, accepts, imports or evaluates FINAL_HOLDOUT data. A positive
result means only that the already-frozen OSS-2 candidate is scientifically
eligible to consume one future holdout evaluation under a separate boundary.
It grants no broker, network, OMS, capital, OrderIntent, PAPER or LIVE
authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from math import isfinite

from .oss2_robustness import (
    OSS2RobustnessEvidence,
    canonical_oss2d_policy,
)


_EXPECTED_PBO_PARTITIONS = 8
_EXPECTED_PBO_COMBINATIONS = 70
_EXPECTED_BOOTSTRAP_ITERATIONS = 2_000
_EXPECTED_BOOTSTRAP_BLOCK_SIZE = 4
_EXPECTED_BOOTSTRAP_SEED = 20_260_904
_EXPECTED_FAMILY_SIZE = 12
_EXPECTED_METRIC = "common_window_sharpe"
_EXPECTED_COST_MULTIPLIERS = (1.5, 2.0)


class OSS2HoldoutEligibilityGovernanceError(RuntimeError):
    """Raised when OSS-2E receives evidence outside its frozen authority."""


class OSS2HoldoutEligibilityDecision(str, Enum):
    HOLDOUT_ELIGIBLE = "HOLDOUT_ELIGIBLE"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class OSS2HoldoutEligibilityPolicy:
    """Frozen DEVELOPMENT-only thresholds set before FINAL_HOLDOUT observation."""

    max_pbo: float = 0.35
    min_deflated_sharpe_probability: float = 0.80
    min_bootstrap_probability_positive: float = 0.60
    min_bootstrap_median_compounded_return: float = 0.0
    min_bootstrap_lower_compounded_return: float = -0.10
    min_stressed_net_return: float = 0.0
    min_stressed_sharpe: float = 0.0
    max_stressed_drawdown: float = 0.35
    min_neighbor_median_sharpe: float = 0.0
    min_fraction_selected_at_least_neighbor: float = 0.50
    min_selected_minus_neighbor_median: float = -0.25

    def __post_init__(self) -> None:
        probability_fields = (
            self.max_pbo,
            self.min_deflated_sharpe_probability,
            self.min_bootstrap_probability_positive,
            self.max_stressed_drawdown,
            self.min_fraction_selected_at_least_neighbor,
        )
        if any(not isfinite(value) or value < 0 or value > 1 for value in probability_fields):
            raise ValueError("OSS-2E probability/drawdown thresholds must be finite in [0,1]")
        scalar_fields = (
            self.min_bootstrap_median_compounded_return,
            self.min_bootstrap_lower_compounded_return,
            self.min_stressed_net_return,
            self.min_stressed_sharpe,
            self.min_neighbor_median_sharpe,
            self.min_selected_minus_neighbor_median,
        )
        if any(not isfinite(value) for value in scalar_fields):
            raise ValueError("OSS-2E scalar thresholds must be finite")
        if self.min_bootstrap_lower_compounded_return > self.min_bootstrap_median_compounded_return:
            raise ValueError("OSS-2E bootstrap lower threshold cannot exceed median threshold")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "max_pbo": self.max_pbo,
                "min_deflated_sharpe_probability": self.min_deflated_sharpe_probability,
                "min_bootstrap_probability_positive": self.min_bootstrap_probability_positive,
                "min_bootstrap_median_compounded_return": self.min_bootstrap_median_compounded_return,
                "min_bootstrap_lower_compounded_return": self.min_bootstrap_lower_compounded_return,
                "min_stressed_net_return": self.min_stressed_net_return,
                "min_stressed_sharpe": self.min_stressed_sharpe,
                "max_stressed_drawdown": self.max_stressed_drawdown,
                "min_neighbor_median_sharpe": self.min_neighbor_median_sharpe,
                "min_fraction_selected_at_least_neighbor": self.min_fraction_selected_at_least_neighbor,
                "min_selected_minus_neighbor_median": self.min_selected_minus_neighbor_median,
            }
        )


@dataclass(frozen=True, slots=True)
class OSS2HoldoutEligibilityGate:
    gate_id: str
    passed: bool
    observed: float
    comparison: str
    threshold: float

    def __post_init__(self) -> None:
        if not self.gate_id.strip():
            raise ValueError("OSS-2E gate_id is required")
        if self.comparison not in {"<=", ">="}:
            raise ValueError("OSS-2E comparison must be <= or >=")
        if not isfinite(self.observed) or not isfinite(self.threshold):
            raise ValueError("OSS-2E gate values must be finite")
        expected = self.observed <= self.threshold if self.comparison == "<=" else self.observed >= self.threshold
        if self.passed is not expected:
            raise ValueError("OSS-2E gate pass flag does not match comparison")


@dataclass(frozen=True, slots=True)
class OSS2HoldoutEligibilityEvidence:
    campaign_id: str
    selected_trial_id: str
    oss2d_evidence_fingerprint: str
    policy_fingerprint: str
    decision: OSS2HoldoutEligibilityDecision
    gates: tuple[OSS2HoldoutEligibilityGate, ...]
    failed_gate_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.campaign_id.strip() or not self.selected_trial_id.strip():
            raise ValueError("OSS-2E campaign and selected trial are required")
        _sha(self.oss2d_evidence_fingerprint, "oss2d_evidence_fingerprint")
        _sha(self.policy_fingerprint, "policy_fingerprint")
        if not self.gates:
            raise ValueError("OSS-2E requires at least one gate")
        gate_ids = tuple(gate.gate_id for gate in self.gates)
        if len(set(gate_ids)) != len(gate_ids):
            raise ValueError("OSS-2E gate ids must be unique")
        expected_failed = tuple(gate.gate_id for gate in self.gates if not gate.passed)
        if self.failed_gate_ids != expected_failed:
            raise ValueError("OSS-2E failed gate list must match gate evidence")
        expected_decision = (
            OSS2HoldoutEligibilityDecision.HOLDOUT_ELIGIBLE
            if not expected_failed
            else OSS2HoldoutEligibilityDecision.REJECT
        )
        if self.decision is not expected_decision:
            raise ValueError("OSS-2E decision must be mechanically derived from gates")

    @property
    def candidate_freeze_fingerprint(self) -> str:
        """Hash freezing the exact candidate and DEVELOPMENT evidence before holdout."""
        return _hash(
            {
                "campaign_id": self.campaign_id,
                "selected_trial_id": self.selected_trial_id,
                "oss2d_evidence_fingerprint": self.oss2d_evidence_fingerprint,
                "policy_fingerprint": self.policy_fingerprint,
                "decision": self.decision.value,
            }
        )

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "campaign_id": self.campaign_id,
                "selected_trial_id": self.selected_trial_id,
                "oss2d_evidence_fingerprint": self.oss2d_evidence_fingerprint,
                "policy_fingerprint": self.policy_fingerprint,
                "decision": self.decision.value,
                "failed_gate_ids": list(self.failed_gate_ids),
                "gates": [
                    {
                        "gate_id": gate.gate_id,
                        "passed": gate.passed,
                        "observed": gate.observed,
                        "comparison": gate.comparison,
                        "threshold": gate.threshold,
                    }
                    for gate in self.gates
                ],
            }
        )


def canonical_oss2e_policy() -> OSS2HoldoutEligibilityPolicy:
    """Return the fixed OSS-2E policy; callers cannot tune it to observed evidence."""
    return OSS2HoldoutEligibilityPolicy()


def evaluate_oss2e_holdout_eligibility(
    robustness: OSS2RobustnessEvidence,
) -> OSS2HoldoutEligibilityEvidence:
    """Decide whether frozen OSS-2 DEVELOPMENT evidence may proceed to holdout.

    The function has deliberately no holdout argument and no I/O surface.
    """
    policy = canonical_oss2e_policy()
    _verify_oss2d_contract(robustness)

    stress = {float(item.multiplier): item for item in robustness.cost_stress}
    gates = (
        _le("PBO_MAX", robustness.pbo.pbo, policy.max_pbo),
        _ge(
            "DEFLATED_SHARPE_PROBABILITY_MIN",
            robustness.deflated_sharpe.deflated_sharpe_probability,
            policy.min_deflated_sharpe_probability,
        ),
        _ge(
            "BOOTSTRAP_PROBABILITY_POSITIVE_MIN",
            robustness.bootstrap.probability_positive,
            policy.min_bootstrap_probability_positive,
        ),
        _ge(
            "BOOTSTRAP_MEDIAN_RETURN_MIN",
            robustness.bootstrap.median_compounded_return,
            policy.min_bootstrap_median_compounded_return,
        ),
        _ge(
            "BOOTSTRAP_LOWER_RETURN_MIN",
            robustness.bootstrap.lower_compounded_return,
            policy.min_bootstrap_lower_compounded_return,
        ),
        _ge(
            "COST_1_5X_NET_RETURN_MIN",
            stress[1.5].common_window_net_return,
            policy.min_stressed_net_return,
        ),
        _ge(
            "COST_1_5X_SHARPE_MIN",
            stress[1.5].common_window_sharpe,
            policy.min_stressed_sharpe,
        ),
        _le(
            "COST_1_5X_DRAWDOWN_MAX",
            stress[1.5].common_window_max_drawdown,
            policy.max_stressed_drawdown,
        ),
        _ge(
            "COST_2X_NET_RETURN_MIN",
            stress[2.0].common_window_net_return,
            policy.min_stressed_net_return,
        ),
        _ge(
            "COST_2X_SHARPE_MIN",
            stress[2.0].common_window_sharpe,
            policy.min_stressed_sharpe,
        ),
        _le(
            "COST_2X_DRAWDOWN_MAX",
            stress[2.0].common_window_max_drawdown,
            policy.max_stressed_drawdown,
        ),
        _ge(
            "LOCAL_NEIGHBOR_MEDIAN_SHARPE_MIN",
            robustness.local_sensitivity.neighbor_median_sharpe,
            policy.min_neighbor_median_sharpe,
        ),
        _ge(
            "LOCAL_SELECTED_FRACTION_MIN",
            robustness.local_sensitivity.fraction_selected_at_least_neighbor,
            policy.min_fraction_selected_at_least_neighbor,
        ),
        _ge(
            "LOCAL_SELECTED_MINUS_MEDIAN_MIN",
            robustness.local_sensitivity.selected_minus_neighbor_median,
            policy.min_selected_minus_neighbor_median,
        ),
    )
    failed = tuple(gate.gate_id for gate in gates if not gate.passed)
    decision = (
        OSS2HoldoutEligibilityDecision.HOLDOUT_ELIGIBLE
        if not failed
        else OSS2HoldoutEligibilityDecision.REJECT
    )
    return OSS2HoldoutEligibilityEvidence(
        campaign_id=robustness.campaign_id,
        selected_trial_id=robustness.selected_trial_id,
        oss2d_evidence_fingerprint=robustness.fingerprint,
        policy_fingerprint=policy.fingerprint,
        decision=decision,
        gates=gates,
        failed_gate_ids=failed,
    )


def _verify_oss2d_contract(robustness: OSS2RobustnessEvidence) -> None:
    if robustness.policy_fingerprint != canonical_oss2d_policy().fingerprint:
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2D policy fingerprint mismatch")
    if robustness.pbo.partitions != _EXPECTED_PBO_PARTITIONS:
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2D PBO partition count mismatch")
    if robustness.pbo.balanced_partitions is not True:
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2E requires balanced OSS-2D PBO")
    if robustness.pbo.combinations_evaluated != _EXPECTED_PBO_COMBINATIONS:
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2D PBO combination count mismatch")
    if not isfinite(robustness.pbo.pbo) or not 0 <= robustness.pbo.pbo <= 1:
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2D PBO value invalid")
    if robustness.deflated_sharpe.family_size != _EXPECTED_FAMILY_SIZE:
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2D family size mismatch")
    if robustness.deflated_sharpe.metric_name != _EXPECTED_METRIC:
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2D Deflated Sharpe metric mismatch")
    if robustness.deflated_sharpe.selected_trial_id != robustness.selected_trial_id:
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2D selected trial mismatch")
    probability = robustness.deflated_sharpe.deflated_sharpe_probability
    if not isfinite(probability) or not 0 <= probability <= 1:
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2D Deflated Sharpe probability invalid")
    bootstrap = robustness.bootstrap
    if (
        bootstrap.iterations != _EXPECTED_BOOTSTRAP_ITERATIONS
        or bootstrap.block_size != _EXPECTED_BOOTSTRAP_BLOCK_SIZE
        or bootstrap.seed != _EXPECTED_BOOTSTRAP_SEED
    ):
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2D bootstrap policy mismatch")
    if bootstrap.observations != robustness.deflated_sharpe.sample_size:
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2D sample-size binding mismatch")
    multipliers = tuple(float(item.multiplier) for item in robustness.cost_stress)
    if multipliers != _EXPECTED_COST_MULTIPLIERS:
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2D cost stress universe mismatch")
    for item in robustness.cost_stress:
        if (
            not isfinite(item.common_window_net_return)
            or not isfinite(item.common_window_sharpe)
            or not isfinite(item.common_window_max_drawdown)
            or not 0 <= item.common_window_max_drawdown <= 1
        ):
            raise OSS2HoldoutEligibilityGovernanceError("OSS-2D cost stress evidence invalid")
    local = robustness.local_sensitivity
    if len(local.neighbors) < 2:
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2D local sensitivity is incomplete")
    if not all(
        isfinite(value)
        for value in (
            local.neighbor_median_sharpe,
            local.fraction_selected_at_least_neighbor,
            local.selected_minus_neighbor_median,
        )
    ):
        raise OSS2HoldoutEligibilityGovernanceError("OSS-2D local sensitivity is invalid")


def _le(gate_id: str, observed: float, threshold: float) -> OSS2HoldoutEligibilityGate:
    return OSS2HoldoutEligibilityGate(
        gate_id=gate_id,
        passed=observed <= threshold,
        observed=float(observed),
        comparison="<=",
        threshold=float(threshold),
    )


def _ge(gate_id: str, observed: float, threshold: float) -> OSS2HoldoutEligibilityGate:
    return OSS2HoldoutEligibilityGate(
        gate_id=gate_id,
        passed=observed >= threshold,
        observed=float(observed),
        comparison=">=",
        threshold=float(threshold),
    )


def _sha(value: str, name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be lowercase sha256 hex")


def _hash(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
