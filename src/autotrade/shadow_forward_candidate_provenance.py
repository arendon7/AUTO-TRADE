from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

import autotrade.promotion_strategy_version_binding as w83_resolution_module
import autotrade.strategy_execution_binding as w83_binding_module
from autotrade.promotion_fee_accounting import SHADOW_FORWARD_BLOCKER
from autotrade.promotion_strategy_version_binding import (
    PromotionStrategyVersionResolution,
)
from autotrade.research.forward import FrozenForwardPolicy
from autotrade.research.shadow import FrozenShadowConfig, StrategyShadowObservation
from autotrade.strategy_execution_binding import ExecutionStrategyBindingEvidence


CANDIDATE_IDENTITY_VERSION = "W84_SHADOW_FORWARD_CANDIDATE_IDENTITY_V1"
SHADOW_CONFIG_BINDING_VERSION = "W84_CANDIDATE_ISOLATED_SHADOW_CONFIG_V1"
OBSERVATION_BINDING_VERSION = "W84_CANDIDATE_SHADOW_OBSERVATION_PROVENANCE_V1"
FORWARD_POLICY_BINDING_VERSION = "W84_CANDIDATE_FORWARD_POLICY_PROVENANCE_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class ShadowForwardCandidateProvenanceError(RuntimeError):
    pass


class ShadowForwardCandidateProvenanceIntegrityError(
    ShadowForwardCandidateProvenanceError
):
    pass


