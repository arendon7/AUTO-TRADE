from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping

from .alpaca_paper_bracket import (
    AlpacaEquityBracketRequest,
    AlpacaNestedBracketAttestation,
)
from .alpaca_paper_canary_coordinator import PreparedPaperCanaryPackage
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation
from .alpaca_paper_operator_decision import PaperOperatorDecisionContext


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PaperOperationalError(RuntimeError):
    pass


class PaperOperationalConflict(PaperOperationalError):
    pass


class PaperOperationalIntegrityError(PaperOperationalError):
    pass


@dataclass(frozen=True, slots=True)
class PaperOperationalWorkspace:
    root: Path

    def __post_init__(self) -> None:
        if not isinstance(self.root, Path):
            raise TypeError("workspace root must be pathlib.Path")

    @classmethod
    def initialize(cls, root: Path) -> "PaperOperationalWorkspace":
        if not isinstance(root, Path):
            raise TypeError("workspace root must be pathlib.Path")
        if root.exists() and root.is_symlink():
            raise PaperOperationalIntegrityError("workspace root cannot be a symlink")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not root.is_dir():
            raise PaperOperationalIntegrityError("workspace root must be a directory")
        try:
            root.chmod(0o700)
        except OSError as exc:
            raise PaperOperationalIntegrityError("cannot restrict workspace permissions") from exc
        return cls(root=root.resolve())

    @property
    def account_attestation_path(self) -> Path:
        return self.root / "account_attestation.json"

    @property
    def prepared_package_path(self) -> Path:
        return self.root / "prepared_package.json"

    @property
    def expected_bracket_path(self) -> Path:
        return self.root / "expected_bracket.json"

    @property
    def operator_context_path(self) -> Path:
        return self.root / "operator_context.json"

    @property
    def bracket_attestation_path(self) -> Path:
        return self.root / "bracket_attestation.json"

    @property
    def evidence_manifest_path(self) -> Path:
        return self.root / "evidence_manifest.json"

    @property
    def qualification_report_path(self) -> Path:
        return self.root / "qualification_report.json"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def core_db_path(self) -> Path:
        return self.root / "core.sqlite3"

    @property
    def submission_db_path(self) -> Path:
        return self.root / "submission.sqlite3"

    @property
    def permit_db_path(self) -> Path:
        return self.root / "permit.sqlite3"

    @property
    def operator_db_path(self) -> Path:
        return self.root / "operator.sqlite3"

    @property
    def trade_updates_db_path(self) -> Path:
        return self.root / "trade_updates.sqlite3"

    def write_account_attestation(
        self,
        attestation: AlpacaPaperAccountAttestation,
    ) -> Path:
        if not isinstance(attestation, AlpacaPaperAccountAttestation):
            raise TypeError("PAPER account attestation is required")
        payload = account_attestation_payload(attestation)
        _write_json_idempotent(self.account_attestation_path, payload)
        return self.account_attestation_path

    def write_prepared_canary(
        self,
        package: PreparedPaperCanaryPackage,
        expected_bracket: AlpacaEquityBracketRequest,
    ) -> tuple[Path, Path, Path]:
        if not isinstance(package, PreparedPaperCanaryPackage):
            raise TypeError("PreparedPaperCanaryPackage is required")
        if not isinstance(expected_bracket, AlpacaEquityBracketRequest):
            raise TypeError("AlpacaEquityBracketRequest is required")
        if package.network_write_authorized is not False:
            raise PaperOperationalIntegrityError("prepared package cannot authorize network write")
        if package.next_action != "OPERATOR_DECISION_REQUIRED":
            raise PaperOperationalIntegrityError("prepared package must require operator decision")
        if expected_bracket.order_id != package.order_id:
            raise PaperOperationalIntegrityError("prepared package/bracket order_id mismatch")
        if expected_bracket.client_order_id != package.client_order_id:
            raise PaperOperationalIntegrityError("prepared package/bracket client_order_id mismatch")
        if expected_bracket.payload_hash != package.bracket_payload_hash:
            raise PaperOperationalIntegrityError("prepared package/bracket payload hash mismatch")
        if (
            expected_bracket.instrument_master_fingerprint
            != package.instrument_master_fingerprint
        ):
            raise PaperOperationalIntegrityError(
                "prepared package/bracket Instrument Master mismatch"
            )
        if not self.account_attestation_path.exists():
            raise PaperOperationalIntegrityError(
                "exact PAPER account attestation must be persisted before canary package"
            )
        account_payload = _read_json_object(self.account_attestation_path)
        if _string(account_payload, "environment") != "PAPER":
            raise PaperOperationalIntegrityError("workspace account evidence is not PAPER")
        if (
            _string(account_payload, "attestation_fingerprint")
            != package.account_attestation_fingerprint
        ):
            raise PaperOperationalIntegrityError(
                "prepared package account attestation does not match workspace evidence"
            )
        if account_payload.get("credentials_persisted") is not False:
            raise PaperOperationalIntegrityError(
                "workspace account evidence cannot claim persisted credentials"
            )

        package_payload = package.canonical_payload()
        context = PaperOperatorDecisionContext.from_prepared_package(package)
        context_payload = context.to_dict()
        bracket_payload = expected_bracket_payload(expected_bracket)
        _write_json_idempotent(self.prepared_package_path, package_payload)
        _write_json_idempotent(self.expected_bracket_path, bracket_payload)
        _write_json_idempotent(self.operator_context_path, context_payload)

        attestation_hash = _file_sha256(self.account_attestation_path)
        manifest = {
            "schema_version": 1,
            "environment": "PAPER",
            "prepared_package_hash": package.package_hash,
            "operator_preparation_hash": context.preparation_hash,
            "attempt_id": package.attempt_id,
            "files": {
                "account_attestation.json": attestation_hash,
                "prepared_package.json": _file_sha256(self.prepared_package_path),
                "expected_bracket.json": _file_sha256(self.expected_bracket_path),
                "operator_context.json": _file_sha256(self.operator_context_path),
            },
            "network_write_authorized": False,
            "next_action": "OPERATOR_DECISION_REQUIRED",
            "external_order_submitted": False,
            "live_trading": "BLOCKED",
        }
        _write_json_idempotent(self.manifest_path, manifest)
        return self.prepared_package_path, self.operator_context_path, self.manifest_path

    def write_bracket_attestation(
        self,
        attestation: AlpacaNestedBracketAttestation,
        *,
        expected_bracket: AlpacaEquityBracketRequest,
    ) -> Path:
        if not isinstance(attestation, AlpacaNestedBracketAttestation):
            raise TypeError("AlpacaNestedBracketAttestation is required")
        if not isinstance(expected_bracket, AlpacaEquityBracketRequest):
            raise TypeError("AlpacaEquityBracketRequest is required")
        if attestation.client_order_id != expected_bracket.client_order_id:
            raise PaperOperationalIntegrityError(
                "broker bracket attestation client_order_id mismatch"
            )
        payload = bracket_attestation_payload(attestation)
        _write_json_idempotent(self.bracket_attestation_path, payload)
        return self.bracket_attestation_path

    def write_qualification_report_payload(self, payload: Mapping[str, object]) -> Path:
        if not isinstance(payload, Mapping):
            raise TypeError("qualification payload must be a mapping")
        document = dict(payload)
        if document.get("capital_authority") != "NONE":
            raise PaperOperationalIntegrityError("qualification cannot grant capital authority")
        if document.get("external_paper_qualified") is not True:
            raise PaperOperationalIntegrityError("qualification status must be true")
        if document.get("live_trading") != "BLOCKED":
            raise PaperOperationalIntegrityError("qualification cannot unblock LIVE")
        if document.get("profitability_claim") is not False:
            raise PaperOperationalIntegrityError("qualification cannot claim profitability")
        report_hash = document.get("report_hash")
        if not isinstance(report_hash, str) or not _HASH_RE.fullmatch(report_hash):
            raise PaperOperationalIntegrityError("qualification report hash is invalid")
        _write_json_idempotent(self.qualification_report_path, document)
        return self.qualification_report_path

    def write_evidence_manifest(
        self,
        *,
        order_id: str,
        client_order_id: str,
        report_hash: str,
        submission_event_head_hash: str,
        trade_update_scope_hash: str,
        trade_update_head_hash: str,
    ) -> Path:
        for label, value in (
            ("report_hash", report_hash),
            ("submission_event_head_hash", submission_event_head_hash),
            ("trade_update_scope_hash", trade_update_scope_hash),
            ("trade_update_head_hash", trade_update_head_hash),
        ):
            if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                raise PaperOperationalIntegrityError(f"{label} is invalid")
        required = (
            self.manifest_path,
            self.expected_bracket_path,
            self.bracket_attestation_path,
            self.qualification_report_path,
        )
        if any(not path.is_file() or path.is_symlink() for path in required):
            raise PaperOperationalIntegrityError(
                "cannot finalize evidence manifest with missing or symlinked artifacts"
            )
        manifest = {
            "schema_version": 1,
            "environment": "PAPER",
            "order_id": order_id,
            "client_order_id": client_order_id,
            "report_hash": report_hash,
            "submission_event_head_hash": submission_event_head_hash,
            "trade_update_scope_hash": trade_update_scope_hash,
            "trade_update_head_hash": trade_update_head_hash,
            "files": {
                "manifest.json": _file_sha256(self.manifest_path),
                "expected_bracket.json": _file_sha256(self.expected_bracket_path),
                "bracket_attestation.json": _file_sha256(self.bracket_attestation_path),
                "qualification_report.json": _file_sha256(self.qualification_report_path),
            },
            "capital_authority": "NONE",
            "external_paper_evidence_complete": True,
            "external_order_submitted": True,
            "profitability_claim": False,
            "live_trading": "BLOCKED",
        }
        _write_json_idempotent(self.evidence_manifest_path, manifest)
        return self.evidence_manifest_path


