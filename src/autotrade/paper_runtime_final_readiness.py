from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from enum import StrEnum
from hashlib import sha256
import json
import re

import autotrade.paper_candidate_admission_lifecycle as lifecycle
import autotrade.paper_runtime_asset_truth as asset_module
import autotrade.paper_runtime_broker_truth as broker_module
import autotrade.paper_runtime_candidate_identity as candidate_module
import autotrade.paper_runtime_market_truth as market_module
import autotrade.paper_runtime_readiness_source_snapshot as source_module
import autotrade.paper_runtime_safety_health_truth as safety_module
from autotrade.paper_runtime_asset_truth import PaperRuntimeAssetTruthProof
from autotrade.paper_runtime_broker_truth import PaperRuntimeBrokerTruthProof
from autotrade.paper_runtime_candidate_identity import PaperRuntimeCandidateIdentityProof
from autotrade.paper_runtime_market_truth import PaperRuntimeMarketTruthProof
from autotrade.paper_runtime_readiness_source_snapshot import W85DurableEligibilitySnapshotProof
from autotrade.paper_runtime_safety_health_truth import PaperRuntimeSafetyHealthTruthProof

PAPER_RUNTIME_FINAL_READINESS_VERSION = "W86_PAPER_RUNTIME_FINAL_READINESS_V1"
W85_PROBATION_NOTIONAL_MAX_USD = Decimal("5")
W85_PROBATION_ORDER_MAX = 1
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")

class PaperRuntimeFinalReadinessIntegrityError(RuntimeError):
    pass

class PaperRuntimeReadinessStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"

class PaperRuntimeReadinessBlocker(StrEnum):
    W85_CANDIDATE_NOT_ACTIVE = "W85_CANDIDATE_NOT_ACTIVE"
    W85_SOURCE_STALE = "W85_SOURCE_STALE"
    W85_ADMISSION_EXPIRED = "W85_ADMISSION_EXPIRED"
    BROKER_TRUTH_STALE = "BROKER_TRUTH_STALE"
    BROKER_ACCOUNT_NOT_FLAT = "BROKER_ACCOUNT_NOT_FLAT"
    ASSET_TRUTH_STALE = "ASSET_TRUTH_STALE"
    MARKET_TRUTH_STALE = "MARKET_TRUTH_STALE"
    SAFETY_HEALTH_TRUTH_STALE = "SAFETY_HEALTH_TRUTH_STALE"
    SIDE_NOT_SUPPORTED_FOR_FLAT_CANARY = "SIDE_NOT_SUPPORTED_FOR_FLAT_CANARY"
    MINIMUM_NOTIONAL_EXCEEDS_PROBATION_CAP = "MINIMUM_NOTIONAL_EXCEEDS_PROBATION_CAP"

_FRESHNESS = frozenset({
    PaperRuntimeReadinessBlocker.W85_SOURCE_STALE,
    PaperRuntimeReadinessBlocker.W85_ADMISSION_EXPIRED,
    PaperRuntimeReadinessBlocker.BROKER_TRUTH_STALE,
    PaperRuntimeReadinessBlocker.ASSET_TRUTH_STALE,
    PaperRuntimeReadinessBlocker.MARKET_TRUTH_STALE,
    PaperRuntimeReadinessBlocker.SAFETY_HEALTH_TRUTH_STALE,
})

