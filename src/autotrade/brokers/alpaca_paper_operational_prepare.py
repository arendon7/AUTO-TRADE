from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping

from autotrade.domain import MarketSnapshot, OrderIntent, RiskDecision

from .alpaca_paper_bracket import PaperEquityVenueRules
from .alpaca_paper_canary_coordinator import (
    PaperCanaryCoordinator,
    PaperCanaryPreparationResult,
    PreparedPaperCanaryPackage,
)
from .alpaca_paper_canary_permit import SQLitePaperCanaryPermitRegistry
from .alpaca_paper_core_provenance import (
    PaperCoreProvenance,
    PaperOperationalCoreProvenanceReader,
)
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation
from .alpaca_paper_operational import (
    PaperOperationalIntegrityError,
    PaperOperationalWorkspace,
    _read_json_object,
    _write_json_idempotent,
    expected_bracket_payload,
    read_expected_bracket,
    read_prepared_package,
)
from .alpaca_paper_preparation_snapshot import (
    read_preparation_snapshot,
    write_preparation_snapshot,
)
from .alpaca_paper_submission import SQLitePaperSubmissionRegistry


_CORE_PROVENANCE_FILENAME = "core_provenance.json"
_CORE_FIELDS = {
    "core_db_sha256",
    "health_bridge_fingerprint",
    "health_bridge_version",
    "intent_fingerprint",
    "order_id",
    "order_record_fingerprint",
    "order_status",
    "portfolio_snapshot_hash",
    "portfolio_version",
    "provenance_hash",
    "risk_decision_fingerprint",
    "safety_observed_fingerprint",
    "safety_version",
    "strategy_health_fingerprint",
    "strategy_health_version",
    "strategy_id",
    "verified_at",
}
_DOCUMENT_FIELDS = {
    "schema_version",
    "environment",
    "attempt_id",
    "package_hash",
    "order_id",
    "core_provenance",
    "network_write_authorized",
    "external_order_submitted",
    "live_trading",
    "document_hash",
}


@dataclass(frozen=True, slots=True)
class PaperOperationalPreparation:
    result: PaperCanaryPreparationResult
    account_attestation_path: Path
    prepared_package_path: Path
    expected_bracket_path: Path
    preparation_snapshot_path: Path
    core_provenance_path: Path
    operator_context_path: Path
    manifest_path: Path

    def __post_init__(self) -> None:
        if self.result.package.network_write_authorized is not False:
            raise ValueError("operational preparation cannot authorize network write")
        if self.result.package.next_action != "OPERATOR_DECISION_REQUIRED":
            raise ValueError("operational preparation must stop at operator decision")
        for path in (
            self.account_attestation_path,
            self.prepared_package_path,
            self.expected_bracket_path,
            self.preparation_snapshot_path,
            self.core_provenance_path,
            self.operator_context_path,
            self.manifest_path,
        ):
            if not isinstance(path, Path) or not path.is_file() or path.is_symlink():
                raise ValueError("operational preparation artifacts must be regular files")


