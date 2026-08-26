from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re

import autotrade.fee_product_economics as product_module
import autotrade.paper_candidate_admission_final_verification as final_admission
import autotrade.paper_runtime_readiness_source_snapshot as source_snapshot
import autotrade.promotion_strategy_version_binding as w83_module
from autotrade.fee_product_economics import (
    FeeProductEconomicsEvidence,
    FeeProductEconomicsStatus,
)
from autotrade.paper_candidate_admission_final_verification import (
    PaperCandidateAdmissionFinalVerification,
)
from autotrade.paper_candidate_admission_lifecycle import PaperCandidateEligibilityState
from autotrade.paper_runtime_readiness_source_snapshot import (
    W85DurableEligibilitySnapshotProof,
)
from autotrade.promotion_strategy_version_binding import (
    PromotionStrategyVersionResolution,
)


PAPER_RUNTIME_CANDIDATE_IDENTITY_VERSION = "W86_PAPER_RUNTIME_CANDIDATE_IDENTITY_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,11}$")


class PaperRuntimeCandidateIdentityError(RuntimeError):
    pass


class PaperRuntimeCandidateIdentityIntegrityError(PaperRuntimeCandidateIdentityError):
    pass


@dataclass(frozen=True, slots=True)
class PaperRuntimeCandidateIdentityProof:
    proof_id: str
    contract_version: str
    w85_source_snapshot_hash: str
    authority_key: str
    admission_id: str
    admission_hash: str
    final_admission_verification_hash: str
    w83_resolution_id: str
    w83_resolution_hash: str
    w83_binding_hash: str
    selected_trial_fingerprint: str
    selected_strategy_id: str
    selected_strategy_version: str
    strategy_spec_hash: str
    loaded_runtime_code_hash: str
    fee_product_economics_hash: str
    intent_fingerprint: str
    product_id: str
    asset_class: str
    venue: str
    symbol: str
    side: str
    base_currency: str
    quote_currency: str
    product_identity_verified: bool
    strategy_runtime_identity_verified: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    proof_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("proof_id", self.proof_id),
            ("admission_id", self.admission_id),
            ("w83_resolution_id", self.w83_resolution_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
            ("product_id", self.product_id),
            ("asset_class", self.asset_class),
            ("venue", self.venue),
        ):
            _require_id(value, label)
        if self.contract_version != PAPER_RUNTIME_CANDIDATE_IDENTITY_VERSION:
            raise PaperRuntimeCandidateIdentityIntegrityError(
                "W86 candidate identity version is not canonical"
            )
        for label, value in (
            ("w85_source_snapshot_hash", self.w85_source_snapshot_hash),
            ("authority_key", self.authority_key),
            ("admission_hash", self.admission_hash),
            ("final_admission_verification_hash", self.final_admission_verification_hash),
            ("w83_resolution_hash", self.w83_resolution_hash),
            ("w83_binding_hash", self.w83_binding_hash),
            ("selected_trial_fingerprint", self.selected_trial_fingerprint),
            ("strategy_spec_hash", self.strategy_spec_hash),
            ("loaded_runtime_code_hash", self.loaded_runtime_code_hash),
            ("fee_product_economics_hash", self.fee_product_economics_hash),
            ("intent_fingerprint", self.intent_fingerprint),
            ("proof_hash", self.proof_hash),
        ):
            _require_hash(value, label)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise PaperRuntimeCandidateIdentityIntegrityError("symbol is required")
        if self.side not in {"BUY", "SELL"}:
            raise PaperRuntimeCandidateIdentityIntegrityError(
                "side must be canonical BUY or SELL"
            )
        _require_currency(self.base_currency, "base_currency")
        _require_currency(self.quote_currency, "quote_currency")
        if self.base_currency == self.quote_currency:
            raise PaperRuntimeCandidateIdentityIntegrityError(
                "base and quote currencies must differ"
            )
        if (
            self.product_identity_verified is not True
            or self.strategy_runtime_identity_verified is not True
        ):
            raise PaperRuntimeCandidateIdentityIntegrityError(
                "W86 identity proof requires exact product and runtime binding"
            )
        _require_no_execution_authority(
            paper_execution=self.paper_execution_authorized,
            external=self.external_execution_authorized,
            runtime=self.runtime_execution_authorized,
            capital=self.capital_authority,
            live=self.live_trading,
        )
        if self.proof_hash != _hash(_payload(self, include_hash=False)):
            raise PaperRuntimeCandidateIdentityIntegrityError(
                "W86 candidate identity proof hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def bind_paper_runtime_candidate_identity(
    *,
    proof_id: str,
    source_proof: W85DurableEligibilitySnapshotProof,
    final_verification: PaperCandidateAdmissionFinalVerification,
    w83_resolution: PromotionStrategyVersionResolution,
    product_economics: FeeProductEconomicsEvidence,
) -> PaperRuntimeCandidateIdentityProof:
    """Bind W86 runtime instrument identity without caller-selected product fields."""

    _require_id(proof_id, "proof_id")
    if not isinstance(source_proof, W85DurableEligibilitySnapshotProof):
        raise TypeError("source_proof must be W85DurableEligibilitySnapshotProof")
    if not isinstance(final_verification, PaperCandidateAdmissionFinalVerification):
        raise TypeError(
            "final_verification must be PaperCandidateAdmissionFinalVerification"
        )
    if not isinstance(w83_resolution, PromotionStrategyVersionResolution):
        raise TypeError("w83_resolution must be PromotionStrategyVersionResolution")
    if not isinstance(product_economics, FeeProductEconomicsEvidence):
        raise TypeError("product_economics must be FeeProductEconomicsEvidence")

    _validate_source_proof(source_proof)
    _validate_final_verification(final_verification)
    _validate_w83(w83_resolution)
    _validate_product(product_economics)

    if (
        source_proof.current_state is not PaperCandidateEligibilityState.ACTIVE
        or source_proof.candidate_currently_eligible is not True
    ):
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "W86 runtime identity requires current ACTIVE W85 candidate"
        )
    if source_proof.final_admission_verification_hash != final_verification.verification_hash:
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "W86 source proof does not bind exact final W85 verification"
        )
    if (
        source_proof.authority_key != final_verification.authority_key
        or source_proof.admission_id != final_verification.admission_id
        or source_proof.admission_hash != final_verification.admission_hash
    ):
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "W86 source proof and final W85 verification identify different admission"
        )
    if final_verification.w83_resolution_hash != w83_resolution.resolution_hash:
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "final W85 verification does not bind exact supplied W83 resolution"
        )
    if final_verification.w83_binding_hash != w83_resolution.binding_evidence_hash:
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "final W85 verification does not bind exact W83 execution evidence"
        )

    snapshot_checks = (
        (
            source_proof.selected_trial_fingerprint,
            w83_resolution.selected_trial_fingerprint,
            "selected trial fingerprint",
        ),
        (source_proof.strategy_spec_hash, w83_resolution.strategy_spec_hash, "strategy spec"),
        (
            source_proof.loaded_runtime_code_hash,
            w83_resolution.loaded_runtime_code_hash,
            "loaded runtime",
        ),
        (
            source_proof.fee_product_economics_hash,
            w83_resolution.fee_product_economics_hash,
            "fee product economics",
        ),
        (source_proof.intent_fingerprint, w83_resolution.intent_fingerprint, "intent"),
    )
    for durable_value, supplied_value, label in snapshot_checks:
        if durable_value != supplied_value:
            raise PaperRuntimeCandidateIdentityIntegrityError(
                f"W83 {label} differs from exact W85 source snapshot"
            )

    if w83_resolution.fee_product_economics_hash != product_economics.evidence_hash:
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "W83 does not bind exact supplied W82 product economics"
        )
    if w83_resolution.intent_fingerprint != product_economics.intent_fingerprint:
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "W82 product economics intent differs from exact W83 candidate"
        )

    values = {
        "proof_id": proof_id,
        "contract_version": PAPER_RUNTIME_CANDIDATE_IDENTITY_VERSION,
        "w85_source_snapshot_hash": source_proof.proof_hash,
        "authority_key": source_proof.authority_key,
        "admission_id": source_proof.admission_id,
        "admission_hash": source_proof.admission_hash,
        "final_admission_verification_hash": final_verification.verification_hash,
        "w83_resolution_id": w83_resolution.resolution_id,
        "w83_resolution_hash": w83_resolution.resolution_hash,
        "w83_binding_hash": w83_resolution.binding_evidence_hash,
        "selected_trial_fingerprint": w83_resolution.selected_trial_fingerprint,
        "selected_strategy_id": w83_resolution.selected_strategy_id,
        "selected_strategy_version": w83_resolution.selected_strategy_version,
        "strategy_spec_hash": w83_resolution.strategy_spec_hash,
        "loaded_runtime_code_hash": w83_resolution.loaded_runtime_code_hash,
        "fee_product_economics_hash": product_economics.evidence_hash,
        "intent_fingerprint": product_economics.intent_fingerprint,
        "product_id": product_economics.product_id,
        "asset_class": product_economics.asset_class,
        "venue": product_economics.venue,
        "symbol": product_economics.symbol,
        "side": product_economics.side.value,
        "base_currency": product_economics.base_currency,
        "quote_currency": product_economics.quote_currency,
        "product_identity_verified": True,
        "strategy_runtime_identity_verified": True,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return PaperRuntimeCandidateIdentityProof(
        **values,
        proof_hash=_hash(_payload_from_values(values)),
    )


def _validate_source_proof(value: W85DurableEligibilitySnapshotProof) -> None:
    expected = source_snapshot._hash(source_snapshot._proof_payload(value, include_hash=False))
    if value.proof_hash != expected:
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "W85 source snapshot proof hash mismatch"
        )
    if (
        value.sqlite_read_only is not True
        or value.sqlite_snapshot_consistent is not True
        or value.concurrent_durable_change_detected is not False
        or value.durable_admission_verified is not True
        or value.durable_lifecycle_verified is not True
    ):
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "W85 source snapshot safety boundary is not intact"
        )
    _require_no_execution_authority(
        paper_execution=value.paper_execution_authorized,
        external=value.external_execution_authorized,
        runtime=value.runtime_execution_authorized,
        capital=value.capital_authority,
        live=value.live_trading,
    )