@dataclass(frozen=True, slots=True)
class PaperRuntimeFinalReadinessPolicy:
    source_age_seconds: int = 30
    broker_age_seconds: int = 30
    asset_age_seconds: int = 30
    market_age_seconds: int = 5
    safety_health_age_seconds: int = 30
    ready_ttl_seconds: int = 5

    def __post_init__(self) -> None:
        for name, value, upper in (
            ("source_age_seconds", self.source_age_seconds, 30),
            ("broker_age_seconds", self.broker_age_seconds, 30),
            ("asset_age_seconds", self.asset_age_seconds, 30),
            ("market_age_seconds", self.market_age_seconds, 5),
            ("safety_health_age_seconds", self.safety_health_age_seconds, 30),
            ("ready_ttl_seconds", self.ready_ttl_seconds, 5),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
                raise ValueError(f"{name} must be integer seconds in [1, {upper}]")

    @property
    def fingerprint(self) -> str:
        return _hash({
            "source_age_seconds": self.source_age_seconds,
            "broker_age_seconds": self.broker_age_seconds,
            "asset_age_seconds": self.asset_age_seconds,
            "market_age_seconds": self.market_age_seconds,
            "safety_health_age_seconds": self.safety_health_age_seconds,
            "ready_ttl_seconds": self.ready_ttl_seconds,
        })

@dataclass(frozen=True, slots=True)
class PaperRuntimeFinalReadinessReceipt:
    receipt_id: str
    contract_version: str
    policy_hash: str
    source_snapshot_hash: str
    candidate_identity_hash: str
    broker_truth_hash: str
    asset_truth_hash: str
    market_truth_hash: str
    safety_health_truth_hash: str
    authority_key: str
    admission_hash: str
    strategy_id: str
    product_id: str
    symbol: str
    account_id: str
    probation_notional_cap_usd: Decimal
    probation_order_cap: int
    minimum_executable_quantity: Decimal
    conservative_unit_price: Decimal
    minimum_executable_notional_usd: Decimal
    probation_headroom_usd: Decimal
    status: PaperRuntimeReadinessStatus
    blocker_codes: tuple[PaperRuntimeReadinessBlocker, ...]
    observed_at: datetime
    valid_until: datetime
    upstream_integrity_verified: bool
    freshness_verified: bool
    minimum_notional_compatible: bool
    separate_execution_approval_required: bool
    order_intent_created: bool
    oms_handoff_performed: bool
    capital_reserved: bool
    broker_write_performed: bool
    paper_runtime_ready: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        _id(self.receipt_id, "receipt_id")
        if self.contract_version != PAPER_RUNTIME_FINAL_READINESS_VERSION:
            raise PaperRuntimeFinalReadinessIntegrityError("non-canonical W86 final readiness version")
        for name in (
            "policy_hash", "source_snapshot_hash", "candidate_identity_hash", "broker_truth_hash",
            "asset_truth_hash", "market_truth_hash", "safety_health_truth_hash", "authority_key",
            "admission_hash", "receipt_hash",
        ):
            _sha(getattr(self, name), name)
        for name in ("strategy_id", "product_id"):
            _id(getattr(self, name), name)
        _aware(self.observed_at, "observed_at")
        _aware(self.valid_until, "valid_until")
        for name in (
            "probation_notional_cap_usd", "minimum_executable_quantity",
            "conservative_unit_price", "minimum_executable_notional_usd",
        ):
            _positive(getattr(self, name), name)
        if not isinstance(self.probation_headroom_usd, Decimal) or not self.probation_headroom_usd.is_finite():
            raise PaperRuntimeFinalReadinessIntegrityError("probation_headroom_usd must be finite")
        if self.probation_order_cap != W85_PROBATION_ORDER_MAX:
            raise PaperRuntimeFinalReadinessIntegrityError("probation order cap must remain 1")
        if self.probation_notional_cap_usd > W85_PROBATION_NOTIONAL_MAX_USD:
            raise PaperRuntimeFinalReadinessIntegrityError("probation notional cap may not exceed USD 5")
        ready = not self.blocker_codes
        expected_status = PaperRuntimeReadinessStatus.READY if ready else PaperRuntimeReadinessStatus.BLOCKED
        if self.status is not expected_status or self.paper_runtime_ready is not ready:
            raise PaperRuntimeFinalReadinessIntegrityError("status/readiness disagrees with blockers")
        expected_compatible = self.minimum_executable_notional_usd <= self.probation_notional_cap_usd
        if self.minimum_notional_compatible is not expected_compatible:
            raise PaperRuntimeFinalReadinessIntegrityError("minimum-notional flag is inconsistent")
        fresh = not any(code in _FRESHNESS for code in self.blocker_codes)
        if self.freshness_verified is not fresh:
            raise PaperRuntimeFinalReadinessIntegrityError("freshness flag is inconsistent")
        if self.probation_headroom_usd != self.probation_notional_cap_usd - self.minimum_executable_notional_usd:
            raise PaperRuntimeFinalReadinessIntegrityError("probation headroom is inconsistent")
        if ready and self.valid_until < self.observed_at:
            raise PaperRuntimeFinalReadinessIntegrityError("READY receipt is stale")
        if not ready and self.valid_until != self.observed_at:
            raise PaperRuntimeFinalReadinessIntegrityError("BLOCKED receipt must expire immediately")
        if self.upstream_integrity_verified is not True:
            raise PaperRuntimeFinalReadinessIntegrityError("upstream integrity not verified")
        if (
            self.separate_execution_approval_required is not True
            or self.order_intent_created is not False
            or self.oms_handoff_performed is not False
            or self.capital_reserved is not False
            or self.broker_write_performed is not False
            or self.paper_execution_authorized is not False
            or self.external_execution_authorized is not False
            or self.runtime_execution_authorized is not False
            or self.capital_authority != "NONE"
            or self.live_trading != "BLOCKED"
        ):
            raise PaperRuntimeFinalReadinessIntegrityError(
                "runtime readiness may not grant execution, capital, broker-write, OMS, or LIVE authority"
            )
        if self.receipt_hash != _hash(_payload(self, include_hash=False)):
            raise PaperRuntimeFinalReadinessIntegrityError("final readiness receipt hash mismatch")

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)