class PaperOperationalCanaryPreparer:
    """Persist one exact offline canary package without execution authority.

    The supplied coordinator must be backed by the exact durable ``core.sqlite3``
    in the operational workspace. The service first persists only non-authorizing
    package/snapshot evidence, verifies OMS/Safety/Portfolio/Health provenance
    read-only, commits a tamper-evident provenance artifact, and only then emits
    the operator context/manifest. It has no writer or transport surface.
    """

    def __init__(
        self,
        *,
        workspace: PaperOperationalWorkspace,
        coordinator: PaperCanaryCoordinator,
    ) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("operational workspace is required")
        if not isinstance(coordinator, PaperCanaryCoordinator):
            raise TypeError("authoritative PaperCanaryCoordinator is required")
        self._workspace = workspace
        self._coordinator = coordinator

    def prepare(
        self,
        *,
        intent: OrderIntent,
        decision: RiskDecision,
        market: MarketSnapshot,
        account_attestation: AlpacaPaperAccountAttestation,
        venue_rules: PaperEquityVenueRules,
        take_profit_price: Decimal,
        stop_loss_price: Decimal,
        submission_registry: SQLitePaperSubmissionRegistry,
        permit_registry: SQLitePaperCanaryPermitRegistry,
        now: datetime,
        certified_tracks: tuple[str, ...],
        reconciliation_clean: bool,
        unresolved_unknown_orders: int,
        kill_switch_engaged: bool,
        health_allows_new_exposure: bool,
        prior_canary_submissions: int,
    ) -> PaperOperationalPreparation:
        account_path = self._workspace.write_account_attestation(account_attestation)
        result = self._coordinator.prepare(
            intent=intent,
            decision=decision,
            market=market,
            account_attestation=account_attestation,
            venue_rules=venue_rules,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price,
            submission_registry=submission_registry,
            permit_registry=permit_registry,
            now=now,
            certified_tracks=certified_tracks,
            reconciliation_clean=reconciliation_clean,
            unresolved_unknown_orders=unresolved_unknown_orders,
            kill_switch_engaged=kill_switch_engaged,
            health_allows_new_exposure=health_allows_new_exposure,
            prior_canary_submissions=prior_canary_submissions,
        )

        # Stage only evidence that cannot authorize execution. In particular,
        # operator_context.json and manifest.json do not exist until the exact
        # workspace core.sqlite3 has passed read-only provenance verification.
        package_path = self._workspace.prepared_package_path
        _write_json_idempotent(
            package_path,
            result.package.canonical_payload(),
        )
        _write_json_idempotent(
            self._workspace.expected_bracket_path,
            expected_bracket_payload(result.bracket),
        )
        snapshot_path = write_preparation_snapshot(
            self._workspace,
            package=result.package,
            decision=decision,
            market=market,
            approval=result.approval,
        )

        persisted = read_prepared_package(package_path)
        persisted_bracket = read_expected_bracket(self._workspace.expected_bracket_path)
        snapshot_decision, snapshot_market, snapshot_approval = read_preparation_snapshot(
            self._workspace,
            package=result.package,
        )
        if persisted != result.package:
            raise PaperOperationalIntegrityError(
                "persisted canary package differs from coordinator result"
            )
        if persisted_bracket != result.bracket:
            raise PaperOperationalIntegrityError(
                "persisted expected bracket differs from coordinator result"
            )
        if (
            snapshot_decision != decision
            or snapshot_market != market
            or snapshot_approval != result.approval
        ):
            raise PaperOperationalIntegrityError(
                "persisted preparation snapshot differs from coordinator inputs"
            )

        provenance = PaperOperationalCoreProvenanceReader(self._workspace).verify(now=now)
        core_provenance_path = self._workspace.root / _CORE_PROVENANCE_FILENAME
        provenance_document = _core_provenance_document(
            result=result,
            provenance=provenance,
        )
        _write_json_idempotent(core_provenance_path, provenance_document)
        verify_core_provenance_document(
            self._workspace,
            package=result.package,
            observed=provenance,
        )

        package_path, context_path, manifest_path = self._workspace.write_prepared_canary(
            result.package,
            result.bracket,
        )
        return PaperOperationalPreparation(
            result=result,
            account_attestation_path=account_path,
            prepared_package_path=package_path,
            expected_bracket_path=self._workspace.expected_bracket_path,
            preparation_snapshot_path=snapshot_path,
            core_provenance_path=core_provenance_path,
            operator_context_path=context_path,
            manifest_path=manifest_path,
        )


def core_provenance_path(workspace: PaperOperationalWorkspace) -> Path:
    if not isinstance(workspace, PaperOperationalWorkspace):
        raise TypeError("operational workspace is required")
    return workspace.root / _CORE_PROVENANCE_FILENAME


