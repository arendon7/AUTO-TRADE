from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable, Mapping

from autotrade.brokers.alpaca_paper_bracket import AlpacaEquityBracketRequest
from autotrade.brokers.alpaca_paper_connectivity_prepare import _read_account
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalWorkspace,
    _read_json_object,
)
from autotrade.brokers.alpaca_paper_reconciliation import (
    AlpacaPaperBracketReconciler,
    PaperReconciliationOutcome,
)
from autotrade.brokers.alpaca_paper_reconciliation_gateway import (
    AlpacaPaperOrderLookupGateway,
)
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.brokers.alpaca_paper_writer import (
    PAPER_ORDER_PATH,
    AlpacaPaperWriteRequest,
    AlpacaPaperWriteTransport,
    AlpacaPaperWriterConfig,
    PaperWriterAmbiguous,
    PaperWriterDisabled,
    PaperWriterPolicyError,
    UrllibAlpacaPaperWriteTransport,
    _required_str,
    _response_request_id,
    _strict_json_object,
    _validate_final_url,
    _validate_write_request,
    _validate_writer_base_url,
)
from autotrade.connectivity_execution_freshness_binding import (
    ConnectivityBoundFinalFreshnessResult,
)
from autotrade.connectivity_workspace_stage import (
    ConnectivityWorkspaceStageResult,
    ConnectivityWorkspaceStagingBridge,
)
from autotrade.persistence import SQLiteRuntime, SQLiteSafetyStateStore


_PREPARATION = "connectivity_preparation.json"
_STAGING = "connectivity_staging.json"
_POST_OBSERVATION = "connectivity_post_observation.json"
_POST_AMBIGUITY = "connectivity_post_ambiguity.json"


class ConnectivityWorkspacePostError(RuntimeError):
    pass


class ConnectivityWorkspacePostBlocked(ConnectivityWorkspacePostError):
    pass


class ConnectivityWorkspacePostConflict(ConnectivityWorkspacePostError):
    pass


class ConnectivityWorkspacePostAmbiguous(ConnectivityWorkspacePostError):
    """One external POST may have happened; restart is reconciliation-only."""


@dataclass(frozen=True, slots=True)
class ConnectivityPostContext:
    account: AlpacaPaperAccountAttestation
    bracket: AlpacaEquityBracketRequest
    order_id: str
    client_order_id: str
    attempt_id: str
    preparation_hash: str