def _validate_final_verification(value: PaperCandidateAdmissionFinalVerification) -> None:
    expected = final_admission._hash(final_admission._payload(value, include_hash=False))
    if value.verification_hash != expected:
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "final W85 verification hash mismatch"
        )
    if (
        value.paper_candidate_was_admitted is not True
        or value.admission_source_truth_verified is not True
        or value.w84_source_truth_verified is not True
        or value.w84_admission_source_proof_bound is not True
        or value.historical_w84_timestamp_used_for_freshness is not False
    ):
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "final W85 verification truth boundary is not intact"
        )
    _require_no_execution_authority(
        paper_execution=value.paper_execution_authorized,
        external=value.external_execution_authorized,
        runtime=value.runtime_execution_authorized,
        capital=value.capital_authority,
        live=value.live_trading,
    )


def _validate_w83(value: PromotionStrategyVersionResolution) -> None:
    expected = w83_module._hash(w83_module._payload(value, include_hash=False))
    if value.resolution_hash != expected:
        raise PaperRuntimeCandidateIdentityIntegrityError("W83 resolution hash mismatch")
    if (
        value.strategy_version_execution_bound is not True
        or value.paper_candidate_authorized is not False
        or value.external_execution_authorized is not False
        or value.runtime_execution_authorized is not False
        or value.capital_authority != "NONE"
        or value.live_trading != "BLOCKED"
    ):
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "W83 strategy/runtime boundary is not intact"
        )


