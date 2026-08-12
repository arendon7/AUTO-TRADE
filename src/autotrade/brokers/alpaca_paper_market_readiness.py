from __future__ import annotations

from datetime import datetime
from pathlib import Path

from autotrade.domain import market_fingerprint

from .alpaca_paper_market_evidence import (
    PaperMarketEvidenceError,
    PaperMarketEvidenceStore,
)
from .alpaca_paper_operational import PaperOperationalWorkspace, read_prepared_package
from .alpaca_paper_readiness import (
    PaperOperationalReadinessInspector,
    PaperReadinessError,
    PaperReadinessIntegrityError,
    PaperReadinessPhase,
)


MARKET_DATA_PREFLIGHT_REQUIRED = "MARKET_DATA_PREFLIGHT_REQUIRED"
MARKET_DATA_NEXT_ACTION = "RUN_SEPARATE_GET_ONLY_IEX_MARKET_PREFLIGHT"


def inspect_market_aware_readiness(*, root: Path, now: datetime) -> dict[str, object]:
    """Project base R6 readiness plus immutable equity market evidence.

    This helper is read-only and non-authorizing. The base readiness inspector
    remains the durable state authority; this projection inserts the missing
    GET-only market-data step before offline preparation and cross-checks any
    persisted market artifact against an already prepared package.
    """

    if not isinstance(root, Path):
        raise TypeError("readiness workspace root must be pathlib.Path")
    report = PaperOperationalReadinessInspector(root).inspect(now=now)
    payload = report.to_dict()
    workspace = PaperOperationalWorkspace(root=root.expanduser().resolve())
    market_path = workspace.root / "market_snapshot.json"

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
    "MARKET_DATA_PREFLIGHT_REQUIRED",
    "MARKET_DATA_NEXT_ACTION",
    "inspect_market_aware_readiness",
    "PaperReadinessError",
]
