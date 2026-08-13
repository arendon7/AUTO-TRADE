from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from .alpaca_paper_asset import AlpacaPaperEquityAssetAttestation
from .alpaca_paper_operational import (
    PaperOperationalConflict,
    PaperOperationalIntegrityError,
    PaperOperationalWorkspace,
    _read_json_object,
    _write_json_idempotent,
)


ARTIFACT_NAME = "asset_attestation.json"


class PaperAssetEvidenceError(RuntimeError):
    pass


class PaperAssetEvidenceStore:
    def __init__(self, workspace: PaperOperationalWorkspace) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        self._workspace = workspace

    @property
    def path(self) -> Path:
        return self._workspace.root / ARTIFACT_NAME

    def write(self, attestation: AlpacaPaperEquityAssetAttestation) -> Path:
        if not isinstance(attestation, AlpacaPaperEquityAssetAttestation):
            raise TypeError("AlpacaPaperEquityAssetAttestation is required")
        account = self._read_bound_account_evidence()
        if attestation.account_attestation_fingerprint != account.get(
            "attestation_fingerprint"
        ):
            raise PaperAssetEvidenceError(
                "asset evidence does not bind the persisted account attestation"
            )
        if attestation.credential_reference != account.get("credential_reference"):
            raise PaperAssetEvidenceError(
                "asset evidence does not bind the persisted PAPER credential reference"
            )
        payload = {
            "schema_version": 1,
            "environment": "PAPER",
            **attestation.to_dict(),
            "attestation_fingerprint": attestation.fingerprint,
            "network_method": "GET",
            "credentials_persisted": False,
            "broker_mutation_performed": False,
            "execution_authorized": False,
            "capital_authority": "NONE",
            "profitability_claim": False,
            "live_trading": "BLOCKED",
        }
        try:
            _write_json_idempotent(self.path, payload)
        except (PaperOperationalIntegrityError, PaperOperationalConflict) as exc:
            raise PaperAssetEvidenceError("cannot persist PAPER asset evidence") from exc
        return self.path

    def read(self) -> AlpacaPaperEquityAssetAttestation:
        try:
            raw = _read_json_object(self.path)
        except PaperOperationalIntegrityError as exc:
            raise PaperAssetEvidenceError("cannot read PAPER asset evidence") from exc
        _validate_envelope(raw)
        try:
            attributes = raw.get("attributes")
            if not isinstance(attributes, list) or any(
                not isinstance(value, str) for value in attributes
            ):
                raise ValueError("attributes must be string array")
            attestation = AlpacaPaperEquityAssetAttestation(
                symbol=_string(raw, "symbol"),
                asset_id=_string(raw, "asset_id"),
                asset_class=_string(raw, "asset_class"),
                exchange=_string(raw, "exchange"),
                status=_string(raw, "status"),
                tradable=_boolean(raw, "tradable"),
                fractionable=_boolean(raw, "fractionable"),
                min_order_size=_decimal(raw, "min_order_size"),
                min_trade_increment=_decimal(raw, "min_trade_increment"),
                price_increment=_decimal(raw, "price_increment"),
                constraint_source=_string(raw, "constraint_source"),
                attributes=tuple(attributes),
                account_attestation_fingerprint=_string(
                    raw, "account_attestation_fingerprint"
                ),
                credential_reference=_string(raw, "credential_reference"),
                observed_at=_datetime(raw, "observed_at"),
                request_id=_string(raw, "request_id"),
                response_sha256=_string(raw, "response_sha256"),
                source_host=_string(raw, "source_host"),
                source_path=_string(raw, "source_path"),
            )
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise PaperAssetEvidenceError("persisted PAPER asset evidence is invalid") from exc
        if raw.get("whole_share_canary_supported") is not True:
            raise PaperAssetEvidenceError("asset evidence must preserve whole-share canary support")
        if attestation.fingerprint != raw.get("attestation_fingerprint"):
            raise PaperAssetEvidenceError("PAPER asset evidence fingerprint mismatch")

        account = self._read_bound_account_evidence()
        if attestation.account_attestation_fingerprint != account.get(
            "attestation_fingerprint"
        ):
            raise PaperAssetEvidenceError(
                "asset evidence no longer matches persisted account evidence"
            )
        if attestation.credential_reference != account.get("credential_reference"):
            raise PaperAssetEvidenceError(
                "asset evidence no longer matches persisted credential reference"
            )
        return attestation

    def _read_bound_account_evidence(self) -> dict[str, object]:
        try:
            raw = _read_json_object(self._workspace.account_attestation_path)
        except PaperOperationalIntegrityError as exc:
            raise PaperAssetEvidenceError(
                "persisted PAPER account attestation is required before asset evidence"
            ) from exc
        if raw.get("environment") != "PAPER":
            raise PaperAssetEvidenceError("persisted account evidence is not PAPER")
        if raw.get("credentials_persisted") is not False:
            raise PaperAssetEvidenceError("persisted account evidence cannot contain credentials")
        fingerprint = raw.get("attestation_fingerprint")
        credential_reference = raw.get("credential_reference")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise PaperAssetEvidenceError("persisted account attestation fingerprint is invalid")
        if not isinstance(credential_reference, str) or len(credential_reference) != 64:
            raise PaperAssetEvidenceError("persisted account credential reference is invalid")
        return raw


def _validate_envelope(raw: Mapping[str, object]) -> None:
    if raw.get("schema_version") != 1 or raw.get("environment") != "PAPER":
        raise PaperAssetEvidenceError("PAPER asset evidence envelope is invalid")
    for key, expected in (
        ("network_method", "GET"),
        ("credentials_persisted", False),
        ("broker_mutation_performed", False),
        ("execution_authorized", False),
        ("capital_authority", "NONE"),
        ("profitability_claim", False),
        ("live_trading", "BLOCKED"),
    ):
        if raw.get(key) != expected:
            raise PaperAssetEvidenceError(f"unsafe PAPER asset evidence field: {key}")


def _string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be non-empty string")
    return value


def _boolean(raw: Mapping[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be boolean")
    return value


def _decimal(raw: Mapping[str, object], key: str) -> Decimal:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be decimal string")
    return Decimal(value)


def _datetime(raw: Mapping[str, object], key: str) -> datetime:
    value = _string(raw, key)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{key} must be timezone-aware")
    return parsed


__all__ = ["ARTIFACT_NAME", "PaperAssetEvidenceError", "PaperAssetEvidenceStore"]