def finalize_paper_runtime_readiness(
    *,
    receipt_id: str,
    source_snapshot: W85DurableEligibilitySnapshotProof,
    candidate_identity: PaperRuntimeCandidateIdentityProof,
    broker_truth: PaperRuntimeBrokerTruthProof,
    asset_truth: PaperRuntimeAssetTruthProof,
    market_truth: PaperRuntimeMarketTruthProof,
    safety_health_truth: PaperRuntimeSafetyHealthTruthProof,
    policy: PaperRuntimeFinalReadinessPolicy | None = None,
) -> PaperRuntimeFinalReadinessReceipt:
    """Issue finite read-only runtime readiness; never execution authority."""
    _id(receipt_id, "receipt_id")
    for value, expected, name in (
        (source_snapshot, W85DurableEligibilitySnapshotProof, "source_snapshot"),
        (candidate_identity, PaperRuntimeCandidateIdentityProof, "candidate_identity"),
        (broker_truth, PaperRuntimeBrokerTruthProof, "broker_truth"),
        (asset_truth, PaperRuntimeAssetTruthProof, "asset_truth"),
        (market_truth, PaperRuntimeMarketTruthProof, "market_truth"),
        (safety_health_truth, PaperRuntimeSafetyHealthTruthProof, "safety_health_truth"),
    ):
        if not isinstance(value, expected):
            raise TypeError(f"{name} has invalid type")
    policy = policy or PaperRuntimeFinalReadinessPolicy()
    if not isinstance(policy, PaperRuntimeFinalReadinessPolicy):
        raise TypeError("policy has invalid type")
    _validate_integrity(source_snapshot, candidate_identity, broker_truth, asset_truth, market_truth, safety_health_truth)
    _validate_chain(source_snapshot, candidate_identity, broker_truth, asset_truth, market_truth, safety_health_truth)
    if source_snapshot.probation_notional_cap_usd > W85_PROBATION_NOTIONAL_MAX_USD:
        raise PaperRuntimeFinalReadinessIntegrityError("durable W85 cap exceeds USD 5")
    if source_snapshot.probation_order_cap != W85_PROBATION_ORDER_MAX:
        raise PaperRuntimeFinalReadinessIntegrityError("durable W85 order cap differs from 1")

    now = _utc(_now_utc())
    observed = {
        "source": source_snapshot.reproved_at,
        "broker": broker_truth.observed_at,
        "asset": asset_truth.observed_at,
        "market": market_truth.observed_at,
        "safety": safety_health_truth.observed_at,
    }
    ages: dict[str, Decimal] = {}
    for name, value in observed.items():
        if _utc(value) > now:
            raise PaperRuntimeFinalReadinessIntegrityError(f"{name} evidence is in process future")
        ages[name] = Decimal(str((now - _utc(value)).total_seconds()))

    minimum_quantity = _ceil(asset_truth.min_order_size, asset_truth.min_trade_increment)
    conservative_price = _ceil(
        market_truth.ask_price if candidate_identity.side == "BUY" else market_truth.bid_price,
        asset_truth.price_increment,
    )
    minimum_notional = minimum_quantity * conservative_price

    blockers: list[PaperRuntimeReadinessBlocker] = []
    if source_snapshot.current_state is not lifecycle.PaperCandidateEligibilityState.ACTIVE or source_snapshot.candidate_currently_eligible is not True:
        blockers.append(PaperRuntimeReadinessBlocker.W85_CANDIDATE_NOT_ACTIVE)
    if ages["source"] > Decimal(policy.source_age_seconds):
        blockers.append(PaperRuntimeReadinessBlocker.W85_SOURCE_STALE)
    if now > _utc(source_snapshot.admission_valid_until):
        blockers.append(PaperRuntimeReadinessBlocker.W85_ADMISSION_EXPIRED)
    if ages["broker"] > Decimal(policy.broker_age_seconds) or now > _utc(broker_truth.broker_truth_valid_until):
        blockers.append(PaperRuntimeReadinessBlocker.BROKER_TRUTH_STALE)
    if broker_truth.position_count != 0 or broker_truth.open_order_count != 0 or broker_truth.clean_for_candidate_start is not True:
        blockers.append(PaperRuntimeReadinessBlocker.BROKER_ACCOUNT_NOT_FLAT)
    if ages["asset"] > Decimal(policy.asset_age_seconds) or now > _utc(asset_truth.asset_truth_valid_until):
        blockers.append(PaperRuntimeReadinessBlocker.ASSET_TRUTH_STALE)
    if ages["market"] > Decimal(policy.market_age_seconds) or now > _utc(market_truth.market_truth_valid_until):
        blockers.append(PaperRuntimeReadinessBlocker.MARKET_TRUTH_STALE)
    if ages["safety"] > Decimal(policy.safety_health_age_seconds) or now > _utc(safety_health_truth.safety_health_valid_until):
        blockers.append(PaperRuntimeReadinessBlocker.SAFETY_HEALTH_TRUTH_STALE)
    if candidate_identity.side != "BUY":
        blockers.append(PaperRuntimeReadinessBlocker.SIDE_NOT_SUPPORTED_FOR_FLAT_CANARY)
    if minimum_notional > source_snapshot.probation_notional_cap_usd:
        blockers.append(PaperRuntimeReadinessBlocker.MINIMUM_NOTIONAL_EXCEEDS_PROBATION_CAP)

    blocker_codes = tuple(blockers)
    ready = not blocker_codes
    valid_until = now
    if ready:
        valid_until = min(
            _utc(source_snapshot.admission_valid_until),
            _utc(source_snapshot.reproved_at) + timedelta(seconds=policy.source_age_seconds),
            _utc(broker_truth.broker_truth_valid_until),
            _utc(broker_truth.observed_at) + timedelta(seconds=policy.broker_age_seconds),
            _utc(asset_truth.asset_truth_valid_until),
            _utc(asset_truth.observed_at) + timedelta(seconds=policy.asset_age_seconds),
            _utc(market_truth.market_truth_valid_until),
            _utc(market_truth.observed_at) + timedelta(seconds=policy.market_age_seconds),
            _utc(safety_health_truth.safety_health_valid_until),
            _utc(safety_health_truth.observed_at) + timedelta(seconds=policy.safety_health_age_seconds),
            now + timedelta(seconds=policy.ready_ttl_seconds),
        )

    values = {
        "receipt_id": receipt_id,
        "contract_version": PAPER_RUNTIME_FINAL_READINESS_VERSION,
        "policy_hash": policy.fingerprint,
        "source_snapshot_hash": source_snapshot.proof_hash,
        "candidate_identity_hash": candidate_identity.proof_hash,
        "broker_truth_hash": broker_truth.proof_hash,
        "asset_truth_hash": asset_truth.proof_hash,
        "market_truth_hash": market_truth.proof_hash,
        "safety_health_truth_hash": safety_health_truth.proof_hash,
        "authority_key": candidate_identity.authority_key,
        "admission_hash": candidate_identity.admission_hash,
        "strategy_id": candidate_identity.selected_strategy_id,
        "product_id": candidate_identity.product_id,
        "symbol": candidate_identity.symbol,
        "account_id": broker_truth.account_id,
        "probation_notional_cap_usd": source_snapshot.probation_notional_cap_usd,
        "probation_order_cap": source_snapshot.probation_order_cap,
        "minimum_executable_quantity": minimum_quantity,
        "conservative_unit_price": conservative_price,
        "minimum_executable_notional_usd": minimum_notional,
        "probation_headroom_usd": source_snapshot.probation_notional_cap_usd - minimum_notional,
        "status": PaperRuntimeReadinessStatus.READY if ready else PaperRuntimeReadinessStatus.BLOCKED,
        "blocker_codes": blocker_codes,
        "observed_at": now,
        "valid_until": valid_until,
        "upstream_integrity_verified": True,
        "freshness_verified": not any(code in _FRESHNESS for code in blocker_codes),
        "minimum_notional_compatible": minimum_notional <= source_snapshot.probation_notional_cap_usd,
        "separate_execution_approval_required": True,
        "order_intent_created": False,
        "oms_handoff_performed": False,
        "capital_reserved": False,
        "broker_write_performed": False,
        "paper_runtime_ready": ready,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return PaperRuntimeFinalReadinessReceipt(**values, receipt_hash=_hash(_payload_values(values)))

def _validate_integrity(source, candidate, broker, asset, market, safety) -> None:
    hashes = (
        (source.proof_hash, source_module._hash(source_module._proof_payload(source, include_hash=False)), "source"),
        (candidate.proof_hash, candidate_module._hash(candidate_module._payload(candidate, include_hash=False)), "candidate"),
        (broker.proof_hash, broker_module._hash(broker_module._proof_payload(broker, include_hash=False)), "broker"),
        (asset.proof_hash, asset_module._hash(asset_module._proof_payload(asset, include_hash=False)), "asset"),
        (market.proof_hash, market_module._hash(market_module._proof_payload(market, include_hash=False)), "market"),
        (safety.proof_hash, safety_module._hash(safety_module._proof_payload(safety, include_hash=False)), "Safety/Health"),
    )
    for actual, expected, name in hashes:
        if actual != expected:
            raise PaperRuntimeFinalReadinessIntegrityError(f"{name} proof hash mismatch")
    if not (
        source.durable_admission_verified and source.durable_lifecycle_verified and source.sqlite_read_only
        and source.sqlite_snapshot_consistent and not source.concurrent_durable_change_detected
        and candidate.product_identity_verified and candidate.strategy_runtime_identity_verified
        and broker.account_environment_verified and broker.crypto_entitlement_verified
        and broker.portfolio_truth_verified and broker.read_only_broker_truth and not broker.network_write_performed
        and asset.asset_metadata_verified and asset.read_only_asset_truth and not asset.network_write_performed
        and market.market_truth_verified and market.both_sides_fresh and market.read_only_market_truth
        and not market.network_write_performed and safety.ledger_integrity_verified
        and safety.safety_projection_verified and safety.strategy_health_verified and safety.portfolio_health_verified
        and safety.read_only_core_truth and safety.sqlite_snapshot_consistent
        and not safety.concurrent_durable_change_detected and not safety.kill_switch_active and not safety.circuit_active
    ):
        raise PaperRuntimeFinalReadinessIntegrityError("upstream verification boundary is incomplete")
    for value in (source, candidate, broker, asset, market, safety):
        if (
            getattr(value, "paper_runtime_ready", False) or value.paper_execution_authorized
            or value.external_execution_authorized or value.runtime_execution_authorized
            or value.capital_authority != "NONE" or value.live_trading != "BLOCKED"
        ):
            raise PaperRuntimeFinalReadinessIntegrityError("upstream proof contains authority escalation")

def _validate_chain(source, candidate, broker, asset, market, safety) -> None:
    if candidate.w85_source_snapshot_hash != source.proof_hash:
        raise PaperRuntimeFinalReadinessIntegrityError("candidate/source snapshot mismatch")
    pairs = (
        (broker.candidate_identity_hash, candidate.proof_hash),
        (asset.candidate_identity_hash, candidate.proof_hash),
        (market.candidate_identity_hash, candidate.proof_hash),
        (safety.candidate_identity_hash, candidate.proof_hash),
        (asset.broker_truth_hash, broker.proof_hash),
        (market.broker_truth_hash, broker.proof_hash),
        (market.asset_truth_hash, asset.proof_hash),
    )
    if any(left != right for left, right in pairs):
        raise PaperRuntimeFinalReadinessIntegrityError("W86 proof-chain hash mismatch")
    for value in (source, broker, asset, market, safety):
        if value.authority_key != candidate.authority_key or value.admission_hash != candidate.admission_hash:
            raise PaperRuntimeFinalReadinessIntegrityError("authority/admission mismatch")
    if source.admission_id != candidate.admission_id or safety.selected_strategy_id != candidate.selected_strategy_id:
        raise PaperRuntimeFinalReadinessIntegrityError("admission/strategy mismatch")
    if not (
        broker.product_id == asset.product_id == market.product_id == candidate.product_id
        and broker.venue == asset.venue == market.venue == candidate.venue
        and broker.asset_class == asset.asset_class == candidate.asset_class
        and broker.symbol == asset.candidate_symbol == market.candidate_symbol == candidate.symbol
        and broker.quote_currency == asset.quote_currency == market.quote_currency == candidate.quote_currency
        and asset.base_currency == market.base_currency == candidate.base_currency
    ):
        raise PaperRuntimeFinalReadinessIntegrityError("product/symbol/currency mismatch")
    if asset.account_id != broker.account_id or market.account_id != broker.account_id:
        raise PaperRuntimeFinalReadinessIntegrityError("cross-account truth mismatch")
    if asset.credential_reference != broker.credential_reference or market.credential_reference != broker.credential_reference:
        raise PaperRuntimeFinalReadinessIntegrityError("cross-credential truth mismatch")
    if market.asset_attestation_fingerprint != asset.asset_attestation_fingerprint:
        raise PaperRuntimeFinalReadinessIntegrityError("market/asset attestation mismatch")

def _ceil(value: Decimal, increment: Decimal) -> Decimal:
    _positive(value, "value")
    _positive(increment, "increment")
    return (value / increment).to_integral_value(rounding=ROUND_CEILING) * increment

def _payload(receipt: PaperRuntimeFinalReadinessReceipt, *, include_hash: bool) -> dict[str, object]:
    values = {name: getattr(receipt, name) for name in PaperRuntimeFinalReadinessReceipt.__dataclass_fields__ if name != "receipt_hash"}
    payload = _payload_values(values)
    if include_hash:
        payload["receipt_hash"] = receipt.receipt_hash
    return payload

def _payload_values(values: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            result[key] = _utc(value).isoformat()
        elif isinstance(value, Decimal):
            result[key] = str(value)
        elif isinstance(value, (PaperRuntimeReadinessStatus, PaperRuntimeReadinessBlocker)):
            result[key] = value.value
        elif isinstance(value, tuple):
            result[key] = [item.value for item in value]
        else:
            result[key] = value
    return result

def _id(value: str, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperRuntimeFinalReadinessIntegrityError(f"{name} must be canonical identifier")

def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperRuntimeFinalReadinessIntegrityError(f"{name} must be lowercase sha256")

def _positive(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise PaperRuntimeFinalReadinessIntegrityError(f"{name} must be finite positive Decimal")

def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperRuntimeFinalReadinessIntegrityError(f"{name} must be timezone-aware")

def _utc(value: datetime) -> datetime:
    _aware(value, "datetime")
    return value.astimezone(timezone.utc)

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

__all__ = [
    "PAPER_RUNTIME_FINAL_READINESS_VERSION",
    "W85_PROBATION_NOTIONAL_MAX_USD",
    "W85_PROBATION_ORDER_MAX",
    "PaperRuntimeFinalReadinessIntegrityError",
    "PaperRuntimeFinalReadinessPolicy",
    "PaperRuntimeFinalReadinessReceipt",
    "PaperRuntimeReadinessBlocker",
    "PaperRuntimeReadinessStatus",
    "finalize_paper_runtime_readiness",
]
