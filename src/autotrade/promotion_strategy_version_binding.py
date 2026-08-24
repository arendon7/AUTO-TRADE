from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import inspect
import json
from pathlib import Path
import re
import sys

from autotrade.domain import OrderIntent, intent_fingerprint
from autotrade.promotion_fee_accounting import (
    PromotionFeeAccountingResolution,
    PromotionFeeAccountingStatus,
    SHADOW_FORWARD_BLOCKER,
    STRATEGY_VERSION_BLOCKER,
)
from autotrade.research import dsl as research_dsl
from autotrade.research import market as research_market
from autotrade.research import strategy as research_strategy
from autotrade.research.trials import TrialPhase, TrialSpec
import autotrade.strategy_execution_binding as binding_module
from autotrade.strategy_execution_binding import (
    ExecutionStrategyBindingEvidence,
    ExecutionStrategyBindingStatus,
)


PROMOTION_STRATEGY_VERSION_RESOLUTION_VERSION = (
    "W83_PROMOTION_STRATEGY_VERSION_RESOLUTION_V1"
)
RUNTIME_CODE_IDENTITY_VERSION = "W83_SAFE_DSL_RUNTIME_CODE_IDENTITY_V2"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_PYTHON_RUNTIME_RE = re.compile(r"^[A-Za-z0-9._-]+-[0-9]+\.[0-9]+\.[0-9]+$")


class PromotionStrategyVersionResolutionIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SafeDslRuntimeIdentity:
    version: str
    python_runtime: str
    dsl_source_hash: str
    strategy_source_hash: str
    market_source_hash: str
    identity_hash: str

    def __post_init__(self) -> None:
        if self.version != RUNTIME_CODE_IDENTITY_VERSION:
            raise PromotionStrategyVersionResolutionIntegrityError(
                "runtime identity version is not canonical W83"
            )
        if not isinstance(self.python_runtime, str) or not _PYTHON_RUNTIME_RE.fullmatch(
            self.python_runtime
        ):
            raise PromotionStrategyVersionResolutionIntegrityError(
                "python runtime identity must include implementation and exact patch version"
            )
        for label, value in (
            ("dsl_source_hash", self.dsl_source_hash),
            ("strategy_source_hash", self.strategy_source_hash),
            ("market_source_hash", self.market_source_hash),
            ("identity_hash", self.identity_hash),
        ):
            _require_hash(value, label)
        if self.identity_hash != _hash(_runtime_identity_payload(self, include_hash=False)):
            raise PromotionStrategyVersionResolutionIntegrityError(
                "safe DSL runtime identity hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _runtime_identity_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PromotionStrategyVersionResolution:
    resolution_id: str
    contract_version: str
    runtime_identity_version: str
    binding_id: str
    binding_evidence_hash: str
    w82_resolution_id: str
    w82_resolution_hash: str
    promotion_policy_id: str
    promotion_policy_hash: str
    selected_trial_id: str
    selected_trial_fingerprint: str
    selected_strategy_id: str
    selected_strategy_version: str
    trial_code_version: str
    loaded_runtime_code_hash: str
    runtime_python: str
    runtime_dsl_source_hash: str
    runtime_strategy_source_hash: str
    runtime_market_source_hash: str
    strategy_spec_hash: str
    fee_product_economics_hash: str
    intent_fingerprint: str
    resolved_promotion_blockers: tuple[str, ...]
    remaining_promotion_blockers: tuple[str, ...]
    strategy_version_execution_bound: bool
    shadow_forward_promotion_bound: bool
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    resolved_at: datetime
    resolution_hash: str

    def __post_init__(self) -> None:
        _require_id(self.resolution_id, "resolution_id")
        if self.contract_version != PROMOTION_STRATEGY_VERSION_RESOLUTION_VERSION:
            raise PromotionStrategyVersionResolutionIntegrityError(
                "resolution contract version is not canonical W83"
            )
        if self.runtime_identity_version != RUNTIME_CODE_IDENTITY_VERSION:
            raise PromotionStrategyVersionResolutionIntegrityError(
                "runtime identity version is not canonical W83"
            )
        for label, value in (
            ("binding_evidence_hash", self.binding_evidence_hash),
            ("w82_resolution_hash", self.w82_resolution_hash),
            ("promotion_policy_hash", self.promotion_policy_hash),
            ("selected_trial_fingerprint", self.selected_trial_fingerprint),
            ("trial_code_version", self.trial_code_version),
            ("loaded_runtime_code_hash", self.loaded_runtime_code_hash),
            ("runtime_dsl_source_hash", self.runtime_dsl_source_hash),
            ("runtime_strategy_source_hash", self.runtime_strategy_source_hash),
            ("runtime_market_source_hash", self.runtime_market_source_hash),
            ("strategy_spec_hash", self.strategy_spec_hash),
            ("fee_product_economics_hash", self.fee_product_economics_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("resolution_hash", self.resolution_hash),
        ):
            _require_hash(value, label)
        runtime_identity = SafeDslRuntimeIdentity(
            version=self.runtime_identity_version,
            python_runtime=self.runtime_python,
            dsl_source_hash=self.runtime_dsl_source_hash,
            strategy_source_hash=self.runtime_strategy_source_hash,
            market_source_hash=self.runtime_market_source_hash,
            identity_hash=self.loaded_runtime_code_hash,
        )
        if self.trial_code_version != runtime_identity.identity_hash:
            raise PromotionStrategyVersionResolutionIntegrityError(
                "trial code version must equal loaded safe DSL runtime identity"
            )
        if self.resolved_promotion_blockers != (STRATEGY_VERSION_BLOCKER,):
            raise PromotionStrategyVersionResolutionIntegrityError(
                "W83 may resolve only EXECUTION_STRATEGY_VERSION_UNBOUND"
            )
        if self.remaining_promotion_blockers != tuple(
            sorted(set(self.remaining_promotion_blockers))
        ):
            raise PromotionStrategyVersionResolutionIntegrityError(
                "remaining blockers must be unique sorted"
            )
        if (
            STRATEGY_VERSION_BLOCKER in self.remaining_promotion_blockers
            or SHADOW_FORWARD_BLOCKER not in self.remaining_promotion_blockers
        ):
            raise PromotionStrategyVersionResolutionIntegrityError(
                "W83 must resolve strategy-version and retain Shadow/Forward"
            )
        if (
            self.strategy_version_execution_bound is not True
            or self.shadow_forward_promotion_bound is not False
        ):
            raise PromotionStrategyVersionResolutionIntegrityError(
                "W83 binding flags are inconsistent"
            )
        if (
            self.paper_candidate_authorized is not False
            or self.external_execution_authorized is not False
            or self.runtime_execution_authorized is not False
            or self.capital_authority != "NONE"
            or self.live_trading != "BLOCKED"
        ):
            raise PromotionStrategyVersionResolutionIntegrityError(
                "W83 may not grant PAPER, execution, capital, or LIVE authority"
            )
        _require_aware(self.resolved_at, "resolved_at")
        if self.resolution_hash != _hash(_payload(self, include_hash=False)):
            raise PromotionStrategyVersionResolutionIntegrityError(
                "strategy-version resolution hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def build_safe_dsl_runtime_identity() -> SafeDslRuntimeIdentity:
    """Bind every local source file that can change safe-DSL signal semantics."""

    values = {
        "version": RUNTIME_CODE_IDENTITY_VERSION,
        "python_runtime": (
            f"{sys.implementation.name}-"
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        "dsl_source_hash": _source_sha256(
            research_dsl.StrategySpec,
            "research/dsl.py",
        ),
        "strategy_source_hash": _source_sha256(
            research_strategy.StrategyContext,
            "research/strategy.py",
        ),
        "market_source_hash": _source_sha256(
            research_market.Bar,
            "research/market.py",
        ),
    }
    return SafeDslRuntimeIdentity(
        **values,
        identity_hash=_hash(_runtime_identity_payload_from_values(values)),
    )


def safe_dsl_runtime_code_hash() -> str:
    """Compatibility helper returning the semantic runtime identity hash."""

    return build_safe_dsl_runtime_identity().identity_hash


def resolve_promotion_strategy_version_binding(
    *,
    resolution_id: str,
    binding_evidence: ExecutionStrategyBindingEvidence,
    selected_trial: TrialSpec,
    w82_resolution: PromotionFeeAccountingResolution,
    execution_intent: OrderIntent,
    resolved_at: datetime,
) -> PromotionStrategyVersionResolution:
    """Remove only the strategy-version blocker for the exact W82 candidate."""

    _require_id(resolution_id, "resolution_id")
    if not isinstance(binding_evidence, ExecutionStrategyBindingEvidence):
        raise TypeError("binding_evidence must be ExecutionStrategyBindingEvidence")
    if not isinstance(selected_trial, TrialSpec):
        raise TypeError("selected_trial must be TrialSpec")
    if not isinstance(w82_resolution, PromotionFeeAccountingResolution):
        raise TypeError("w82_resolution must be PromotionFeeAccountingResolution")
    if not isinstance(execution_intent, OrderIntent):
        raise TypeError("execution_intent must be OrderIntent")
    _require_aware(resolved_at, "resolved_at")

    _validate_binding(binding_evidence)
    runtime_identity = build_safe_dsl_runtime_identity()
    _validate_trial(
        binding_evidence=binding_evidence,
        selected_trial=selected_trial,
        loaded_runtime_hash=runtime_identity.identity_hash,
    )
    _validate_w82(
        binding_evidence=binding_evidence,
        w82_resolution=w82_resolution,
        execution_intent=execution_intent,
    )
    if (
        _utc(resolved_at) < _utc(binding_evidence.assessed_at)
        or _utc(resolved_at) < _utc(w82_resolution.resolved_at)
        or _utc(resolved_at) < _utc(execution_intent.created_at)
    ):
        raise PromotionStrategyVersionResolutionIntegrityError(
            "W83 resolution violates temporal causality"
        )

    remaining = tuple(
        sorted(
            blocker
            for blocker in w82_resolution.remaining_promotion_blockers
            if blocker != STRATEGY_VERSION_BLOCKER
        )
    )
    values = {
        "resolution_id": resolution_id,
        "contract_version": PROMOTION_STRATEGY_VERSION_RESOLUTION_VERSION,
        "runtime_identity_version": runtime_identity.version,
        "binding_id": binding_evidence.binding_id,
        "binding_evidence_hash": binding_evidence.evidence_hash,
        "w82_resolution_id": w82_resolution.resolution_id,
        "w82_resolution_hash": w82_resolution.resolution_hash,
        "promotion_policy_id": binding_evidence.promotion_policy_id,
        "promotion_policy_hash": binding_evidence.promotion_policy_hash,
        "selected_trial_id": selected_trial.trial_id,
        "selected_trial_fingerprint": selected_trial.fingerprint,
        "selected_strategy_id": selected_trial.strategy_id,
        "selected_strategy_version": selected_trial.strategy_version,
        "trial_code_version": selected_trial.code_version,
        "loaded_runtime_code_hash": runtime_identity.identity_hash,
        "runtime_python": runtime_identity.python_runtime,
        "runtime_dsl_source_hash": runtime_identity.dsl_source_hash,
        "runtime_strategy_source_hash": runtime_identity.strategy_source_hash,
        "runtime_market_source_hash": runtime_identity.market_source_hash,
        "strategy_spec_hash": binding_evidence.strategy_spec_hash,
        "fee_product_economics_hash": binding_evidence.fee_product_economics_hash,
        "intent_fingerprint": binding_evidence.intent_fingerprint,
        "resolved_promotion_blockers": (STRATEGY_VERSION_BLOCKER,),
        "remaining_promotion_blockers": remaining,
        "strategy_version_execution_bound": True,
        "shadow_forward_promotion_bound": False,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "resolved_at": resolved_at,
    }
    return PromotionStrategyVersionResolution(
        **values,
        resolution_hash=_hash(_payload_from_values(values)),
    )


def _validate_binding(value: ExecutionStrategyBindingEvidence) -> None:
    expected_hash = binding_module._hash(
        binding_module._evidence_payload(value, include_hash=False)
    )
    if value.evidence_hash != expected_hash:
        raise PromotionStrategyVersionResolutionIntegrityError(
            "W83 binding evidence hash mismatch"
        )
    if (
        value.status is not ExecutionStrategyBindingStatus.PASS
        or value.artifact_frozen_in_selected_trial is not True
        or value.dataset_bound is not True
        or value.intent_semantics_bound is not True
        or value.strategy_version_binding_proven is not True
    ):
        raise PromotionStrategyVersionResolutionIntegrityError(
            "W83 binding proof is incomplete"
        )
    if (
        value.shadow_forward_promotion_bound is not False
        or value.paper_candidate_authorized is not False
        or value.external_execution_authorized is not False
        or value.runtime_execution_authorized is not False
        or value.capital_authority != "NONE"
        or value.live_trading != "BLOCKED"
    ):
        raise PromotionStrategyVersionResolutionIntegrityError(
            "W83 binding authority boundary is not intact"
        )


def _validate_trial(
    *,
    binding_evidence: ExecutionStrategyBindingEvidence,
    selected_trial: TrialSpec,
    loaded_runtime_hash: str,
) -> None:
    expected_parameters_hash = binding_module._hash(dict(selected_trial.parameters))
    if (
        selected_trial.phase is not TrialPhase.DEVELOPMENT
        or bool(selected_trial.holdout_authorization_id)
        or selected_trial.trial_id != binding_evidence.selected_trial_id
        or selected_trial.fingerprint != binding_evidence.selected_trial_fingerprint
        or selected_trial.strategy_id != binding_evidence.selected_strategy_id
        or selected_trial.strategy_version != binding_evidence.selected_strategy_version
        or selected_trial.dataset_hash != binding_evidence.trial_dataset_hash
        or selected_trial.parameters.get("spec_hash")
        != binding_evidence.strategy_spec_hash
        or expected_parameters_hash != binding_evidence.trial_parameters_hash
    ):
        raise PromotionStrategyVersionResolutionIntegrityError(
            "selected trial no longer matches frozen W83 binding evidence"
        )
    if (
        selected_trial.code_version != binding_evidence.trial_code_version
        or selected_trial.code_version != loaded_runtime_hash
    ):
        raise PromotionStrategyVersionResolutionIntegrityError(
            "selected trial code_version differs from loaded safe DSL runtime"
        )


def _validate_w82(
    *,
    binding_evidence: ExecutionStrategyBindingEvidence,
    w82_resolution: PromotionFeeAccountingResolution,
    execution_intent: OrderIntent,
) -> None:
    expected_w82_hash = binding_module.fee_resolution_module._hash(
        binding_module.fee_resolution_module._payload(
            w82_resolution, include_hash=False
        )
    )
    intent_hash = intent_fingerprint(execution_intent)
    if (
        w82_resolution.resolution_hash != expected_w82_hash
        or w82_resolution.status is not PromotionFeeAccountingStatus.PASS
        or not w82_resolution.fee_accounting_complete
        or w82_resolution.strategy_version_execution_bound is not False
        or STRATEGY_VERSION_BLOCKER
        not in w82_resolution.remaining_promotion_blockers
        or SHADOW_FORWARD_BLOCKER
        not in w82_resolution.remaining_promotion_blockers
    ):
        raise PromotionStrategyVersionResolutionIntegrityError(
            "W82 prerequisite is not exact fee-complete unresolved input"
        )
    if (
        w82_resolution.broker_authoritative_fee_proven is not False
        or w82_resolution.realized_profitability_authorized is not False
        or w82_resolution.paper_candidate_authorized is not False
        or w82_resolution.external_execution_authorized is not False
        or w82_resolution.capital_authority != "NONE"
        or w82_resolution.live_trading != "BLOCKED"
    ):
        raise PromotionStrategyVersionResolutionIntegrityError(
            "W82 authority/no-claims boundary is not intact"
        )
    if (
        binding_evidence.w82_resolution_id != w82_resolution.resolution_id
        or binding_evidence.w82_resolution_hash != w82_resolution.resolution_hash
        or binding_evidence.fee_product_economics_hash
        != w82_resolution.fee_product_economics_hash
        or binding_evidence.promotion_policy_id != w82_resolution.promotion_policy_id
        or binding_evidence.promotion_policy_hash != w82_resolution.promotion_policy_hash
        or binding_evidence.selected_strategy_id != w82_resolution.selected_strategy_id
        or binding_evidence.selected_strategy_version
        != w82_resolution.selected_strategy_version
        or binding_evidence.intent_fingerprint != intent_hash
        or w82_resolution.intent_fingerprint != intent_hash
        or execution_intent.strategy_id != binding_evidence.selected_strategy_id
    ):
        raise PromotionStrategyVersionResolutionIntegrityError(
            "W83 binding does not match exact W82 candidate/intent"
        )


def _runtime_identity_payload(
    value: SafeDslRuntimeIdentity, *, include_hash: bool
) -> dict[str, object]:
    payload = _runtime_identity_payload_from_values(
        {
            "version": value.version,
            "python_runtime": value.python_runtime,
            "dsl_source_hash": value.dsl_source_hash,
            "strategy_source_hash": value.strategy_source_hash,
            "market_source_hash": value.market_source_hash,
        }
    )
    if include_hash:
        payload["identity_hash"] = value.identity_hash
    return payload


def _runtime_identity_payload_from_values(
    values: dict[str, object],
) -> dict[str, object]:
    return dict(values)


def _payload(
    value: PromotionStrategyVersionResolution, *, include_hash: bool
) -> dict[str, object]:
    payload = _payload_from_values(
        {
            name: getattr(value, name)
            for name in (
                "resolution_id",
                "contract_version",
                "runtime_identity_version",
                "binding_id",
                "binding_evidence_hash",
                "w82_resolution_id",
                "w82_resolution_hash",
                "promotion_policy_id",
                "promotion_policy_hash",
                "selected_trial_id",
                "selected_trial_fingerprint",
                "selected_strategy_id",
                "selected_strategy_version",
                "trial_code_version",
                "loaded_runtime_code_hash",
                "runtime_python",
                "runtime_dsl_source_hash",
                "runtime_strategy_source_hash",
                "runtime_market_source_hash",
                "strategy_spec_hash",
                "fee_product_economics_hash",
                "intent_fingerprint",
                "resolved_promotion_blockers",
                "remaining_promotion_blockers",
                "strategy_version_execution_bound",
                "shadow_forward_promotion_bound",
                "paper_candidate_authorized",
                "external_execution_authorized",
                "runtime_execution_authorized",
                "capital_authority",
                "live_trading",
                "resolved_at",
            )
        }
    )
    if include_hash:
        payload["resolution_hash"] = value.resolution_hash
    return payload


def _payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    payload["resolved_promotion_blockers"] = list(
        payload["resolved_promotion_blockers"]
    )
    payload["remaining_promotion_blockers"] = list(
        payload["remaining_promotion_blockers"]
    )
    payload["resolved_at"] = _utc_iso(payload["resolved_at"])
    return payload


def _source_sha256(subject: object, label: str) -> str:
    source_path = inspect.getsourcefile(subject)
    if source_path is None:
        raise PromotionStrategyVersionResolutionIntegrityError(
            f"cannot locate {label} runtime source"
        )
    return sha256(Path(source_path).read_bytes()).hexdigest()


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PromotionStrategyVersionResolutionIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PromotionStrategyVersionResolutionIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise PromotionStrategyVersionResolutionIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _utc_iso(value: object) -> str:
    if not isinstance(value, datetime):
        raise PromotionStrategyVersionResolutionIntegrityError(
            "datetime value required"
        )
    return _utc(value).isoformat()


def _hash(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


__all__ = [
    "PROMOTION_STRATEGY_VERSION_RESOLUTION_VERSION",
    "RUNTIME_CODE_IDENTITY_VERSION",
    "PromotionStrategyVersionResolution",
    "PromotionStrategyVersionResolutionIntegrityError",
    "SafeDslRuntimeIdentity",
    "build_safe_dsl_runtime_identity",
    "resolve_promotion_strategy_version_binding",
    "safe_dsl_runtime_code_hash",
]
