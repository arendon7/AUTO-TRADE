from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from autotrade.domain import MarketSnapshot, OrderIntent, RiskDecision

from .alpaca_paper_bracket import PaperEquityVenueRules
from .alpaca_paper_canary_coordinator import (
    PaperCanaryCoordinator,
    PaperCanaryPreparationResult,
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
        if _read_json_object(core_provenance_path) != provenance_document:
            raise PaperOperationalIntegrityError(
                "persisted core provenance differs from verified durable state"
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
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=True,
    ).encode("utf-8")
    return {**body, "document_hash": sha256(canonical).hexdigest()}
