from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re

from .alpaca_paper_crypto_cold_start_execution_attempt import (
    CryptoColdStartExecutionAttemptCheckpoint,
)
from .alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleState,
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from .alpaca_paper_crypto_order import AlpacaPaperCryptoOrderRequest, CryptoOrderRole
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


class CryptoColdStartUnknownRecoveryError(RuntimeError):
    pass


class CryptoColdStartUnknownRecoveryBlocked(CryptoColdStartUnknownRecoveryError):
    pass


@dataclass(frozen=True, slots=True)
class CryptoColdStartUnknownRecoveryReceipt:
    lifecycle_id: str
    order_absence_fingerprint: str
    reconciliation_fingerprint: str
    position_fingerprint: str
    fresh_account_fingerprint: str
    flat_account_fingerprint: str | None
    observed_position_quantity: Decimal
    resulting_status: CryptoLifecycleStatus
    attempt_count: int
    client_order_id: str
    checkpoint_hash: str
    retry_authorized: bool
    recovered_at: datetime
    receipt_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("order_absence_fingerprint", self.order_absence_fingerprint),
            ("reconciliation_fingerprint", self.reconciliation_fingerprint),
            ("position_fingerprint", self.position_fingerprint),
            ("fresh_account_fingerprint", self.fresh_account_fingerprint),
            ("checkpoint_hash", self.checkpoint_hash),
            ("receipt_hash", self.receipt_hash),
        ):
            if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                raise ValueError(f"{label} must be lowercase SHA-256")
        if self.flat_account_fingerprint is not None and not _HASH_RE.fullmatch(
            self.flat_account_fingerprint
        ):
            raise ValueError("flat_account_fingerprint must be lowercase SHA-256")
        if (
            not isinstance(self.observed_position_quantity, Decimal)
            or not self.observed_position_quantity.is_finite()
            or self.observed_position_quantity < 0
        ):
            raise ValueError("observed_position_quantity must be finite and non-negative")
        if self.attempt_count != 1:
            raise ValueError("cold-start UNKNOWN recovery preserves exactly one write attempt")
        if self.retry_authorized is not False:
            raise ValueError("cold-start UNKNOWN recovery never authorizes retry")
        _require_aware(self.recovered_at, "recovered_at")
        if self.receipt_hash != _hash_payload(_receipt_payload(self, include_hash=False)):
            raise ValueError("cold-start UNKNOWN recovery receipt hash mismatch")


