from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json

from autotrade.connectivity_canary_authority import (
    CONNECTIVITY_CANARY_STRATEGY_ID,
    ConnectivityCanaryAuthority,
)
from autotrade.domain import OrderStatus, intent_fingerprint

from .alpaca_paper_canary import PaperCanaryApproval, PaperCanaryContext, PaperCanaryGateRejected
from .alpaca_paper_gateway import ALPACA_PAPER_ACCOUNT_PATH, ALPACA_PAPER_TRADING_HOST
from .alpaca_paper_submission import PaperSubmissionStatus

CERTIFIED_TRACKS = frozenset({"R0", "R1", "R2", "R3", "R4", "R5"})
MAX_CONNECTIVITY_NOTIONAL = Decimal("10")
MAX_ACCOUNT_FRACTION = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class ConnectivityCanaryGate:
    """Purpose-specific offline gate; connectivity authority is never Strategy Health."""

    authority: ConnectivityCanaryAuthority

    def __post_init__(self) -> None:
        if not isinstance(self.authority, ConnectivityCanaryAuthority):
            raise TypeError("ConnectivityCanaryAuthority is required")

    def approve(self, context: PaperCanaryContext) -> PaperCanaryApproval:
        if not isinstance(context, PaperCanaryContext):
            raise TypeError("PaperCanaryContext is required")
        order = context.order
        binding = context.binding
        submission = context.submission_state
        account = context.account_attestation
        authority = self.authority
        if context.health_allows_new_exposure is not False:
            raise PaperCanaryGateRejected("CONNECTIVITY_CANARY must not be represented as Strategy Health approval")
        if not authority.is_valid_at(context.now):
            raise PaperCanaryGateRejected("CONNECTIVITY_CANARY authority is expired or not yet valid")
        if order.status is not OrderStatus.VALIDATED:
            raise PaperCanaryGateRejected("connectivity canary requires OMS VALIDATED order")
        if order.intent.strategy_id != CONNECTIVITY_CANARY_STRATEGY_ID:
            raise PaperCanaryGateRejected("connectivity canary strategy_id is not exact reserved id")
        if order.order_id != authority.order_id:
            raise PaperCanaryGateRejected("connectivity authority/order mismatch")
        if intent_fingerprint(order.intent) != authority.intent_fingerprint:
            raise PaperCanaryGateRejected("connectivity authority/intent fingerprint mismatch")
        if order.risk_decision_id != authority.risk_decision_id:
            raise PaperCanaryGateRejected("connectivity authority/RiskDecision mismatch")
        if order.intent.quantity != authority.max_quantity or order.intent.quantity != Decimal("1"):
            raise PaperCanaryGateRejected("first connectivity canary must be exactly one whole share")
        if order.intent.side.value != "BUY" or order.intent.order_type.value != "LIMIT":
            raise PaperCanaryGateRejected("first connectivity canary is BUY LIMIT only")
        if binding.order_id != order.order_id or binding.intent_fingerprint != authority.intent_fingerprint:
            raise PaperCanaryGateRejected("connectivity submission binding/order mismatch")
        if binding.risk_decision_id != authority.risk_decision_id:
            raise PaperCanaryGateRejected("connectivity submission binding/RiskDecision mismatch")
        if submission.status is not PaperSubmissionStatus.PREPARED or submission.attempt_count != 0:
            raise PaperCanaryGateRejected("connectivity submission must be fresh PREPARED with zero attempts")
        if context.certified_tracks != CERTIFIED_TRACKS:
            raise PaperCanaryGateRejected("connectivity canary requires exact certified R0-R5 tracks")
        if not context.reconciliation_clean or context.unresolved_unknown_orders != 0:
            raise PaperCanaryGateRejected("connectivity canary requires clean reconciliation and no UNKNOWN orders")
        if context.kill_switch_engaged:
            raise PaperCanaryGateRejected("connectivity canary is blocked by kill switch")
        if context.prior_canary_submissions != 0:
            raise PaperCanaryGateRejected("first connectivity canary permits no prior canary submission")
        if account.source_host != ALPACA_PAPER_TRADING_HOST or account.source_path != ALPACA_PAPER_ACCOUNT_PATH:
            raise PaperCanaryGateRejected("connectivity canary requires exact PAPER account endpoint")
        if account.status != "ACTIVE" or account.currency != "USD":
            raise PaperCanaryGateRejected("connectivity canary requires ACTIVE USD PAPER account")
        if account.fingerprint != authority.account_attestation_fingerprint:
            raise PaperCanaryGateRejected("connectivity authority/account mismatch")
        notional = order.notional
        effective_cap = min(
            MAX_CONNECTIVITY_NOTIONAL,
            authority.max_notional,
            account.portfolio_value * MAX_ACCOUNT_FRACTION,
            account.buying_power,
        )
        if notional <= 0 or notional > effective_cap:
            raise PaperCanaryGateRejected(f"connectivity canary notional {notional} exceeds strict cap {effective_cap}")
        expires_at = authority.expires_at
        approval_hash = _approval_hash(
            order_id=order.order_id,
            client_order_id=binding.client_order_id,
            binding_hash=binding.binding_hash,
            account_attestation_fingerprint=account.fingerprint,
            risk_decision_id=order.risk_decision_id,
            notional=notional,
            effective_notional_cap=effective_cap,
            issued_at=context.now,
            expires_at=expires_at,
        )
        return PaperCanaryApproval(
            order_id=order.order_id,
            client_order_id=binding.client_order_id,
            binding_hash=binding.binding_hash,
            account_attestation_fingerprint=account.fingerprint,
            risk_decision_id=order.risk_decision_id,
            notional=notional,
            effective_notional_cap=effective_cap,
            issued_at=context.now,
            expires_at=expires_at,
            approval_hash=approval_hash,
        )


def _approval_hash(*, order_id: str, client_order_id: str, binding_hash: str, account_attestation_fingerprint: str, risk_decision_id: str, notional: Decimal, effective_notional_cap: Decimal, issued_at: datetime, expires_at: datetime) -> str:
    payload = {
        "order_id": order_id,
        "client_order_id": client_order_id,
        "binding_hash": binding_hash,
        "account_attestation_fingerprint": account_attestation_fingerprint,
        "risk_decision_id": risk_decision_id,
        "notional": str(notional),
        "effective_notional_cap": str(effective_notional_cap),
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode("utf-8")).hexdigest()


__all__ = ["ConnectivityCanaryGate", "CERTIFIED_TRACKS"]