@dataclass(frozen=True, slots=True)
class CandidateShadowForwardIdentity:
    contract_version: str
    w83_resolution_id: str
    w83_resolution_hash: str
    w83_binding_id: str
    w83_binding_evidence_hash: str
    promotion_policy_id: str
    promotion_policy_hash: str
    selected_trial_id: str
    selected_trial_fingerprint: str
    selected_strategy_id: str
    selected_strategy_version: str
    strategy_spec_hash: str
    runtime_code_hash: str
    trial_dataset_hash: str
    fee_product_economics_hash: str
    intent_fingerprint: str
    identity_hash: str

    def __post_init__(self) -> None:
        if self.contract_version != CANDIDATE_IDENTITY_VERSION:
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "candidate identity version is not canonical W84"
            )
        for label, value in (
            ("w83_resolution_id", self.w83_resolution_id),
            ("w83_binding_id", self.w83_binding_id),
            ("promotion_policy_id", self.promotion_policy_id),
            ("selected_trial_id", self.selected_trial_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
        ):
            _require_id(value, label)
        for label, value in (
            ("w83_resolution_hash", self.w83_resolution_hash),
            ("w83_binding_evidence_hash", self.w83_binding_evidence_hash),
            ("promotion_policy_hash", self.promotion_policy_hash),
            ("selected_trial_fingerprint", self.selected_trial_fingerprint),
            ("strategy_spec_hash", self.strategy_spec_hash),
            ("runtime_code_hash", self.runtime_code_hash),
            ("trial_dataset_hash", self.trial_dataset_hash),
            ("fee_product_economics_hash", self.fee_product_economics_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("identity_hash", self.identity_hash),
        ):
            _require_hash(value, label)
        if self.identity_hash != _hash(_identity_payload(self, include_hash=False)):
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "candidate identity hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _identity_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class CandidateShadowConfigBinding:
    contract_version: str
    candidate_identity_hash: str
    config_id: str
    activated_at: datetime
    initial_nav: Decimal
    selected_strategy_id: str
    selected_strategy_weight: Decimal
    source_config_hash: str
    shadow_config_fingerprint: str
    binding_hash: str

    def __post_init__(self) -> None:
        if self.contract_version != SHADOW_CONFIG_BINDING_VERSION:
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "shadow config binding version is not canonical W84"
            )
        _require_hash(self.candidate_identity_hash, "candidate_identity_hash")
        _require_id(self.config_id, "config_id")
        _require_id(self.selected_strategy_id, "selected_strategy_id")
        _require_aware(self.activated_at, "activated_at")
        _require_positive(self.initial_nav, "initial_nav")
        if self.selected_strategy_weight != Decimal("1"):
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "candidate promotion shadow must be isolated at weight 1"
            )
        _require_hash(self.source_config_hash, "source_config_hash")
        _require_hash(self.shadow_config_fingerprint, "shadow_config_fingerprint")
        _require_hash(self.binding_hash, "binding_hash")
        if self.binding_hash != _hash(_shadow_config_payload(self, include_hash=False)):
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "shadow config binding hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _shadow_config_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class CandidateShadowObservationBinding:
    contract_version: str
    candidate_identity_hash: str
    shadow_config_fingerprint: str
    selected_strategy_id: str
    period_started_at: datetime
    period_ended_at: datetime
    return_fraction: Decimal
    measurement_contract: str
    measurement_hash: str
    source_fingerprint: str
    measurement_verified_by_w84: bool
    binding_hash: str

    def __post_init__(self) -> None:
        if self.contract_version != OBSERVATION_BINDING_VERSION:
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "observation binding version is not canonical W84"
            )
        _require_hash(self.candidate_identity_hash, "candidate_identity_hash")
        _require_hash(self.shadow_config_fingerprint, "shadow_config_fingerprint")
        _require_id(self.selected_strategy_id, "selected_strategy_id")
        _require_aware(self.period_started_at, "period_started_at")
        _require_aware(self.period_ended_at, "period_ended_at")
        if _utc(self.period_started_at) >= _utc(self.period_ended_at):
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "candidate observation period must have positive duration"
            )
        _require_return(self.return_fraction)
        _require_id(self.measurement_contract, "measurement_contract")
        _require_hash(self.measurement_hash, "measurement_hash")
        _require_hash(self.source_fingerprint, "source_fingerprint")
        if self.measurement_verified_by_w84 is not False:
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "W84 provenance contract may not claim external measurement verification"
            )
        expected_source = _hash(
            _observation_source_payload(
                candidate_identity_hash=self.candidate_identity_hash,
                shadow_config_fingerprint=self.shadow_config_fingerprint,
                selected_strategy_id=self.selected_strategy_id,
                period_started_at=self.period_started_at,
                period_ended_at=self.period_ended_at,
                return_fraction=self.return_fraction,
                measurement_contract=self.measurement_contract,
                measurement_hash=self.measurement_hash,
            )
        )
        if self.source_fingerprint != expected_source:
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "candidate observation source fingerprint mismatch"
            )
        if self.binding_hash != _hash(_observation_binding_payload(self, include_hash=False)):
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "candidate observation binding hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _observation_binding_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class CandidateForwardPolicyBinding:
    contract_version: str
    candidate_identity_hash: str
    w83_resolution_hash: str
    campaign_id: str
    frozen_at: datetime
    activated_at: datetime
    shadow_config_fingerprint: str
    frozen_parameters_hash: str
    source_code_hash: str
    forward_policy_fingerprint: str
    performance_qualification_deferred: bool
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    capital_authority: str
    live_trading: str
    binding_hash: str

    def __post_init__(self) -> None:
        if self.contract_version != FORWARD_POLICY_BINDING_VERSION:
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "forward policy binding version is not canonical W84"
            )
        for label, value in (
            ("candidate_identity_hash", self.candidate_identity_hash),
            ("w83_resolution_hash", self.w83_resolution_hash),
            ("shadow_config_fingerprint", self.shadow_config_fingerprint),
            ("frozen_parameters_hash", self.frozen_parameters_hash),
            ("source_code_hash", self.source_code_hash),
            ("forward_policy_fingerprint", self.forward_policy_fingerprint),
            ("binding_hash", self.binding_hash),
        ):
            _require_hash(value, label)
        _require_id(self.campaign_id, "campaign_id")
        _require_aware(self.frozen_at, "frozen_at")
        _require_aware(self.activated_at, "activated_at")
        if _utc(self.frozen_at) > _utc(self.activated_at):
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "forward policy must be frozen before activation"
            )
        if self.performance_qualification_deferred is not True:
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "W84 provenance must defer performance qualification"
            )
        if (
            self.paper_candidate_authorized is not False
            or self.external_execution_authorized is not False
            or self.capital_authority != "NONE"
            or self.live_trading != "BLOCKED"
        ):
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "W84 provenance may not grant PAPER, execution, capital, or LIVE authority"
            )
        if self.binding_hash != _hash(_forward_binding_payload(self, include_hash=False)):
            raise ShadowForwardCandidateProvenanceIntegrityError(
                "forward policy binding hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _forward_binding_payload(self, include_hash=True)


def build_candidate_shadow_forward_identity(
    *,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
) -> CandidateShadowForwardIdentity:
    _validate_w83_chain(w83_resolution=w83_resolution, binding_evidence=binding_evidence)
    values = {
        "contract_version": CANDIDATE_IDENTITY_VERSION,
        "w83_resolution_id": w83_resolution.resolution_id,
        "w83_resolution_hash": w83_resolution.resolution_hash,
        "w83_binding_id": binding_evidence.binding_id,
        "w83_binding_evidence_hash": binding_evidence.evidence_hash,
        "promotion_policy_id": w83_resolution.promotion_policy_id,
        "promotion_policy_hash": w83_resolution.promotion_policy_hash,
        "selected_trial_id": w83_resolution.selected_trial_id,
        "selected_trial_fingerprint": w83_resolution.selected_trial_fingerprint,
        "selected_strategy_id": w83_resolution.selected_strategy_id,
        "selected_strategy_version": w83_resolution.selected_strategy_version,
        "strategy_spec_hash": w83_resolution.strategy_spec_hash,
        "runtime_code_hash": w83_resolution.loaded_runtime_code_hash,
        "trial_dataset_hash": binding_evidence.trial_dataset_hash,
        "fee_product_economics_hash": w83_resolution.fee_product_economics_hash,
        "intent_fingerprint": w83_resolution.intent_fingerprint,
    }
    return CandidateShadowForwardIdentity(
        **values,
        identity_hash=_hash(_identity_payload_from_values(values)),
    )


def build_candidate_isolated_shadow_config(
    *,
    identity: CandidateShadowForwardIdentity,
    config_id: str,
    activated_at: datetime,
    initial_nav: Decimal,
    w83_resolved_at: datetime,
) -> tuple[FrozenShadowConfig, CandidateShadowConfigBinding]:
    _validate_identity(identity)
    _require_id(config_id, "config_id")
    _require_aware(activated_at, "activated_at")
    _require_aware(w83_resolved_at, "w83_resolved_at")
    _require_positive(initial_nav, "initial_nav")
    if _utc(activated_at) < _utc(w83_resolved_at):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "candidate shadow config may not activate before W83 resolution"
        )
    source_payload = {
        "contract_version": SHADOW_CONFIG_BINDING_VERSION,
        "candidate_identity_hash": identity.identity_hash,
        "config_id": config_id,
        "activated_at": _utc_iso(activated_at),
        "initial_nav": _decimal(initial_nav),
        "strategy_weights": {identity.selected_strategy_id: "1"},
    }
    source_hash = _hash(source_payload)
    config = FrozenShadowConfig(
        config_id=config_id,
        activated_at=activated_at,
        initial_nav=initial_nav,
        strategy_weights={identity.selected_strategy_id: Decimal("1")},
        source_config_hash=source_hash,
    )
    values = {
        "contract_version": SHADOW_CONFIG_BINDING_VERSION,
        "candidate_identity_hash": identity.identity_hash,
        "config_id": config.config_id,
        "activated_at": config.activated_at,
        "initial_nav": config.initial_nav,
        "selected_strategy_id": identity.selected_strategy_id,
        "selected_strategy_weight": Decimal("1"),
        "source_config_hash": config.source_config_hash,
        "shadow_config_fingerprint": config.fingerprint,
    }
    binding = CandidateShadowConfigBinding(
        **values,
        binding_hash=_hash(_shadow_config_payload_from_values(values)),
    )
    return config, binding