class CryptoColdStartUnknownRecoveryCoordinator:
    """Resolve the first-canary durable UNKNOWN without ever granting POST retry.

    An exact client-order-id 404 is not proof that the original POST did not
    arrive. Position truth is mandatory. A remaining long position is committed
    as HALTED_RECONCILIATION_REQUIRED. A zero pair position is accepted as flat
    only with fresh all-account evidence proving zero positions and zero open
    orders for the same PAPER account and credential reference.
    """

    def recover_entry(
        self,
        *,
        lifecycle: SQLiteCryptoPaperLifecycle,
        lifecycle_id: str,
        requested_order: AlpacaPaperCryptoOrderRequest,
        reconciliation: CryptoBrokerUnknownReconciliation,
        checkpoint: CryptoColdStartExecutionAttemptCheckpoint,
        fresh_account: AlpacaPaperAccountAttestation,
        flat_account: PaperFlatAccountAttestation | None,
        now: datetime,
    ) -> CryptoColdStartUnknownRecoveryReceipt:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        if not isinstance(lifecycle, SQLiteCryptoPaperLifecycle):
            raise CryptoColdStartUnknownRecoveryBlocked(
                "authoritative crypto lifecycle is required"
            )
        if not isinstance(requested_order, AlpacaPaperCryptoOrderRequest):
            raise CryptoColdStartUnknownRecoveryBlocked(
                "exact requested crypto order is required"
            )
        if requested_order.role is not CryptoOrderRole.ENTRY:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "cold-start UNKNOWN recovery accepts ENTRY only"
            )
        if not isinstance(reconciliation, CryptoBrokerUnknownReconciliation):
            raise CryptoColdStartUnknownRecoveryBlocked(
                "exact order-404 UNKNOWN reconciliation is required"
            )
        if not isinstance(checkpoint, CryptoColdStartExecutionAttemptCheckpoint):
            raise CryptoColdStartUnknownRecoveryBlocked(
                "exact cold-start PRE_CONSUME checkpoint is required"
            )
        if not isinstance(fresh_account, AlpacaPaperAccountAttestation):
            raise CryptoColdStartUnknownRecoveryBlocked(
                "fresh PAPER account attestation is required"
            )

        snapshot = lifecycle.snapshot(lifecycle_id)
        state = snapshot.state
        self._validate_checkpoint(
            checkpoint=checkpoint,
            requested_order=requested_order,
            lifecycle_id=lifecycle_id,
            binding_hash=snapshot.binding.fingerprint,
            state=state,
        )
        if snapshot.binding.entry_client_order_id != requested_order.client_order_id:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "lifecycle client_order_id differs from cold-start recovery order"
            )
        expected_account_reference = checkpoint.pre_consume.account_reference
        expected_credential_reference = checkpoint.pre_consume.credential_reference
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
                raise CryptoColdStartUnknownRecoveryBlocked(
                    "remaining UNKNOWN exposure must halt; flat evidence cannot override a long position"
                )
            flat_fingerprint = None
            next_status = CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED
        else:
            if flat_account is None:
                raise CryptoColdStartUnknownRecoveryBlocked(
                    "order 404 plus zero pair position is insufficient; fresh all-account flatness is required"
                )
            self._validate_flat_account(
                flat_account=flat_account,
                fresh_account=fresh_account,
                now=instant,
            )
            flat_fingerprint = flat_account.fingerprint
            next_status = CryptoLifecycleStatus.FLAT_RECONCILED

        recovered = lifecycle.recover_entry_unknown_absence(
            lifecycle_id,
            client_order_id=requested_order.client_order_id,
            position_quantity=position.quantity,
            order_absence_fingerprint=reconciliation.order_absence.fingerprint,
            reconciliation_fingerprint=reconciliation.fingerprint,
            position_fingerprint=position.fingerprint,
            fresh_account_fingerprint=fresh_account.fingerprint,
            flat_account_fingerprint=flat_fingerprint,
            checkpoint_hash=checkpoint.record_hash,
            at=instant,
        )
        if recovered.entry_attempt_count != 1:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "cold-start UNKNOWN recovery changed durable entry attempt count"
            )
        if recovered.status is not next_status:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "cold-start lifecycle returned unexpected recovery status"
            )

        values = {
            "lifecycle_id": lifecycle_id,
            "order_absence_fingerprint": reconciliation.order_absence.fingerprint,
            "reconciliation_fingerprint": reconciliation.fingerprint,
            "position_fingerprint": position.fingerprint,
            "fresh_account_fingerprint": fresh_account.fingerprint,
            "flat_account_fingerprint": flat_fingerprint,
            "observed_position_quantity": position.quantity,
            "resulting_status": recovered.status,
            "attempt_count": recovered.entry_attempt_count,
            "client_order_id": requested_order.client_order_id,
            "checkpoint_hash": checkpoint.record_hash,
            "retry_authorized": False,
            "recovered_at": instant,
        }
        return CryptoColdStartUnknownRecoveryReceipt(
            **values,
            receipt_hash=_hash_payload(_receipt_payload_from_values(values)),
        )

    @staticmethod
    def _validate_checkpoint(
        *,
        checkpoint: CryptoColdStartExecutionAttemptCheckpoint,
        requested_order: AlpacaPaperCryptoOrderRequest,
        lifecycle_id: str,
        binding_hash: str,
        state: CryptoLifecycleState,
    ) -> None:
        if checkpoint.client_order_id != requested_order.client_order_id:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "cold-start checkpoint client_order_id mismatch"
            )
        if checkpoint.pre_consume.lifecycle_binding_hash != binding_hash:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "cold-start checkpoint lifecycle binding mismatch"
            )
        if checkpoint.pre_consume.lifecycle_status is not CryptoLifecycleStatus.ENTRY_PREPARED:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "cold-start PRE_CONSUME checkpoint did not bind ENTRY_PREPARED"
            )
        if (
            state.status is not CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
            or state.entry_attempt_count != 1
        ):
            raise CryptoColdStartUnknownRecoveryBlocked(
                "cold-start recovery requires durable ENTRY_SUBMISSION_UNKNOWN attempt=1"
            )
        if snapshot_id := getattr(checkpoint.pre_consume, "portfolio_snapshot_id", ""):
            if not snapshot_id.startswith("r6-crypto-paper-cold-start:"):
                raise CryptoColdStartUnknownRecoveryBlocked(
                    "cold-start checkpoint Portfolio provenance is invalid"
                )
        if not lifecycle_id.strip():
            raise CryptoColdStartUnknownRecoveryBlocked("lifecycle_id is required")

    @staticmethod
    def _validate_account(
        *,
        account: AlpacaPaperAccountAttestation,
        expected_account_reference: str,
        expected_credential_reference: str,
        now: datetime,
    ) -> None:
        if account.account_reference != expected_account_reference:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "fresh PAPER account differs from cold-start checkpoint"
            )
        if account.credential_reference != expected_credential_reference:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "fresh PAPER credential differs from cold-start checkpoint"
            )
        if account.status != "ACTIVE" or account.currency != "USD":
            raise CryptoColdStartUnknownRecoveryBlocked(
                "fresh PAPER account is not ACTIVE USD"
            )
        if (
            account.source_host != ALPACA_PAPER_TRADING_HOST
            or account.source_path != ALPACA_PAPER_ACCOUNT_PATH
        ):
            raise CryptoColdStartUnknownRecoveryBlocked(
                "fresh PAPER account provenance is invalid"
            )
        _require_fresh(
            account.attested_at,
            now=now,
            ttl=RECOVERY_EVIDENCE_TTL,
            label="fresh PAPER account",
        )

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
            raise CryptoColdStartUnknownRecoveryBlocked(
                "order-404 evidence client_order_id mismatch"
            )
        if position.symbol != requested_order.symbol:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "order-404 position symbol mismatch"
            )
        if absence.credential_reference != expected_credential_reference:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "order-404 evidence credential mismatch"
            )
        if position.credential_reference != expected_credential_reference:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "order-404 position credential mismatch"
            )
        if (
            absence.observed_at != reconciliation.observed_at
            or position.observed_at != reconciliation.observed_at
        ):
            raise CryptoColdStartUnknownRecoveryBlocked(
                "order-404 evidence timestamps are not one atomic observation"
            )
        _require_fresh(
            reconciliation.observed_at,
            now=now,
            ttl=RECOVERY_EVIDENCE_TTL,
            label="UNKNOWN reconciliation",
        )

    @staticmethod
    def _validate_flat_account(
        *,
        flat_account: PaperFlatAccountAttestation,
        fresh_account: AlpacaPaperAccountAttestation,
        now: datetime,
    ) -> None:
        if not flat_account.clean_for_first_canary:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "all-account evidence is not flat"
            )
        if flat_account.account_attestation_fingerprint != fresh_account.fingerprint:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "flat-account evidence is not bound to fresh PAPER account"
            )
        if flat_account.credential_reference != fresh_account.credential_reference:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "flat-account credential differs from fresh PAPER account"
            )
        if flat_account.source_host != ALPACA_PAPER_TRADING_HOST:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "flat-account evidence host is invalid"
            )
        if flat_account.positions_path != POSITIONS_PATH:
            raise CryptoColdStartUnknownRecoveryBlocked(
                "flat-account positions path is invalid"
            )
        if flat_account.orders_path != f"{ORDERS_PATH}?{ORDERS_QUERY}":
            raise CryptoColdStartUnknownRecoveryBlocked(
                "flat-account open-orders path/query is invalid"
            )
        _require_fresh(
            flat_account.attested_at,
            now=now,
            ttl=FLAT_EVIDENCE_TTL,
            label="flat-account evidence",
        )