def account_attestation_payload(attestation: AlpacaPaperAccountAttestation) -> dict[str, object]:
    return {
        "schema_version": 1,
        "environment": "PAPER",
        "account_id": attestation.account_id,
        "account_reference": attestation.account_reference,
        "credential_reference": attestation.credential_reference,
        "status": attestation.status,
        "currency": attestation.currency,
        "buying_power": str(attestation.buying_power),
        "portfolio_value": str(attestation.portfolio_value),
        "shorting_enabled": attestation.shorting_enabled,
        "attested_at": attestation.attested_at.isoformat(),
        "request_id": attestation.request_id,
        "source_host": attestation.source_host,
        "source_path": attestation.source_path,
        "attestation_fingerprint": attestation.fingerprint,
        "credentials_persisted": False,
        "external_order_submitted": False,
        "live_trading": "BLOCKED",
    }


def expected_bracket_payload(bracket: AlpacaEquityBracketRequest) -> dict[str, object]:
    if not isinstance(bracket, AlpacaEquityBracketRequest):
        raise TypeError("AlpacaEquityBracketRequest is required")
    return {
        "schema_version": 1,
        "environment": "PAPER",
        "order_id": bracket.order_id,
        "client_order_id": bracket.client_order_id,
        "asset_class": bracket.asset_class,
        "instrument_master_fingerprint": bracket.instrument_master_fingerprint,
        "canonical_payload": dict(bracket.canonical_payload),
        "payload_json": bracket.payload_json,
        "payload_hash": bracket.payload_hash,
        "network_write_authorized": False,
        "live_trading": "BLOCKED",
    }


