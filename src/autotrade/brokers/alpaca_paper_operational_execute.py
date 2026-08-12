from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3

from autotrade.domain import OrderRecord, OrderStatus
from autotrade.health_bridge import (
    HealthControlState,
    HealthEntityKind,
    HealthState,
    SQLiteHealthBridgeStore,
)
from autotrade.oms import OrderManagementSystem
from autotrade.persistence import (
    SQLiteEventLedger,
    SQLiteOrderStore,
    SQLitePortfolioStore,
    SQLiteRuntime,
    SQLiteSafetyStateStore,
    _order_from_json,
)

from .alpaca_paper_canary_permit import SQLitePaperCanaryPermitRegistry
from .alpaca_paper_core_provenance import PaperOperationalCoreProvenanceReader
from .alpaca_paper_execution_bridge import (
    PaperCanaryExecutionBridge,
    PaperExecutionStageResult,
)
from .alpaca_paper_final_guard import PaperFinalWriteGuard
from .alpaca_paper_flat_account import (
    ORDERS_PATH,
    ORDERS_QUERY,
    POSITIONS_PATH,
    PaperFlatAccountAttestation,
)
from .alpaca_paper_flat_account_evidence import (
    PaperFlatAccountEvidenceError,
    PaperFlatAccountEvidenceStore,
)
from .alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from .alpaca_paper_operational import (
    PaperOperationalIntegrityError,
    PaperOperationalWorkspace,
    _read_json_object,
    account_attestation_payload,
    read_expected_bracket,
    read_prepared_package,
)
from .alpaca_paper_operational_prepare import verify_core_provenance_document
from .alpaca_paper_operator_decision import (
    PaperOperatorDecisionContext,
    PaperOperatorDecisionStatus,
    SQLitePaperOperatorDecisionRegistry,
)
from .alpaca_paper_preparation_snapshot import read_preparation_snapshot
from .alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from .alpaca_paper_writer import (
    AlpacaPaperSingleShotWriter,
    PaperSubmitAttemptResult,
)


FLAT_ACCOUNT_MAX_AGE_SECONDS = 30


class PaperOperationalExecutionError(RuntimeError):
    pass


class PaperOperationalExecutionBlocked(PaperOperationalExecutionError):
    pass


class _NoBrokerExecutionSurface:
    """OMS constructor dependency that can never use the legacy broker path."""

    def submit(self, **_kwargs):
        raise PaperOperationalExecutionBlocked(
            "operational PAPER runtime forbids the legacy OMS broker submission surface"
        )


