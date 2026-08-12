from __future__ import annotations

from datetime import datetime
from pathlib import Path

from autotrade.domain import market_fingerprint

from .alpaca_paper_flat_account_evidence import (
    PaperFlatAccountEvidenceError,
    PaperFlatAccountEvidenceStore,
)
from .alpaca_paper_market_evidence import (
    PaperMarketEvidenceError,
    PaperMarketEvidenceStore,
)
from .alpaca_paper_operational import (
    PaperOperationalWorkspace,
    _read_json_object,
    read_prepared_package,
)
from .alpaca_paper_readiness import (
    PaperOperationalReadinessInspector,
    PaperReadinessError,
    PaperReadinessIntegrityError,
    PaperReadinessPhase,
)


FLAT_ACCOUNT_PREFLIGHT_REQUIRED = "FLAT_ACCOUNT_PREFLIGHT_REQUIRED"
FLAT_ACCOUNT_NEXT_ACTION = "RUN_SEPARATE_GET_ONLY_FLAT_ACCOUNT_PREFLIGHT"
BLOCKED_EXISTING_PAPER_EXPOSURE = "BLOCKED_EXISTING_PAPER_EXPOSURE"
MARKET_DATA_PREFLIGHT_REQUIRED = "MARKET_DATA_PREFLIGHT_REQUIRED"
MARKET_DATA_NEXT_ACTION = "RUN_SEPARATE_GET_ONLY_IEX_MARKET_PREFLIGHT"


def inspect_market_aware_readiness(*, root: Path, now: datetime) -> dict[str, object]:
    """Project base R6 readiness plus flat-account and equity-market evidence.

    This helper is read-only and non-authorizing. The base readiness inspector
    remains the durable state authority. For the first external canary this
    projection inserts two additional GET-only gates before offline preparation:
    exact PAPER account flatness, then fresh IEX equity market evidence.
    """

    if not isinstance(root, Path):
        raise TypeError("readiness workspace root must be pathlib.Path")
    report = PaperOperationalReadinessInspector(root).inspect(now=now)
    payload = report.to_dict()
    workspace = PaperOperationalWorkspace(root=root.expanduser().resolve())
    flat_store = PaperFlatAccountEvidenceStore(workspace)
    market_path = workspace.root / "market_snapshot.json"

    payload["flat_account_evidence_present"] = flat_store.path.is_file()
    payload["flat_account_clean_for_first_canary"] = None
    payload["flat_account_position_count"] = None
    payload["flat_account_open_order_count"] = None

    if report.phase is PaperReadinessPhase.PREPARATION_REQUIRED:
        if not flat_store.path.is_file():
            payload["market_evidence_present"] = market_path.is_file()
            payload["market_symbol"] = None
            payload["market_fingerprint"] = None
            payload["phase"] = FLAT_ACCOUNT_PREFLIGHT_REQUIRED
            payload["next_action"] = FLAT_ACCOUNT_NEXT_ACTION
            return payload
        try:
            flat = flat_store.read()
        except PaperFlatAccountEvidenceError as exc:
            raise PaperReadinessIntegrityError("persisted flat-account evidence is invalid") from exc
        account = _read_json_object(workspace.account_attestation_path)
        if flat.account_attestation_fingerprint != account.get("attestation_fingerprint"):
            raise PaperReadinessIntegrityError(
                "flat-account evidence does not bind the current account attestation"
            )
        if flat.credential_reference != account.get("credential_reference"):
            raise PaperReadinessIntegrityError(
                "flat-account evidence does not bind the current PAPER credential reference"
            )
        payload["flat_account_clean_for_first_canary"] = flat.clean_for_first_canary
        payload["flat_account_position_count"] = flat.position_count
        payload["flat_account_open_order_count"] = flat.open_order_count
        payload["flat_account_fingerprint"] = flat.fingerprint
        if not flat.clean_for_first_canary:
            payload["phase"] = BLOCKED_EXISTING_PAPER_EXPOSURE
            payload["next_action"] = "STOP_AND_REVIEW_EXISTING_PAPER_EXPOSURE_MANUALLY"
            payload["market_evidence_present"] = market_path.is_file()
            payload["market_symbol"] = None
            payload["market_fingerprint"] = None
            return payload

    if not market_path.is_file():
        payload["market_evidence_present"] = False
        payload["market_symbol"] = None
        payload["market_fingerprint"] = None
        if report.phase is PaperReadinessPhase.PREPARATION_REQUIRED:
            payload["phase"] = MARKET_DATA_PREFLIGHT_REQUIRED
            payload["next_action"] = MARKET_DATA_NEXT_ACTION
        return payload

    try:
        attestation = PaperMarketEvidenceStore(workspace).read()
    except PaperMarketEvidenceError as exc:
        raise PaperReadinessIntegrityError("persisted equity market evidence is invalid") from exc

    observed_fingerprint = market_fingerprint(attestation.market)
    payload["market_evidence_present"] = True
    payload["market_symbol"] = attestation.market.symbol
    payload["market_fingerprint"] = observed_fingerprint
    payload["market_feed"] = attestation.feed
    payload["market_currency"] = attestation.currency

    if workspace.prepared_package_path.is_file():
        package = read_prepared_package(workspace.prepared_package_path)
        if package.market_fingerprint != observed_fingerprint:
            raise PaperReadinessIntegrityError(
                "prepared package market fingerprint does not match persisted equity market evidence"
            )
        if package.order_id != report.order_id:
            raise PaperReadinessIntegrityError(
                "prepared package identity disagrees with durable readiness state"
            )

    return payload


__all__ = [
    "FLAT_ACCOUNT_PREFLIGHT_REQUIRED",
    "FLAT_ACCOUNT_NEXT_ACTION",
    "BLOCKED_EXISTING_PAPER_EXPOSURE",
    "MARKET_DATA_PREFLIGHT_REQUIRED",
    "MARKET_DATA_NEXT_ACTION",
    "inspect_market_aware_readiness",
    "PaperReadinessError",
]
