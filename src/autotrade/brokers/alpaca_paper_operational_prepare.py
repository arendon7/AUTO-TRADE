from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from autotrade.domain import MarketSnapshot, OrderIntent, RiskDecision

from .alpaca_paper_bracket import PaperEquityVenueRules
from .alpaca_paper_canary_coordinator import (
    PaperCanaryCoordinator,
    PaperCanaryPreparationResult,
)
from .alpaca_paper_canary_permit import SQLitePaperCanaryPermitRegistry
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation
from .alpaca_paper_operational import (
    PaperOperationalIntegrityError,
    PaperOperationalWorkspace,
    read_prepared_package,
)
from .alpaca_paper_submission import SQLitePaperSubmissionRegistry


@dataclass(frozen=True, slots=True)
class PaperOperationalPreparation:
    result: PaperCanaryPreparationResult
    account_attestation_path: Path
    prepared_package_path: Path
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
            self.operator_context_path,
            self.manifest_path,
        ):
            if not isinstance(path, Path) or not path.is_file():
                raise ValueError("operational preparation artifacts must exist")


class PaperOperationalCanaryPreparer:
    """Persist one exact offline canary package without execution authority.

    The supplied coordinator already owns authoritative OMS/Safety/Health
    dependencies. This service does not construct alternate control state, has
    no writer/transport surface, and deliberately stops before human approval.
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
        package_path, context_path, manifest_path = self._workspace.write_prepared_canary(
            result.package
        )
        persisted = read_prepared_package(package_path)
        if persisted != result.package:
            raise PaperOperationalIntegrityError(
                "persisted canary package differs from coordinator result"
            )
        return PaperOperationalPreparation(
            result=result,
            account_attestation_path=account_path,
            prepared_package_path=package_path,
            operator_context_path=context_path,
            manifest_path=manifest_path,
        )