class _ExistingHealthStateReader:
    """Read existing Health state without importing Research into R6 execution authority."""

    def __init__(self, path: Path) -> None:
        _require_regular_db(path, "core")
        self._path = path

    def get(
        self,
        entity_id: str,
        entity_kind: HealthEntityKind,
    ) -> HealthControlState | None:
        if not isinstance(entity_id, str) or not entity_id or entity_id != entity_id.strip():
            raise PaperOperationalExecutionBlocked("Health entity_id is invalid")
        if not isinstance(entity_kind, HealthEntityKind):
            raise PaperOperationalExecutionBlocked("Health entity_kind is invalid")
        uri = f"{self._path.resolve().as_uri()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        except sqlite3.Error as exc:
            raise PaperOperationalExecutionBlocked("cannot open Health state read-only") from exc
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute(
                "SELECT * FROM health_state_v2 WHERE entity_kind=? AND entity_id=?",
                (entity_kind.value, entity_id),
            ).fetchone()
        except sqlite3.Error as exc:
            raise PaperOperationalExecutionBlocked("cannot read authoritative Health state") from exc
        finally:
            conn.close()
        if row is None:
            return None
        try:
            state = HealthControlState(
                entity_id=str(row["entity_id"]),
                entity_kind=HealthEntityKind(str(row["entity_kind"])),
                state=HealthState(str(row["state"])),
                version=int(row["version"]),
                distinct_quarantine_count=int(row["distinct_quarantine_count"]),
                baseline_fingerprint=str(row["baseline_fingerprint"]),
                policy_fingerprint=str(row["policy_fingerprint"]),
                last_assessment_fingerprint=str(row["last_assessment_fingerprint"]),
                updated_at=datetime.fromisoformat(str(row["updated_at"])),
                recovery_ack_head=str(row["recovery_ack_head"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PaperOperationalExecutionBlocked("authoritative Health state is invalid") from exc
        if str(row["state_hash"]) != state.fingerprint:
            raise PaperOperationalExecutionBlocked("authoritative Health state hash mismatch")
        return state


@dataclass(frozen=True, slots=True)
class PaperOperationalExecutionResult:
    stage: PaperExecutionStageResult
    submit: PaperSubmitAttemptResult
    portfolio_health_entity_id: str

    def __post_init__(self) -> None:
        if self.stage.attempt_id != self.submit.attempt_id:
            raise ValueError("execution stage/submit attempt mismatch")
        if self.stage.order.order_id != self.submit.order_id:
            raise ValueError("execution stage/submit order mismatch")
        if not self.portfolio_health_entity_id:
            raise ValueError("portfolio health entity id is required")


class PaperOperationalExecutionRuntime:
    """Reopen one exact prepared workspace and perform one human-gated PAPER attempt.

    This runtime does not prepare a canary and cannot mint operator authority.
    Fresh execution is allowed only from OMS VALIDATED after current read-only
    provenance still matches preparation. Restart execution is allowed from
    OMS SUBMITTING only when the human decision was already consumed by the
    exact same attempt. Any submission state other than PREPARED is strictly
    reconciliation/evidence territory and can never issue another POST here.

    The first-canary flat-account observation is also a hard runtime guard, not
    merely a UI/readiness hint: it must remain current and prove zero positions
    plus zero open orders before this class materializes any writable control
    plane object or consumes operator authority.
    """

    def __init__(
        self,
        *,
        workspace: PaperOperationalWorkspace,
        writer: AlpacaPaperSingleShotWriter,
    ) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("operational workspace is required")
        if not isinstance(writer, AlpacaPaperSingleShotWriter):
            raise TypeError("single-shot PAPER writer is required")
        self._workspace = workspace
        self._writer = writer

    def execute_once(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        now: datetime,
    ) -> PaperOperationalExecutionResult:
        _require_aware(now)
        instant = now.astimezone(timezone.utc)
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise PaperOperationalExecutionBlocked("exact PAPER credentials are required")
        if not self._writer.enabled:
            raise PaperOperationalExecutionBlocked("external PAPER writer is disabled")

        package = read_prepared_package(self._workspace.prepared_package_path)
        expected_bracket = read_expected_bracket(self._workspace.expected_bracket_path)
        decision, market, approval = read_preparation_snapshot(
            self._workspace,
            package=package,
        )
        account = _read_account_attestation(self._workspace.account_attestation_path)
        if account.fingerprint != package.account_attestation_fingerprint:
            raise PaperOperationalExecutionBlocked(
                "workspace account attestation does not match prepared package"
            )
        if credentials.credential_reference != account.credential_reference:
            raise PaperOperationalExecutionBlocked(
                "runtime credentials do not match prepared PAPER account"
            )
        if (
            account.source_host != ALPACA_PAPER_TRADING_HOST
            or account.source_path != ALPACA_PAPER_ACCOUNT_PATH
        ):
            raise PaperOperationalExecutionBlocked(
                "workspace account attestation endpoint is not exact PAPER"
            )

        _require_fresh_clean_flat_account_evidence(
            workspace=self._workspace,
            account=account,
            credentials=credentials,
            now=instant,
        )

        _require_regular_db(self._workspace.core_db_path, "core")
        _require_regular_db(self._workspace.submission_db_path, "submission")
        _require_regular_db(self._workspace.permit_db_path, "permit")
        _require_regular_db(self._workspace.operator_db_path, "operator")

        submission_registry = SQLitePaperSubmissionRegistry(
            SQLiteRuntime(self._workspace.submission_db_path)
        )
        submission_state = submission_registry.get(package.order_id)
        if submission_state.status is not PaperSubmissionStatus.PREPARED:
            raise PaperOperationalExecutionBlocked(
                f"submission is {submission_state.status.value}; POST replay is forbidden, continue with reconciliation/evidence"
            )
        if submission_state.attempt_count != 0:
            raise PaperOperationalExecutionBlocked(
                "PREPARED submission with nonzero attempt_count is invalid for execution"
            )
        if submission_state.client_order_id != package.client_order_id:
            raise PaperOperationalExecutionBlocked("submission client_order_id changed")
        if submission_state.binding_hash != package.submission_binding_hash:
            raise PaperOperationalExecutionBlocked("submission binding changed")

        operator_registry = SQLitePaperOperatorDecisionRegistry(
            SQLiteRuntime(self._workspace.operator_db_path)
        )
        context = PaperOperatorDecisionContext.from_prepared_package(package)
        operator_state = operator_registry.get(context.preparation_hash)
        operator_decision = operator_state.decision
        if operator_decision.context != context:
            raise PaperOperationalExecutionBlocked(
                "durable human decision does not match prepared package"
            )

        current_order = _read_core_order_read_only(
            self._workspace.core_db_path,
            order_id=package.order_id,
        )
        if current_order.status is OrderStatus.VALIDATED:
            if operator_state.status is not PaperOperatorDecisionStatus.ISSUED:
                raise PaperOperationalExecutionBlocked(
                    "fresh execution requires an ISSUED human decision"
                )
            if not operator_decision.is_valid_at(instant):
                raise PaperOperationalExecutionBlocked(
                    "human decision is expired or not yet valid"
                )
            observed = PaperOperationalCoreProvenanceReader(self._workspace).verify(
                now=instant
            )
            verify_core_provenance_document(
                self._workspace,
                package=package,
                observed=observed,
            )
        elif current_order.status is OrderStatus.SUBMITTING:
            if (
                operator_state.status is not PaperOperatorDecisionStatus.CONSUMED
                or operator_state.consumed_attempt_id != package.attempt_id
                or operator_state.consumed_at is None
            ):
                raise PaperOperationalExecutionBlocked(
                    "SUBMITTING restart requires the same-attempt consumed human decision"
                )
        else:
            raise PaperOperationalExecutionBlocked(
                f"operational execution requires VALIDATED/SUBMITTING OMS state, found {current_order.status.value}"
            )

        portfolio_health_entity_id = _discover_portfolio_health_entity_id(
            self._workspace.core_db_path
        )

        # Writable control-plane objects are reconstructed only after every
        # static workspace, fresh flat-account and provenance guard has passed.
        # Health reads remain isolated from Research authority and are validated
        # by their state hash.
        core_runtime = SQLiteRuntime(self._workspace.core_db_path)
        health_reader = _ExistingHealthStateReader(self._workspace.core_db_path)
        health_bridge = SQLiteHealthBridgeStore(
            core_runtime,
            health_reader=health_reader,
        )
        order_store = SQLiteOrderStore(core_runtime)
        safety_store = SQLiteSafetyStateStore(core_runtime)
        portfolio_store = SQLitePortfolioStore(core_runtime)
        oms = OrderManagementSystem(
            broker=_NoBrokerExecutionSurface(),
            ledger=SQLiteEventLedger(core_runtime),
            order_store=order_store,
            safety_state_store=safety_store,
            health_bridge=health_bridge,
            portfolio_health_entity_id=portfolio_health_entity_id,
        )
        bridge = PaperCanaryExecutionBridge(oms=oms)
        stage = bridge.stage_after_operator_decision(
            package=package,
            operator_decision=operator_decision,
            operator_registry=operator_registry,
            risk_decision=decision,
            market=market,
            now=instant,
        )

        permit_registry = SQLitePaperCanaryPermitRegistry(
            SQLiteRuntime(self._workspace.permit_db_path)
        )
        final_guard = PaperFinalWriteGuard(
            order_store=order_store,
            safety_state_store=safety_store,
            portfolio_store=portfolio_store,
            health_bridge=health_bridge,
            portfolio_health_entity_id=portfolio_health_entity_id,
        )
        submit = self._writer.submit_once(
            credentials=credentials,
            account_attestation=account,
            expected_bracket=expected_bracket,
            approval=approval,
            prepared_package=package,
            operator_decision=operator_decision,
            operator_registry=operator_registry,
            execution_stage=stage,
            permit_registry=permit_registry,
            submission_registry=submission_registry,
            oms=oms,
            external_handoff=stage.handoff,
            final_guard=final_guard,
            attempt_id=package.attempt_id,
            now=instant,
        )
        return PaperOperationalExecutionResult(
            stage=stage,
            submit=submit,
            portfolio_health_entity_id=portfolio_health_entity_id,
        )


def _require_fresh_clean_flat_account_evidence(
    *,
    workspace: PaperOperationalWorkspace,
    account: AlpacaPaperAccountAttestation,
    credentials: AlpacaPaperCredentials,
    now: datetime,
) -> PaperFlatAccountAttestation:
    try:
        flat = PaperFlatAccountEvidenceStore(workspace).read()
    except PaperFlatAccountEvidenceError as exc:
        raise PaperOperationalExecutionBlocked(
            "fresh clean flat-account evidence is required before execution"
        ) from exc

    if flat.account_attestation_fingerprint != account.fingerprint:
        raise PaperOperationalExecutionBlocked(
            "flat-account evidence does not match prepared PAPER account"
        )
    if flat.credential_reference != credentials.credential_reference:
        raise PaperOperationalExecutionBlocked(
            "flat-account evidence does not match runtime PAPER credentials"
        )
    if (
        flat.source_host != ALPACA_PAPER_TRADING_HOST
        or flat.positions_path != POSITIONS_PATH
        or flat.orders_path != f"{ORDERS_PATH}?{ORDERS_QUERY}"
    ):
        raise PaperOperationalExecutionBlocked(
            "flat-account evidence endpoint set is not exact PAPER"
        )
    if not flat.clean_for_first_canary:
        raise PaperOperationalExecutionBlocked(
            "first PAPER canary requires zero broker positions and zero open orders"
        )

    age_seconds = (now - flat.attested_at.astimezone(timezone.utc)).total_seconds()
    if age_seconds < 0:
        raise PaperOperationalExecutionBlocked(
            "flat-account evidence timestamp is from the future"
        )
    if age_seconds > FLAT_ACCOUNT_MAX_AGE_SECONDS:
        raise PaperOperationalExecutionBlocked(
            "flat-account evidence is stale; repeat the GET-only flat-account preflight"
        )
    return flat


def _read_account_attestation(path: Path) -> AlpacaPaperAccountAttestation:
    raw = _read_json_object(path)
    try:
        attestation = AlpacaPaperAccountAttestation(
            account_id=_required_str(raw, "account_id"),
            account_reference=_required_str(raw, "account_reference"),
            credential_reference=_required_str(raw, "credential_reference"),
            status=_required_str(raw, "status"),
            currency=_required_str(raw, "currency"),
            buying_power=_required_decimal(raw, "buying_power"),
            portfolio_value=_required_decimal(raw, "portfolio_value"),
            shorting_enabled=_required_bool(raw, "shorting_enabled"),
            attested_at=_required_datetime(raw, "attested_at"),
            request_id=_required_str(raw, "request_id"),
            source_host=_required_str(raw, "source_host"),
            source_path=_required_str(raw, "source_path"),
        )
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise PaperOperationalIntegrityError(
            "account attestation artifact is invalid"
        ) from exc
    if account_attestation_payload(attestation) != raw:
        raise PaperOperationalIntegrityError(
            "account attestation artifact is not canonical"
        )
    return attestation


def _read_core_order_read_only(path: Path, *, order_id: str) -> OrderRecord:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    except sqlite3.Error as exc:
        raise PaperOperationalExecutionBlocked(
            "cannot open core database read-only"
        ) from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            "SELECT record_json FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise PaperOperationalExecutionBlocked("cannot read durable OMS order") from exc
    finally:
        conn.close()
    if len(rows) != 1:
        raise PaperOperationalExecutionBlocked(
            "exact durable OMS order is missing or duplicated"
        )
    try:
        order = _order_from_json(str(rows[0]["record_json"]))
    except Exception as exc:
        raise PaperOperationalExecutionBlocked(
            "durable OMS order payload is invalid"
        ) from exc
    if order.order_id != order_id:
        raise PaperOperationalExecutionBlocked("durable OMS order identity changed")
    return order


def _discover_portfolio_health_entity_id(path: Path) -> str:
    """Resolve the only durable Portfolio Health identity; never accept caller input."""

    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    except sqlite3.Error as exc:
        raise PaperOperationalExecutionBlocked(
            "cannot open core database for Health identity"
        ) from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            """
            SELECT b.entity_id
            FROM health_bridge_state AS b
            JOIN health_state_v2 AS h
              ON h.entity_kind = b.entity_kind AND h.entity_id = b.entity_id
            WHERE b.entity_kind = ?
            ORDER BY b.entity_id
            """,
            (HealthEntityKind.PORTFOLIO.value,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise PaperOperationalExecutionBlocked(
            "cannot resolve durable Portfolio Health identity"
        ) from exc
    finally:
        conn.close()
    identities = tuple(str(row["entity_id"]) for row in rows)
    if (
        len(identities) != 1
        or not identities[0]
        or identities[0] != identities[0].strip()
    ):
        raise PaperOperationalExecutionBlocked(
            "operational execution requires exactly one canonical durable Portfolio Health identity"
        )
    return identities[0]


def _require_regular_db(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise PaperOperationalExecutionBlocked(
            f"{label} SQLite database must already exist as a regular file"
        )


def _required_str(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be non-empty string")
    return value


def _required_bool(raw: dict[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be bool")
    return value


def _required_decimal(raw: dict[str, object], key: str) -> Decimal:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise ValueError(f"{key} must be finite")
    return parsed


def _required_datetime(raw: dict[str, object], key: str) -> datetime:
    value = _required_str(raw, key)
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed)
    if parsed.isoformat() != value:
        raise ValueError(f"{key} timestamp must be canonical")
    return parsed


def _require_aware(value: datetime) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("execution time must be timezone-aware")