def verify_core_provenance_document(
    workspace: PaperOperationalWorkspace,
    *,
    package: PreparedPaperCanaryPackage,
    observed: PaperCoreProvenance,
) -> str:
    """Verify persisted provenance against the exact prepared package and current core.

    ``observed`` must come from a fresh read-only ``core.sqlite3`` verification.
    Verification timestamps may differ between preparation and operator review;
    all durable state identities, versions, fingerprints and the DB byte hash
    must remain identical.
    """

    if not isinstance(workspace, PaperOperationalWorkspace):
        raise TypeError("operational workspace is required")
    if not isinstance(package, PreparedPaperCanaryPackage):
        raise TypeError("prepared PAPER canary package is required")
    if not isinstance(observed, PaperCoreProvenance):
        raise TypeError("observed core provenance is required")
    path = core_provenance_path(workspace)
    if not path.is_file() or path.is_symlink():
        raise PaperOperationalIntegrityError("core provenance artifact must be a regular file")
    raw = _read_json_object(path)
    if set(raw) != _DOCUMENT_FIELDS:
        raise PaperOperationalIntegrityError("core provenance document is non-canonical")
    document_hash = _required_hash(raw, "document_hash")
    body = {key: value for key, value in raw.items() if key != "document_hash"}
    if document_hash != _document_hash(body):
        raise PaperOperationalIntegrityError("core provenance document hash mismatch")
    if raw.get("schema_version") != 1 or raw.get("environment") != "PAPER":
        raise PaperOperationalIntegrityError("core provenance document is not PAPER schema v1")
    if raw.get("attempt_id") != package.attempt_id:
        raise PaperOperationalIntegrityError("core provenance attempt does not match prepared package")
    if raw.get("package_hash") != package.package_hash:
        raise PaperOperationalIntegrityError("core provenance package hash mismatch")
    if raw.get("order_id") != package.order_id:
        raise PaperOperationalIntegrityError("core provenance order does not match prepared package")
    if raw.get("network_write_authorized") is not False:
        raise PaperOperationalIntegrityError("core provenance cannot authorize network write")
    if raw.get("external_order_submitted") is not False:
        raise PaperOperationalIntegrityError("core provenance cannot claim external submission")
    if raw.get("live_trading") != "BLOCKED":
        raise PaperOperationalIntegrityError("core provenance must keep LIVE trading blocked")

    core_raw = raw.get("core_provenance")
    if not isinstance(core_raw, dict) or set(core_raw) != _CORE_FIELDS:
        raise PaperOperationalIntegrityError("core provenance payload is non-canonical")
    stored = _parse_core_provenance(core_raw)
    if stored.order_id != package.order_id:
        raise PaperOperationalIntegrityError("stored core provenance order mismatch")
    if stored.intent_fingerprint != package.intent_fingerprint:
        raise PaperOperationalIntegrityError("stored core provenance intent mismatch")
    if stored.risk_decision_fingerprint != package.risk_decision_fingerprint:
        raise PaperOperationalIntegrityError("stored core provenance RiskDecision mismatch")
    if stored.safety_version != package.risk_decision_safety_state_version:
        raise PaperOperationalIntegrityError("stored core provenance Safety version mismatch")

    durable_fields = (
        "order_id",
        "order_status",
        "order_record_fingerprint",
        "strategy_id",
        "intent_fingerprint",
        "risk_decision_fingerprint",
        "safety_version",
        "safety_observed_fingerprint",
        "portfolio_version",
        "portfolio_snapshot_hash",
        "strategy_health_version",
        "strategy_health_fingerprint",
        "health_bridge_version",
        "health_bridge_fingerprint",
        "core_db_sha256",
    )
    for field in durable_fields:
        if getattr(stored, field) != getattr(observed, field):
            raise PaperOperationalIntegrityError(
                f"current core provenance differs from prepared evidence: {field}"
            )
    if observed.verified_at < stored.verified_at:
        raise PaperOperationalIntegrityError("current core provenance predates prepared evidence")
    return document_hash


