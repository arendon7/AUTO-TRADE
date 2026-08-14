from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

from .alpaca_paper_crypto_execution_attempt import CryptoExecutionAttemptCheckpoint
from .alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleEventType,
    CryptoLifecycleIntegrityError,
    CryptoLifecycleState,
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from .alpaca_paper_crypto_order import AlpacaPaperCryptoOrderRequest, CryptoOrderRole
from .alpaca_paper_crypto_protection_execution_attempt import (
    CryptoProtectionExecutionAttemptCheckpoint,
)
from .alpaca_paper_crypto_reconciliation import CryptoBrokerUnknownReconciliation
from .alpaca_paper_flat_account import (
    ORDERS_PATH,
    ORDERS_QUERY,
    POSITIONS_PATH,
    PaperFlatAccountAttestation,
)
from .alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
)


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
RECOVERY_EVIDENCE_TTL = timedelta(seconds=15)
FLAT_EVIDENCE_TTL = timedelta(seconds=10)


class CryptoUnknownRecoveryError(RuntimeError):
    pass


class CryptoUnknownRecoveryBlocked(CryptoUnknownRecoveryError):
    pass


@dataclass(frozen=True, slots=True)
class CryptoUnknownRecoveryReceipt:
    lifecycle_id: str
    role: CryptoOrderRole
    order_absence_fingerprint: str
    reconciliation_fingerprint: str
    position_fingerprint: str
    fresh_account_fingerprint: str
    flat_account_fingerprint: str | None
    observed_position_quantity: Decimal
    resulting_status: CryptoLifecycleStatus
    attempt_count: int
    client_order_id: str
    retry_authorized: bool
    recovered_at: datetime
    receipt_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("order_absence_fingerprint", self.order_absence_fingerprint),
            ("reconciliation_fingerprint", self.reconciliation_fingerprint),
            ("position_fingerprint", self.position_fingerprint),
            ("fresh_account_fingerprint", self.fresh_account_fingerprint),
            ("receipt_hash", self.receipt_hash),
        ):
            if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                raise ValueError(f"{label} must be lowercase SHA-256")
        if self.flat_account_fingerprint is not None and not _HASH_RE.fullmatch(self.flat_account_fingerprint):
            raise ValueError("flat_account_fingerprint must be lowercase SHA-256")
        if not isinstance(self.observed_position_quantity, Decimal) or not self.observed_position_quantity.is_finite() or self.observed_position_quantity < 0:
            raise ValueError("observed_position_quantity must be finite and non-negative")
        if self.attempt_count != 1:
            raise ValueError("UNKNOWN recovery preserves exactly one write attempt")
        if self.retry_authorized is not False:
            raise ValueError("UNKNOWN recovery never authorizes retry")
        _require_aware(self.recovered_at, "recovered_at")
        if self.receipt_hash != _hash_payload(_receipt_payload(self, include_hash=False)):
            raise ValueError("UNKNOWN recovery receipt hash mismatch")