def build_candidate_shadow_observation(
    *,
    identity: CandidateShadowForwardIdentity,
    config_binding: CandidateShadowConfigBinding,
    period_started_at: datetime,
    period_ended_at: datetime,
    return_fraction: Decimal,
    measurement_contract: str,
    measurement_hash: str,
) -> tuple[StrategyShadowObservation, CandidateShadowObservationBinding]:
    _validate_identity(identity)
    _validate_config_binding(identity=identity, binding=config_binding)
    _require_aware(period_started_at, "period_started_at")
    _require_aware(period_ended_at, "period_ended_at")
    if _utc(period_started_at) < _utc(config_binding.activated_at):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "candidate observation may not predate shadow config activation"
        )
    _require_return(return_fraction)
    _require_id(measurement_contract, "measurement_contract")
    _require_hash(measurement_hash, "measurement_hash")
    source_hash = _hash(
        _observation_source_payload(
            candidate_identity_hash=identity.identity_hash,
            shadow_config_fingerprint=config_binding.shadow_config_fingerprint,
            selected_strategy_id=identity.selected_strategy_id,
            period_started_at=period_started_at,
            period_ended_at=period_ended_at,
            return_fraction=return_fraction,
            measurement_contract=measurement_contract,
            measurement_hash=measurement_hash,
        )
    )
    observation = StrategyShadowObservation(
        strategy_id=identity.selected_strategy_id,
        period_started_at=period_started_at,
        period_ended_at=period_ended_at,
        return_fraction=return_fraction,
        source_fingerprint=source_hash,
    )
    values = {
        "contract_version": OBSERVATION_BINDING_VERSION,
        "candidate_identity_hash": identity.identity_hash,
        "shadow_config_fingerprint": config_binding.shadow_config_fingerprint,
        "selected_strategy_id": identity.selected_strategy_id,
        "period_started_at": period_started_at,
        "period_ended_at": period_ended_at,
        "return_fraction": return_fraction,
        "measurement_contract": measurement_contract,
        "measurement_hash": measurement_hash,
        "source_fingerprint": source_hash,
        "measurement_verified_by_w84": False,
    }
    binding = CandidateShadowObservationBinding(
        **values,
        binding_hash=_hash(_observation_binding_payload_from_values(values)),
    )
    return observation, binding


