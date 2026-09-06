"""OSS-3D2J preregistered predictive FINAL_HOLDOUT protocol.

D2J freezes the exact D2I DEVELOPMENT ranking winner, the exact protected
FINAL_HOLDOUT identity commitment and the ex-ante predictive decision rule for
one later evaluation.  It does not accept raw holdout label values, inspect
holdout outcomes, issue/consume a permit, or evaluate the holdout.

The protocol is predictive rather than economic.  Even a later PASS may only
establish one-shot out-of-sample predictive evidence.  It cannot establish
trading profitability or authorize promotion, PAPER, LIVE or capital.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
import sqlite3
from typing import Mapping

from autotrade.research.oss3_development_model_tournament import PRIMARY_METRIC

from .development_winner_seal import (
    OSS3D2I_CONTRACT_VERSION,
    SELECTION_SCOPE,
    DevelopmentWinnerSealError,
    DevelopmentWinnerSelectionSeal,
    verify_development_winner_seal,
)
from .family_evaluation_batch import (
    FamilyEvaluationBatchEvidence,
    FamilyEvaluationPreregistration,
)


OSS3D2J_CONTRACT_VERSION = "OSS3D2J_FINAL_HOLDOUT_PROTOCOL_V1"
OSS3D2J_COMMITMENT_VERSION = "OSS3D2J_PROTECTED_FINAL_HOLDOUT_COMMITMENT_V1"
POLICY_ID = "OSS3D2J_PREDICTIVE_FINAL_VALIDATION_POLICY_V1"
FINAL_HOLDOUT_SPLIT = "FINAL_HOLDOUT"
FINAL_VALIDATION_PURPOSE = "final_validation"
SINGLE_CANDIDATE_POLICY = "ONE_FROZEN_WINNER_NO_RESELECTION_V1"
SIGN_TEST_POLICY = "ONE_SIDED_EXACT_SIGN_TEST_V1"
ZERO_IC_POLICY = "EXCLUDE_ZERO_RANK_IC_FROM_SIGN_TEST_V1"
MIN_MEAN_CROSS_SECTIONAL_RANK_IC = 0.02
MAX_ONE_SIDED_SIGN_TEST_P_VALUE = 0.05
MIN_HOLDOUT_CROSS_SECTIONS = 30
MIN_HOLDOUT_TOTAL_OBSERVATIONS = 90
MIN_CROSS_SECTION_OBSERVATIONS = 3
MIN_NONZERO_RANK_IC_CROSS_SECTIONS = 20
MAX_EVALUATIONS = 1

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")


class OSS3FinalHoldoutProtocolError(RuntimeError):
    """Base OSS-3D2J failure."""


class OSS3FinalHoldoutProtocolIntegrityError(OSS3FinalHoldoutProtocolError):
    """Stored or supplied protocol identity drifted."""


class OSS3FinalHoldoutProtocolGovernanceError(OSS3FinalHoldoutProtocolError):
    """Operation exceeds protocol-only FINAL_HOLDOUT governance."""


class OSS3FinalHoldoutProtocolConflict(OSS3FinalHoldoutProtocolError):
    """Append-only durable identity conflicts with an existing protocol."""


@dataclass(frozen=True, slots=True)
class OSS3ProtectedFinalHoldoutCommitment:
    """Value-opaque identity for the one future predictive holdout.

    The hashes may commit to protected feature/label artifacts, but this object
    intentionally contains no label values or per-row outcomes.  Structural
    counts and key hashes are safe to inspect before final validation and make
    sample adequacy and dataset substitution fail closed.
    """

    commitment_version: str
    source_campaign_id: str
    research_split_hash: str
    source_universe_hash: str
    label_definition_hash: str
    feature_artifact_hash: str
    label_artifact_hash: str
    evaluation_keyset_hash: str
    cross_section_key_hash: str
    partition_start: str
    partition_end: str
    row_count: int
    cross_section_count: int
    minimum_cross_section_observation_count: int
    label_values_exposed: bool = False
    final_holdout_observed: bool = False

    def __post_init__(self) -> None:
        if self.commitment_version != OSS3D2J_COMMITMENT_VERSION:
            raise OSS3FinalHoldoutProtocolIntegrityError(
                "noncanonical OSS-3D2J holdout commitment version"
            )
        _require_id(self.source_campaign_id, "source_campaign_id")
        for name in (
            "research_split_hash",
            "source_universe_hash",
            "label_definition_hash",
            "feature_artifact_hash",
            "label_artifact_hash",
            "evaluation_keyset_hash",
            "cross_section_key_hash",
        ):
            _require_hash(getattr(self, name), name)
        start = _parse_canonical_utc(self.partition_start, "partition_start")
        end = _parse_canonical_utc(self.partition_end, "partition_end")
        if not start < end:
            raise OSS3FinalHoldoutProtocolGovernanceError(
                "FINAL_HOLDOUT commitment window must be positive"
            )
        if (
            not isinstance(self.row_count, int)
            or isinstance(self.row_count, bool)
            or self.row_count < MIN_HOLDOUT_TOTAL_OBSERVATIONS
        ):
            raise OSS3FinalHoldoutProtocolGovernanceError(
                "FINAL_HOLDOUT row_count is below preregistered sample floor"
            )
        if (
            not isinstance(self.cross_section_count, int)
            or isinstance(self.cross_section_count, bool)
            or self.cross_section_count < MIN_HOLDOUT_CROSS_SECTIONS
        ):
            raise OSS3FinalHoldoutProtocolGovernanceError(
                "FINAL_HOLDOUT cross_section_count is below preregistered sample floor"
            )
        if (
            not isinstance(self.minimum_cross_section_observation_count, int)
            or isinstance(self.minimum_cross_section_observation_count, bool)
            or self.minimum_cross_section_observation_count < MIN_CROSS_SECTION_OBSERVATIONS
        ):
            raise OSS3FinalHoldoutProtocolGovernanceError(
                "FINAL_HOLDOUT cross sections are too small for Rank IC"
            )
        if self.row_count < self.cross_section_count * self.minimum_cross_section_observation_count:
            raise OSS3FinalHoldoutProtocolIntegrityError(
                "FINAL_HOLDOUT structural counts are internally inconsistent"
            )
        if self.label_values_exposed or self.final_holdout_observed:
            raise OSS3FinalHoldoutProtocolGovernanceError(
                "D2J requires a value-opaque, unobserved FINAL_HOLDOUT commitment"
            )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "commitment_version": self.commitment_version,
            "source_campaign_id": self.source_campaign_id,
            "research_split_hash": self.research_split_hash,
            "source_universe_hash": self.source_universe_hash,
            "label_definition_hash": self.label_definition_hash,
            "feature_artifact_hash": self.feature_artifact_hash,
            "label_artifact_hash": self.label_artifact_hash,
            "evaluation_keyset_hash": self.evaluation_keyset_hash,
            "cross_section_key_hash": self.cross_section_key_hash,
            "partition_start": self.partition_start,
            "partition_end": self.partition_end,
            "row_count": self.row_count,
            "cross_section_count": self.cross_section_count,
            "minimum_cross_section_observation_count": self.minimum_cross_section_observation_count,
            "label_values_exposed": self.label_values_exposed,
            "final_holdout_observed": self.final_holdout_observed,
        }


@dataclass(frozen=True, slots=True)
class OSS3FinalHoldoutProtocolPolicy:
    policy_id: str = POLICY_ID
    primary_metric: str = PRIMARY_METRIC
    min_mean_cross_sectional_rank_ic: float = MIN_MEAN_CROSS_SECTIONAL_RANK_IC
    max_one_sided_sign_test_p_value: float = MAX_ONE_SIDED_SIGN_TEST_P_VALUE
    min_holdout_cross_sections: int = MIN_HOLDOUT_CROSS_SECTIONS
    min_holdout_total_observations: int = MIN_HOLDOUT_TOTAL_OBSERVATIONS
    min_cross_section_observations: int = MIN_CROSS_SECTION_OBSERVATIONS
    min_nonzero_rank_ic_cross_sections: int = MIN_NONZERO_RANK_IC_CROSS_SECTIONS
    sign_test_policy: str = SIGN_TEST_POLICY
    zero_ic_policy: str = ZERO_IC_POLICY
    single_candidate_policy: str = SINGLE_CANDIDATE_POLICY
    max_evaluations: int = MAX_EVALUATIONS
    retuning_allowed: bool = False
    reselection_allowed: bool = False
    fallback_candidate_allowed: bool = False
    second_attempt_allowed: bool = False
    failure_is_terminal: bool = True
    split_name: str = FINAL_HOLDOUT_SPLIT
    permit_purpose: str = FINAL_VALIDATION_PURPOSE

    def __post_init__(self) -> None:
        if self.policy_id != POLICY_ID:
            raise OSS3FinalHoldoutProtocolGovernanceError("noncanonical D2J policy id")
        if self.primary_metric != PRIMARY_METRIC:
            raise OSS3FinalHoldoutProtocolGovernanceError(
                "D2J primary metric must remain D2E Rank IC"
            )
        if (
            isinstance(self.min_mean_cross_sectional_rank_ic, bool)
            or not isinstance(self.min_mean_cross_sectional_rank_ic, (int, float))
            or not isfinite(float(self.min_mean_cross_sectional_rank_ic))
            or float(self.min_mean_cross_sectional_rank_ic)
            != MIN_MEAN_CROSS_SECTIONAL_RANK_IC
        ):
            raise OSS3FinalHoldoutProtocolGovernanceError(
                "D2J Rank IC threshold is frozen"
            )
        if (
            isinstance(self.max_one_sided_sign_test_p_value, bool)
            or not isinstance(self.max_one_sided_sign_test_p_value, (int, float))
            or not isfinite(float(self.max_one_sided_sign_test_p_value))
            or float(self.max_one_sided_sign_test_p_value)
            != MAX_ONE_SIDED_SIGN_TEST_P_VALUE
        ):
            raise OSS3FinalHoldoutProtocolGovernanceError(
                "D2J sign-test threshold is frozen"
            )
        for name, expected in (
            ("min_holdout_cross_sections", MIN_HOLDOUT_CROSS_SECTIONS),
            ("min_holdout_total_observations", MIN_HOLDOUT_TOTAL_OBSERVATIONS),
            ("min_cross_section_observations", MIN_CROSS_SECTION_OBSERVATIONS),
            ("min_nonzero_rank_ic_cross_sections", MIN_NONZERO_RANK_IC_CROSS_SECTIONS),
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value != expected:
                raise OSS3FinalHoldoutProtocolGovernanceError(
                    f"D2J sample policy drifted: {name}"
                )
        if (
            self.sign_test_policy != SIGN_TEST_POLICY
            or self.zero_ic_policy != ZERO_IC_POLICY
            or self.single_candidate_policy != SINGLE_CANDIDATE_POLICY
        ):
            raise OSS3FinalHoldoutProtocolGovernanceError(
                "D2J statistical policy drifted"
            )
        if self.max_evaluations != 1:
            raise OSS3FinalHoldoutProtocolGovernanceError(
                "D2J permits one future evaluation only"
            )
        if (
            self.retuning_allowed
            or self.reselection_allowed
            or self.fallback_candidate_allowed
            or self.second_attempt_allowed
        ):
            raise OSS3FinalHoldoutProtocolGovernanceError(
                "D2J forbids retuning, reselection, fallback candidates and second attempts"
            )
        if self.failure_is_terminal is not True:
            raise OSS3FinalHoldoutProtocolGovernanceError(
                "D2J FINAL_HOLDOUT failure must be terminal"
            )
        if (
            self.split_name != FINAL_HOLDOUT_SPLIT
            or self.permit_purpose != FINAL_VALIDATION_PURPOSE
        ):
            raise OSS3FinalHoldoutProtocolGovernanceError(
                "D2J holdout split/purpose drifted"
            )

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "primary_metric": self.primary_metric,
            "min_mean_cross_sectional_rank_ic": float(
                self.min_mean_cross_sectional_rank_ic
            ),
            "max_one_sided_sign_test_p_value": float(
                self.max_one_sided_sign_test_p_value
            ),
            "min_holdout_cross_sections": self.min_holdout_cross_sections,
            "min_holdout_total_observations": self.min_holdout_total_observations,
            "min_cross_section_observations": self.min_cross_section_observations,
            "min_nonzero_rank_ic_cross_sections": self.min_nonzero_rank_ic_cross_sections,
            "sign_test_policy": self.sign_test_policy,
            "zero_ic_policy": self.zero_ic_policy,
            "single_candidate_policy": self.single_candidate_policy,
            "max_evaluations": self.max_evaluations,
            "retuning_allowed": self.retuning_allowed,
            "reselection_allowed": self.reselection_allowed,
            "fallback_candidate_allowed": self.fallback_candidate_allowed,
            "second_attempt_allowed": self.second_attempt_allowed,
            "failure_is_terminal": self.failure_is_terminal,
            "split_name": self.split_name,
            "permit_purpose": self.permit_purpose,
        }


@dataclass(frozen=True, slots=True)
class OSS3FinalHoldoutProtocolReceipt:
    protocol_id: str
    contract_version: str
    source_d2i_contract_version: str
    source_d2i_seal_fingerprint: str
    source_d2h_preregistration_fingerprint: str
    source_d2h_batch_evidence_fingerprint: str
    selection_scope: str
    selected_trial_id: str
    selected_hypothesis_id: str
    model_family: str
    model_config_hash: str
    request_hash: str
    prediction_artifact_hash: str
    prediction_receipt_hash: str
    environment_attestation_hash: str
    d2g_run_evidence_hash: str
    d2d_evaluation_artifact_hash: str
    shared_runner_code_hash: str
    runtime_environment_hash: str
    d2e_plan_fingerprint: str
    d2e_tournament_evidence_fingerprint: str
    source_winner_primary_metric: float
    source_winner_raw_p_value: float
    source_winner_holm_adjusted_p_value: float
    holdout_commitment_version: str
    holdout_commitment_fingerprint: str
    holdout_source_campaign_id: str
    holdout_research_split_hash: str
    holdout_source_universe_hash: str
    holdout_label_definition_hash: str
    holdout_feature_artifact_hash: str
    holdout_label_artifact_hash: str
    holdout_evaluation_keyset_hash: str
    holdout_cross_section_key_hash: str
    holdout_partition_start: str
    holdout_partition_end: str
    holdout_row_count: int
    holdout_cross_section_count: int
    holdout_minimum_cross_section_observation_count: int
    policy_fingerprint: str
    expected_holdout_authorization_id: str
    primary_metric: str
    min_mean_cross_sectional_rank_ic: float
    max_one_sided_sign_test_p_value: float
    min_holdout_cross_sections: int
    min_holdout_total_observations: int
    min_cross_section_observations: int
    min_nonzero_rank_ic_cross_sections: int
    sign_test_policy: str
    zero_ic_policy: str
    single_candidate_policy: str
    max_evaluations: int
    retuning_allowed: bool
    reselection_allowed: bool
    fallback_candidate_allowed: bool
    second_attempt_allowed: bool
    failure_is_terminal: bool
    split_name: str
    permit_purpose: str
    final_holdout_observed: bool
    final_holdout_consumed: bool
    holdout_permit_issued: bool
    holdout_permit_consumed: bool
    final_holdout_checkout_authorized: bool
    predictive_validation_passed: bool
    profitability_claim_authorized: bool
    promotion_authorized: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        for name in (
            "protocol_id",
            "selected_trial_id",
            "selected_hypothesis_id",
            "model_family",
            "holdout_source_campaign_id",
            "expected_holdout_authorization_id",
        ):
            _require_id(getattr(self, name), name)
        for name in (
            "source_d2i_seal_fingerprint",
            "source_d2h_preregistration_fingerprint",
            "source_d2h_batch_evidence_fingerprint",
            "model_config_hash",
            "request_hash",
            "prediction_artifact_hash",
            "prediction_receipt_hash",
            "environment_attestation_hash",
            "d2g_run_evidence_hash",
            "d2d_evaluation_artifact_hash",
            "shared_runner_code_hash",
            "runtime_environment_hash",
            "d2e_plan_fingerprint",
            "d2e_tournament_evidence_fingerprint",
            "holdout_commitment_fingerprint",
            "holdout_research_split_hash",
            "holdout_source_universe_hash",
            "holdout_label_definition_hash",
            "holdout_feature_artifact_hash",
            "holdout_label_artifact_hash",
            "holdout_evaluation_keyset_hash",
            "holdout_cross_section_key_hash",
            "policy_fingerprint",
            "receipt_hash",
        ):
            _require_hash(getattr(self, name), name)
        if self.contract_version != OSS3D2J_CONTRACT_VERSION:
            raise OSS3FinalHoldoutProtocolIntegrityError(
                "noncanonical D2J contract version"
            )
        if self.source_d2i_contract_version != OSS3D2I_CONTRACT_VERSION:
            raise OSS3FinalHoldoutProtocolIntegrityError(
                "D2J requires canonical D2I seal"
            )
        if self.holdout_commitment_version != OSS3D2J_COMMITMENT_VERSION:
            raise OSS3FinalHoldoutProtocolIntegrityError(
                "D2J requires canonical protected holdout commitment"
            )
        if self.selection_scope != SELECTION_SCOPE:
            raise OSS3FinalHoldoutProtocolIntegrityError(
                "D2J source selection scope drifted"
            )
        for name in (
            "source_winner_primary_metric",
            "source_winner_raw_p_value",
            "source_winner_holm_adjusted_p_value",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if not 0.0 <= self.source_winner_raw_p_value <= 1.0:
            raise ValueError("source_winner_raw_p_value outside [0,1]")
        if not 0.0 <= self.source_winner_holm_adjusted_p_value <= 1.0:
            raise ValueError("source_winner_holm_adjusted_p_value outside [0,1]")
        if self.source_winner_holm_adjusted_p_value < self.source_winner_raw_p_value:
            raise OSS3FinalHoldoutProtocolIntegrityError(
                "source Holm-adjusted p-value cannot be below raw p-value"
            )
        _parse_canonical_utc(self.holdout_partition_start, "holdout_partition_start")
        _parse_canonical_utc(self.holdout_partition_end, "holdout_partition_end")
        policy = canonical_oss3d2j_policy()
        if self.policy_fingerprint != policy.fingerprint:
            raise OSS3FinalHoldoutProtocolIntegrityError(
                "D2J policy fingerprint mismatch"
            )
        expected_policy = policy.to_dict()
        for name in (
            "primary_metric",
            "min_mean_cross_sectional_rank_ic",
            "max_one_sided_sign_test_p_value",
            "min_holdout_cross_sections",
            "min_holdout_total_observations",
            "min_cross_section_observations",
            "min_nonzero_rank_ic_cross_sections",
            "sign_test_policy",
            "zero_ic_policy",
            "single_candidate_policy",
            "max_evaluations",
            "retuning_allowed",
            "reselection_allowed",
            "fallback_candidate_allowed",
            "second_attempt_allowed",
            "failure_is_terminal",
            "split_name",
            "permit_purpose",
        ):
            if getattr(self, name) != expected_policy[name]:
                raise OSS3FinalHoldoutProtocolIntegrityError(
                    f"D2J policy field drifted: {name}"
                )
        if self.holdout_row_count < policy.min_holdout_total_observations:
            raise OSS3FinalHoldoutProtocolIntegrityError(
                "D2J receipt holdout row_count below frozen policy"
            )
        if self.holdout_cross_section_count < policy.min_holdout_cross_sections:
            raise OSS3FinalHoldoutProtocolIntegrityError(
                "D2J receipt holdout cross sections below frozen policy"
            )
        if (
            self.holdout_minimum_cross_section_observation_count
            < policy.min_cross_section_observations
        ):
            raise OSS3FinalHoldoutProtocolIntegrityError(
                "D2J receipt holdout cross-section size below frozen policy"
            )
        expected_authorization = _authorization_id(
            protocol_id=self.protocol_id,
            d2i_seal_fingerprint=self.source_d2i_seal_fingerprint,
            holdout_commitment_fingerprint=self.holdout_commitment_fingerprint,
            selected_trial_id=self.selected_trial_id,
            model_config_hash=self.model_config_hash,
            policy_fingerprint=self.policy_fingerprint,
        )
        if self.expected_holdout_authorization_id != expected_authorization:
            raise OSS3FinalHoldoutProtocolIntegrityError(
                "D2J expected authorization identity mismatch"
            )
        _deny_authority(self)
        if self.receipt_hash != _hash(self.to_dict(include_hash=False)):
            raise OSS3FinalHoldoutProtocolIntegrityError("D2J receipt hash mismatch")

    @property
    def gate_specification(self) -> tuple[tuple[str, str, float], ...]:
        return (
            (
                "FINAL_NONZERO_RANK_IC_CROSS_SECTIONS_MIN",
                ">=",
                float(self.min_nonzero_rank_ic_cross_sections),
            ),
            (
                "FINAL_MEAN_CROSS_SECTIONAL_RANK_IC_MIN",
                ">=",
                float(self.min_mean_cross_sectional_rank_ic),
            ),
            (
                "FINAL_ONE_SIDED_EXACT_SIGN_TEST_P_MAX",
                "<=",
                float(self.max_one_sided_sign_test_p_value),
            ),
        )

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload = {
            "protocol_id": self.protocol_id,
            "contract_version": self.contract_version,
            "source_d2i_contract_version": self.source_d2i_contract_version,
            "source_d2i_seal_fingerprint": self.source_d2i_seal_fingerprint,
            "source_d2h_preregistration_fingerprint": self.source_d2h_preregistration_fingerprint,
            "source_d2h_batch_evidence_fingerprint": self.source_d2h_batch_evidence_fingerprint,
            "selection_scope": self.selection_scope,
            "selected_trial_id": self.selected_trial_id,
            "selected_hypothesis_id": self.selected_hypothesis_id,
            "model_family": self.model_family,
            "model_config_hash": self.model_config_hash,
            "request_hash": self.request_hash,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "prediction_receipt_hash": self.prediction_receipt_hash,
            "environment_attestation_hash": self.environment_attestation_hash,
            "d2g_run_evidence_hash": self.d2g_run_evidence_hash,
            "d2d_evaluation_artifact_hash": self.d2d_evaluation_artifact_hash,
            "shared_runner_code_hash": self.shared_runner_code_hash,
            "runtime_environment_hash": self.runtime_environment_hash,
            "d2e_plan_fingerprint": self.d2e_plan_fingerprint,
            "d2e_tournament_evidence_fingerprint": self.d2e_tournament_evidence_fingerprint,
            "source_winner_primary_metric": float(self.source_winner_primary_metric),
            "source_winner_raw_p_value": float(self.source_winner_raw_p_value),
            "source_winner_holm_adjusted_p_value": float(
                self.source_winner_holm_adjusted_p_value
            ),
            "holdout_commitment_version": self.holdout_commitment_version,
            "holdout_commitment_fingerprint": self.holdout_commitment_fingerprint,
            "holdout_source_campaign_id": self.holdout_source_campaign_id,
            "holdout_research_split_hash": self.holdout_research_split_hash,
            "holdout_source_universe_hash": self.holdout_source_universe_hash,
            "holdout_label_definition_hash": self.holdout_label_definition_hash,
            "holdout_feature_artifact_hash": self.holdout_feature_artifact_hash,
            "holdout_label_artifact_hash": self.holdout_label_artifact_hash,
            "holdout_evaluation_keyset_hash": self.holdout_evaluation_keyset_hash,
            "holdout_cross_section_key_hash": self.holdout_cross_section_key_hash,
            "holdout_partition_start": self.holdout_partition_start,
            "holdout_partition_end": self.holdout_partition_end,
            "holdout_row_count": self.holdout_row_count,
            "holdout_cross_section_count": self.holdout_cross_section_count,
            "holdout_minimum_cross_section_observation_count": self.holdout_minimum_cross_section_observation_count,
            "policy_fingerprint": self.policy_fingerprint,
            "expected_holdout_authorization_id": self.expected_holdout_authorization_id,
            "primary_metric": self.primary_metric,
            "min_mean_cross_sectional_rank_ic": float(
                self.min_mean_cross_sectional_rank_ic
            ),
            "max_one_sided_sign_test_p_value": float(
                self.max_one_sided_sign_test_p_value
            ),
            "min_holdout_cross_sections": self.min_holdout_cross_sections,
            "min_holdout_total_observations": self.min_holdout_total_observations,
            "min_cross_section_observations": self.min_cross_section_observations,
            "min_nonzero_rank_ic_cross_sections": self.min_nonzero_rank_ic_cross_sections,
            "sign_test_policy": self.sign_test_policy,
            "zero_ic_policy": self.zero_ic_policy,
            "single_candidate_policy": self.single_candidate_policy,
            "max_evaluations": self.max_evaluations,
            "retuning_allowed": self.retuning_allowed,
            "reselection_allowed": self.reselection_allowed,
            "fallback_candidate_allowed": self.fallback_candidate_allowed,
            "second_attempt_allowed": self.second_attempt_allowed,
            "failure_is_terminal": self.failure_is_terminal,
            "split_name": self.split_name,
            "permit_purpose": self.permit_purpose,
            "final_holdout_observed": self.final_holdout_observed,
            "final_holdout_consumed": self.final_holdout_consumed,
            "holdout_permit_issued": self.holdout_permit_issued,
            "holdout_permit_consumed": self.holdout_permit_consumed,
            "final_holdout_checkout_authorized": self.final_holdout_checkout_authorized,
            "predictive_validation_passed": self.predictive_validation_passed,
            "profitability_claim_authorized": self.profitability_claim_authorized,
            "promotion_authorized": self.promotion_authorized,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }
        if include_hash:
            payload["receipt_hash"] = self.receipt_hash
        return payload


class SQLiteOSS3FinalHoldoutProtocolRegistry:
    """Append-only one-seal/one-holdout D2J protocol registry."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS oss3_final_holdout_protocols (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    protocol_id TEXT NOT NULL UNIQUE,
                    source_d2i_seal_fingerprint TEXT NOT NULL UNIQUE,
                    source_d2h_preregistration_fingerprint TEXT NOT NULL,
                    source_d2h_batch_evidence_fingerprint TEXT NOT NULL,
                    holdout_commitment_fingerprint TEXT NOT NULL UNIQUE,
                    selected_trial_id TEXT NOT NULL,
                    model_config_hash TEXT NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    expected_holdout_authorization_id TEXT NOT NULL UNIQUE,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS oss3_final_holdout_protocols_no_update
                BEFORE UPDATE ON oss3_final_holdout_protocols
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2J registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss3_final_holdout_protocols_no_delete
                BEFORE DELETE ON oss3_final_holdout_protocols
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2J registry is append-only');
                END;
                """
            )
            conn.commit()
        finally:
            conn.close()

    def preregister_and_record(
        self,
        *,
        protocol_id: str,
        seal: DevelopmentWinnerSelectionSeal,
        preregistration: FamilyEvaluationPreregistration,
        batch_evidence: FamilyEvaluationBatchEvidence,
        holdout_commitment: OSS3ProtectedFinalHoldoutCommitment,
    ) -> OSS3FinalHoldoutProtocolReceipt:
        _require_id(protocol_id, "protocol_id")
        _verify_d2i_source(
            seal=seal,
            preregistration=preregistration,
            batch_evidence=batch_evidence,
        )
        if not isinstance(holdout_commitment, OSS3ProtectedFinalHoldoutCommitment):
            raise TypeError(
                "holdout_commitment must be OSS3ProtectedFinalHoldoutCommitment"
            )
        candidate = _build_receipt(
            protocol_id=protocol_id,
            seal=seal,
            holdout_commitment=holdout_commitment,
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_protocol = conn.execute(
                "SELECT * FROM oss3_final_holdout_protocols WHERE protocol_id = ?",
                (protocol_id,),
            ).fetchone()
            if existing_protocol is not None:
                existing = _receipt_from_row(existing_protocol)
                if existing.source_d2i_seal_fingerprint != candidate.source_d2i_seal_fingerprint:
                    raise OSS3FinalHoldoutProtocolConflict(
                        "D2J protocol_id is bound to another D2I seal"
                    )
                if existing != candidate:
                    raise OSS3FinalHoldoutProtocolConflict(
                        "D2J protocol_id conflicts with frozen receipt"
                    )
                conn.execute("COMMIT")
                return existing

            existing_seal = conn.execute(
                "SELECT * FROM oss3_final_holdout_protocols "
                "WHERE source_d2i_seal_fingerprint = ?",
                (candidate.source_d2i_seal_fingerprint,),
            ).fetchone()
            if existing_seal is not None:
                existing = _receipt_from_row(existing_seal)
                if existing != candidate:
                    raise OSS3FinalHoldoutProtocolConflict(
                        "D2I seal already has a different D2J protocol"
                    )
                conn.execute("COMMIT")
                return existing

            existing_holdout = conn.execute(
                "SELECT * FROM oss3_final_holdout_protocols "
                "WHERE holdout_commitment_fingerprint = ?",
                (candidate.holdout_commitment_fingerprint,),
            ).fetchone()
            if existing_holdout is not None:
                existing = _receipt_from_row(existing_holdout)
                if existing != candidate:
                    raise OSS3FinalHoldoutProtocolConflict(
                        "protected FINAL_HOLDOUT commitment is already bound"
                    )
                conn.execute("COMMIT")
                return existing

            conn.execute(
                """
                INSERT INTO oss3_final_holdout_protocols(
                    protocol_id,
                    source_d2i_seal_fingerprint,
                    source_d2h_preregistration_fingerprint,
                    source_d2h_batch_evidence_fingerprint,
                    holdout_commitment_fingerprint,
                    selected_trial_id,
                    model_config_hash,
                    policy_fingerprint,
                    expected_holdout_authorization_id,
                    receipt_hash,
                    receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.protocol_id,
                    candidate.source_d2i_seal_fingerprint,
                    candidate.source_d2h_preregistration_fingerprint,
                    candidate.source_d2h_batch_evidence_fingerprint,
                    candidate.holdout_commitment_fingerprint,
                    candidate.selected_trial_id,
                    candidate.model_config_hash,
                    candidate.policy_fingerprint,
                    candidate.expected_holdout_authorization_id,
                    candidate.receipt_hash,
                    _canonical_json(candidate.to_dict()),
                ),
            )
            conn.execute("COMMIT")
            return candidate
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise OSS3FinalHoldoutProtocolConflict(
                "D2J durable protocol identity conflict"
            ) from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get_for_seal(
        self,
        seal_fingerprint: str,
    ) -> OSS3FinalHoldoutProtocolReceipt | None:
        _require_hash(seal_fingerprint, "seal_fingerprint")
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM oss3_final_holdout_protocols "
                "WHERE source_d2i_seal_fingerprint = ?",
                (seal_fingerprint,),
            ).fetchone()
            return _receipt_from_row(row) if row is not None else None
        finally:
            conn.close()