class CryptoPaperUnknownRecoveryCoordinator:
    """Offline resolution for durable UNKNOWN when exact order lookup returns 404.

    Order absence never grants retry authority. The exact position GET is always
    required. Remaining long exposure is persisted into a HALTED lifecycle. A
    flat result is accepted only with a fresh same-account all-account flatness
    attestation proving zero positions and zero open orders.
    """

    def recover(
        self,
        *,
        lifecycle: SQLiteCryptoPaperLifecycle,
        lifecycle_id: str,
        requested_order: AlpacaPaperCryptoOrderRequest,
        reconciliation: CryptoBrokerUnknownReconciliation,
        checkpoint: CryptoExecutionAttemptCheckpoint | CryptoProtectionExecutionAttemptCheckpoint,
        fresh_account: AlpacaPaperAccountAttestation,
        flat_account: PaperFlatAccountAttestation | None,
        now: datetime,
    ) -> CryptoUnknownRecoveryReceipt:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        if not isinstance(lifecycle, SQLiteCryptoPaperLifecycle):
            raise CryptoUnknownRecoveryBlocked("authoritative crypto lifecycle is required")
        if not isinstance(requested_order, AlpacaPaperCryptoOrderRequest):
            raise CryptoUnknownRecoveryBlocked("exact requested crypto order is required")
        if not isinstance(reconciliation, CryptoBrokerUnknownReconciliation):
            raise CryptoUnknownRecoveryBlocked("exact order-404 UNKNOWN reconciliation is required")
        if not isinstance(fresh_account, AlpacaPaperAccountAttestation):
            raise CryptoUnknownRecoveryBlocked("fresh PAPER account attestation is required")

        snapshot = lifecycle.snapshot(lifecycle_id)
        state = snapshot.state
        expected_account_reference, expected_credential_reference = self._validate_checkpoint(
            checkpoint=checkpoint,
            requested_order=requested_order,
            lifecycle_id=lifecycle_id,
            binding_hash=snapshot.binding.fingerprint,
            state=state,
        )
        self._validate_account(
            account=fresh_account,
            expected_account_reference=expected_account_reference,
            expected_credential_reference=expected_credential_reference,
            now=instant,
        )
        self._validate_reconciliation(
            reconciliation=reconciliation,
            requested_order=requested_order,
            expected_credential_reference=expected_credential_reference,
            now=instant,
        )

        position = reconciliation.position
        flat_fingerprint: str | None
        if position.quantity > 0:
            if flat_account is not None:
                raise CryptoUnknownRecoveryBlocked(
                    "remaining UNKNOWN exposure must halt; flat-account evidence is not a recovery override"
                )
            flat_fingerprint = None
            event_type = CryptoLifecycleEventType.HALTED
            next_status = CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED
        else:
            if flat_account is None:
                raise CryptoUnknownRecoveryBlocked(
                    "order 404 plus zero exact position is insufficient; fresh all-account flatness is required"
                )
            self._validate_flat_account(
                flat_account=flat_account,
                fresh_account=fresh_account,
                now=instant,
            )
            flat_fingerprint = flat_account.fingerprint
            event_type = CryptoLifecycleEventType.FLAT_RECONCILED
            next_status = CryptoLifecycleStatus.FLAT_RECONCILED

        payload = {
            "kind": "R6_CRYPTO_UNKNOWN_ORDER_404_RECOVERY",
            "role": requested_order.role.value,
            "client_order_id": requested_order.client_order_id,
            "order_absence_fingerprint": reconciliation.order_absence.fingerprint,
            "unknown_reconciliation_fingerprint": reconciliation.fingerprint,
            "position_fingerprint": position.fingerprint,
            "observed_position_quantity": format(position.quantity, "f"),
            "fresh_account_fingerprint": fresh_account.fingerprint,
            "account_reference": fresh_account.account_reference,
            "credential_reference": fresh_account.credential_reference,
            "flat_account_fingerprint": flat_fingerprint,
            "retry_authorized": False,
        }

        def transition(_binding, current: CryptoLifecycleState, _payload) -> CryptoLifecycleState:
            self._validate_unknown_state(current, requested_order)
            return replace(
                current,
                status=next_status,
                confirmed_net_long_quantity=position.quantity,
            )

        # Package-internal transaction primitive: preserves the canonical event
        # chain/control hash without exposing a second persistence implementation.
        recovered = lifecycle._mutate(  # noqa: SLF001 - deliberate package-internal lifecycle transition
            lifecycle_id,
            at=instant,
            event_type=event_type,
            payload=payload,
            transition=transition,
        )
        attempt_count = (
            recovered.entry_attempt_count
            if requested_order.role is CryptoOrderRole.ENTRY
            else recovered.protection_attempt_count
        )
        if attempt_count != 1:
            raise CryptoLifecycleIntegrityError("UNKNOWN recovery changed durable attempt count")
        expected_client_id = (
            snapshot.binding.entry_client_order_id
            if requested_order.role is CryptoOrderRole.ENTRY
            else recovered.protection_client_order_id
        )
        if expected_client_id != requested_order.client_order_id:
            raise CryptoLifecycleIntegrityError("UNKNOWN recovery changed durable client_order_id binding")

        values = {
            "lifecycle_id": lifecycle_id,
            "role": requested_order.role,
            "order_absence_fingerprint": reconciliation.order_absence.fingerprint,
            "reconciliation_fingerprint": reconciliation.fingerprint,
            "position_fingerprint": position.fingerprint,
            "fresh_account_fingerprint": fresh_account.fingerprint,
            "flat_account_fingerprint": flat_fingerprint,
            "observed_position_quantity": position.quantity,
            "resulting_status": recovered.status,
            "attempt_count": attempt_count,
            "client_order_id": requested_order.client_order_id,
            "retry_authorized": False,
            "recovered_at": instant,
        }
        return CryptoUnknownRecoveryReceipt(
            **values,
            receipt_hash=_hash_payload(_receipt_payload_from_values(values)),
        )

    @staticmethod
    def _validate_checkpoint(
        *,
        checkpoint: CryptoExecutionAttemptCheckpoint | CryptoProtectionExecutionAttemptCheckpoint,
        requested_order: AlpacaPaperCryptoOrderRequest,
        lifecycle_id: str,
        binding_hash: str,
        state: CryptoLifecycleState,
    ) -> tuple[str, str]:
        if requested_order.role is CryptoOrderRole.ENTRY:
            if not isinstance(checkpoint, CryptoExecutionAttemptCheckpoint):
                raise CryptoUnknownRecoveryBlocked("ENTRY UNKNOWN recovery requires exact ENTRY execution checkpoint")
            if checkpoint.client_order_id != requested_order.client_order_id:
                raise CryptoUnknownRecoveryBlocked("ENTRY checkpoint client_order_id mismatch")
            if checkpoint.pre_consume.lifecycle_binding_hash != binding_hash:
                raise CryptoUnknownRecoveryBlocked("ENTRY checkpoint lifecycle binding mismatch")
            if state.status is not CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN or state.entry_attempt_count != 1:
                raise CryptoUnknownRecoveryBlocked("ENTRY recovery requires durable ENTRY_SUBMISSION_UNKNOWN attempt=1")
            return checkpoint.pre_consume.account_reference, checkpoint.pre_consume.credential_reference

        if requested_order.role is CryptoOrderRole.PROTECTION:
            if not isinstance(checkpoint, CryptoProtectionExecutionAttemptCheckpoint):
                raise CryptoUnknownRecoveryBlocked("PROTECTION UNKNOWN recovery requires exact protection checkpoint")
            if checkpoint.lifecycle_id != lifecycle_id:
                raise CryptoUnknownRecoveryBlocked("PROTECTION checkpoint lifecycle mismatch")
            if checkpoint.client_order_id != requested_order.client_order_id:
                raise CryptoUnknownRecoveryBlocked("PROTECTION checkpoint client_order_id mismatch")
            if checkpoint.pre_consume.lifecycle_control_hash == state.control_hash:
                raise CryptoUnknownRecoveryBlocked("PROTECTION UNKNOWN recovery requires lifecycle advancement after PRE_CONSUME")
            if state.status is not CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN or state.protection_attempt_count != 1:
                raise CryptoUnknownRecoveryBlocked("PROTECTION recovery requires durable PROTECTION_SUBMISSION_UNKNOWN attempt=1")
            return checkpoint.pre_consume.account_reference, checkpoint.pre_consume.credential_reference

        raise CryptoUnknownRecoveryBlocked("unsupported crypto order role")

    @staticmethod
    def _validate_unknown_state(state: CryptoLifecycleState, requested_order: AlpacaPaperCryptoOrderRequest) -> None:
        if requested_order.role is CryptoOrderRole.ENTRY:
            if state.status is not CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN or state.entry_attempt_count != 1:
                raise CryptoUnknownRecoveryBlocked("ENTRY UNKNOWN state changed before recovery commit")
            return
        if requested_order.role is CryptoOrderRole.PROTECTION:
            if state.status is not CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN or state.protection_attempt_count != 1:
                raise CryptoUnknownRecoveryBlocked("PROTECTION UNKNOWN state changed before recovery commit")
            if state.protection_client_order_id != requested_order.client_order_id:
                raise CryptoUnknownRecoveryBlocked("PROTECTION lifecycle client_order_id mismatch")
            return
        raise CryptoUnknownRecoveryBlocked("unsupported crypto order role")

    @staticmethod
    def _validate_account(
        *,
        account: AlpacaPaperAccountAttestation,
        expected_account_reference: str,
        expected_credential_reference: str,
        now: datetime,
    ) -> None:
        if account.account_reference != expected_account_reference:
            raise CryptoUnknownRecoveryBlocked("fresh PAPER account differs from durable execution checkpoint")
        if account.credential_reference != expected_credential_reference:
            raise CryptoUnknownRecoveryBlocked("fresh PAPER credential differs from durable execution checkpoint")
        if account.status != "ACTIVE" or account.currency != "USD":
            raise CryptoUnknownRecoveryBlocked("fresh PAPER account is not ACTIVE USD")
        if account.source_host != ALPACA_PAPER_TRADING_HOST or account.source_path != ALPACA_PAPER_ACCOUNT_PATH:
            raise CryptoUnknownRecoveryBlocked("fresh PAPER account provenance is invalid")
        _require_fresh(account.attested_at, now=now, ttl=RECOVERY_EVIDENCE_TTL, label="fresh PAPER account")

    @staticmethod
    def _validate_reconciliation(
        *,
        reconciliation: CryptoBrokerUnknownReconciliation,
        requested_order: AlpacaPaperCryptoOrderRequest,
        expected_credential_reference: str,
        now: datetime,
    ) -> None:
        absence = reconciliation.order_absence
        position = reconciliation.position
        if absence.client_order_id != requested_order.client_order_id:
            raise CryptoUnknownRecoveryBlocked("order-404 evidence client_order_id mismatch")
        if position.symbol != requested_order.symbol:
            raise CryptoUnknownRecoveryBlocked("order-404 position symbol mismatch")
        if absence.credential_reference != expected_credential_reference:
            raise CryptoUnknownRecoveryBlocked("order-404 evidence credential mismatch")
        if position.credential_reference != expected_credential_reference:
            raise CryptoUnknownRecoveryBlocked("order-404 position credential mismatch")
        if absence.observed_at != reconciliation.observed_at or position.observed_at != reconciliation.observed_at:
            raise CryptoUnknownRecoveryBlocked("order-404 evidence timestamps are not one atomic observation")
        _require_fresh(reconciliation.observed_at, now=now, ttl=RECOVERY_EVIDENCE_TTL, label="UNKNOWN reconciliation")

    @staticmethod
    def _validate_flat_account(
        *,
        flat_account: PaperFlatAccountAttestation,
        fresh_account: AlpacaPaperAccountAttestation,
        now: datetime,
    ) -> None:
        if not flat_account.clean_for_first_canary:
            raise CryptoUnknownRecoveryBlocked("all-account evidence is not flat")
        if flat_account.account_attestation_fingerprint != fresh_account.fingerprint:
            raise CryptoUnknownRecoveryBlocked("flat-account evidence is not bound to fresh PAPER account")
        if flat_account.credential_reference != fresh_account.credential_reference:
            raise CryptoUnknownRecoveryBlocked("flat-account credential differs from fresh PAPER account")
        if flat_account.source_host != ALPACA_PAPER_TRADING_HOST:
            raise CryptoUnknownRecoveryBlocked("flat-account evidence host is invalid")
        if flat_account.positions_path != POSITIONS_PATH:
            raise CryptoUnknownRecoveryBlocked("flat-account positions path is invalid")
        if flat_account.orders_path != f"{ORDERS_PATH}?{ORDERS_QUERY}":
            raise CryptoUnknownRecoveryBlocked("flat-account open-orders path/query is invalid")
        _require_fresh(flat_account.attested_at, now=now, ttl=FLAT_EVIDENCE_TTL, label="flat-account evidence")