def build_candidate_forward_policy(
    *,
    identity: CandidateShadowForwardIdentity,
    config_binding: CandidateShadowConfigBinding,
    campaign_id: str,
    frozen_at: datetime,
    activated_at: datetime,
    w83_resolved_at: datetime,
) -> tuple[FrozenForwardPolicy, CandidateForwardPolicyBinding]:
    _validate_identity(identity)
    _validate_config_binding(identity=identity, binding=config_binding)
    _require_id(campaign_id, "campaign_id")
    _require_aware(frozen_at, "frozen_at")
    _require_aware(activated_at, "activated_at")
    _require_aware(w83_resolved_at, "w83_resolved_at")
    if _utc(frozen_at) < _utc(w83_resolved_at):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "candidate forward policy may not freeze before W83 resolution"
        )
    if _utc(frozen_at) > _utc(activated_at):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "candidate forward policy must freeze before activation"
        )
    if _utc(activated_at) < _utc(config_binding.activated_at):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "candidate forward policy may not activate before shadow config"
        )
    frozen_payload = {
        "contract_version": FORWARD_POLICY_BINDING_VERSION,
        "candidate_identity_hash": identity.identity_hash,
        "w83_resolution_hash": identity.w83_resolution_hash,
        "campaign_id": campaign_id,
        "frozen_at": _utc_iso(frozen_at),
        "activated_at": _utc_iso(activated_at),
        "shadow_config_fingerprint": config_binding.shadow_config_fingerprint,
        "performance_qualification_deferred": True,
    }
    frozen_parameters_hash = _hash(frozen_payload)
    policy = FrozenForwardPolicy(
        campaign_id=campaign_id,
        activated_at=activated_at,
        shadow_config_fingerprint=config_binding.shadow_config_fingerprint,
        frozen_parameters_hash=frozen_parameters_hash,
        source_code_hash=identity.runtime_code_hash,
    )
    values = {
        "contract_version": FORWARD_POLICY_BINDING_VERSION,
        "candidate_identity_hash": identity.identity_hash,
        "w83_resolution_hash": identity.w83_resolution_hash,
        "campaign_id": policy.campaign_id,
        "frozen_at": frozen_at,
        "activated_at": policy.activated_at,
        "shadow_config_fingerprint": policy.shadow_config_fingerprint,
        "frozen_parameters_hash": policy.frozen_parameters_hash,
        "source_code_hash": policy.source_code_hash,
        "forward_policy_fingerprint": policy.fingerprint,
        "performance_qualification_deferred": True,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    binding = CandidateForwardPolicyBinding(
        **values,
        binding_hash=_hash(_forward_binding_payload_from_values(values)),
    )
    return policy, binding


def _validate_w83_chain(
    *,
    w83_resolution: PromotionStrategyVersionResolution,
    binding_evidence: ExecutionStrategyBindingEvidence,
) -> None:
    if not isinstance(w83_resolution, PromotionStrategyVersionResolution):
        raise TypeError("w83_resolution must be PromotionStrategyVersionResolution")
    if not isinstance(binding_evidence, ExecutionStrategyBindingEvidence):
        raise TypeError("binding_evidence must be ExecutionStrategyBindingEvidence")
    if w83_resolution.resolution_hash != w83_resolution_module._hash(
        w83_resolution_module._payload(w83_resolution, include_hash=False)
    ):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "W83 resolution hash mismatch"
        )
    if binding_evidence.evidence_hash != w83_binding_module._hash(
        w83_binding_module._evidence_payload(binding_evidence, include_hash=False)
    ):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "W83 binding evidence hash mismatch"
        )
    if w83_resolution.binding_evidence_hash != binding_evidence.evidence_hash:
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "W83 resolution is not bound to supplied execution binding evidence"
        )
    exact_pairs = (
        (w83_resolution.promotion_policy_id, binding_evidence.promotion_policy_id),
        (w83_resolution.promotion_policy_hash, binding_evidence.promotion_policy_hash),
        (w83_resolution.selected_trial_id, binding_evidence.selected_trial_id),
        (
            w83_resolution.selected_trial_fingerprint,
            binding_evidence.selected_trial_fingerprint,
        ),
        (w83_resolution.selected_strategy_id, binding_evidence.selected_strategy_id),
        (
            w83_resolution.selected_strategy_version,
            binding_evidence.selected_strategy_version,
        ),
        (w83_resolution.strategy_spec_hash, binding_evidence.strategy_spec_hash),
        (w83_resolution.trial_code_version, binding_evidence.trial_code_version),
        (
            w83_resolution.fee_product_economics_hash,
            binding_evidence.fee_product_economics_hash,
        ),
        (w83_resolution.intent_fingerprint, binding_evidence.intent_fingerprint),
    )
    if any(left != right for left, right in exact_pairs):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "W83 candidate identity differs between resolution and binding evidence"
        )
    if w83_resolution.loaded_runtime_code_hash != binding_evidence.trial_code_version:
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "W83 loaded runtime is not the preregistered trial code version"
        )
    if w83_resolution.remaining_promotion_blockers != (SHADOW_FORWARD_BLOCKER,):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "W84 requires Shadow/Forward to be the sole remaining promotion blocker"
        )
    if (
        w83_resolution.strategy_version_execution_bound is not True
        or w83_resolution.shadow_forward_promotion_bound is not False
        or w83_resolution.paper_candidate_authorized is not False
        or w83_resolution.external_execution_authorized is not False
        or w83_resolution.runtime_execution_authorized is not False
        or w83_resolution.capital_authority != "NONE"
        or w83_resolution.live_trading != "BLOCKED"
    ):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "W83 authority boundary is not intact"
        )


