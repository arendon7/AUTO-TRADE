from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

import autotrade.paper_runtime_broker_truth as broker_module
import autotrade.paper_runtime_final_readiness as final_module
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
)
from autotrade.paper_runtime_broker_truth import PaperRuntimeBrokerTruthProof
from autotrade.paper_runtime_final_readiness import (
    PaperRuntimeFinalReadinessReceipt,
    PaperRuntimeReadinessStatus,
)


PAPER_RUNTIME_FUNDING_CAPACITY_VERSION = "W86_PAPER_RUNTIME_FUNDING_CAPACITY_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_ACCOUNT_ID_RE = re.compile(r"^[0-9a-fA-F-]{16,64}$")


class PaperRuntimeFundingCapacityIntegrityError(RuntimeError):
    pass


class PaperRuntimeFundingCapacityStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class PaperRuntimeFundingCapacityBlocker(StrEnum):
    FINAL_RUNTIME_NOT_READY = "FINAL_RUNTIME_NOT_READY"
    FINAL_RUNTIME_RECEIPT_EXPIRED = "FINAL_RUNTIME_RECEIPT_EXPIRED"
    ACCOUNT_ATTESTATION_STALE = "ACCOUNT_ATTESTATION_STALE"
    INSUFFICIENT_BUYING_POWER = "INSUFFICIENT_BUYING_POWER"


@dataclass(frozen=True, slots=True)
class PaperRuntimeFundingCapacityPolicy:
    max_account_age_seconds: int = 5
    ready_ttl_seconds: int = 2

    def __post_init__(self) -> None:
        for name, value, upper in (
            ("max_account_age_seconds", self.max_account_age_seconds, 5),
            ("ready_ttl_seconds", self.ready_ttl_seconds, 2),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
                raise ValueError(f"{name} must be integer seconds in [1, {upper}]")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "max_account_age_seconds": self.max_account_age_seconds,
                "ready_ttl_seconds": self.ready_ttl_seconds,
            }
        )


