from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import socket
import ssl
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from autotrade.oms import ExternalSubmissionHandoff, OrderManagementSystem

from .alpaca_paper_bracket import AlpacaEquityBracketRequest
from .alpaca_paper_canary import PaperCanaryApproval
from .alpaca_paper_canary_coordinator import PreparedPaperCanaryPackage
from .alpaca_paper_execution_bridge import PaperExecutionStageResult
from .alpaca_paper_operator_decision import (
    PaperOperatorDecision,
    PaperOperatorDecisionContext,
    PaperOperatorDecisionStatus,
    SQLitePaperOperatorDecisionRegistry,
)
from .alpaca_paper_final_guard import (
    PaperFinalWriteBlocked,
    PaperFinalWriteGuard,
    PaperFinalWritePhase,
)
from .alpaca_paper_canary_permit import (
    PaperCanaryPermitStatus,
    SQLitePaperCanaryPermitRegistry,
)
from .alpaca_paper_gateway import (
    ALPACA_LIVE_TRADING_HOST,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from .alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)


PAPER_ORDER_PATH = "/v2/orders"


class PaperWriterError(RuntimeError):
    pass


class PaperWriterDisabled(PaperWriterError):
    pass


class PaperWriterPolicyError(PaperWriterError):
    pass


class PaperWriterBlocked(PaperWriterError):
    pass


class PaperWriterAmbiguous(PaperWriterError):
    """Network may have accepted the order; durable state remains UNKNOWN."""


@dataclass(frozen=True, slots=True)
class AlpacaPaperWriterConfig:
    enabled: bool = False
    base_url: str = f"https://{ALPACA_PAPER_TRADING_HOST}"
    timeout_seconds: float = 5.0
    max_response_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if not 0 < self.timeout_seconds <= 15:
            raise ValueError("timeout_seconds must be >0 and <=15")
        if not 1 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("max_response_bytes must be between 1 and 1048576")


@dataclass(frozen=True, slots=True)
class AlpacaPaperWriteRequest:
    method: str
    url: str
    timeout_seconds: float
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class AlpacaPaperWriteResponse:
    status_code: int
    body: bytes
    final_url: str
    headers: Mapping[str, str]


class AlpacaPaperWriteTransport(Protocol):
    def write(self, request: AlpacaPaperWriteRequest) -> AlpacaPaperWriteResponse: ...


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PaperWriterPolicyError("PAPER order redirects are forbidden")


class UrllibAlpacaPaperWriteTransport:
    """Exactly-one HTTP request per `write` invocation; no retry logic exists."""

    def __init__(self, *, max_response_bytes: int = 256 * 1024) -> None:
        if not 1 <= max_response_bytes <= 1_048_576:
            raise ValueError("max_response_bytes must be between 1 and 1048576")
        self._max_response_bytes = max_response_bytes
        self._opener = build_opener(
            ProxyHandler({}),
            HTTPSHandler(context=ssl.create_default_context()),
            _RejectRedirectHandler(),
        )

    def write(self, request: AlpacaPaperWriteRequest) -> AlpacaPaperWriteResponse:
        _validate_write_request(request)
        raw = Request(
            request.url,
            data=request.body,
            method="POST",
            headers=dict(request.headers),
        )
        try:
            with self._opener.open(raw, timeout=request.timeout_seconds) as response:  # noqa: S310
                body = response.read(self._max_response_bytes + 1)
                if len(body) > self._max_response_bytes:
                    raise PaperWriterAmbiguous("PAPER order response exceeded size limit")
                return AlpacaPaperWriteResponse(
                    status_code=int(response.status),
                    body=body,
                    final_url=response.geturl(),
                    headers={key.lower(): value for key, value in response.headers.items()},
                )
        except PaperWriterPolicyError:
            raise
        except HTTPError as exc:
            body = exc.read(self._max_response_bytes + 1)
            if len(body) > self._max_response_bytes:
                raise PaperWriterAmbiguous("PAPER order error response exceeded size limit") from exc
            return AlpacaPaperWriteResponse(
                status_code=int(exc.code),
                body=body,
                final_url=exc.geturl(),
                headers={key.lower(): value for key, value in exc.headers.items()},
            )
        except URLError as exc:
            raise PaperWriterAmbiguous("PAPER order transport result is ambiguous") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise PaperWriterAmbiguous("PAPER order timed out after UNKNOWN was persisted") from exc


