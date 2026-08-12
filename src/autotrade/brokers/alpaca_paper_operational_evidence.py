from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from autotrade.persistence import SQLiteRuntime

from .alpaca_paper_bracket import (
    AlpacaEquityBracketRequest,
    AlpacaNestedBracketAttestation,
)
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation, AlpacaPaperCredentials
from .alpaca_paper_operational import (
    PaperOperationalIntegrityError,
    PaperOperationalWorkspace,
    read_bracket_attestation,
    read_expected_bracket,
    read_prepared_package,
)
from .alpaca_paper_qualification import (
    AlpacaPaperQualificationEvaluator,
    PaperQualificationReport,
)
from .alpaca_paper_reconciliation import AlpacaPaperBracketReconciler
from .alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from .alpaca_paper_trade_updates import (
    PaperTradeUpdateLedgerState,
    PaperTradeUpdateParser,
    PaperTradeUpdateScope,
    SQLitePaperTradeUpdateLedger,
)
from .alpaca_paper_trade_updates_transport import AlpacaPaperTradeUpdatesTransport


class PaperOperationalEvidenceError(RuntimeError):
    pass


class PaperOperationalEvidenceBlocked(PaperOperationalEvidenceError):
    pass


class PaperOperationalEvidenceIncomplete(PaperOperationalEvidenceError):
    pass


@dataclass(frozen=True, slots=True)
class PaperOperationalReconciliationResult:
    found: bool
    submission_status: str
    bracket_attestation: AlpacaNestedBracketAttestation | None
    bracket_attestation_path: Path | None


@dataclass(frozen=True, slots=True)
class PaperOperationalTradeCaptureResult:
    scope: PaperTradeUpdateScope
    ledger_state: PaperTradeUpdateLedgerState
    received_frames: int
    appended_events: int
    idle_polls: int


@dataclass(frozen=True, slots=True)
class PaperOperationalQualificationResult:
    report: PaperQualificationReport
    qualification_report_path: Path
    evidence_manifest_path: Path


