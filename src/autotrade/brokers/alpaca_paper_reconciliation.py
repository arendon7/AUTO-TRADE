from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .alpaca_paper_bracket import (
    AlpacaEquityBracketRequest,
    AlpacaNestedBracketAttestation,
    AlpacaNestedBracketResponseValidator,
)
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation, AlpacaPaperCredentials
from .alpaca_paper_reconciliation_gateway import AlpacaPaperOrderLookupGateway
from .alpaca_paper_submission import (
    PaperSubmissionConflict,
    PaperSubmissionState,
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)


class PaperReconciliationError(RuntimeError):
    pass


class PaperReconciliationBlocked(PaperReconciliationError):
    pass


class PaperReconciliationConflict(PaperReconciliationError):
    pass


@dataclass(frozen=True, slots=True)
class PaperReconciliationOutcome:
    state: PaperSubmissionState
    found: bool
    lookup_request_id: str
    detail_request_id: str | None
    bracket_attestation: AlpacaNestedBracketAttestation | None


class AlpacaPaperBracketReconciler:
    """Resolve an UNKNOWN bracket submission by broker reads only.

    There is deliberately no POST/retry API. A 404 by client_order_id records
    durable absence evidence and leaves the submission UNKNOWN. A discovered
    order is fetched again with nested=true, strictly validated against the
    immutable canonical bracket request, then the durable registry may move to
    ACKNOWLEDGED.
    """

    def __init__(
        self,
        *,
        lookup_gateway: AlpacaPaperOrderLookupGateway,
        response_validator: AlpacaNestedBracketResponseValidator | None = None,
    ) -> None:
        self._lookup_gateway = lookup_gateway
        self._validator = response_validator or AlpacaNestedBracketResponseValidator()

    def reconcile(
        self,
        *,
        registry: SQLitePaperSubmissionRegistry,
        order_id: str,
        credentials: AlpacaPaperCredentials,
        account_attestation: AlpacaPaperAccountAttestation,
        expected_bracket: AlpacaEquityBracketRequest,
        now: datetime,
    ) -> PaperReconciliationOutcome:
        state = registry.get(order_id)
        binding = registry.get_binding(order_id)
        if state.status is not PaperSubmissionStatus.UNKNOWN:
            raise PaperReconciliationBlocked(
                "PAPER reconciliation reads are allowed only for UNKNOWN submission state"
            )
        if expected_bracket.order_id != order_id:
            raise PaperReconciliationConflict("expected bracket order_id mismatch")
        if expected_bracket.client_order_id != binding.client_order_id:
            raise PaperReconciliationConflict("expected bracket client_order_id mismatch")
        if expected_bracket.payload_hash != binding.order_payload_hash:
            raise PaperReconciliationConflict("expected bracket payload hash mismatch")
        if binding.account_attestation_fingerprint != account_attestation.fingerprint:
            raise PaperReconciliationConflict(
                "current account attestation does not match frozen submission binding"
            )

        lookup = self._lookup_gateway.lookup_by_client_order_id(
            credentials=credentials,
            account_attestation=account_attestation,
            client_order_id=binding.client_order_id,
        )
        if not lookup.found:
            updated = registry.record_reconciliation_absent(
                order_id=order_id,
                request_id=lookup.request_id,
                now=now,
            )
            return PaperReconciliationOutcome(
                state=updated,
                found=False,
                lookup_request_id=lookup.request_id,
                detail_request_id=None,
                bracket_attestation=None,
            )

        if lookup.broker_order_id is None:
            raise PaperReconciliationConflict("found broker order lacks broker_order_id")
        detail = self._lookup_gateway.get_nested_order(
            credentials=credentials,
            account_attestation=account_attestation,
            broker_order_id=lookup.broker_order_id,
        )
        if not detail.found or detail.body is None or detail.broker_order_id is None:
            raise PaperReconciliationConflict("nested broker order evidence is incomplete")
        if detail.client_order_id != binding.client_order_id:
            raise PaperReconciliationConflict("nested broker client_order_id mismatch")

        bracket_attestation = self._validator.validate(
            response_body=detail.body,
            request_id=detail.request_id,
            expected=expected_bracket,
        )
        if bracket_attestation.parent_order_id != lookup.broker_order_id:
            raise PaperReconciliationConflict(
                "client lookup and nested order identify different broker orders"
            )
        try:
            updated = registry.reconcile_acknowledged(
                order_id=order_id,
                broker_order_id=bracket_attestation.parent_order_id,
                broker_client_order_id=bracket_attestation.client_order_id,
                broker_order_payload_hash=expected_bracket.payload_hash,
                request_id=detail.request_id,
                now=now,
            )
        except PaperSubmissionConflict as exc:
            raise PaperReconciliationConflict(str(exc)) from exc

        return PaperReconciliationOutcome(
            state=updated,
            found=True,
            lookup_request_id=lookup.request_id,
            detail_request_id=detail.request_id,
            bracket_attestation=bracket_attestation,
        )