@dataclass(frozen=True, slots=True)
class PaperSubmitAttemptResult:
    order_id: str
    client_order_id: str
    attempt_id: str
    http_status: int
    request_id: str
    broker_order_id: str | None
    response_hash: str
    provisionally_accepted: bool
    durable_status: PaperSubmissionStatus
    reconciliation_required: bool
    pre_consume_guard_hash: str
    pre_io_guard_hash: str
    prepared_package_hash: str
    operator_decision_hash: str


class AlpacaPaperSingleShotWriter:
    """One audited PAPER POST, never retried, never self-acknowledged.

    Preconditions are checked while the durable submission is PREPARED. The
    short-lived canary permit is consumed, then the submission is durably moved
    to UNKNOWN, and only then may one POST occur. A process restart that observes
    UNKNOWN is therefore reconciliation-only and can never replay POST.

    Even a valid 2xx response remains UNKNOWN until the separate GET-only
    reconciler proves the frozen client_order_id and nested bracket legs.
    """

    def __init__(
        self,
        *,
        config: AlpacaPaperWriterConfig | None = None,
        transport: AlpacaPaperWriteTransport | None = None,
    ) -> None:
        self._config = config or AlpacaPaperWriterConfig()
        self._transport = transport or UrllibAlpacaPaperWriteTransport(
            max_response_bytes=self._config.max_response_bytes
        )

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def submit_once(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        account_attestation: AlpacaPaperAccountAttestation,
        expected_bracket: AlpacaEquityBracketRequest,
        approval: PaperCanaryApproval,
        prepared_package: PreparedPaperCanaryPackage,
        operator_decision: PaperOperatorDecision,
        operator_registry: SQLitePaperOperatorDecisionRegistry,
        execution_stage: PaperExecutionStageResult,
        permit_registry: SQLitePaperCanaryPermitRegistry,
        submission_registry: SQLitePaperSubmissionRegistry,
        oms: OrderManagementSystem,
        external_handoff: ExternalSubmissionHandoff,
        final_guard: PaperFinalWriteGuard,
        attempt_id: str,
        now,
    ) -> PaperSubmitAttemptResult:
        if not self._config.enabled:
            raise PaperWriterDisabled("external PAPER writer is disabled by default")
        if not isinstance(final_guard, PaperFinalWriteGuard):
            raise PaperWriterBlocked("writer requires authoritative PaperFinalWriteGuard")
        if not isinstance(oms, OrderManagementSystem):
            raise PaperWriterBlocked("writer requires authoritative OrderManagementSystem")
        if not isinstance(external_handoff, ExternalSubmissionHandoff):
            raise PaperWriterBlocked("writer requires durable OMS external handoff")
        _validate_writer_base_url(self._config.base_url)
        if credentials.credential_reference != account_attestation.credential_reference:
            raise PaperWriterPolicyError("writer credentials do not match PAPER account attestation")
        if account_attestation.status != "ACTIVE" or account_attestation.currency != "USD":
            raise PaperWriterPolicyError("writer requires ACTIVE USD PAPER account attestation")
        if (
            account_attestation.source_host != ALPACA_PAPER_TRADING_HOST
            or account_attestation.source_path != "/v2/account"
        ):
            raise PaperWriterPolicyError("writer account attestation endpoint is not exact")
        if not approval.is_valid_at(now):
            raise PaperWriterBlocked("PAPER canary approval is expired or not yet valid")
        if approval.order_id != expected_bracket.order_id:
            raise PaperWriterBlocked("canary approval order_id mismatch")
        if approval.client_order_id != expected_bracket.client_order_id:
            raise PaperWriterBlocked("canary approval client_order_id mismatch")

        if not isinstance(prepared_package, PreparedPaperCanaryPackage):
            raise PaperWriterBlocked("writer requires exact PreparedPaperCanaryPackage")
        if not isinstance(operator_decision, PaperOperatorDecision):
            raise PaperWriterBlocked("writer requires durable human operator decision")
        if not isinstance(operator_registry, SQLitePaperOperatorDecisionRegistry):
            raise PaperWriterBlocked("writer requires authoritative operator decision registry")
        if not isinstance(execution_stage, PaperExecutionStageResult):
            raise PaperWriterBlocked("writer requires certified execution bridge result")
        if prepared_package.network_write_authorized is not False:
            raise PaperWriterBlocked("prepared package cannot carry network authority")
        if prepared_package.next_action != "OPERATOR_DECISION_REQUIRED":
            raise PaperWriterBlocked("prepared package does not encode operator-decision gate")
        if attempt_id != prepared_package.attempt_id:
            raise PaperWriterBlocked("writer attempt_id does not match prepared package")
        if now < prepared_package.prepared_at or now >= prepared_package.execution_deadline:
            raise PaperWriterBlocked("prepared package execution deadline is not valid")
        if prepared_package.order_id != expected_bracket.order_id:
            raise PaperWriterBlocked("prepared package order_id mismatch")
        if prepared_package.client_order_id != expected_bracket.client_order_id:
            raise PaperWriterBlocked("prepared package client_order_id mismatch")
        if prepared_package.bracket_payload_hash != expected_bracket.payload_hash:
            raise PaperWriterBlocked("prepared package bracket payload hash mismatch")
        if prepared_package.account_attestation_fingerprint != account_attestation.fingerprint:
            raise PaperWriterBlocked("prepared package account attestation mismatch")
        if prepared_package.canary_approval_hash != approval.approval_hash:
            raise PaperWriterBlocked("prepared package canary approval mismatch")
        if prepared_package.notional != approval.notional:
            raise PaperWriterBlocked("prepared package canary notional mismatch")

        expected_operator_context = PaperOperatorDecisionContext.from_prepared_package(
            prepared_package
        )
        if operator_decision.context != expected_operator_context:
            raise PaperWriterBlocked("operator decision does not match exact prepared package")
        if not operator_decision.is_valid_at(now):
            raise PaperWriterBlocked("human operator decision is expired or not yet valid")
        try:
            durable_operator = operator_registry.get(expected_operator_context.preparation_hash)
        except Exception as exc:
            raise PaperWriterBlocked("durable human operator decision verification failed") from exc
        if durable_operator.decision != operator_decision:
            raise PaperWriterBlocked("supplied human decision does not match durable evidence")
        if durable_operator.status is not PaperOperatorDecisionStatus.CONSUMED:
            raise PaperWriterBlocked("writer requires human decision already CONSUMED by execution bridge")
        if durable_operator.consumed_attempt_id != attempt_id or durable_operator.consumed_at is None:
            raise PaperWriterBlocked("human decision was not consumed by this exact attempt")

        if execution_stage.package_hash != prepared_package.package_hash:
            raise PaperWriterBlocked("execution bridge package hash mismatch")
        if execution_stage.operator_decision_hash != operator_decision.decision_hash:
            raise PaperWriterBlocked("execution bridge human decision hash mismatch")
        if execution_stage.attempt_id != attempt_id:
            raise PaperWriterBlocked("execution bridge attempt_id mismatch")
        if execution_stage.handoff != external_handoff:
            raise PaperWriterBlocked("execution bridge handoff mismatch")
        if execution_stage.order.order_id != prepared_package.order_id:
            raise PaperWriterBlocked("execution bridge order mismatch")

        state = submission_registry.get(expected_bracket.order_id)
        binding = submission_registry.get_binding(expected_bracket.order_id)
        if state.status is not PaperSubmissionStatus.PREPARED:
            raise PaperWriterBlocked(
                "writer may start only from PREPARED; UNKNOWN/ACKNOWLEDGED are reconciliation-only"
            )
        if state.attempt_count != 0:
            raise PaperWriterBlocked("writer refuses any previously attempted submission")
        if binding.client_order_id != expected_bracket.client_order_id:
            raise PaperWriterBlocked("frozen submission client_order_id mismatch")
        if binding.order_payload_hash != expected_bracket.payload_hash:
            raise PaperWriterBlocked("frozen submission payload hash mismatch")
        if binding.account_attestation_fingerprint != account_attestation.fingerprint:
            raise PaperWriterBlocked("frozen submission account attestation mismatch")
        if approval.binding_hash != binding.fingerprint:
            raise PaperWriterBlocked("canary approval is not bound to frozen submission")
        if approval.account_attestation_fingerprint != account_attestation.fingerprint:
            raise PaperWriterBlocked("canary approval account attestation mismatch")
        if prepared_package.submission_binding_hash != binding.fingerprint:
            raise PaperWriterBlocked("prepared package frozen submission binding mismatch")
        if prepared_package.intent_fingerprint != binding.intent_fingerprint:
            raise PaperWriterBlocked("prepared package intent fingerprint mismatch")
        if prepared_package.risk_decision_id != binding.risk_decision_id:
            raise PaperWriterBlocked("prepared package risk decision mismatch")

        if external_handoff.handoff_id != approval.approval_hash:
            raise PaperWriterBlocked("OMS external handoff is not bound to canary approval")
        if external_handoff.order_id != binding.order_id:
            raise PaperWriterBlocked("OMS external handoff order_id mismatch")
        if external_handoff.intent_fingerprint != binding.intent_fingerprint:
            raise PaperWriterBlocked("OMS external handoff intent fingerprint mismatch")
        if external_handoff.risk_decision_id != binding.risk_decision_id:
            raise PaperWriterBlocked("OMS external handoff risk decision mismatch")
        if external_handoff.authorized_at < approval.issued_at or external_handoff.authorized_at >= approval.expires_at:
            raise PaperWriterBlocked("OMS external handoff is outside canary approval window")
        if durable_operator.consumed_at > external_handoff.authorized_at:
            raise PaperWriterBlocked("OMS handoff predates durable human decision consumption")
        if external_handoff.safety_state_version != prepared_package.risk_decision_safety_state_version:
            raise PaperWriterBlocked("OMS handoff Safety version differs from human-reviewed package")
        if external_handoff.market_fingerprint != prepared_package.market_fingerprint:
            raise PaperWriterBlocked("OMS handoff market differs from human-reviewed package")
        if external_handoff.decision_valid_until != prepared_package.risk_decision_valid_until:
            raise PaperWriterBlocked("OMS handoff expiry differs from human-reviewed package")
        if now > external_handoff.decision_valid_until:
            raise PaperWriterBlocked("OMS external handoff RiskDecision has expired")
        try:
            staged_order = oms.verify_external_submission_handoff(external_handoff)
        except Exception as exc:  # OMS/ledger/order evidence must fail closed
            raise PaperWriterBlocked("durable OMS external handoff verification failed") from exc
        if staged_order.order_id != binding.order_id:
            raise PaperWriterBlocked("verified OMS external handoff order mismatch")

        permit = permit_registry.get(approval.approval_hash)
        if permit.status is PaperCanaryPermitStatus.CONSUMED:
            # The only resumable consumed-permit state is PREPARED + zero submit
            # attempts + the exact same attempt_id. By construction the writer
            # persists UNKNOWN before it can construct/execute the POST, so a
            # PREPARED submission proves no external write could have happened.
            if permit.attempt_id != attempt_id:
                raise PaperWriterBlocked(
                    "canary permit is consumed by another attempt; reconciliation/manual recovery only"
                )
            if permit.consumed_at is None:
                raise PaperWriterBlocked("consumed canary permit is missing consumed_at")
        elif permit.status is not PaperCanaryPermitStatus.ISSUED:
            raise PaperWriterBlocked("canary permit state is not resumable")
        if (
            permit.order_id != binding.order_id
            or permit.client_order_id != binding.client_order_id
            or permit.binding_hash != binding.fingerprint
        ):
            raise PaperWriterBlocked("durable canary permit does not match frozen submission")
        if (
            prepared_package.permit_event_hash
            != permit_registry.get_issued_event_hash(approval.approval_hash)
        ):
            raise PaperWriterBlocked("prepared package permit issuance evidence mismatch")

        try:
            pre_consume_guard = final_guard.authorize(
                approval=approval,
                expected_bracket=expected_bracket,
                submission_registry=submission_registry,
                now=now,
                phase=PaperFinalWritePhase.PRE_CONSUME,
            )
        except PaperFinalWriteBlocked as exc:
            raise PaperWriterBlocked(f"final PRE_CONSUME guard rejected: {exc}") from exc
        if pre_consume_guard.safety_state_version != external_handoff.safety_state_version:
            raise PaperWriterBlocked("Safety version changed after OMS external handoff")

        # Crash-safety order is deliberate:
        # 1) consume permit; a crash now leaves PREPARED + consumed permit, so only
        #    the SAME attempt may resume and no external request has happened.
        # 2) persist UNKNOWN; any crash after this point makes all future writer
        #    calls refuse POST and route to reconciliation only.
        permit_registry.consume(approval=approval, attempt_id=attempt_id, now=now)
        unknown = submission_registry.mark_submit_attempt_unknown(
            order_id=binding.order_id,
            attempt_id=attempt_id,
            now=now,
        )
        if unknown.status is not PaperSubmissionStatus.UNKNOWN:
            raise PaperWriterBlocked("submission failed to persist UNKNOWN before PAPER POST")

        request = AlpacaPaperWriteRequest(
            method="POST",
            url=f"{self._config.base_url}{PAPER_ORDER_PATH}",
            timeout_seconds=self._config.timeout_seconds,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "AUTO-TRADE-R6/0.28R",
                "APCA-API-KEY-ID": credentials.key_id,
                "APCA-API-SECRET-KEY": credentials.secret_key,
            },
            body=expected_bracket.payload_json.encode("utf-8"),
        )
        _validate_write_request(request)

        try:
            pre_io_guard = final_guard.authorize(
                approval=approval,
                expected_bracket=expected_bracket,
                submission_registry=submission_registry,
                now=now,
                phase=PaperFinalWritePhase.PRE_IO,
                expected_attempt_id=attempt_id,
                previous_attestation=pre_consume_guard,
            )
        except PaperFinalWriteBlocked as exc:
            # UNKNOWN is intentionally retained. No POST occurred; only reconciliation
            # may resolve whether any external order exists.
            raise PaperWriterBlocked(f"final PRE_IO guard rejected: {exc}") from exc

        # Exactly one transport invocation. There is intentionally no retry loop.
        response = self._transport.write(request)
        try:
            _validate_final_url(response.final_url)
            request_id = _response_request_id(response)
            payload = _strict_json_object(response.body)
            broker_order_id: str | None = None
            provisionally_accepted = 200 <= response.status_code < 300
            if provisionally_accepted:
                client_order_id = _required_str(payload, "client_order_id")
                if client_order_id != binding.client_order_id:
                    raise PaperWriterAmbiguous(
                        "PAPER POST response client_order_id mismatch; reconcile UNKNOWN"
                    )
                broker_order_id = _required_str(payload, "id")
            elif not isinstance(payload.get("message"), str):
                raise PaperWriterAmbiguous(
                    "non-2xx PAPER response lacks explicit JSON error; reconcile UNKNOWN"
                )
        except PaperWriterAmbiguous:
            raise
        except Exception as exc:
            raise PaperWriterAmbiguous(
                "PAPER POST response is not authoritative; reconcile UNKNOWN"
            ) from exc

        return PaperSubmitAttemptResult(
            order_id=binding.order_id,
            client_order_id=binding.client_order_id,
            attempt_id=attempt_id,
            http_status=response.status_code,
            request_id=request_id,
            broker_order_id=broker_order_id,
            response_hash=sha256(response.body).hexdigest(),
            provisionally_accepted=provisionally_accepted,
            durable_status=PaperSubmissionStatus.UNKNOWN,
            reconciliation_required=True,
            pre_consume_guard_hash=pre_consume_guard.attestation_hash,
            pre_io_guard_hash=pre_io_guard.attestation_hash,
            prepared_package_hash=prepared_package.package_hash,
            operator_decision_hash=operator_decision.decision_hash,
        )


