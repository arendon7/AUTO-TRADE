from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
from pathlib import Path
import sqlite3
from typing import Mapping

from autotrade.domain import OrderStatus

from .alpaca_paper_core_provenance import (
    PaperCoreProvenanceError,
    PaperOperationalCoreProvenanceReader,
)
from .alpaca_paper_operational import (
    PaperOperationalIntegrityError,
    PaperOperationalWorkspace,
    _read_json_object,
    read_expected_bracket,
    read_prepared_package,
)
from .alpaca_paper_operational_prepare import verify_core_provenance_document
from .alpaca_paper_operator_decision import PaperOperatorDecisionContext
from .alpaca_paper_submission import PaperSubmissionStatus


class PaperReadinessError(RuntimeError):
    pass


class PaperReadinessIntegrityError(PaperReadinessError):
    pass


class PaperReadinessPhase(StrEnum):
    ACCOUNT_PREFLIGHT_REQUIRED = "ACCOUNT_PREFLIGHT_REQUIRED"
    PREPARATION_REQUIRED = "PREPARATION_REQUIRED"
    HUMAN_DECISION_REQUIRED = "HUMAN_DECISION_REQUIRED"
    EXPLICIT_EXECUTION_DECISION_REQUIRED = "EXPLICIT_EXECUTION_DECISION_REQUIRED"
    EXPLICIT_EXECUTION_RESUME_REQUIRED = "EXPLICIT_EXECUTION_RESUME_REQUIRED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    EVIDENCE_CAPTURE_REQUIRED = "EVIDENCE_CAPTURE_REQUIRED"
    QUALIFICATION_REVIEW_REQUIRED = "QUALIFICATION_REVIEW_REQUIRED"
    BLOCKED_INCONSISTENT_STATE = "BLOCKED_INCONSISTENT_STATE"


@dataclass(frozen=True, slots=True)
class PaperReadinessReport:
    workspace: str
    phase: PaperReadinessPhase
    next_action: str
    environment: str
    order_id: str | None
    client_order_id: str | None
    package_hash: str | None
    attempt_id: str | None
    oms_status: str | None
    submission_status: str | None
    submission_attempt_count: int | None
    operator_status: str | None
    operator_decision_valid: bool | None
    permit_status: str | None
    account_attested: bool
    core_provenance_verified: bool
    qualification_present: bool
    network_used: bool = False
    broker_write_performed: bool = False
    execution_authorized: bool = False
    capital_authority: str = "NONE"
    profitability_claim: bool = False
    production_status: str = "BLOCKED"

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace": self.workspace,
            "phase": self.phase.value,
            "next_action": self.next_action,
            "environment": self.environment,
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "package_hash": self.package_hash,
            "attempt_id": self.attempt_id,
            "oms_status": self.oms_status,
            "submission_status": self.submission_status,
            "submission_attempt_count": self.submission_attempt_count,
            "operator_status": self.operator_status,
            "operator_decision_valid": self.operator_decision_valid,
            "permit_status": self.permit_status,
            "account_attested": self.account_attested,
            "core_provenance_verified": self.core_provenance_verified,
            "qualification_present": self.qualification_present,
            "network_used": self.network_used,
            "broker_write_performed": self.broker_write_performed,
            "execution_authorized": self.execution_authorized,
            "capital_authority": self.capital_authority,
            "profitability_claim": self.profitability_claim,
            "live_trading": self.production_status,
        }