def bracket_attestation_payload(
    attestation: AlpacaNestedBracketAttestation,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "environment": "PAPER",
        "parent_order_id": attestation.parent_order_id,
        "client_order_id": attestation.client_order_id,
        "take_profit_order_id": attestation.take_profit_order_id,
        "stop_loss_order_id": attestation.stop_loss_order_id,
        "request_id": attestation.request_id,
        "response_hash": attestation.response_hash,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


def read_prepared_package(path: Path) -> PreparedPaperCanaryPackage:
    raw = _read_json_object(path)
    try:
        package = PreparedPaperCanaryPackage(
            order_id=_string(raw, "order_id"),
            client_order_id=_string(raw, "client_order_id"),
            intent_fingerprint=_string(raw, "intent_fingerprint"),
            risk_decision_id=_string(raw, "risk_decision_id"),
            risk_decision_safety_state_version=_integer(
                raw, "risk_decision_safety_state_version"
            ),
            market_fingerprint=_string(raw, "market_fingerprint"),
            risk_decision_valid_until=_datetime(raw, "risk_decision_valid_until"),
            account_attestation_fingerprint=_string(
                raw, "account_attestation_fingerprint"
            ),
            submission_binding_hash=_string(raw, "submission_binding_hash"),
            submission_control_hash=_string(raw, "submission_control_hash"),
            submission_event_head_hash=_string(raw, "submission_event_head_hash"),
            bracket_payload_hash=_string(raw, "bracket_payload_hash"),
            instrument_master_fingerprint=_string(
                raw, "instrument_master_fingerprint"
            ),
            canary_approval_hash=_string(raw, "canary_approval_hash"),
            permit_event_hash=_string(raw, "permit_event_hash"),
            attempt_id=_string(raw, "attempt_id"),
            notional=_decimal(raw, "notional"),
            effective_notional_cap=_decimal(raw, "effective_notional_cap"),
            approval_issued_at=_datetime(raw, "approval_issued_at"),
            approval_expires_at=_datetime(raw, "approval_expires_at"),
            execution_deadline=_datetime(raw, "execution_deadline"),
            prepared_at=_datetime(raw, "prepared_at"),
            order_status=_string(raw, "order_status"),
            network_write_authorized=_boolean(raw, "network_write_authorized"),
            next_action=_string(raw, "next_action"),
            package_hash=_string(raw, "package_hash"),
        )
    except (TypeError, ValueError) as exc:
        raise PaperOperationalIntegrityError("prepared package artifact is invalid") from exc
    if package.canonical_payload() != raw:
        raise PaperOperationalIntegrityError("prepared package artifact is not canonical")
    return package


def read_expected_bracket(path: Path) -> AlpacaEquityBracketRequest:
    raw = _read_json_object(path)
    if raw.get("schema_version") != 1 or raw.get("environment") != "PAPER":
        raise PaperOperationalIntegrityError("expected bracket artifact header is invalid")
    if raw.get("network_write_authorized") is not False or raw.get("live_trading") != "BLOCKED":
        raise PaperOperationalIntegrityError("expected bracket artifact authority changed")
    canonical_payload = raw.get("canonical_payload")
    if not isinstance(canonical_payload, dict):
        raise PaperOperationalIntegrityError("expected bracket canonical payload is invalid")
    try:
        bracket = AlpacaEquityBracketRequest(
            order_id=_string(raw, "order_id"),
            client_order_id=_string(raw, "client_order_id"),
            asset_class=_string(raw, "asset_class"),
            instrument_master_fingerprint=_string(raw, "instrument_master_fingerprint"),
            canonical_payload=canonical_payload,
            payload_json=_string(raw, "payload_json"),
            payload_hash=_string(raw, "payload_hash"),
        )
    except (TypeError, ValueError) as exc:
        raise PaperOperationalIntegrityError("expected bracket artifact is invalid") from exc
    if expected_bracket_payload(bracket) != raw:
        raise PaperOperationalIntegrityError("expected bracket artifact is not canonical")
    return bracket


def read_bracket_attestation(path: Path) -> AlpacaNestedBracketAttestation:
    raw = _read_json_object(path)
    if raw.get("schema_version") != 1 or raw.get("environment") != "PAPER":
        raise PaperOperationalIntegrityError("bracket attestation artifact header is invalid")
    if raw.get("capital_authority") != "NONE" or raw.get("live_trading") != "BLOCKED":
        raise PaperOperationalIntegrityError("bracket attestation artifact authority changed")
    fields = (
        "parent_order_id",
        "client_order_id",
        "take_profit_order_id",
        "stop_loss_order_id",
        "request_id",
    )
    values = {field: _string(raw, field) for field in fields}
    if any(not value for value in values.values()):
        raise PaperOperationalIntegrityError("bracket attestation identifiers cannot be empty")
    response_hash = _string(raw, "response_hash")
    if not _HASH_RE.fullmatch(response_hash):
        raise PaperOperationalIntegrityError("bracket attestation response hash is invalid")
    attestation = AlpacaNestedBracketAttestation(
        parent_order_id=values["parent_order_id"],
        client_order_id=values["client_order_id"],
        take_profit_order_id=values["take_profit_order_id"],
        stop_loss_order_id=values["stop_loss_order_id"],
        request_id=values["request_id"],
        response_hash=response_hash,
    )
    if bracket_attestation_payload(attestation) != raw:
        raise PaperOperationalIntegrityError("bracket attestation artifact is not canonical")
    return attestation


def _write_json_idempotent(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists() and path.is_symlink():
        raise PaperOperationalIntegrityError(f"artifact path cannot be symlink: {path.name}")
    raw = (
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False, ensure_ascii=True)
        + "\n"
    ).encode("utf-8")
    if path.exists():
        existing = path.read_bytes()
        if existing != raw:
            raise PaperOperationalConflict(
                f"refusing to overwrite non-identical operational artifact: {path.name}"
            )
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        os.close(fd)
        temp_path.write_bytes(raw)
        sync_fd = os.open(temp_path, os.O_RDONLY)
        try:
            os.fsync(sync_fd)
        finally:
            os.close(sync_fd)
        temp_path.chmod(0o600)
        os.replace(temp_path, path)
        path.chmod(0o600)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _read_json_object(path: Path) -> dict[str, object]:
    if not isinstance(path, Path):
        raise TypeError("artifact path must be pathlib.Path")
    if path.is_symlink():
        raise PaperOperationalIntegrityError("artifact path cannot be symlink")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperOperationalIntegrityError("cannot read operational JSON artifact") from exc
    if not isinstance(raw, dict):
        raise PaperOperationalIntegrityError("operational artifact root must be an object")
    return raw


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be string")
    return value


def _integer(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be integer")
    return value


def _boolean(raw: Mapping[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be bool")
    return value


def _decimal(raw: Mapping[str, object], key: str) -> Decimal:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{key} is invalid decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{key} must be finite")
    return parsed


def _datetime(raw: Mapping[str, object], key: str) -> datetime:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{key} is invalid datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{key} must be timezone-aware")
    return parsed