def _validate_writer_base_url(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != ALPACA_PAPER_TRADING_HOST
        or parsed.port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise PaperWriterPolicyError("writer base URL must be exact PAPER Trading API origin")
    if parsed.hostname == ALPACA_LIVE_TRADING_HOST:
        raise PaperWriterPolicyError("LIVE Trading API host is forbidden")


def _validate_write_request(request: AlpacaPaperWriteRequest) -> None:
    if request.method != "POST":
        raise PaperWriterPolicyError("PAPER writer request must be exactly POST")
    if not 0 < request.timeout_seconds <= 15:
        raise PaperWriterPolicyError("PAPER writer timeout is invalid")
    parsed = urlsplit(request.url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != ALPACA_PAPER_TRADING_HOST
        or parsed.port not in (None, 443)
        or parsed.path != PAPER_ORDER_PATH
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise PaperWriterPolicyError("PAPER writer URL is not exact /v2/orders authority")
    normalized = {key.lower(): value for key, value in request.headers.items()}
    expected_headers = {
        "accept",
        "content-type",
        "user-agent",
        "apca-api-key-id",
        "apca-api-secret-key",
    }
    if set(normalized) != expected_headers:
        raise PaperWriterPolicyError("PAPER writer headers must match exact allowlist")
    if normalized["accept"] != "application/json" or normalized["content-type"] != "application/json":
        raise PaperWriterPolicyError("PAPER writer JSON headers are not canonical")
    if normalized["user-agent"] != "AUTO-TRADE-R6/0.28R":
        raise PaperWriterPolicyError("PAPER writer User-Agent is not canonical")
    for key in ("apca-api-key-id", "apca-api-secret-key"):
        value = normalized[key]
        if not value or value != value.strip() or any(ord(char) < 33 or ord(char) == 127 for char in value):
            raise PaperWriterPolicyError("PAPER writer credentials are malformed")
    try:
        decoded = request.body.decode("utf-8", errors="strict")
        payload = json.loads(decoded, parse_constant=lambda token: _raise_json_constant(token))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PaperWriterPolicyError("PAPER writer body must be strict UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("order_class") != "bracket":
        raise PaperWriterPolicyError("PAPER writer body must be canonical bracket order")


def _validate_final_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != ALPACA_PAPER_TRADING_HOST
        or parsed.port not in (None, 443)
        or parsed.path != PAPER_ORDER_PATH
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise PaperWriterAmbiguous("PAPER POST final URL changed authority; reconcile UNKNOWN")


def _response_request_id(response: AlpacaPaperWriteResponse) -> str:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise PaperWriterAmbiguous("PAPER POST response must be application/json")
    request_id = response.headers.get("x-request-id", "").strip()
    if not request_id or len(request_id) > 256 or any(ord(char) < 33 or ord(char) == 127 for char in request_id):
        raise PaperWriterAmbiguous("PAPER POST response lacks valid X-Request-ID")
    return request_id


def _strict_json_object(raw: bytes) -> Mapping[str, object]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, parse_constant=lambda token: _raise_json_constant(token))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PaperWriterAmbiguous("PAPER POST response is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PaperWriterAmbiguous("PAPER POST response root must be object")
    return value


def _raise_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PaperWriterAmbiguous(f"PAPER POST response field {key} is required")
    return value