class PaperOperationalReadinessInspector:
    """Describe one local R6 workspace without creating authority or doing I/O.

    The inspector is deliberately non-authorizing. It never loads credentials,
    never opens a network transport, never instantiates writable SQLite stores,
    and opens existing SQLite files using ``mode=ro`` + ``PRAGMA query_only``.
    A report may identify that a separate explicit execution decision is the
    next step, but ``execution_authorized`` is always false.
    """

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise TypeError("readiness workspace root must be pathlib.Path")
        self._root = root

    def inspect(self, *, now: datetime) -> PaperReadinessReport:
        _require_aware(now)
        instant = now.astimezone(timezone.utc)
        root = self._root.expanduser().resolve()
        if not root.exists():
            raise PaperReadinessIntegrityError("workspace does not exist")
        if root.is_symlink() or not root.is_dir():
            raise PaperReadinessIntegrityError("workspace must be a regular directory")
        workspace = PaperOperationalWorkspace(root=root)

        if not workspace.account_attestation_path.is_file():
            return _report(
                root=root,
                phase=PaperReadinessPhase.ACCOUNT_PREFLIGHT_REQUIRED,
                next_action="RUN_SEPARATE_GET_ONLY_PAPER_ACCOUNT_PREFLIGHT",
                account_attested=False,
            )
        account = _read_json_object(workspace.account_attestation_path)
        _validate_account_evidence(account)

        if not workspace.prepared_package_path.is_file():
            return _report(
                root=root,
                phase=PaperReadinessPhase.PREPARATION_REQUIRED,
                next_action="RUN_SEPARATE_OFFLINE_CANARY_PREPARATION",
                account_attested=True,
            )

        package = read_prepared_package(workspace.prepared_package_path)
        bracket = read_expected_bracket(workspace.expected_bracket_path)
        if bracket.order_id != package.order_id:
            raise PaperReadinessIntegrityError("prepared bracket/order identity mismatch")
        if bracket.client_order_id != package.client_order_id:
            raise PaperReadinessIntegrityError("prepared bracket/client_order_id mismatch")
        if bracket.payload_hash != package.bracket_payload_hash:
            raise PaperReadinessIntegrityError("prepared bracket payload hash mismatch")

        context_path = workspace.operator_context_path
        if not context_path.is_file():
            raise PaperReadinessIntegrityError("prepared package is missing operator context")
        try:
            context = PaperOperatorDecisionContext.from_dict(_read_json_object(context_path))
        except (TypeError, ValueError) as exc:
            raise PaperReadinessIntegrityError("operator context is invalid") from exc
        if context != PaperOperatorDecisionContext.from_prepared_package(package):
            raise PaperReadinessIntegrityError("operator context does not match prepared package")

        oms_status = _read_oms_status(workspace.core_db_path, package.order_id)
        submission_status, attempt_count = _read_submission_state(
            workspace.submission_db_path,
            order_id=package.order_id,
            expected_client_order_id=package.client_order_id,
            expected_binding_hash=package.submission_binding_hash,
        )
        operator_status, operator_valid, consumed_attempt = _read_operator_state(
            workspace.operator_db_path,
            preparation_hash=context.preparation_hash,
            now=instant,
        )
        permit_status, permit_attempt = _read_permit_state(
            workspace.permit_db_path,
            approval_hash=package.canary_approval_hash,
            now=instant,
        )
        qualification_present = _qualification_present(workspace.qualification_report_path)

        if submission_status is PaperSubmissionStatus.UNKNOWN:
            return _package_report(
                root=root,
                package=package,
                phase=PaperReadinessPhase.RECONCILIATION_REQUIRED,
                next_action="RUN_SEPARATE_GET_ONLY_RECONCILIATION_AND_EVIDENCE_CAPTURE",
                oms_status=oms_status,
                submission_status=submission_status,
                attempt_count=attempt_count,
                operator_status=operator_status,
                operator_valid=operator_valid,
                permit_status=permit_status,
                qualification_present=qualification_present,
                core_provenance_verified=False,
            )

        if submission_status is PaperSubmissionStatus.ACKNOWLEDGED:
            phase = (
                PaperReadinessPhase.QUALIFICATION_REVIEW_REQUIRED
                if qualification_present
                else PaperReadinessPhase.EVIDENCE_CAPTURE_REQUIRED
            )
            next_action = (
                "REVIEW_EXISTING_PAPER_QUALIFICATION_EVIDENCE"
                if qualification_present
                else "CAPTURE_TRADE_UPDATES_AND_RUN_OFFLINE_QUALIFICATION"
            )
            return _package_report(
                root=root,
                package=package,
                phase=phase,
                next_action=next_action,
                oms_status=oms_status,
                submission_status=submission_status,
                attempt_count=attempt_count,
                operator_status=operator_status,
                operator_valid=operator_valid,
                permit_status=permit_status,
                qualification_present=qualification_present,
                core_provenance_verified=False,
            )

        if submission_status is not PaperSubmissionStatus.PREPARED or attempt_count != 0:
            return _package_report(
                root=root,
                package=package,
                phase=PaperReadinessPhase.BLOCKED_INCONSISTENT_STATE,
                next_action="STOP_AND_INVESTIGATE_DURABLE_SUBMISSION_STATE",
                oms_status=oms_status,
                submission_status=submission_status,
                attempt_count=attempt_count,
                operator_status=operator_status,
                operator_valid=operator_valid,
                permit_status=permit_status,
                qualification_present=qualification_present,
                core_provenance_verified=False,
            )

        if oms_status == OrderStatus.VALIDATED.value:
            provenance_verified = _verify_fresh_provenance(workspace, package, instant)
            if operator_status is None:
                return _package_report(
                    root=root,
                    package=package,
                    phase=PaperReadinessPhase.HUMAN_DECISION_REQUIRED,
                    next_action="RUN_SEPARATE_INTERACTIVE_HUMAN_OPERATOR_DECISION",
                    oms_status=oms_status,
                    submission_status=submission_status,
                    attempt_count=attempt_count,
                    operator_status=None,
                    operator_valid=None,
                    permit_status=permit_status,
                    qualification_present=qualification_present,
                    core_provenance_verified=provenance_verified,
                )
            if operator_status == "ISSUED" and operator_valid is True and permit_status == "ISSUED":
                return _package_report(
                    root=root,
                    package=package,
                    phase=PaperReadinessPhase.EXPLICIT_EXECUTION_DECISION_REQUIRED,
                    next_action="SEPARATE_EXPLICIT_OPERATOR_DECISION_REQUIRED_BEFORE_REAL_PAPER_EXECUTION",
                    oms_status=oms_status,
                    submission_status=submission_status,
                    attempt_count=attempt_count,
                    operator_status=operator_status,
                    operator_valid=True,
                    permit_status=permit_status,
                    qualification_present=qualification_present,
                    core_provenance_verified=provenance_verified,
                )
            return _package_report(
                root=root,
                package=package,
                phase=PaperReadinessPhase.BLOCKED_INCONSISTENT_STATE,
                next_action="STOP_AND_REFRESH_OR_REPREPARE_EXPIRED_OR_INCONSISTENT_AUTHORITY",
                oms_status=oms_status,
                submission_status=submission_status,
                attempt_count=attempt_count,
                operator_status=operator_status,
                operator_valid=operator_valid,
                permit_status=permit_status,
                qualification_present=qualification_present,
                core_provenance_verified=provenance_verified,
            )

        if oms_status == OrderStatus.SUBMITTING.value:
            if (
                operator_status == "CONSUMED"
                and consumed_attempt == package.attempt_id
                and permit_status in {"ISSUED", "CONSUMED"}
                and (permit_attempt is None or permit_attempt == package.attempt_id)
            ):
                return _package_report(
                    root=root,
                    package=package,
                    phase=PaperReadinessPhase.EXPLICIT_EXECUTION_RESUME_REQUIRED,
                    next_action="SEPARATE_EXPLICIT_SAME_ATTEMPT_RESUME_DECISION_REQUIRED",
                    oms_status=oms_status,
                    submission_status=submission_status,
                    attempt_count=attempt_count,
                    operator_status=operator_status,
                    operator_valid=operator_valid,
                    permit_status=permit_status,
                    qualification_present=qualification_present,
                    core_provenance_verified=False,
                )

        return _package_report(
            root=root,
            package=package,
            phase=PaperReadinessPhase.BLOCKED_INCONSISTENT_STATE,
            next_action="STOP_AND_INVESTIGATE_CROSS_STORE_STATE",
            oms_status=oms_status,
            submission_status=submission_status,
            attempt_count=attempt_count,
            operator_status=operator_status,
            operator_valid=operator_valid,
            permit_status=permit_status,
            qualification_present=qualification_present,
            core_provenance_verified=False,
        )