def _validate_product(value: FeeProductEconomicsEvidence) -> None:
    expected = product_module._hash(product_module._evidence_payload(value, include_hash=False))
    if value.evidence_hash != expected:
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "W82 product economics hash mismatch"
        )
    if (
        value.status is not FeeProductEconomicsStatus.PASS
        or value.fee_schedule_conservative is not True
        or value.product_fee_economics_complete is not True
        or value.literal_broker_fee_semantics_modeled is not True
    ):
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "W82 product economics is not complete conservative PASS evidence"
        )
    if (
        value.broker_authoritative_fee_proven is not False
        or value.realized_profitability_authorized is not False
        or value.paper_candidate_authorized is not False
        or value.external_execution_authorized is not False
        or value.capital_authority != "NONE"
        or value.live_trading != "BLOCKED"
    ):
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "W82 product economics authority/no-claims boundary is not intact"
        )


def _payload(
    value: PaperRuntimeCandidateIdentityProof, *, include_hash: bool
) -> dict[str, object]:
    names = tuple(
        field
        for field in PaperRuntimeCandidateIdentityProof.__dataclass_fields__
        if field != "proof_hash"
    )
    payload = _payload_from_values({name: getattr(value, name) for name in names})
    if include_hash:
        payload["proof_hash"] = value.proof_hash
    return payload


def _payload_from_values(values: dict[str, object]) -> dict[str, object]:
    return dict(values)


def _require_no_execution_authority(
    *, paper_execution: bool, external: bool, runtime: bool, capital: str, live: str
) -> None:
    if (
        paper_execution is not False
        or external is not False
        or runtime is not False
        or capital != "NONE"
        or live != "BLOCKED"
    ):
        raise PaperRuntimeCandidateIdentityIntegrityError(
            "W86 candidate identity may not grant execution, capital or LIVE authority"
        )


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperRuntimeCandidateIdentityIntegrityError(
            f"{label} must be canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperRuntimeCandidateIdentityIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_currency(value: str, label: str) -> None:
    if not isinstance(value, str) or not _CURRENCY_RE.fullmatch(value):
        raise PaperRuntimeCandidateIdentityIntegrityError(
            f"{label} must be canonical uppercase currency"
        )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "PAPER_RUNTIME_CANDIDATE_IDENTITY_VERSION",
    "PaperRuntimeCandidateIdentityError",
    "PaperRuntimeCandidateIdentityIntegrityError",
    "PaperRuntimeCandidateIdentityProof",
    "bind_paper_runtime_candidate_identity",
]