def read_oss3d2j_protocol_read_only(
    path: str | Path,
    *,
    seal_fingerprint: str,
) -> OSS3FinalHoldoutProtocolReceipt | None:
    """Read and reconstruct a durable D2J protocol without schema mutation."""
    _require_hash(seal_fingerprint, "seal_fingerprint")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise OSS3FinalHoldoutProtocolIntegrityError(
            "D2J durable registry does not exist"
        )
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='oss3_final_holdout_protocols'"
        ).fetchone()
        if table is None:
            raise OSS3FinalHoldoutProtocolIntegrityError(
                "D2J durable table is missing"
            )
        row = conn.execute(
            "SELECT * FROM oss3_final_holdout_protocols "
            "WHERE source_d2i_seal_fingerprint = ?",
            (seal_fingerprint,),
        ).fetchone()
        return _receipt_from_row(row) if row is not None else None
    finally:
        conn.close()


def canonical_oss3d2j_policy() -> OSS3FinalHoldoutProtocolPolicy:
    return OSS3FinalHoldoutProtocolPolicy()


def _verify_d2i_source(
    *,
    seal: DevelopmentWinnerSelectionSeal,
    preregistration: FamilyEvaluationPreregistration,
    batch_evidence: FamilyEvaluationBatchEvidence,
) -> None:
    if not isinstance(seal, DevelopmentWinnerSelectionSeal):
        raise TypeError("seal must be DevelopmentWinnerSelectionSeal")
    if not isinstance(preregistration, FamilyEvaluationPreregistration):
        raise TypeError("preregistration must be FamilyEvaluationPreregistration")
    if not isinstance(batch_evidence, FamilyEvaluationBatchEvidence):
        raise TypeError("batch_evidence must be FamilyEvaluationBatchEvidence")
    try:
        verify_development_winner_seal(
            seal=seal,
            preregistration=preregistration,
            batch_evidence=batch_evidence,
        )
    except DevelopmentWinnerSealError as exc:
        raise OSS3FinalHoldoutProtocolIntegrityError(
            "D2J source seal does not rebind to exact D2H evidence"
        ) from exc
    if (
        seal.contract_version != OSS3D2I_CONTRACT_VERSION
        or seal.selection_scope != SELECTION_SCOPE
    ):
        raise OSS3FinalHoldoutProtocolGovernanceError(
            "D2J requires canonical D2I ranking-winner seal"
        )
    if seal.preregistration_fingerprint != preregistration.fingerprint:
        raise OSS3FinalHoldoutProtocolIntegrityError(
            "D2J D2H preregistration root mismatch"
        )
    if seal.d2h_batch_evidence_fingerprint != batch_evidence.fingerprint:
        raise OSS3FinalHoldoutProtocolIntegrityError(
            "D2J D2H batch root mismatch"
        )
    if seal.reselection_allowed or seal.retuning_allowed:
        raise OSS3FinalHoldoutProtocolGovernanceError(
            "D2I source must already forbid reselection/retuning"
        )
    if (
        seal.final_holdout_observed
        or seal.final_holdout_authorized
        or seal.holdout_permit_consumed
    ):
        raise OSS3FinalHoldoutProtocolGovernanceError(
            "D2J requires completely untouched FINAL_HOLDOUT"
        )
    if (
        seal.promotion_authorized
        or seal.execution_authorized
        or seal.paper_execution_authorized
    ):
        raise OSS3FinalHoldoutProtocolGovernanceError(
            "D2I source may not authorize promotion/execution"
        )
    if seal.capital_authority != "NONE" or seal.live_trading != "BLOCKED":
        raise OSS3FinalHoldoutProtocolGovernanceError(
            "D2I source may not grant capital/LIVE authority"
        )


