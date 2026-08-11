from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from typing import Iterable

from autotrade.domain import OrderRecord, OrderStatus, OrderType, Side

from .alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
)
from .alpaca_paper_submission import (
    PaperSubmissionBinding,
    PaperSubmissionState,
    PaperSubmissionStatus,
    deterministic_client_order_id,
)


_REQUIRED_CERTIFIED_TRACKS = ("R0", "R1", "R2", "R3", "R4", "R5")


class PaperCanaryError(RuntimeError):
    pass


class PaperCanaryRejected(PaperCanaryError):
    def __init__(self, reasons: Iterable[str]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("; ".join(self.reasons))


@dataclass(frozen=True, slots=True)
class PaperCanaryPolicy:
    enabled: bool = False
    max_notional: Decimal = Decimal("10")
    max_account_fraction: Decimal = Decimal("0.001")
    max_attestation_age_seconds: int = 30
    approval_ttl_seconds: int = 5
    max_prior_canary_submissions: int = 0

    def __post_init__(self) -> None:
        if not _finite_positive(self.max_notional):
            raise ValueError("max_notional must be finite and > 0")
        if (
            not isinstance(self.max_account_fraction, Decimal)
            or not self.max_account_fraction.is_finite()
            or not Decimal("0") < self.max_account_fraction <= Decimal("0.01")
        ):
            raise ValueError("max_account_fraction must be > 0 and <= 0.01")
        if not 1 <= self.max_attestation_age_seconds <= 120:
            raise ValueError("max_attestation_age_seconds must be between 1 and 120")
        if not 1 <= self.approval_ttl_seconds <= 15:
            raise ValueError("approval_ttl_seconds must be between 1 and 15")
        if self.max_prior_canary_submissions != 0:
            raise ValueError("R6 canary is intentionally limited to the first submission")


@dataclass(frozen=True, slots=True)
class PaperCanaryContext:
    order: OrderRecord
    binding: PaperSubmissionBinding
    submission_state: PaperSubmissionState
    account_attestation: AlpacaPaperAccountAttestation
    now: datetime
    certified_tracks: tuple[str, ...]
    reconciliation_clean: bool
    unresolved_unknown_orders: int
    kill_switch_engaged: bool
    health_allows_new_exposure: bool
    prior_canary_submissions: int

    def __post_init__(self) -> None:
        _require_aware(self.now, "now")
        if self.unresolved_unknown_orders < 0:
            raise ValueError("unresolved_unknown_orders cannot be negative")
        if self.prior_canary_submissions < 0:
            raise ValueError("prior_canary_submissions cannot be negative")


@dataclass(frozen=True, slots=True)
class PaperCanaryApproval:
    order_id: str
    client_order_id: str
    binding_hash: str
    account_attestation_fingerprint: str
    risk_decision_id: str
    notional: Decimal
    effective_notional_cap: Decimal
    issued_at: datetime
    expires_at: datetime
    approval_hash: str

    def is_valid_at(self, now: datetime) -> bool:
        _require_aware(now, "now")
        return self.issued_at <= now.astimezone(timezone.utc) < self.expires_at


class PaperCanaryGate:
    """Strict additional preflight for the first bounded external PAPER canary.

    This gate cannot place orders. It only returns a short-lived approval bound
    to an already VALIDATED OMS order, immutable submission binding, fresh PAPER
    account attestation and conservative global safety predicates.
    """

    def __init__(self, policy: PaperCanaryPolicy | None = None) -> None:
        self.policy = policy or PaperCanaryPolicy()

    def approve(self, context: PaperCanaryContext) -> PaperCanaryApproval:
        reasons: list[str] = []
        policy = self.policy
        order = context.order
        intent = order.intent
        now = context.now.astimezone(timezone.utc)
        attestation = context.account_attestation
        binding = context.binding
        submission = context.submission_state

        if not policy.enabled:
            reasons.append("PAPER canary is disabled by default")
        if context.certified_tracks != _REQUIRED_CERTIFIED_TRACKS:
            reasons.append("R0-R5 certified-track prerequisite is not exact")
        if order.status is not OrderStatus.VALIDATED:
            reasons.append("OMS order must be VALIDATED immediately before canary")
        if not order.risk_decision_id:
            reasons.append("validated order is missing deterministic risk_decision_id")
        if order.broker_order_id is not None:
            reasons.append("canary order is already bound to a broker order")
        if intent.side is not Side.BUY:
            reasons.append("R6 first canary is long BUY-only")
        if intent.order_type is not OrderType.LIMIT or intent.limit_price is None:
            reasons.append("R6 first canary requires an explicit LIMIT price")
        if intent.stop_price is not None:
            reasons.append("R6 first canary parent intent cannot carry a stop field")
        if not _finite_positive(intent.quantity):
            reasons.append("canary quantity must be finite and positive")

        if attestation.status != "ACTIVE" or attestation.currency != "USD":
            reasons.append("PAPER account attestation is not ACTIVE USD")
        if attestation.source_host != ALPACA_PAPER_TRADING_HOST:
            reasons.append("PAPER account attestation host is not exact")
        if attestation.source_path != "/v2/account":
            reasons.append("PAPER account attestation path is not exact")
        attested_at = attestation.attested_at.astimezone(timezone.utc)
        age = (now - attested_at).total_seconds()
        if age < 0:
            reasons.append("PAPER account attestation is from the future")
        elif age > policy.max_attestation_age_seconds:
            reasons.append("PAPER account attestation is stale")

        expected_client_order_id = deterministic_client_order_id(order)
        if binding.order_id != order.order_id:
            reasons.append("submission binding order_id mismatch")
        if binding.intent_id != intent.intent_id:
            reasons.append("submission binding intent_id mismatch")
        if binding.risk_decision_id != order.risk_decision_id:
            reasons.append("submission binding risk_decision_id mismatch")
        if binding.client_order_id != expected_client_order_id:
            reasons.append("submission binding client_order_id is not deterministic")
        if binding.account_attestation_fingerprint != attestation.fingerprint:
            reasons.append("submission binding is not bound to current PAPER attestation")
        if submission.order_id != binding.order_id:
            reasons.append("submission state order_id mismatch")
        if submission.client_order_id != binding.client_order_id:
            reasons.append("submission state client_order_id mismatch")
        if submission.binding_hash != binding.fingerprint:
            reasons.append("submission state is not bound to immutable submission binding")
        if submission.status is not PaperSubmissionStatus.PREPARED:
            reasons.append("submission state must be PREPARED before canary approval")
        if submission.attempt_count != 0:
            reasons.append("canary approval cannot follow an external submit attempt")

        if not context.reconciliation_clean:
            reasons.append("portfolio/broker reconciliation is not clean")
        if context.unresolved_unknown_orders != 0:
            reasons.append("unresolved UNKNOWN orders block new PAPER exposure")
        if context.kill_switch_engaged:
            reasons.append("kill switch blocks PAPER canary")
        if not context.health_allows_new_exposure:
            reasons.append("authoritative Health state blocks new exposure")
        if context.prior_canary_submissions > policy.max_prior_canary_submissions:
            reasons.append("R6 bounded canary submission budget is exhausted")

        notional: Decimal | None = None
        effective_cap: Decimal | None = None
        if intent.limit_price is not None and _finite_positive(intent.limit_price):
            notional = intent.quantity * intent.limit_price
            account_fraction_cap = attestation.portfolio_value * policy.max_account_fraction
            effective_cap = min(
                policy.max_notional,
                account_fraction_cap,
                attestation.buying_power,
            )
            if not _finite_positive(effective_cap):
                reasons.append("effective PAPER canary cap is not positive")
            elif notional > effective_cap:
                reasons.append("PAPER canary notional exceeds strict effective cap")
        elif intent.limit_price is not None:
            reasons.append("canary LIMIT price must be finite and positive")

        if reasons:
            raise PaperCanaryRejected(reasons)
        assert notional is not None
        assert effective_cap is not None

        issued_at = now
        expires_at = issued_at + timedelta(seconds=policy.approval_ttl_seconds)
        payload = {
            "account_attestation_fingerprint": attestation.fingerprint,
            "binding_hash": binding.fingerprint,
            "client_order_id": binding.client_order_id,
            "effective_notional_cap": str(effective_cap),
            "expires_at": expires_at.isoformat(),
            "issued_at": issued_at.isoformat(),
            "notional": str(notional),
            "order_id": order.order_id,
            "risk_decision_id": order.risk_decision_id,
        }
        approval_hash = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
        ).hexdigest()
        return PaperCanaryApproval(
            order_id=order.order_id,
            client_order_id=binding.client_order_id,
            binding_hash=binding.fingerprint,
            account_attestation_fingerprint=attestation.fingerprint,
            risk_decision_id=order.risk_decision_id,
            notional=notional,
            effective_notional_cap=effective_cap,
            issued_at=issued_at,
            expires_at=expires_at,
            approval_hash=approval_hash,
        )


def _finite_positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