def _validate_identity(value: CandidateShadowForwardIdentity) -> None:
    if not isinstance(value, CandidateShadowForwardIdentity):
        raise TypeError("identity must be CandidateShadowForwardIdentity")
    if value.identity_hash != _hash(_identity_payload(value, include_hash=False)):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "candidate identity hash mismatch"
        )


def _validate_config_binding(
    *,
    identity: CandidateShadowForwardIdentity,
    binding: CandidateShadowConfigBinding,
) -> None:
    if not isinstance(binding, CandidateShadowConfigBinding):
        raise TypeError("config_binding must be CandidateShadowConfigBinding")
    if binding.candidate_identity_hash != identity.identity_hash:
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "shadow config belongs to another W84 candidate identity"
        )
    if binding.selected_strategy_id != identity.selected_strategy_id:
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "shadow config selected strategy differs from W84 candidate"
        )


def _identity_payload(
    value: CandidateShadowForwardIdentity, *, include_hash: bool
) -> dict[str, object]:
    payload = _identity_payload_from_values(
        {
            key: getattr(value, key)
            for key in (
                "contract_version",
                "w83_resolution_id",
                "w83_resolution_hash",
                "w83_binding_id",
                "w83_binding_evidence_hash",
                "promotion_policy_id",
                "promotion_policy_hash",
                "selected_trial_id",
                "selected_trial_fingerprint",
                "selected_strategy_id",
                "selected_strategy_version",
                "strategy_spec_hash",
                "runtime_code_hash",
                "trial_dataset_hash",
                "fee_product_economics_hash",
                "intent_fingerprint",
            )
        }
    )
    if include_hash:
        payload["identity_hash"] = value.identity_hash
    return payload