def _build_receipt(
    *,
    protocol_id: str,
    seal: DevelopmentWinnerSelectionSeal,
    holdout_commitment: OSS3ProtectedFinalHoldoutCommitment,
) -> OSS3FinalHoldoutProtocolReceipt:
    policy = canonical_oss3d2j_policy()
    authorization_id = _authorization_id(
        protocol_id=protocol_id,
        d2i_seal_fingerprint=seal.fingerprint,
        holdout_commitment_fingerprint=holdout_commitment.fingerprint,
        selected_trial_id=seal.selected_trial_id,
        model_config_hash=seal.model_config_hash,
        policy_fingerprint=policy.fingerprint,
    )
    values: dict[str, object] = {
        "protocol_id": protocol_id,
        "contract_version": OSS3D2J_CONTRACT_VERSION,
        "source_d2i_contract_version": seal.contract_version,
        "source_d2i_seal_fingerprint": seal.fingerprint,
        "source_d2h_preregistration_fingerprint": seal.preregistration_fingerprint,
        "source_d2h_batch_evidence_fingerprint": seal.d2h_batch_evidence_fingerprint,
        "selection_scope": seal.selection_scope,
        "selected_trial_id": seal.selected_trial_id,
        "selected_hypothesis_id": seal.selected_hypothesis_id,
        "model_family": seal.model_family,
        "model_config_hash": seal.model_config_hash,
        "request_hash": seal.request_hash,
        "prediction_artifact_hash": seal.prediction_artifact_hash,
        "prediction_receipt_hash": seal.prediction_receipt_hash,
        "environment_attestation_hash": seal.environment_attestation_hash,
        "d2g_run_evidence_hash": seal.d2g_run_evidence_hash,
        "d2d_evaluation_artifact_hash": seal.d2d_evaluation_artifact_hash,
        "shared_runner_code_hash": seal.shared_runner_code_hash,
        "runtime_environment_hash": seal.runtime_environment_hash,
        "d2e_plan_fingerprint": seal.d2e_plan_fingerprint,
        "d2e_tournament_evidence_fingerprint": seal.d2e_tournament_evidence_fingerprint,
        "source_winner_primary_metric": seal.winner_primary_metric,
        "source_winner_raw_p_value": seal.winner_raw_p_value,
        "source_winner_holm_adjusted_p_value": seal.winner_holm_adjusted_p_value,
        "holdout_commitment_version": holdout_commitment.commitment_version,
        "holdout_commitment_fingerprint": holdout_commitment.fingerprint,
        "holdout_source_campaign_id": holdout_commitment.source_campaign_id,
        "holdout_research_split_hash": holdout_commitment.research_split_hash,
        "holdout_source_universe_hash": holdout_commitment.source_universe_hash,
        "holdout_label_definition_hash": holdout_commitment.label_definition_hash,
        "holdout_feature_artifact_hash": holdout_commitment.feature_artifact_hash,
        "holdout_label_artifact_hash": holdout_commitment.label_artifact_hash,
        "holdout_evaluation_keyset_hash": holdout_commitment.evaluation_keyset_hash,
        "holdout_cross_section_key_hash": holdout_commitment.cross_section_key_hash,
        "holdout_partition_start": holdout_commitment.partition_start,
        "holdout_partition_end": holdout_commitment.partition_end,
        "holdout_row_count": holdout_commitment.row_count,
        "holdout_cross_section_count": holdout_commitment.cross_section_count,
        "holdout_minimum_cross_section_observation_count": holdout_commitment.minimum_cross_section_observation_count,
        "policy_fingerprint": policy.fingerprint,
        "expected_holdout_authorization_id": authorization_id,
        "primary_metric": policy.primary_metric,
        "min_mean_cross_sectional_rank_ic": policy.min_mean_cross_sectional_rank_ic,
        "max_one_sided_sign_test_p_value": policy.max_one_sided_sign_test_p_value,
        "min_holdout_cross_sections": policy.min_holdout_cross_sections,
        "min_holdout_total_observations": policy.min_holdout_total_observations,
        "min_cross_section_observations": policy.min_cross_section_observations,
        "min_nonzero_rank_ic_cross_sections": policy.min_nonzero_rank_ic_cross_sections,
        "sign_test_policy": policy.sign_test_policy,
        "zero_ic_policy": policy.zero_ic_policy,
        "single_candidate_policy": policy.single_candidate_policy,
        "max_evaluations": policy.max_evaluations,
        "retuning_allowed": policy.retuning_allowed,
        "reselection_allowed": policy.reselection_allowed,
        "fallback_candidate_allowed": policy.fallback_candidate_allowed,
        "second_attempt_allowed": policy.second_attempt_allowed,
        "failure_is_terminal": policy.failure_is_terminal,
        "split_name": policy.split_name,
        "permit_purpose": policy.permit_purpose,
        "final_holdout_observed": False,
        "final_holdout_consumed": False,
        "holdout_permit_issued": False,
        "holdout_permit_consumed": False,
        "final_holdout_checkout_authorized": False,
        "predictive_validation_passed": False,
        "profitability_claim_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    receipt_hash = _hash(values)
    return OSS3FinalHoldoutProtocolReceipt(**values, receipt_hash=receipt_hash)


def _authorization_id(
    *,
    protocol_id: str,
    d2i_seal_fingerprint: str,
    holdout_commitment_fingerprint: str,
    selected_trial_id: str,
    model_config_hash: str,
    policy_fingerprint: str,
) -> str:
    digest = _hash(
        {
            "contract_version": OSS3D2J_CONTRACT_VERSION,
            "protocol_id": protocol_id,
            "source_d2i_seal_fingerprint": d2i_seal_fingerprint,
            "holdout_commitment_fingerprint": holdout_commitment_fingerprint,
            "selected_trial_id": selected_trial_id,
            "model_config_hash": model_config_hash,
            "policy_fingerprint": policy_fingerprint,
            "permit_purpose": FINAL_VALIDATION_PURPOSE,
            "max_evaluations": 1,
        }
    )
    return f"oss3d2j:{digest[:48]}"


def _receipt_from_row(row: sqlite3.Row) -> OSS3FinalHoldoutProtocolReceipt:
    try:
        payload = json.loads(str(row["receipt_json"]))
    except json.JSONDecodeError as exc:
        raise OSS3FinalHoldoutProtocolIntegrityError(
            "D2J receipt JSON is invalid"
        ) from exc
    if not isinstance(payload, Mapping):
        raise OSS3FinalHoldoutProtocolIntegrityError(
            "D2J receipt JSON must be an object"
        )
    receipt = OSS3FinalHoldoutProtocolReceipt(**dict(payload))
    for column, expected in (
        ("protocol_id", receipt.protocol_id),
        ("source_d2i_seal_fingerprint", receipt.source_d2i_seal_fingerprint),
        (
            "source_d2h_preregistration_fingerprint",
            receipt.source_d2h_preregistration_fingerprint,
        ),
        (
            "source_d2h_batch_evidence_fingerprint",
            receipt.source_d2h_batch_evidence_fingerprint,
        ),
        ("holdout_commitment_fingerprint", receipt.holdout_commitment_fingerprint),
        ("selected_trial_id", receipt.selected_trial_id),
        ("model_config_hash", receipt.model_config_hash),
        ("policy_fingerprint", receipt.policy_fingerprint),
        (
            "expected_holdout_authorization_id",
            receipt.expected_holdout_authorization_id,
        ),
        ("receipt_hash", receipt.receipt_hash),
    ):
        if str(row[column]) != expected:
            raise OSS3FinalHoldoutProtocolIntegrityError(
                f"D2J durable column mismatch: {column}"
            )
    if _canonical_json(receipt.to_dict()) != str(row["receipt_json"]):
        raise OSS3FinalHoldoutProtocolIntegrityError(
            "D2J durable receipt serialization drifted"
        )
    return receipt


def _deny_authority(receipt: OSS3FinalHoldoutProtocolReceipt) -> None:
    if receipt.final_holdout_observed or receipt.final_holdout_consumed:
        raise OSS3FinalHoldoutProtocolGovernanceError(
            "D2J cannot observe or consume FINAL_HOLDOUT"
        )
    if (
        receipt.holdout_permit_issued
        or receipt.holdout_permit_consumed
        or receipt.final_holdout_checkout_authorized
    ):
        raise OSS3FinalHoldoutProtocolGovernanceError(
            "D2J protocol identity is not a holdout permit"
        )
    if receipt.predictive_validation_passed:
        raise OSS3FinalHoldoutProtocolGovernanceError(
            "D2J cannot claim final predictive validation before evaluation"
        )
    if receipt.profitability_claim_authorized:
        raise OSS3FinalHoldoutProtocolGovernanceError(
            "D2J predictive protocol cannot claim profitability"
        )
    if (
        receipt.promotion_authorized
        or receipt.execution_authorized
        or receipt.paper_execution_authorized
    ):
        raise OSS3FinalHoldoutProtocolGovernanceError(
            "D2J cannot authorize promotion/execution"
        )
    if receipt.capital_authority != "NONE" or receipt.live_trading != "BLOCKED":
        raise OSS3FinalHoldoutProtocolGovernanceError(
            "D2J cannot grant capital/LIVE authority"
        )


def _parse_canonical_utc(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a canonical UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must be UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat()
    if value != canonical:
        raise ValueError(f"{name} must use canonical UTC serialization")
    return parsed


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_id(value: object, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()