def _require_fresh(value: datetime, *, now: datetime, ttl: timedelta, label: str) -> None:
    _require_aware(value, label)
    observed = value.astimezone(timezone.utc)
    if observed > now + timedelta(seconds=3):
        raise CryptoUnknownRecoveryBlocked(f"{label} is future-dated")
    age = now - observed
    if age < timedelta(seconds=-3) or age >= ttl:
        raise CryptoUnknownRecoveryBlocked(f"{label} is stale")


def _receipt_payload(value: CryptoUnknownRecoveryReceipt, *, include_hash: bool) -> dict[str, object]:
    payload = _receipt_payload_from_values(
        {
            "lifecycle_id": value.lifecycle_id,
            "role": value.role,
            "order_absence_fingerprint": value.order_absence_fingerprint,
            "reconciliation_fingerprint": value.reconciliation_fingerprint,
            "position_fingerprint": value.position_fingerprint,
            "fresh_account_fingerprint": value.fresh_account_fingerprint,
            "flat_account_fingerprint": value.flat_account_fingerprint,
            "observed_position_quantity": value.observed_position_quantity,
            "resulting_status": value.resulting_status,
            "attempt_count": value.attempt_count,
            "client_order_id": value.client_order_id,
            "retry_authorized": value.retry_authorized,
            "recovered_at": value.recovered_at,
        }
    )
    if include_hash:
        payload["receipt_hash"] = value.receipt_hash
    return payload


def _receipt_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            payload[key] = value.astimezone(timezone.utc).isoformat()
        elif isinstance(value, Decimal):
            payload[key] = format(value, "f")
        elif isinstance(value, (CryptoOrderRole, CryptoLifecycleStatus)):
            payload[key] = value.value
        else:
            payload[key] = value
    return payload


def _hash_payload(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = [
    "CryptoPaperUnknownRecoveryCoordinator",
    "CryptoUnknownRecoveryBlocked",
    "CryptoUnknownRecoveryError",
    "CryptoUnknownRecoveryReceipt",
    "FLAT_EVIDENCE_TTL",
    "RECOVERY_EVIDENCE_TTL",
]