def _identity_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    return dict(values)


def _shadow_config_payload(
    value: CandidateShadowConfigBinding, *, include_hash: bool
) -> dict[str, object]:
    payload = _shadow_config_payload_from_values(
        {
            "contract_version": value.contract_version,
            "candidate_identity_hash": value.candidate_identity_hash,
            "config_id": value.config_id,
            "activated_at": value.activated_at,
            "initial_nav": value.initial_nav,
            "selected_strategy_id": value.selected_strategy_id,
            "selected_strategy_weight": value.selected_strategy_weight,
            "source_config_hash": value.source_config_hash,
            "shadow_config_fingerprint": value.shadow_config_fingerprint,
        }
    )
    if include_hash:
        payload["binding_hash"] = value.binding_hash
    return payload


def _shadow_config_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["activated_at"] = _utc_iso(payload["activated_at"])
    payload["initial_nav"] = _decimal(payload["initial_nav"])
    payload["selected_strategy_weight"] = _decimal(payload["selected_strategy_weight"])
    return payload


def _observation_source_payload(
    *,
    candidate_identity_hash: str,
    shadow_config_fingerprint: str,
    selected_strategy_id: str,
    period_started_at: datetime,
    period_ended_at: datetime,
    return_fraction: Decimal,
    measurement_contract: str,
    measurement_hash: str,
) -> dict[str, object]:
    return {
        "contract_version": OBSERVATION_BINDING_VERSION,
        "candidate_identity_hash": candidate_identity_hash,
        "shadow_config_fingerprint": shadow_config_fingerprint,
        "selected_strategy_id": selected_strategy_id,
        "period_started_at": _utc_iso(period_started_at),
        "period_ended_at": _utc_iso(period_ended_at),
        "return_fraction": _decimal(return_fraction),
        "measurement_contract": measurement_contract,
        "measurement_hash": measurement_hash,
    }