@dataclass(frozen=True, slots=True)
class ConnectivityPaperPostObservation:
    order_id: str
    client_order_id: str
    attempt_id: str
    http_status: int
    request_id: str
    broker_order_id_observed: str | None
    response_hash: str
    provisionally_accepted: bool

    def __post_init__(self) -> None:
        if not self.order_id or not self.client_order_id or not self.attempt_id:
            raise ValueError("connectivity POST identifiers are required")
        if isinstance(self.http_status, bool) or not isinstance(self.http_status, int) or not 100 <= self.http_status <= 599:
            raise ValueError("http_status is invalid")
        if not self.request_id:
            raise ValueError("request_id is required")
        if len(self.response_hash) != 64 or any(c not in "0123456789abcdef" for c in self.response_hash):
            raise ValueError("response_hash must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class ConnectivityWorkspacePostResult:
    stage: ConnectivityWorkspaceStageResult
    observation: ConnectivityPaperPostObservation
    artifact_path: Path


def load_connectivity_post_context(
    workspace: PaperOperationalWorkspace,
    *,
    credentials: AlpacaPaperCredentials,
    order_id: str,
    client_order_id: str,
    attempt_id: str,
    expected_submission_status: PaperSubmissionStatus,
) -> ConnectivityPostContext:
    """Reconstruct the immutable bracket/account from canonical connectivity evidence."""
    if not isinstance(workspace, PaperOperationalWorkspace):
        raise TypeError("PaperOperationalWorkspace is required")
    if not isinstance(credentials, AlpacaPaperCredentials):
        raise TypeError("AlpacaPaperCredentials is required")
    prep_path = workspace.root / _PREPARATION
    if not prep_path.is_file() or prep_path.is_symlink():
        raise ConnectivityWorkspacePostBlocked("canonical connectivity preparation artifact is required")
    raw = _read_json_object(prep_path)
    preparation_hash = _string(raw, "preparation_hash")
    without_hash = dict(raw)
    without_hash.pop("preparation_hash")
    if preparation_hash != _hash(without_hash):
        raise ConnectivityWorkspacePostConflict("connectivity preparation hash mismatch")
    _require_exact(
        raw,
        {
            "schema_version": 1,
            "environment": "PAPER",
            "purpose": "CONNECTIVITY_CANARY",
            "external_post_authorized": False,
            "external_order_submitted": False,
            "strategy_trading_authorized": False,
            "capital_authority": "NONE",
            "profitability_claim": False,
            "live_trading": "BLOCKED",
        },
        "preparation",
    )

    package = _object(raw, "standard_prepared_package")
    bracket_payload = _object(raw, "expected_bracket")
    _require_exact(
        package,
        {
            "order_id": order_id,
            "client_order_id": client_order_id,
            "attempt_id": attempt_id,
            "network_write_authorized": False,
            "next_action": "OPERATOR_DECISION_REQUIRED",
        },
        "prepared package",
    )
    payload_json = json.dumps(
        bracket_payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=True,
    )
    payload_hash = sha256(payload_json.encode("utf-8")).hexdigest()
    if raw.get("expected_bracket_payload_hash") != payload_hash or package.get("bracket_payload_hash") != payload_hash:
        raise ConnectivityWorkspacePostConflict("connectivity bracket payload binding changed")
    instrument_fp = package.get("instrument_master_fingerprint")
    if not isinstance(instrument_fp, str):
        raise ConnectivityWorkspacePostConflict("instrument master fingerprint is missing")
    try:
        bracket = AlpacaEquityBracketRequest(
            order_id=order_id,
            client_order_id=client_order_id,
            asset_class="us_equity",
            instrument_master_fingerprint=instrument_fp,
            canonical_payload=bracket_payload,
            payload_json=payload_json,
            payload_hash=payload_hash,
        )
    except (TypeError, ValueError) as exc:
        raise ConnectivityWorkspacePostConflict("connectivity expected bracket is invalid") from exc

    try:
        account = _read_account(workspace)
    except Exception as exc:
        raise ConnectivityWorkspacePostConflict("canonical account evidence is invalid") from exc
    if credentials.credential_reference != account.credential_reference:
        raise PaperWriterPolicyError("writer credentials do not match PAPER account attestation")

    if expected_submission_status not in {
        PaperSubmissionStatus.PREPARED,
        PaperSubmissionStatus.UNKNOWN,
    }:
        raise ValueError("connectivity post context supports PREPARED or UNKNOWN only")
    registry = SQLitePaperSubmissionRegistry(SQLiteRuntime(workspace.submission_db_path))
    state = registry.get(order_id)
    frozen = registry.get_binding(order_id)
    expected_attempts = 0 if expected_submission_status is PaperSubmissionStatus.PREPARED else 1
    if state.status is not expected_submission_status or state.attempt_count != expected_attempts:
        raise ConnectivityWorkspacePostBlocked(
            f"submission must be exact {expected_submission_status.value}/{expected_attempts}"
        )
    if (
        frozen.order_id != order_id
        or frozen.client_order_id != client_order_id
        or frozen.order_payload_hash != bracket.payload_hash
        or frozen.account_attestation_fingerprint != account.fingerprint
    ):
        raise ConnectivityWorkspacePostConflict("frozen submission binding changed")
    if package.get("submission_binding_hash") != frozen.fingerprint:
        raise ConnectivityWorkspacePostConflict("prepared package submission binding changed")
    if package.get("account_attestation_fingerprint") != account.fingerprint:
        raise ConnectivityWorkspacePostConflict("prepared package account binding changed")
    return ConnectivityPostContext(
        account=account,
        bracket=bracket,
        order_id=order_id,
        client_order_id=client_order_id,
        attempt_id=attempt_id,
        preparation_hash=preparation_hash,
    )


class ConnectivityWorkspaceOneShotExecutor:
    """Same-process UNKNOWN-before-POST executor; there is no POST resume API."""

    def __init__(
        self,
        *,
        workspace: PaperOperationalWorkspace,
        config: AlpacaPaperWriterConfig | None = None,
        transport: AlpacaPaperWriteTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        self._workspace = workspace
        self._config = config or AlpacaPaperWriterConfig()
        self._transport = transport or UrllibAlpacaPaperWriteTransport(
            max_response_bytes=self._config.max_response_bytes
        )
        self._clock = clock or _utc_now

    def execute_once(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        bound_result: ConnectivityBoundFinalFreshnessResult,
    ) -> ConnectivityWorkspacePostResult:
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise TypeError("AlpacaPaperCredentials is required")
        if not isinstance(bound_result, ConnectivityBoundFinalFreshnessResult):
            raise TypeError("ConnectivityBoundFinalFreshnessResult is required")
        for name in (_POST_OBSERVATION, _POST_AMBIGUITY):
            if (self._workspace.root / name).exists():
                raise ConnectivityWorkspacePostBlocked("POST evidence exists; reconciliation-only")

        before = _aware_utc(self._clock())
        binding = bound_result.binding
        if not binding.is_valid_at(before):
            raise ConnectivityWorkspacePostBlocked("execution/freshness binding expired before POST preflight")
        context = load_connectivity_post_context(
            self._workspace,
            credentials=credentials,
            order_id=binding.order_id,
            client_order_id=binding.client_order_id,
            attempt_id=binding.attempt_id,
            expected_submission_status=PaperSubmissionStatus.PREPARED,
        )
        request = self._prepare_request(credentials=credentials, bracket=context.bracket)

        # No network has happened before this one-way durable ambiguity barrier.
        stage = ConnectivityWorkspaceStagingBridge(self._workspace).stage(
            bound_result=bound_result,
            now=before,
        )
        _verify_stage(stage, bound_result)

        immediately_before_io = _aware_utc(self._clock())
        if not binding.is_valid_at(immediately_before_io) or not stage.handoff.is_valid_at(immediately_before_io):
            self._write_ambiguity(stage, context, invoked=False, reason="FRESHNESS_EXPIRED_BEFORE_IO")
            raise ConnectivityWorkspacePostBlocked(
                "freshness expired after UNKNOWN before POST; reconciliation-only"
            )
        try:
            _verify_final_safety(self._workspace, bound_result)
            _verify_durable_unknown(self._workspace, bound_result)
        except ConnectivityWorkspacePostError:
            self._write_ambiguity(stage, context, invoked=False, reason="FINAL_STATE_DRIFT_BEFORE_IO")
            raise

        try:
            response = self._transport.write(request)  # exactly one transport invocation
            observation = _observe_response(response, stage=stage, context=context)
        except Exception as exc:
            self._write_ambiguity(stage, context, invoked=True, reason="POST_RESULT_AMBIGUOUS")
            if isinstance(exc, ConnectivityWorkspacePostAmbiguous):
                raise
            if isinstance(exc, PaperWriterAmbiguous):
                raise ConnectivityWorkspacePostAmbiguous(
                    "PAPER POST transport result is ambiguous; reconciliation-only"
                ) from exc
            raise ConnectivityWorkspacePostAmbiguous(
                "PAPER POST failed after UNKNOWN; reconciliation-only"
            ) from exc

        path = self._workspace.root / _POST_OBSERVATION
        _write_json_exclusive(
            path,
            {
                "schema_version": 1,
                "environment": "PAPER",
                "purpose": "CONNECTIVITY_CANARY",
                "order_id": observation.order_id,
                "client_order_id": observation.client_order_id,
                "attempt_id": observation.attempt_id,
                "connectivity_preparation_hash": context.preparation_hash,
                "execution_freshness_binding_hash": binding.binding_hash,
                "handoff_hash": stage.handoff.handoff_hash,
                "http_status": observation.http_status,
                "request_id": observation.request_id,
                "broker_order_id_observed": observation.broker_order_id_observed,
                "response_hash": observation.response_hash,
                "provisionally_accepted": observation.provisionally_accepted,
                "external_post_attempted": True,
                "external_post_attempt_count": 1,
                "broker_order_existence": "UNRESOLVED",
                "submission_status": "UNKNOWN",
                "reconciliation_required": True,
                "blind_retry_allowed": False,
                "strategy_trading_authorized": False,
                "capital_authority": "NONE",
                "profitability_claim": False,
                "live_trading": "BLOCKED",
                "next_action": "GET_ONLY_RECONCILIATION_REQUIRED",
            },
        )
        return ConnectivityWorkspacePostResult(stage=stage, observation=observation, artifact_path=path)

    def _prepare_request(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        bracket: AlpacaEquityBracketRequest,
    ) -> AlpacaPaperWriteRequest:
        if not self._config.enabled:
            raise PaperWriterDisabled("external PAPER writer is disabled by default")
        _validate_writer_base_url(self._config.base_url)
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
            body=bracket.payload_json.encode("utf-8"),
        )
        _validate_write_request(request)
        return request

    def _write_ambiguity(
        self,
        stage: ConnectivityWorkspaceStageResult,
        context: ConnectivityPostContext,
        *,
        invoked: bool,
        reason: str,
    ) -> None:
        path = self._workspace.root / _POST_AMBIGUITY
        if path.exists():
            return
        try:
            _write_json_exclusive(
                path,
                {
                    "schema_version": 1,
                    "environment": "PAPER",
                    "purpose": "CONNECTIVITY_CANARY",
                    "order_id": stage.order.order_id,
                    "client_order_id": context.client_order_id,
                    "attempt_id": context.attempt_id,
                    "handoff_hash": stage.handoff.handoff_hash,
                    "transport_invoked": invoked,
                    "external_post_attempt_count_max": 1,
                    "result": "AMBIGUOUS",
                    "reason": reason,
                    "submission_status": "UNKNOWN",
                    "reconciliation_required": True,
                    "blind_retry_allowed": False,
                    "capital_authority": "NONE",
                    "live_trading": "BLOCKED",
                    "next_action": "GET_ONLY_RECONCILIATION_REQUIRED",
                },
            )
        except (ConnectivityWorkspacePostBlocked, OSError):
            pass


class ConnectivityWorkspaceReconciliationRuntime:
    """Restart-safe GET-only reconciliation; this class exposes no write transport."""

    def __init__(
        self,
        *,
        workspace: PaperOperationalWorkspace,
        lookup_gateway: AlpacaPaperOrderLookupGateway,
    ) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        if not isinstance(lookup_gateway, AlpacaPaperOrderLookupGateway):
            raise TypeError("AlpacaPaperOrderLookupGateway is required")
        self._workspace = workspace
        self._reconciler = AlpacaPaperBracketReconciler(lookup_gateway=lookup_gateway)

    def reconcile_once(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        now: datetime,
    ) -> PaperReconciliationOutcome:
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise TypeError("AlpacaPaperCredentials is required")
        instant = _aware_utc(now)
        raw = _read_json_object(self._workspace.root / _STAGING)
        _require_exact(
            raw,
            {
                "schema_version": 1,
                "environment": "PAPER",
                "purpose": "CONNECTIVITY_CANARY",
                "oms_status": "SUBMITTING",
                "submission_status": "UNKNOWN",
                "attempt_count": 1,
                "unknown_before_post_committed": True,
                "oms_staging_completed": True,
                "external_post_authorized": False,
                "external_order_submitted": False,
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
            },
            "staging",
        )
        order_id = _string(raw, "order_id")
        client_order_id = _string(raw, "client_order_id")
        attempt_id = _string(raw, "attempt_id")
        context = load_connectivity_post_context(
            self._workspace,
            credentials=credentials,
            order_id=order_id,
            client_order_id=client_order_id,
            attempt_id=attempt_id,
            expected_submission_status=PaperSubmissionStatus.UNKNOWN,
        )
        registry = SQLitePaperSubmissionRegistry(SQLiteRuntime(self._workspace.submission_db_path))
        return self._reconciler.reconcile(
            registry=registry,
            order_id=order_id,
            credentials=credentials,
            account_attestation=context.account,
            expected_bracket=context.bracket,
            now=instant,
        )


def _verify_stage(
    stage: ConnectivityWorkspaceStageResult,
    bound_result: ConnectivityBoundFinalFreshnessResult,
) -> None:
    binding = bound_result.binding
    if (
        stage.order.order_id != binding.order_id
        or stage.order.status.value != "SUBMITTING"
        or stage.submission.status is not PaperSubmissionStatus.UNKNOWN
        or stage.submission.attempt_count != 1
        or stage.submission.client_order_id != binding.client_order_id
        or stage.handoff.attempt_id != binding.attempt_id
        or stage.handoff.execution_freshness_binding_hash != binding.binding_hash
        or stage.submission.broker_order_id is not None
        or stage.submission.broker_client_order_id is not None
    ):
        raise ConnectivityWorkspacePostConflict("staging result does not match exact one-shot binding")


def _verify_final_safety(
    workspace: PaperOperationalWorkspace,
    bound_result: ConnectivityBoundFinalFreshnessResult,
) -> None:
    try:
        safety = SQLiteSafetyStateStore(SQLiteRuntime(workspace.core_db_path)).get()
    except Exception as exc:
        raise ConnectivityWorkspacePostConflict(
            "cannot re-read Safety immediately before POST; reconciliation-only"
        ) from exc
    if safety.version != bound_result.binding.safety_state_version:
        raise ConnectivityWorkspacePostBlocked(
            "Safety state version changed after staging; reconciliation-only"
        )
    if safety.kill_switch_active or safety.circuit_active:
        raise ConnectivityWorkspacePostBlocked(
            "Safety kill/circuit activated after staging; reconciliation-only"
        )


def _verify_durable_unknown(
    workspace: PaperOperationalWorkspace,
    bound_result: ConnectivityBoundFinalFreshnessResult,
) -> None:
    binding = bound_result.binding
    try:
        registry = SQLitePaperSubmissionRegistry(SQLiteRuntime(workspace.submission_db_path))
        state = registry.get(binding.order_id)
        frozen = registry.get_binding(binding.order_id)
    except Exception as exc:
        raise ConnectivityWorkspacePostConflict(
            "cannot re-read durable UNKNOWN immediately before POST; reconciliation-only"
        ) from exc
    if (
        state.status is not PaperSubmissionStatus.UNKNOWN
        or state.attempt_count != 1
        or state.client_order_id != binding.client_order_id
        or state.broker_order_id is not None
        or state.broker_client_order_id is not None
        or frozen.order_id != binding.order_id
        or frozen.client_order_id != binding.client_order_id
    ):
        raise ConnectivityWorkspacePostConflict(
            "durable submission changed after UNKNOWN staging; reconciliation-only"
        )


def _observe_response(response, *, stage, context) -> ConnectivityPaperPostObservation:
    try:
        _validate_final_url(response.final_url)
        request_id = _response_request_id(response)
        payload = _strict_json_object(response.body)
        broker_order_id: str | None = None
        accepted = 200 <= response.status_code < 300
        if accepted:
            if _required_str(payload, "client_order_id") != context.client_order_id:
                raise ConnectivityWorkspacePostAmbiguous("PAPER POST client_order_id mismatch")
            broker_order_id = _required_str(payload, "id")
        elif not isinstance(payload.get("message"), str):
            raise ConnectivityWorkspacePostAmbiguous("non-2xx response lacks explicit JSON error")
    except ConnectivityWorkspacePostAmbiguous:
        raise
    except Exception as exc:
        raise ConnectivityWorkspacePostAmbiguous("PAPER POST response is not authoritative") from exc
    return ConnectivityPaperPostObservation(
        order_id=stage.order.order_id,
        client_order_id=context.client_order_id,
        attempt_id=context.attempt_id,
        http_status=int(response.status_code),
        request_id=request_id,
        broker_order_id_observed=broker_order_id,
        response_hash=sha256(response.body).hexdigest(),
        provisionally_accepted=accepted,
    )


def _require_exact(raw: Mapping[str, object], expected: Mapping[str, object], label: str) -> None:
    for key, value in expected.items():
        if raw.get(key) != value:
            raise ConnectivityWorkspacePostConflict(f"unsafe {label} field: {key}")


def _write_json_exclusive(path: Path, payload: Mapping[str, object]) -> None:
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
        path.chmod(0o600)
    except FileExistsError as exc:
        raise ConnectivityWorkspacePostBlocked(f"{path.name} already exists") from exc


def _hash(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ConnectivityWorkspacePostConflict(f"{key} must be non-empty string")
    return value


def _object(raw: Mapping[str, object], key: str) -> dict[str, object]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConnectivityWorkspacePostConflict(f"{key} must be object")
    return value


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock/now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "ConnectivityPaperPostObservation",
    "ConnectivityPostContext",
    "ConnectivityWorkspaceOneShotExecutor",
    "ConnectivityWorkspacePostAmbiguous",
    "ConnectivityWorkspacePostBlocked",
    "ConnectivityWorkspacePostConflict",
    "ConnectivityWorkspacePostError",
    "ConnectivityWorkspacePostResult",
    "ConnectivityWorkspaceReconciliationRuntime",
    "load_connectivity_post_context",
]
