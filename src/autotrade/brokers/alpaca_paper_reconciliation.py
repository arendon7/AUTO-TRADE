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
        self._validate_binding(
            order_id=order_id,
            binding=binding,
            expected_bracket=expected_bracket,
            account_attestation=account_attestation,
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
        bracket_attestation = self._validate_nested_detail(
            detail=detail,
            expected_bracket=expected_bracket,
            expected_client_order_id=binding.client_order_id,
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

    def recover_acknowledged_attestation(
        self,
        *,
        registry: SQLitePaperSubmissionRegistry,
        order_id: str,
        credentials: AlpacaPaperCredentials,
        account_attestation: AlpacaPaperAccountAttestation,
        expected_bracket: AlpacaEquityBracketRequest,
    ) -> AlpacaNestedBracketAttestation:
        """Rehydrate child-leg evidence after ACK persistence but artifact crash.

        This path is GET-only and never changes submission state. It exists so
        a crash after durable ACKNOWLEDGED but before local artifact persistence
        cannot force a blind POST, state rollback, or in-memory reconstruction.
        """

        state = registry.get(order_id)
        binding = registry.get_binding(order_id)
        if state.status is not PaperSubmissionStatus.ACKNOWLEDGED:
            raise PaperReconciliationBlocked(
                "acknowledged bracket recovery requires ACKNOWLEDGED submission state"
            )
        self._validate_binding(
            order_id=order_id,
            binding=binding,
            expected_bracket=expected_bracket,
            account_attestation=account_attestation,
        )
        if not state.broker_order_id or not state.broker_client_order_id:
            raise PaperReconciliationConflict(
                "ACKNOWLEDGED submission lacks durable broker identity"
            )
        detail = self._lookup_gateway.get_nested_order(
            credentials=credentials,
            account_attestation=account_attestation,
            broker_order_id=state.broker_order_id,
        )
        bracket_attestation = self._validate_nested_detail(
            detail=detail,
            expected_bracket=expected_bracket,
            expected_client_order_id=binding.client_order_id,
        )
        if bracket_attestation.parent_order_id != state.broker_order_id:
            raise PaperReconciliationConflict(
                "recovered bracket parent does not match durable ACKNOWLEDGED identity"
            )
        if bracket_attestation.client_order_id != state.broker_client_order_id:
            raise PaperReconciliationConflict(
                "recovered bracket client_order_id does not match durable ACKNOWLEDGED identity"
            )
        return bracket_attestation

    @staticmethod
    def _validate_binding(
        *,
        order_id: str,
        binding,
        expected_bracket: AlpacaEquityBracketRequest,
        account_attestation: AlpacaPaperAccountAttestation,
    ) -> None:
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

    def _validate_nested_detail(
        self,
        *,
        detail,
        expected_bracket: AlpacaEquityBracketRequest,
        expected_client_order_id: str,
    ) -> AlpacaNestedBracketAttestation:
        if not detail.found or detail.body is None or detail.broker_order_id is None:
            raise PaperReconciliationConflict("nested broker order evidence is incomplete")
        if detail.client_order_id != expected_client_order_id:
            raise PaperReconciliationConflict("nested broker client_order_id mismatch")
        return self._validator.validate(
            response_body=detail.body,
            request_id=detail.request_id,
            expected=expected_bracket,
        )