def _core_provenance_document(
    *,
    result: PaperCanaryPreparationResult,
    provenance: PaperCoreProvenance,
) -> dict[str, object]:
    package = result.package
    if provenance.order_id != package.order_id:
        raise PaperOperationalIntegrityError("core provenance order does not match prepared package")
    if provenance.intent_fingerprint != package.intent_fingerprint:
        raise PaperOperationalIntegrityError("core provenance intent does not match prepared package")
    if provenance.risk_decision_fingerprint != package.risk_decision_fingerprint:
        raise PaperOperationalIntegrityError(
            "core provenance RiskDecision does not match prepared package"
        )
    if provenance.safety_version != package.risk_decision_safety_state_version:
        raise PaperOperationalIntegrityError("core provenance Safety version mismatch")

    core_payload = {
        "core_db_sha256": provenance.core_db_sha256,
        "health_bridge_fingerprint": provenance.health_bridge_fingerprint,
        "health_bridge_version": provenance.health_bridge_version,
        "intent_fingerprint": provenance.intent_fingerprint,
        "order_id": provenance.order_id,
        "order_record_fingerprint": provenance.order_record_fingerprint,
        "order_status": provenance.order_status,
        "portfolio_snapshot_hash": provenance.portfolio_snapshot_hash,
        "portfolio_version": provenance.portfolio_version,
        "provenance_hash": provenance.provenance_hash,
        "risk_decision_fingerprint": provenance.risk_decision_fingerprint,
        "safety_observed_fingerprint": provenance.safety_observed_fingerprint,
        "safety_version": provenance.safety_version,
        "strategy_health_fingerprint": provenance.strategy_health_fingerprint,
        "strategy_health_version": provenance.strategy_health_version,
        "strategy_id": provenance.strategy_id,
        "verified_at": provenance.verified_at.isoformat(),
    }
    body: dict[str, object] = {
        "schema_version": 1,
        "environment": "PAPER",
        "attempt_id": package.attempt_id,
        "package_hash": package.package_hash,
        "order_id": package.order_id,
        "core_provenance": core_payload,
        "network_write_authorized": False,
        "external_order_submitted": False,
        "live_trading": "BLOCKED",
    }
    return {**body, "document_hash": _document_hash(body)}


def _parse_core_provenance(payload: Mapping[str, object]) -> PaperCoreProvenance:
    try:
        verified_at = datetime.fromisoformat(_required_str(payload, "verified_at"))
        return PaperCoreProvenance(
            order_id=_required_str(payload, "order_id"),
            order_status=_required_str(payload, "order_status"),
            order_record_fingerprint=_required_hash(payload, "order_record_fingerprint"),
            strategy_id=_required_str(payload, "strategy_id"),
            intent_fingerprint=_required_hash(payload, "intent_fingerprint"),
            risk_decision_fingerprint=_required_hash(payload, "risk_decision_fingerprint"),
            safety_version=_required_int(payload, "safety_version"),
            safety_observed_fingerprint=_required_hash(payload, "safety_observed_fingerprint"),
            portfolio_version=_required_int(payload, "portfolio_version"),
            portfolio_snapshot_hash=_required_hash(payload, "portfolio_snapshot_hash"),
            strategy_health_version=_required_int(payload, "strategy_health_version"),
            strategy_health_fingerprint=_required_hash(payload, "strategy_health_fingerprint"),
            health_bridge_version=_required_int(payload, "health_bridge_version"),
            health_bridge_fingerprint=_required_hash(payload, "health_bridge_fingerprint"),
            core_db_sha256=_required_hash(payload, "core_db_sha256"),
            verified_at=verified_at,
            provenance_hash=_required_hash(payload, "provenance_hash"),
        )
    except (TypeError, ValueError) as exc:
        raise PaperOperationalIntegrityError("core provenance payload is invalid") from exc


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PaperOperationalIntegrityError(f"core provenance {key} must be non-empty text")
    return value


def _required_hash(payload: Mapping[str, object], key: str) -> str:
    value = _required_str(payload, key)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise PaperOperationalIntegrityError(f"core provenance {key} must be lowercase SHA-256")
    return value


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PaperOperationalIntegrityError(f"core provenance {key} must be integer")
    return value


def _document_hash(body: Mapping[str, object]) -> str:
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=True,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()