def _require_fresh(
    value: datetime,
    *,
    now: datetime,
    ttl: timedelta,
    label: str,
) -> None:
    _require_aware(value, label)
    observed = value.astimezone(timezone.utc)
    if observed > now + timedelta(seconds=3):
        raise CryptoColdStartUnknownRecoveryBlocked(f"{label} is future-dated")
    age = now - observed
    if age < timedelta(seconds=-3) or age >= ttl:
        raise CryptoColdStartUnknownRecoveryBlocked(f"{label} is stale")


def _receipt_payload(
    value: CryptoColdStartUnknownRecoveryReceipt,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload = _receipt_payload_from_values(
        {
            "lifecycle_id": value.lifecycle_id,
            "order_absence_fingerprint": value.order_absence_fingerprint,
            "reconciliation_fingerprint": value.reconciliation_fingerprint,
            "position_fingerprint": value.position_fingerprint,
            "fresh_account_fingerprint": value.fresh_account_fingerprint,
            "flat_account_fingerprint": value.flat_account_fingerprint,
            "observed_position_quantity": value.observed_position_quantity,
            "resulting_status": value.resulting_status,
            "attempt_count": value.attempt_count,
            "client_order_id": value.client_order_id,
            "checkpoint_hash": value.checkpoint_hash,
            "retry_authorized": value.retry_authorized,
            "recovered_at": value.recovered_at,
        }
    )
    if include_hash:
        payload["receipt_hash"] = value.receipt_hash
    return payload


def _receipt_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    resulting_status = values["resulting_status"]
    if isinstance(resulting_status, CryptoLifecycleStatus):
        status_text = resulting_status.value
    else:
        status_text = str(resulting_status)
    recovered_at = values["recovered_at"]
    if not isinstance(recovered_at, datetime):
        raise TypeError("recovered_at must be datetime")
    observed_quantity = values["observed_position_quantity"]
    if not isinstance(observed_quantity, Decimal):
        raise TypeError("observed_position_quantity must be Decimal")
    return {
        "lifecycle_id": str(values["lifecycle_id"]),
        "order_absence_fingerprint": str(values["order_absence_fingerprint"]),
        "reconciliation_fingerprint": str(values["reconciliation_fingerprint"]),
        "position_fingerprint": str(values["position_fingerprint"]),
        "fresh_account_fingerprint": str(values["fresh_account_fingerprint"]),
        "flat_account_fingerprint": values["flat_account_fingerprint"],
        "observed_position_quantity": format(observed_quantity, "f"),
        "resulting_status": status_text,
        "attempt_count": int(values["attempt_count"]),
        "client_order_id": str(values["client_order_id"]),
        "checkpoint_hash": str(values["checkpoint_hash"]),
        "retry_authorized": bool(values["retry_authorized"]),
        "recovered_at": recovered_at.astimezone(timezone.utc).isoformat(),
    }


def _hash_payload(payload: dict[str, object]) -> str:
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = [
    "CryptoColdStartUnknownRecoveryBlocked",
    "CryptoColdStartUnknownRecoveryCoordinator",
    "CryptoColdStartUnknownRecoveryError",
    "CryptoColdStartUnknownRecoveryReceipt",
]