@dataclass(frozen=True, slots=True)
class PaperRuntimeFundingCapacityProof:
    proof_id: str
    contract_version: str
    policy_hash: str
    final_readiness_hash: str
    broker_truth_hash: str
    candidate_identity_hash: str
    authority_key: str
    admission_hash: str
    product_id: str
    symbol: str
    account_id: str
    account_reference: str
    credential_reference: str
    account_attestation_fingerprint: str
    account_attested_at: datetime
    buying_power_usd: Decimal
    portfolio_value_usd: Decimal
    minimum_executable_notional_usd: Decimal
    buying_power_headroom_usd: Decimal
    status: PaperRuntimeFundingCapacityStatus
    blocker_codes: tuple[PaperRuntimeFundingCapacityBlocker, ...]
    observed_at: datetime
    valid_until: datetime
    upstream_integrity_verified: bool
    account_binding_verified: bool
    account_fresh: bool
    buying_power_sufficient: bool
    separate_execution_approval_required: bool
    capital_reserved: bool
    broker_write_performed: bool
    paper_runtime_ready: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    proof_hash: str

    def __post_init__(self) -> None:
        _id(self.proof_id, "proof_id")
        if self.contract_version != PAPER_RUNTIME_FUNDING_CAPACITY_VERSION:
            raise PaperRuntimeFundingCapacityIntegrityError(
                "non-canonical W86 funding-capacity version"
            )
        for name in (
            "policy_hash",
            "final_readiness_hash",
            "broker_truth_hash",
            "candidate_identity_hash",
            "authority_key",
            "admission_hash",
            "account_reference",
            "credential_reference",
            "account_attestation_fingerprint",
            "proof_hash",
        ):
            _sha(getattr(self, name), name)
        for name in ("product_id",):
            _id(getattr(self, name), name)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise PaperRuntimeFundingCapacityIntegrityError("symbol is required")
        if not _ACCOUNT_ID_RE.fullmatch(self.account_id):
            raise PaperRuntimeFundingCapacityIntegrityError("account_id is invalid")
        _aware(self.account_attested_at, "account_attested_at")
        _aware(self.observed_at, "observed_at")
        _aware(self.valid_until, "valid_until")
        _nonnegative_decimal(self.buying_power_usd, "buying_power_usd")
        _nonnegative_decimal(self.portfolio_value_usd, "portfolio_value_usd")
        _positive_decimal(
            self.minimum_executable_notional_usd,
            "minimum_executable_notional_usd",
        )
        if (
            not isinstance(self.buying_power_headroom_usd, Decimal)
            or not self.buying_power_headroom_usd.is_finite()
        ):
            raise PaperRuntimeFundingCapacityIntegrityError(
                "buying_power_headroom_usd must be finite Decimal"
            )
        expected_headroom = self.buying_power_usd - self.minimum_executable_notional_usd
        if self.buying_power_headroom_usd != expected_headroom:
            raise PaperRuntimeFundingCapacityIntegrityError(
                "buying-power headroom is inconsistent"
            )
        expected_sufficient = self.buying_power_usd >= self.minimum_executable_notional_usd
        if self.buying_power_sufficient is not expected_sufficient:
            raise PaperRuntimeFundingCapacityIntegrityError(
                "buying-power sufficiency flag is inconsistent"
            )
        ready = not self.blocker_codes
        expected_status = (
            PaperRuntimeFundingCapacityStatus.READY
            if ready
            else PaperRuntimeFundingCapacityStatus.BLOCKED
        )
        if self.status is not expected_status or self.paper_runtime_ready is not ready:
            raise PaperRuntimeFundingCapacityIntegrityError(
                "funding status/readiness disagrees with blocker set"
            )
        if ready and self.valid_until < self.observed_at:
            raise PaperRuntimeFundingCapacityIntegrityError(
                "READY funding proof is already stale"
            )
        if not ready and self.valid_until != self.observed_at:
            raise PaperRuntimeFundingCapacityIntegrityError(
                "BLOCKED funding proof must expire immediately"
            )
        if self.upstream_integrity_verified is not True or self.account_binding_verified is not True:
            raise PaperRuntimeFundingCapacityIntegrityError(
                "funding proof lacks verified upstream/account binding"
            )
        if (
            self.separate_execution_approval_required is not True
            or self.capital_reserved is not False
            or self.broker_write_performed is not False
            or self.paper_execution_authorized is not False
            or self.external_execution_authorized is not False
            or self.runtime_execution_authorized is not False
            or self.capital_authority != "NONE"
            or self.live_trading != "BLOCKED"
        ):
            raise PaperRuntimeFundingCapacityIntegrityError(
                "funding readiness may not grant execution, capital reservation, broker write, or LIVE authority"
            )
        if self.proof_hash != _hash(_payload(self, include_hash=False)):
            raise PaperRuntimeFundingCapacityIntegrityError(
                "funding-capacity proof hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def bind_paper_runtime_funding_capacity(
    *,
    proof_id: str,
    final_readiness: PaperRuntimeFinalReadinessReceipt,
    broker_truth: PaperRuntimeBrokerTruthProof,
    account_attestation: AlpacaPaperAccountAttestation,
    policy: PaperRuntimeFundingCapacityPolicy | None = None,
) -> PaperRuntimeFundingCapacityProof:
    """Bind available PAPER buying power to finite runtime readiness without reserving money."""

    _id(proof_id, "proof_id")
    if not isinstance(final_readiness, PaperRuntimeFinalReadinessReceipt):
        raise TypeError("final_readiness must be PaperRuntimeFinalReadinessReceipt")
    if not isinstance(broker_truth, PaperRuntimeBrokerTruthProof):
        raise TypeError("broker_truth must be PaperRuntimeBrokerTruthProof")
    if not isinstance(account_attestation, AlpacaPaperAccountAttestation):
        raise TypeError("account_attestation must be AlpacaPaperAccountAttestation")
    effective_policy = policy or PaperRuntimeFundingCapacityPolicy()
    if not isinstance(effective_policy, PaperRuntimeFundingCapacityPolicy):
        raise TypeError("policy must be PaperRuntimeFundingCapacityPolicy")

    _validate_upstream(final_readiness, broker_truth)
    _validate_account_binding(account_attestation, broker_truth)

    now = _utc(_now_utc())
    account_at = _utc(account_attestation.attested_at)
    if account_at > now:
        raise PaperRuntimeFundingCapacityIntegrityError(
            "PAPER account funding attestation is in process future"
        )
    if _utc(final_readiness.observed_at) > now:
        raise PaperRuntimeFundingCapacityIntegrityError(
            "final runtime readiness receipt is in process future"
        )

    account_age = now - account_at
    account_fresh = account_age <= timedelta(
        seconds=effective_policy.max_account_age_seconds
    )
    minimum_notional = final_readiness.minimum_executable_notional_usd
    buying_power = account_attestation.buying_power
    portfolio_value = account_attestation.portfolio_value
    buying_power_sufficient = buying_power >= minimum_notional

    blockers: list[PaperRuntimeFundingCapacityBlocker] = []
    if (
        final_readiness.status is not PaperRuntimeReadinessStatus.READY
        or final_readiness.paper_runtime_ready is not True
    ):
        blockers.append(PaperRuntimeFundingCapacityBlocker.FINAL_RUNTIME_NOT_READY)
    elif now > _utc(final_readiness.valid_until):
        blockers.append(PaperRuntimeFundingCapacityBlocker.FINAL_RUNTIME_RECEIPT_EXPIRED)
    if not account_fresh:
        blockers.append(PaperRuntimeFundingCapacityBlocker.ACCOUNT_ATTESTATION_STALE)
    if not buying_power_sufficient:
        blockers.append(PaperRuntimeFundingCapacityBlocker.INSUFFICIENT_BUYING_POWER)

    blocker_codes = tuple(blockers)
    ready = not blocker_codes
    valid_until = now
    if ready:
        valid_until = min(
            _utc(final_readiness.valid_until),
            account_at + timedelta(seconds=effective_policy.max_account_age_seconds),
            now + timedelta(seconds=effective_policy.ready_ttl_seconds),
        )

    values = {
        "proof_id": proof_id,
        "contract_version": PAPER_RUNTIME_FUNDING_CAPACITY_VERSION,
        "policy_hash": effective_policy.fingerprint,
        "final_readiness_hash": final_readiness.receipt_hash,
        "broker_truth_hash": broker_truth.proof_hash,
        "candidate_identity_hash": final_readiness.candidate_identity_hash,
        "authority_key": final_readiness.authority_key,
        "admission_hash": final_readiness.admission_hash,
        "product_id": final_readiness.product_id,
        "symbol": final_readiness.symbol,
        "account_id": account_attestation.account_id,
        "account_reference": account_attestation.account_reference,
        "credential_reference": account_attestation.credential_reference,
        "account_attestation_fingerprint": account_attestation.fingerprint,
        "account_attested_at": account_at,
        "buying_power_usd": buying_power,
        "portfolio_value_usd": portfolio_value,
        "minimum_executable_notional_usd": minimum_notional,
        "buying_power_headroom_usd": buying_power - minimum_notional,
        "status": (
            PaperRuntimeFundingCapacityStatus.READY
            if ready
            else PaperRuntimeFundingCapacityStatus.BLOCKED
        ),
        "blocker_codes": blocker_codes,
        "observed_at": now,
        "valid_until": valid_until,
        "upstream_integrity_verified": True,
        "account_binding_verified": True,
        "account_fresh": account_fresh,
        "buying_power_sufficient": buying_power_sufficient,
        "separate_execution_approval_required": True,
        "capital_reserved": False,
        "broker_write_performed": False,
        "paper_runtime_ready": ready,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return PaperRuntimeFundingCapacityProof(
        **values,
        proof_hash=_hash(_payload_values(values)),
    )


def _validate_upstream(
    final_readiness: PaperRuntimeFinalReadinessReceipt,
    broker_truth: PaperRuntimeBrokerTruthProof,
) -> None:
    expected_final = final_module._hash(
        final_module._payload(final_readiness, include_hash=False)
    )
    if final_readiness.receipt_hash != expected_final:
        raise PaperRuntimeFundingCapacityIntegrityError(
            "final runtime readiness receipt hash mismatch"
        )
    expected_broker = broker_module._hash(
        broker_module._proof_payload(broker_truth, include_hash=False)
    )
    if broker_truth.proof_hash != expected_broker:
        raise PaperRuntimeFundingCapacityIntegrityError(
            "broker truth proof hash mismatch"
        )
    if final_readiness.broker_truth_hash != broker_truth.proof_hash:
        raise PaperRuntimeFundingCapacityIntegrityError(
            "funding gate received a different broker truth than final readiness"
        )
    if (
        final_readiness.candidate_identity_hash != broker_truth.candidate_identity_hash
        or final_readiness.authority_key != broker_truth.authority_key
        or final_readiness.admission_hash != broker_truth.admission_hash
        or final_readiness.product_id != broker_truth.product_id
        or final_readiness.symbol != broker_truth.symbol
        or final_readiness.account_id != broker_truth.account_id
    ):
        raise PaperRuntimeFundingCapacityIntegrityError(
            "final readiness and broker truth identity chain mismatch"
        )
    if (
        broker_truth.read_only_broker_truth is not True
        or broker_truth.network_write_performed is not False
        or final_readiness.separate_execution_approval_required is not True
        or final_readiness.order_intent_created is not False
        or final_readiness.oms_handoff_performed is not False
        or final_readiness.capital_reserved is not False
        or final_readiness.broker_write_performed is not False
    ):
        raise PaperRuntimeFundingCapacityIntegrityError(
            "upstream read-only/no-money-movement boundary is incomplete"
        )
    for value in (final_readiness, broker_truth):
        if (
            value.paper_execution_authorized is not False
            or value.external_execution_authorized is not False
            or value.runtime_execution_authorized is not False
            or value.capital_authority != "NONE"
            or value.live_trading != "BLOCKED"
        ):
            raise PaperRuntimeFundingCapacityIntegrityError(
                "upstream proof contains execution/capital/LIVE authority escalation"
            )


def _validate_account_binding(
    account: AlpacaPaperAccountAttestation,
    broker: PaperRuntimeBrokerTruthProof,
) -> None:
    if account.fingerprint != broker.account_attestation_fingerprint:
        raise PaperRuntimeFundingCapacityIntegrityError(
            "funding attestation differs from broker-bound account attestation"
        )
    if (
        account.account_id != broker.account_id
        or account.account_reference != broker.account_reference
        or account.credential_reference != broker.credential_reference
        or _utc(account.attested_at) != _utc(broker.account_attested_at)
        or account.request_id != broker.account_request_id
    ):
        raise PaperRuntimeFundingCapacityIntegrityError(
            "PAPER account funding identity differs from broker truth"
        )
    if account.status != "ACTIVE":
        raise PaperRuntimeFundingCapacityIntegrityError(
            "PAPER account funding attestation is not ACTIVE"
        )
    if account.currency != "USD" or account.currency != broker.quote_currency:
        raise PaperRuntimeFundingCapacityIntegrityError(
            "PAPER account funding currency differs from broker/candidate quote currency"
        )
    if (
        account.source_host != ALPACA_PAPER_TRADING_HOST
        or account.source_path != ALPACA_PAPER_ACCOUNT_PATH
    ):
        raise PaperRuntimeFundingCapacityIntegrityError(
            "PAPER account funding source is not canonical"
        )
    _nonnegative_decimal(account.buying_power, "account buying_power")
    _nonnegative_decimal(account.portfolio_value, "account portfolio_value")


def _payload(
    proof: PaperRuntimeFundingCapacityProof,
    *,
    include_hash: bool,
) -> dict[str, object]:
    values = {
        name: getattr(proof, name)
        for name in PaperRuntimeFundingCapacityProof.__dataclass_fields__
        if name != "proof_hash"
    }
    payload = _payload_values(values)
    if include_hash:
        payload["proof_hash"] = proof.proof_hash
    return payload


def _payload_values(values: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            payload[key] = _utc(value).isoformat()
        elif isinstance(value, Decimal):
            payload[key] = str(value)
        elif isinstance(value, (PaperRuntimeFundingCapacityStatus, PaperRuntimeFundingCapacityBlocker)):
            payload[key] = value.value
        elif isinstance(value, tuple):
            payload[key] = [item.value for item in value]
        else:
            payload[key] = value
    return payload


def _id(value: str, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperRuntimeFundingCapacityIntegrityError(
            f"{name} must be canonical identifier"
        )


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperRuntimeFundingCapacityIntegrityError(
            f"{name} must be lowercase sha256"
        )


def _positive_decimal(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise PaperRuntimeFundingCapacityIntegrityError(
            f"{name} must be finite positive Decimal"
        )


def _nonnegative_decimal(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise PaperRuntimeFundingCapacityIntegrityError(
            f"{name} must be finite non-negative Decimal"
        )


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperRuntimeFundingCapacityIntegrityError(
            f"{name} must be timezone-aware"
        )


def _utc(value: datetime) -> datetime:
    _aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "PAPER_RUNTIME_FUNDING_CAPACITY_VERSION",
    "PaperRuntimeFundingCapacityBlocker",
    "PaperRuntimeFundingCapacityIntegrityError",
    "PaperRuntimeFundingCapacityPolicy",
    "PaperRuntimeFundingCapacityProof",
    "PaperRuntimeFundingCapacityStatus",
    "bind_paper_runtime_funding_capacity",
]