class PaperOperationalEvidenceCollector:
    """Post-submit PAPER evidence path with no order-write authority.

    The collector can only reconcile an already-UNKNOWN submission using GET,
    recover already-ACKNOWLEDGED child-leg evidence using GET, receive the
    already-authorized ``trade_updates`` control stream, append strict durable
    events, and run the offline qualification evaluator. It deliberately has no
    OMS staging, operator-decision minting, or order POST surface.
    """

    def __init__(
        self,
        *,
        workspace: PaperOperationalWorkspace,
        reconciler: AlpacaPaperBracketReconciler,
        trade_updates_transport: AlpacaPaperTradeUpdatesTransport,
        parser: PaperTradeUpdateParser | None = None,
        qualifier: AlpacaPaperQualificationEvaluator | None = None,
    ) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("operational workspace is required")
        if not isinstance(reconciler, AlpacaPaperBracketReconciler):
            raise TypeError("PAPER bracket reconciler is required")
        if not isinstance(trade_updates_transport, AlpacaPaperTradeUpdatesTransport):
            raise TypeError("PAPER trade_updates transport is required")
        self._workspace = workspace
        self._reconciler = reconciler
        self._trade_updates_transport = trade_updates_transport
        self._parser = parser or PaperTradeUpdateParser()
        self._qualifier = qualifier or AlpacaPaperQualificationEvaluator()

    def reconcile_and_persist(
        self,
        *,
        registry: SQLitePaperSubmissionRegistry,
        credentials: AlpacaPaperCredentials,
        account_attestation: AlpacaPaperAccountAttestation,
        now: datetime,
    ) -> PaperOperationalReconciliationResult:
        package, expected_bracket = self._bound_preparation()
        self._require_account_binding(package.account_attestation_fingerprint, account_attestation)
        state = registry.get(package.order_id)

        if self._workspace.bracket_attestation_path.exists():
            attestation = read_bracket_attestation(self._workspace.bracket_attestation_path)
            self._require_acknowledged_binding(
                registry=registry,
                expected_bracket=expected_bracket,
                attestation=attestation,
            )
            return PaperOperationalReconciliationResult(
                found=True,
                submission_status=PaperSubmissionStatus.ACKNOWLEDGED.value,
                bracket_attestation=attestation,
                bracket_attestation_path=self._workspace.bracket_attestation_path,
            )

        if state.status is PaperSubmissionStatus.UNKNOWN:
            outcome = self._reconciler.reconcile(
                registry=registry,
                order_id=package.order_id,
                credentials=credentials,
                account_attestation=account_attestation,
                expected_bracket=expected_bracket,
                now=now,
            )
            if not outcome.found:
                return PaperOperationalReconciliationResult(
                    found=False,
                    submission_status=outcome.state.status.value,
                    bracket_attestation=None,
                    bracket_attestation_path=None,
                )
            if outcome.bracket_attestation is None:
                raise PaperOperationalEvidenceIncomplete(
                    "reconciliation found broker order without bracket attestation"
                )
            attestation = outcome.bracket_attestation
        elif state.status is PaperSubmissionStatus.ACKNOWLEDGED:
            attestation = self._reconciler.recover_acknowledged_attestation(
                registry=registry,
                order_id=package.order_id,
                credentials=credentials,
                account_attestation=account_attestation,
                expected_bracket=expected_bracket,
            )
        else:
            raise PaperOperationalEvidenceBlocked(
                "broker reconciliation evidence requires UNKNOWN or ACKNOWLEDGED submission state"
            )

        attestation_path = self._workspace.write_bracket_attestation(
            attestation,
            expected_bracket=expected_bracket,
        )
        persisted = read_bracket_attestation(attestation_path)
        if persisted != attestation:
            raise PaperOperationalIntegrityError(
                "persisted broker bracket attestation differs from validated evidence"
            )
        self._require_acknowledged_binding(
            registry=registry,
            expected_bracket=expected_bracket,
            attestation=persisted,
        )
        return PaperOperationalReconciliationResult(
            found=True,
            submission_status=PaperSubmissionStatus.ACKNOWLEDGED.value,
            bracket_attestation=persisted,
            bracket_attestation_path=attestation_path,
        )

    def capture_trade_updates(
        self,
        *,
        registry: SQLitePaperSubmissionRegistry,
        credentials: AlpacaPaperCredentials,
        max_frames: int = 32,
        max_idle_polls: int = 3,
        timeout_seconds: float = 5.0,
    ) -> PaperOperationalTradeCaptureResult:
        if isinstance(max_frames, bool) or not isinstance(max_frames, int) or not 1 <= max_frames <= 256:
            raise ValueError("max_frames must be integer between 1 and 256")
        if (
            isinstance(max_idle_polls, bool)
            or not isinstance(max_idle_polls, int)
            or not 1 <= max_idle_polls <= 20
        ):
            raise ValueError("max_idle_polls must be integer between 1 and 20")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
            raise TypeError("timeout_seconds must be numeric")
        if not 0 < float(timeout_seconds) <= 60:
            raise ValueError("timeout_seconds must be > 0 and <= 60")

        package, expected_bracket = self._bound_preparation()
        if not self._workspace.bracket_attestation_path.is_file():
            raise PaperOperationalEvidenceBlocked(
                "trade_updates capture requires persisted reconciled bracket attestation"
            )
        attestation = read_bracket_attestation(self._workspace.bracket_attestation_path)
        self._require_acknowledged_binding(
            registry=registry,
            expected_bracket=expected_bracket,
            attestation=attestation,
        )
        scope = PaperTradeUpdateScope.from_bracket(
            symbol=str(expected_bracket.canonical_payload["symbol"]),
            attestation=attestation,
        )
        ledger = SQLitePaperTradeUpdateLedger(
            SQLiteRuntime(self._workspace.trade_updates_db_path),
            scope=scope,
        )

        received_frames = 0
        appended_events = 0
        idle_polls = 0
        session = self._trade_updates_transport.connect_and_listen(credentials=credentials)
        try:
            while received_frames < max_frames and idle_polls < max_idle_polls:
                frame = session.receive(timeout_seconds=float(timeout_seconds))
                if frame is None:
                    idle_polls += 1
                    continue
                received_frames += 1
                idle_polls = 0
                event = self._parser.parse(frame, scope=scope)
                if ledger.append(event):
                    appended_events += 1
        finally:
            session.close()

        try:
            ledger_state = ledger.verify()
        except Exception as exc:
            raise PaperOperationalEvidenceIncomplete(
                "trade_updates capture produced no verifiable durable evidence"
            ) from exc
        return PaperOperationalTradeCaptureResult(
            scope=scope,
            ledger_state=ledger_state,
            received_frames=received_frames,
            appended_events=appended_events,
            idle_polls=idle_polls,
        )

    def qualify(
        self,
        *,
        registry: SQLitePaperSubmissionRegistry,
        evaluated_at: datetime,
    ) -> PaperOperationalQualificationResult:
        package, expected_bracket = self._bound_preparation()
        if not self._workspace.bracket_attestation_path.is_file():
            raise PaperOperationalEvidenceBlocked(
                "qualification requires persisted broker bracket attestation"
            )
        attestation = read_bracket_attestation(self._workspace.bracket_attestation_path)
        self._require_acknowledged_binding(
            registry=registry,
            expected_bracket=expected_bracket,
            attestation=attestation,
        )
        scope = PaperTradeUpdateScope.from_bracket(
            symbol=str(expected_bracket.canonical_payload["symbol"]),
            attestation=attestation,
        )
        ledger = SQLitePaperTradeUpdateLedger(
            SQLiteRuntime(self._workspace.trade_updates_db_path),
            scope=scope,
        )
        report = self._qualifier.qualify(
            expected_bracket=expected_bracket,
            bracket_attestation=attestation,
            submission_registry=registry,
            trade_update_ledger=ledger,
            evaluated_at=evaluated_at,
        )
        report_path = self._workspace.write_qualification_report_payload(report.to_dict())
        persisted_report = PaperQualificationReport.read(report_path)
        if persisted_report != report:
            raise PaperOperationalIntegrityError(
                "persisted qualification report differs from evaluated report"
            )
        evidence_manifest_path = self._workspace.write_evidence_manifest(
            order_id=package.order_id,
            client_order_id=package.client_order_id,
            report_hash=report.report_hash,
            submission_event_head_hash=report.submission_event_head_hash,
            trade_update_scope_hash=report.trade_update_scope_hash,
            trade_update_head_hash=report.trade_update_head_hash,
        )
        return PaperOperationalQualificationResult(
            report=report,
            qualification_report_path=report_path,
            evidence_manifest_path=evidence_manifest_path,
        )

    def _bound_preparation(self) -> tuple[object, AlpacaEquityBracketRequest]:
        package = read_prepared_package(self._workspace.prepared_package_path)
        expected_bracket = read_expected_bracket(self._workspace.expected_bracket_path)
        if expected_bracket.order_id != package.order_id:
            raise PaperOperationalIntegrityError("workspace package/bracket order_id mismatch")
        if expected_bracket.client_order_id != package.client_order_id:
            raise PaperOperationalIntegrityError("workspace package/bracket client_order_id mismatch")
        if expected_bracket.payload_hash != package.bracket_payload_hash:
            raise PaperOperationalIntegrityError("workspace package/bracket payload hash mismatch")
        if (
            expected_bracket.instrument_master_fingerprint
            != package.instrument_master_fingerprint
        ):
            raise PaperOperationalIntegrityError(
                "workspace package/bracket Instrument Master mismatch"
            )
        return package, expected_bracket

    @staticmethod
    def _require_account_binding(
        expected_fingerprint: str,
        account_attestation: AlpacaPaperAccountAttestation,
    ) -> None:
        if not isinstance(account_attestation, AlpacaPaperAccountAttestation):
            raise TypeError("PAPER account attestation is required")
        if account_attestation.fingerprint != expected_fingerprint:
            raise PaperOperationalEvidenceBlocked(
                "current PAPER account attestation differs from prepared package"
            )

    @staticmethod
    def _require_acknowledged_binding(
        *,
        registry: SQLitePaperSubmissionRegistry,
        expected_bracket: AlpacaEquityBracketRequest,
        attestation: AlpacaNestedBracketAttestation,
    ) -> None:
        state = registry.get(expected_bracket.order_id)
        binding = registry.get_binding(expected_bracket.order_id)
        if state.status is not PaperSubmissionStatus.ACKNOWLEDGED:
            raise PaperOperationalEvidenceBlocked(
                "broker bracket evidence requires durable ACKNOWLEDGED submission state"
            )
        if binding.client_order_id != expected_bracket.client_order_id:
            raise PaperOperationalIntegrityError("submission/bracket client_order_id mismatch")
        if binding.order_payload_hash != expected_bracket.payload_hash:
            raise PaperOperationalIntegrityError("submission/bracket payload hash mismatch")
        if state.broker_order_id != attestation.parent_order_id:
            raise PaperOperationalIntegrityError("submission/bracket parent broker ID mismatch")
        if state.broker_client_order_id != attestation.client_order_id:
            raise PaperOperationalIntegrityError("submission/bracket broker client ID mismatch")
