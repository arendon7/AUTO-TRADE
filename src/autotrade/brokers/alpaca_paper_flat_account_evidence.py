from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Mapping

from .alpaca_paper_flat_account import PaperFlatAccountAttestation
from .alpaca_paper_operational import (
    PaperOperationalIntegrityError,
    PaperOperationalWorkspace,
    _read_json_object,
    _write_json_idempotent,
)


ARTIFACT_NAME = "flat_account_attestation.json"


class PaperFlatAccountEvidenceError(RuntimeError):
    pass


class PaperFlatAccountEvidenceStore:
    def __init__(self, workspace: PaperOperationalWorkspace) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        self._workspace = workspace

    @property
    def path(self) -> Path:
        return self._workspace.root / ARTIFACT_NAME

    def write(self, attestation: PaperFlatAccountAttestation) -> Path:
        if not isinstance(attestation, PaperFlatAccountAttestation):
            raise TypeError("PaperFlatAccountAttestation is required")
        payload = {
            "schema_version": 1,
            "environment": "PAPER",
            **attestation.to_dict(),
            "attestation_fingerprint": attestation.fingerprint,
            "credentials_persisted": False,
            "broker_mutation_performed": False,
            "execution_authorized": False,
            "capital_authority": "NONE",
            "production_status": "PAPER_ONLY_LIVE_BLOCKED",
        }
        try:
            _write_json_idempotent(self.path, payload)
        except PaperOperationalIntegrityError as exc:
            raise PaperFlatAccountEvidenceError("cannot persist flat-account evidence") from exc
        return self.path

    def read(self) -> PaperFlatAccountAttestation:
        try:
            raw = _read_json_object(self.path)
        except PaperOperationalIntegrityError as exc:
            raise PaperFlatAccountEvidenceError("cannot read flat-account evidence") from exc
        _validate_envelope(raw)
        try:
            attestation = PaperFlatAccountAttestation(
                account_attestation_fingerprint=_string(raw, "account_attestation_fingerprint"),
                credential_reference=_string(raw, "credential_reference"),
                position_count=_integer(raw, "position_count"),
                open_order_count=_integer(raw, "open_order_count"),
                positions_response_hash=_string(raw, "positions_response_hash"),
                orders_response_hash=_string(raw, "orders_response_hash"),
                positions_request_id=_string(raw, "positions_request_id"),
                orders_request_id=_string(raw, "orders_request_id"),
                attested_at=_datetime(raw, "attested_at"),
                source_host=_string(raw, "source_host"),
                positions_path=_string(raw, "positions_path"),
                orders_path=_string(raw, "orders_path"),
            )
        except (TypeError, ValueError) as exc:
            raise PaperFlatAccountEvidenceError("persisted flat-account evidence is invalid") from exc
        if attestation.to_dict().get("clean_for_first_canary") != raw.get("clean_for_first_canary"):
            raise PaperFlatAccountEvidenceError("flat-account clean-state evidence mismatch")
        if attestation.fingerprint != raw.get("attestation_fingerprint"):
            raise PaperFlatAccountEvidenceError("flat-account evidence fingerprint mismatch")
        return attestation


def _validate_envelope(raw: Mapping[str, object]) -> None:
    if raw.get("schema_version") != 1 or raw.get("environment") != "PAPER":
        raise PaperFlatAccountEvidenceError("flat-account evidence envelope is invalid")
    if raw.get("credentials_persisted") is not False:
        raise PaperFlatAccountEvidenceError("flat-account evidence cannot persist credentials")
    if raw.get("broker_mutation_performed") is not False:
        raise PaperFlatAccountEvidenceError("flat-account evidence may not claim broker mutation")
    if raw.get("execution_authorized") is not False:
        raise PaperFlatAccountEvidenceError("flat-account evidence may not authorize execution")
    if raw.get("capital_authority") != "NONE":
        raise PaperFlatAccountEvidenceError("flat-account evidence may not grant capital authority")
    if raw.get("production_status") != "PAPER_ONLY_LIVE_BLOCKED":
        raise PaperFlatAccountEvidenceError("flat-account evidence must preserve blocked production status")


def _string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be non-empty string")
    return value


def _integer(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be integer")
    return value


def _datetime(raw: Mapping[str, object], key: str) -> datetime:
    value = _string(raw, key)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{key} must be timezone-aware")
    return parsed