def _verify_fresh_provenance(
    workspace: PaperOperationalWorkspace,
    package,
    now: datetime,
) -> bool:
    try:
        observed = PaperOperationalCoreProvenanceReader(workspace).verify(now=now)
        verify_core_provenance_document(workspace, package=package, observed=observed)
    except (PaperCoreProvenanceError, PaperOperationalIntegrityError, OSError, ValueError) as exc:
        raise PaperReadinessIntegrityError("current same-workspace core provenance is invalid") from exc
    return True


def _validate_account_evidence(raw: Mapping[str, object]) -> None:
    if raw.get("environment") != "PAPER":
        raise PaperReadinessIntegrityError("account evidence environment is not PAPER")
    if raw.get("credentials_persisted") is not False:
        raise PaperReadinessIntegrityError("account evidence cannot persist credentials")
    fingerprint = raw.get("attestation_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise PaperReadinessIntegrityError("account attestation fingerprint is invalid")
    if raw.get("live_trading") != "BLOCKED":
        raise PaperReadinessIntegrityError("account evidence must keep LIVE blocked")


def _read_oms_status(path: Path, order_id: str) -> str:
    rows = _query_ro(
        path,
        "SELECT record_json FROM orders WHERE order_id = ?",
        (order_id,),
    )
    if len(rows) != 1:
        raise PaperReadinessIntegrityError("exact durable OMS order is missing or duplicated")
    try:
        payload = json.loads(str(rows[0]["record_json"]))
    except json.JSONDecodeError as exc:
        raise PaperReadinessIntegrityError("durable OMS order JSON is invalid") from exc
    if not isinstance(payload, dict) or payload.get("order_id") != order_id:
        raise PaperReadinessIntegrityError("durable OMS order identity mismatch")
    status = payload.get("status")
    if not isinstance(status, str):
        raise PaperReadinessIntegrityError("durable OMS order status is invalid")
    return status


def _read_submission_state(
    path: Path,
    *,
    order_id: str,
    expected_client_order_id: str,
    expected_binding_hash: str,
) -> tuple[PaperSubmissionStatus, int]:
    rows = _query_ro(
        path,
        "SELECT client_order_id, binding_hash, status, attempt_count FROM alpaca_paper_submission_control WHERE order_id = ?",
        (order_id,),
    )
    if len(rows) != 1:
        raise PaperReadinessIntegrityError("submission control state is missing or duplicated")
    row = rows[0]
    if str(row["client_order_id"]) != expected_client_order_id:
        raise PaperReadinessIntegrityError("submission client_order_id mismatch")
    if str(row["binding_hash"]) != expected_binding_hash:
        raise PaperReadinessIntegrityError("submission binding hash mismatch")
    try:
        status = PaperSubmissionStatus(str(row["status"]))
        attempt_count = int(row["attempt_count"])
    except (TypeError, ValueError) as exc:
        raise PaperReadinessIntegrityError("submission status/control fields are invalid") from exc
    if attempt_count < 0:
        raise PaperReadinessIntegrityError("submission attempt_count is invalid")
    return status, attempt_count


def _read_operator_state(
    path: Path,
    *,
    preparation_hash: str,
    now: datetime,
) -> tuple[str | None, bool | None, str | None]:
    if not path.is_file():
        return None, None, None
    rows = _query_ro(
        path,
        "SELECT event_type, occurred_at, payload_json FROM alpaca_paper_operator_decision_events WHERE preparation_hash = ? ORDER BY sequence",
        (preparation_hash,),
    )
    if not rows:
        return None, None, None
    if len(rows) not in {1, 2}:
        raise PaperReadinessIntegrityError("operator decision event cardinality is invalid")
    issued = rows[0]
    if str(issued["event_type"]) != "ISSUED":
        raise PaperReadinessIntegrityError("operator decision issuance event is missing")
    try:
        payload = json.loads(str(issued["payload_json"]))
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise PaperReadinessIntegrityError("operator decision issuance payload is invalid") from exc
    _require_aware(expires_at)
    valid = now < expires_at.astimezone(timezone.utc)
    if len(rows) == 1:
        return "ISSUED", valid, None
    consumed = rows[1]
    if str(consumed["event_type"]) != "CONSUMED":
        raise PaperReadinessIntegrityError("operator decision second event is invalid")
    try:
        consumed_payload = json.loads(str(consumed["payload_json"]))
        attempt_id = str(consumed_payload["attempt_id"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PaperReadinessIntegrityError("operator decision consumption payload is invalid") from exc
    return "CONSUMED", valid, attempt_id


def _read_permit_state(
    path: Path,
    *,
    approval_hash: str,
    now: datetime,
) -> tuple[str | None, str | None]:
    if not path.is_file():
        return None, None
    rows = _query_ro(
        path,
        "SELECT event_type, payload_json FROM alpaca_paper_canary_permit_events WHERE approval_hash = ? ORDER BY sequence",
        (approval_hash,),
    )
    if not rows:
        return None, None
    if len(rows) not in {1, 2}:
        raise PaperReadinessIntegrityError("canary permit event cardinality is invalid")
    if str(rows[0]["event_type"]) != "ISSUED":
        raise PaperReadinessIntegrityError("canary permit issuance event is missing")
    if len(rows) == 1:
        try:
            issued = json.loads(str(rows[0]["payload_json"]))
            expires_at = datetime.fromisoformat(str(issued["expires_at"]))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise PaperReadinessIntegrityError("canary permit issuance payload is invalid") from exc
        _require_aware(expires_at)
        if now >= expires_at.astimezone(timezone.utc):
            return "EXPIRED", None
        return "ISSUED", None
    if str(rows[1]["event_type"]) != "CONSUMED":
        raise PaperReadinessIntegrityError("canary permit second event is invalid")
    try:
        consumed = json.loads(str(rows[1]["payload_json"]))
        attempt_id = str(consumed["attempt_id"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PaperReadinessIntegrityError("canary permit consumption payload is invalid") from exc
    return "CONSUMED", attempt_id


def _qualification_present(path: Path) -> bool:
    if not path.is_file():
        return False
    raw = _read_json_object(path)
    if raw.get("live_trading") != "BLOCKED":
        raise PaperReadinessIntegrityError("qualification evidence must keep LIVE blocked")
    if raw.get("profitability_claim") is not False:
        raise PaperReadinessIntegrityError("qualification evidence cannot claim profitability")
    return True


def _query_ro(path: Path, sql: str, params: tuple[object, ...]) -> list[sqlite3.Row]:
    if not path.is_file() or path.is_symlink():
        raise PaperReadinessIntegrityError(f"required SQLite file is missing or unsafe: {path.name}")
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    except sqlite3.Error as exc:
        raise PaperReadinessIntegrityError(f"cannot open {path.name} read-only") from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        return list(conn.execute(sql, params).fetchall())
    except sqlite3.Error as exc:
        raise PaperReadinessIntegrityError(f"cannot inspect {path.name} read-only") from exc
    finally:
        conn.close()


def _report(
    *,
    root: Path,
    phase: PaperReadinessPhase,
    next_action: str,
    account_attested: bool,
) -> PaperReadinessReport:
    return PaperReadinessReport(
        workspace=str(root),
        phase=phase,
        next_action=next_action,
        environment="PAPER",
        order_id=None,
        client_order_id=None,
        package_hash=None,
        attempt_id=None,
        oms_status=None,
        submission_status=None,
        submission_attempt_count=None,
        operator_status=None,
        operator_decision_valid=None,
        permit_status=None,
        account_attested=account_attested,
        core_provenance_verified=False,
        qualification_present=False,
    )


def _package_report(
    *,
    root: Path,
    package,
    phase: PaperReadinessPhase,
    next_action: str,
    oms_status: str,
    submission_status: PaperSubmissionStatus,
    attempt_count: int,
    operator_status: str | None,
    operator_valid: bool | None,
    permit_status: str | None,
    qualification_present: bool,
    core_provenance_verified: bool,
) -> PaperReadinessReport:
    return PaperReadinessReport(
        workspace=str(root),
        phase=phase,
        next_action=next_action,
        environment="PAPER",
        order_id=package.order_id,
        client_order_id=package.client_order_id,
        package_hash=package.package_hash,
        attempt_id=package.attempt_id,
        oms_status=oms_status,
        submission_status=submission_status.value,
        submission_attempt_count=attempt_count,
        operator_status=operator_status,
        operator_decision_valid=operator_valid,
        permit_status=permit_status,
        account_attested=True,
        core_provenance_verified=core_provenance_verified,
        qualification_present=qualification_present,
    )


def _require_aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("readiness observation time must be timezone-aware")