def _observation_binding_payload(
    value: CandidateShadowObservationBinding, *, include_hash: bool
) -> dict[str, object]:
    payload = _observation_binding_payload_from_values(
        {
            "contract_version": value.contract_version,
            "candidate_identity_hash": value.candidate_identity_hash,
            "shadow_config_fingerprint": value.shadow_config_fingerprint,
            "selected_strategy_id": value.selected_strategy_id,
            "period_started_at": value.period_started_at,
            "period_ended_at": value.period_ended_at,
            "return_fraction": value.return_fraction,
            "measurement_contract": value.measurement_contract,
            "measurement_hash": value.measurement_hash,
            "source_fingerprint": value.source_fingerprint,
            "measurement_verified_by_w84": value.measurement_verified_by_w84,
        }
    )
    if include_hash:
        payload["binding_hash"] = value.binding_hash
    return payload


def _observation_binding_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["period_started_at"] = _utc_iso(payload["period_started_at"])
    payload["period_ended_at"] = _utc_iso(payload["period_ended_at"])
    payload["return_fraction"] = _decimal(payload["return_fraction"])
    return payload


def _forward_binding_payload(
    value: CandidateForwardPolicyBinding, *, include_hash: bool
) -> dict[str, object]:
    payload = _forward_binding_payload_from_values(
        {
            "contract_version": value.contract_version,
            "candidate_identity_hash": value.candidate_identity_hash,
            "w83_resolution_hash": value.w83_resolution_hash,
            "campaign_id": value.campaign_id,
            "frozen_at": value.frozen_at,
            "activated_at": value.activated_at,
            "shadow_config_fingerprint": value.shadow_config_fingerprint,
            "frozen_parameters_hash": value.frozen_parameters_hash,
            "source_code_hash": value.source_code_hash,
            "forward_policy_fingerprint": value.forward_policy_fingerprint,
            "performance_qualification_deferred": value.performance_qualification_deferred,
            "paper_candidate_authorized": value.paper_candidate_authorized,
            "external_execution_authorized": value.external_execution_authorized,
            "capital_authority": value.capital_authority,
            "live_trading": value.live_trading,
        }
    )
    if include_hash:
        payload["binding_hash"] = value.binding_hash
    return payload


def _forward_binding_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["frozen_at"] = _utc_iso(payload["frozen_at"])
    payload["activated_at"] = _utc_iso(payload["activated_at"])
    return payload


def _require_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: object, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            f"{label} must be lowercase SHA-256"
        )


def _require_aware(value: object, label: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _require_positive(value: object, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ShadowForwardCandidateProvenanceIntegrityError(
            f"{label} must be finite Decimal > 0"
        )


def _require_return(value: object) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value <= Decimal("-1")
    ):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "return_fraction must be finite Decimal > -1"
        )


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _utc_iso(value: object) -> str:
    if not isinstance(value, datetime):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "canonical datetime payload requires datetime"
        )
    return _utc(value).isoformat()


def _decimal(value: object) -> str:
    if not isinstance(value, Decimal):
        raise ShadowForwardCandidateProvenanceIntegrityError(
            "canonical decimal payload requires Decimal"
        )
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _hash(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "CANDIDATE_IDENTITY_VERSION",
    "FORWARD_POLICY_BINDING_VERSION",
    "OBSERVATION_BINDING_VERSION",
    "SHADOW_CONFIG_BINDING_VERSION",
    "CandidateForwardPolicyBinding",
    "CandidateShadowConfigBinding",
    "CandidateShadowForwardIdentity",
    "CandidateShadowObservationBinding",
    "ShadowForwardCandidateProvenanceError",
    "ShadowForwardCandidateProvenanceIntegrityError",
    "build_candidate_forward_policy",
    "build_candidate_isolated_shadow_config",
    "build_candidate_shadow_forward_identity",
    "build_candidate_shadow_observation",
]
