from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Mapping

from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalWorkspace,
    _file_sha256,
)
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionState,
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.connectivity_execution_freshness_binding import (
    ConnectivityBoundFinalFreshnessResult,
    SQLiteConnectivityExecutionFreshnessRegistry,
)
from autotrade.connectivity_final_freshness import (
    ConnectivityFinalFreshnessStatus,
    SQLiteConnectivityFinalFreshnessRegistry,
)
from autotrade.connectivity_oms_stage import (
    ConnectivityOmsStager,
    ConnectivitySubmissionHandoff,
)
from autotrade.domain import OrderRecord, OrderStatus, market_fingerprint, risk_decision_fingerprint
from autotrade.persistence import (
    SQLiteEventLedger,
    SQLiteOrderStore,
    SQLiteRuntime,
    SQLiteSafetyStateStore,
)

_BINDING_DB = "connectivity_execution_freshness_binding.sqlite3"
_BINDING_ARTIFACT = "connectivity_execution_freshness_binding.json"
_FINAL_FRESHNESS_DB = "connectivity_final_freshness.sqlite3"
_FINAL_FRESHNESS_ARTIFACT = "connectivity_final_freshness.json"
_STAGING_ARTIFACT = "connectivity_staging.json"


class ConnectivityWorkspaceStageError(RuntimeError):
    pass


class ConnectivityWorkspaceStageRejected(ConnectivityWorkspaceStageError):
    pass


class ConnectivityWorkspaceStageConflict(ConnectivityWorkspaceStageError):
    pass


@dataclass(frozen=True, slots=True)
class ConnectivityWorkspaceStageResult:
    order: OrderRecord
    handoff: ConnectivitySubmissionHandoff
    submission: PaperSubmissionState
    artifact_path: Path


class ConnectivityWorkspaceStagingBridge:
    """Commit the pre-POST connectivity ambiguity barrier without any network API.

    The bridge is intentionally not a writer. It verifies the execution/freshness
    chain, stages the OMS through the typed connectivity kernel, and commits the
    durable submission UNKNOWN state before any future external POST can exist.
    """

    def __init__(self, workspace: PaperOperationalWorkspace) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        self._workspace = workspace

    @property
    def artifact_path(self) -> Path:
        return self._workspace.root / _STAGING_ARTIFACT

    def stage(
        self,
        *,
        bound_result: ConnectivityBoundFinalFreshnessResult,
        now: datetime,
    ) -> ConnectivityWorkspaceStageResult:
        if not isinstance(bound_result, ConnectivityBoundFinalFreshnessResult):
            raise TypeError("ConnectivityBoundFinalFreshnessResult is required")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if self.artifact_path.exists():
            raise ConnectivityWorkspaceStageRejected(
                "connectivity staging artifact already exists; never stage twice"
            )

        binding = bound_result.binding
        final_result = bound_result.final_freshness
        if not binding.is_valid_at(now):
            raise ConnectivityWorkspaceStageRejected(
                "execution/freshness binding expired before staging"
            )
        if not final_result.permit.is_valid_at(now):
            raise ConnectivityWorkspaceStageRejected(
                "Final Freshness permit expired before staging"
            )

        self._verify_binding_evidence(bound_result)
        self._verify_final_freshness_evidence(bound_result)

        # This check must occur before constructing SQLiteRuntime because runtime
        # initialization can legitimately touch SQLite/WAL metadata.
        if _file_sha256(self._workspace.core_db_path) != binding.core_db_sha256_after_fresh_safety:
            raise ConnectivityWorkspaceStageRejected(
                "core.sqlite3 changed after Final Freshness; reacquire the whole attempt"
            )

        submission_registry = SQLitePaperSubmissionRegistry(
            SQLiteRuntime(self._workspace.submission_db_path)
        )
        submission = submission_registry.get(binding.order_id)
        submission_binding = submission_registry.get_binding(binding.order_id)
        if submission.client_order_id != binding.client_order_id:
            raise ConnectivityWorkspaceStageConflict("submission client_order_id changed")
        if submission_binding.order_id != binding.order_id or submission_binding.client_order_id != binding.client_order_id:
            raise ConnectivityWorkspaceStageConflict("submission immutable binding changed")
        if submission.broker_order_id is not None or submission.broker_client_order_id is not None:
            raise ConnectivityWorkspaceStageRejected(
                "broker identity already exists before connectivity staging"
            )

        core_runtime = SQLiteRuntime(self._workspace.core_db_path)
        ledger = SQLiteEventLedger(core_runtime)
        ledger.verify_integrity()
        order_store = SQLiteOrderStore(core_runtime)
        order = order_store.get_by_order_id(binding.order_id)
        if order is None:
            raise ConnectivityWorkspaceStageConflict("connectivity OMS order is missing")

        if order.status is OrderStatus.VALIDATED:
            if submission.status is not PaperSubmissionStatus.PREPARED or submission.attempt_count != 0:
                raise ConnectivityWorkspaceStageRejected(
                    "first connectivity staging requires OMS VALIDATED and submission PREPARED/0"
                )
        elif order.status is OrderStatus.SUBMITTING:
            # Crash-safe replay is accepted only after the ambiguity barrier for
            # the same one-shot attempt already exists.
            if submission.status is not PaperSubmissionStatus.UNKNOWN or submission.attempt_count != 1:
                raise ConnectivityWorkspaceStageConflict(
                    "SUBMITTING replay requires the existing UNKNOWN one-shot barrier"
                )
            events = submission_registry.events(binding.order_id)
            matches = tuple(
                event
                for event in events
                if event.event_type.value == "SUBMIT_ATTEMPT_UNKNOWN"
                and event.payload.get("attempt_id") == binding.attempt_id
            )
            if len(matches) != 1:
                raise ConnectivityWorkspaceStageConflict(
                    "UNKNOWN replay is not bound to the expected attempt"
                )
        else:
            raise ConnectivityWorkspaceStageRejected(
                f"connectivity staging cannot start from OMS {order.status.value}"
            )

        stager = ConnectivityOmsStager(
            order_store=order_store,
            ledger=ledger,
            safety_state_store=SQLiteSafetyStateStore(core_runtime),
        )
        staged, handoff = stager.stage(
            order_id=binding.order_id,
            attempt_id=binding.attempt_id,
            execution_freshness_binding_hash=binding.binding_hash,
            final_freshness_permit_hash=binding.final_freshness_permit_hash,
            decision=final_result.fresh_risk_decision,
            market=final_result.fresh_market.market,
            now=now,
            valid_until=binding.expires_at,
        )
        stager.verify_handoff(handoff)

        try:
            unknown = submission_registry.mark_submit_attempt_unknown(
                order_id=binding.order_id,
                attempt_id=binding.attempt_id,
                now=now,
            )
        except Exception as exc:
            raise ConnectivityWorkspaceStageConflict(
                "OMS staged but UNKNOWN-before-POST barrier failed; STOP_AND_RECONCILE"
            ) from exc

        if unknown.status is not PaperSubmissionStatus.UNKNOWN or unknown.attempt_count != 1:
            raise ConnectivityWorkspaceStageConflict(
                "UNKNOWN-before-POST barrier did not commit exactly one attempt"
            )
        if unknown.broker_order_id is not None or unknown.broker_client_order_id is not None:
            raise ConnectivityWorkspaceStageConflict(
                "broker identity appeared before external POST"
            )
        stager.verify_handoff(handoff)
        ledger.verify_integrity()

        artifact = {
            "schema_version": 1,
            "environment": "PAPER",
            "purpose": "CONNECTIVITY_CANARY",
            "order_id": binding.order_id,
            "client_order_id": binding.client_order_id,
            "attempt_id": binding.attempt_id,
            "execution_freshness_binding_hash": binding.binding_hash,
            "final_freshness_permit_hash": binding.final_freshness_permit_hash,
            "fresh_risk_decision_id": binding.fresh_risk_decision_id,
            "handoff_hash": handoff.handoff_hash,
            "handoff_event_id": handoff.event_id,
            "oms_status": staged.status.value,
            "submission_status": unknown.status.value,
            "attempt_count": unknown.attempt_count,
            "unknown_before_post_committed": True,
            "oms_staging_completed": True,
            "external_post_authorized": False,
            "external_order_submitted": False,
            "strategy_health_required": False,
            "strategy_trading_authorized": False,
            "capital_authority": "NONE",
            "profitability_claim": False,
            "live_trading": "BLOCKED",
            "next_action": "CONNECTIVITY_WRITER_NOT_YET_AVAILABLE",
        }
        _write_json_exclusive(self.artifact_path, artifact)
        return ConnectivityWorkspaceStageResult(
            order=staged,
            handoff=handoff,
            submission=unknown,
            artifact_path=self.artifact_path,
        )

    def _verify_binding_evidence(
        self, bound_result: ConnectivityBoundFinalFreshnessResult
    ) -> None:
        expected_artifact = self._workspace.root / _BINDING_ARTIFACT
        if bound_result.artifact_path != expected_artifact or expected_artifact.is_symlink():
            raise ConnectivityWorkspaceStageRejected(
                "execution/freshness artifact path is not canonical"
            )
        if not expected_artifact.is_file():
            raise ConnectivityWorkspaceStageRejected(
                "execution/freshness artifact is missing"
            )
        registry = SQLiteConnectivityExecutionFreshnessRegistry(
            SQLiteRuntime(self._workspace.root / _BINDING_DB)
        )
        durable = registry.get(bound_result.binding.binding_hash)
        if durable != bound_result.state or durable.binding != bound_result.binding:
            raise ConnectivityWorkspaceStageConflict(
                "execution/freshness durable binding changed"
            )
        raw = _read_json(expected_artifact)
        expected = {
            "schema_version": 1,
            "environment": "PAPER",
            "purpose": "CONNECTIVITY_CANARY",
            "binding": bound_result.binding.payload(),
            "registry_event_hash": bound_result.state.event_hash,
            "second_human_execution_intent_bound": True,
            "final_freshness_bound": True,
            "max_external_post_attempts": 1,
            "oms_staging_authorized": False,
            "external_post_authorized": False,
            "external_order_submitted": False,
            "strategy_health_required": False,
            "strategy_trading_authorized": False,
            "capital_authority": "NONE",
            "profitability_claim": False,
            "live_trading": "BLOCKED",
            "next_action": "CONNECTIVITY_STAGING_BRIDGE_REQUIRED",
        }
        if raw != expected:
            raise ConnectivityWorkspaceStageRejected(
                "execution/freshness artifact is unsafe or non-canonical"
            )

    def _verify_final_freshness_evidence(
        self, bound_result: ConnectivityBoundFinalFreshnessResult
    ) -> None:
        binding = bound_result.binding
        final_result = bound_result.final_freshness
        permit = final_result.permit
        if permit.permit_hash != binding.final_freshness_permit_hash:
            raise ConnectivityWorkspaceStageConflict("Final Freshness permit hash changed")
        if final_result.state.status is not ConnectivityFinalFreshnessStatus.ISSUED:
            raise ConnectivityWorkspaceStageRejected("Final Freshness is not ISSUED")
        if final_result.state.event_hash != binding.final_freshness_event_hash:
            raise ConnectivityWorkspaceStageConflict("Final Freshness event hash changed")
        durable = SQLiteConnectivityFinalFreshnessRegistry(
            SQLiteRuntime(self._workspace.root / _FINAL_FRESHNESS_DB)
        ).get(permit.permit_hash)
        if durable != final_result.state:
            raise ConnectivityWorkspaceStageConflict("Final Freshness registry changed")
        expected_artifact = self._workspace.root / _FINAL_FRESHNESS_ARTIFACT
        if final_result.artifact_path != expected_artifact or expected_artifact.is_symlink():
            raise ConnectivityWorkspaceStageRejected("Final Freshness artifact path is not canonical")
        if _file_sha256(expected_artifact) != binding.final_freshness_artifact_sha256:
            raise ConnectivityWorkspaceStageRejected("Final Freshness artifact hash changed")
        if (
            final_result.fresh_risk_decision.decision_id != binding.fresh_risk_decision_id
            or risk_decision_fingerprint(final_result.fresh_risk_decision)
            != binding.fresh_risk_decision_fingerprint
        ):
            raise ConnectivityWorkspaceStageConflict("fresh RiskDecision binding changed")
        if market_fingerprint(final_result.fresh_market.market) != binding.fresh_market_fingerprint:
            raise ConnectivityWorkspaceStageConflict("fresh market binding changed")
        if final_result.fresh_risk_decision.safety_state_version != binding.safety_state_version:
            raise ConnectivityWorkspaceStageConflict("fresh Safety version binding changed")
        if permit.core_db_sha256_after_fresh_safety != binding.core_db_sha256_after_fresh_safety:
            raise ConnectivityWorkspaceStageConflict("fresh core provenance binding changed")


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConnectivityWorkspaceStageRejected(f"invalid staging prerequisite artifact: {path.name}") from exc
    if not isinstance(raw, dict):
        raise ConnectivityWorkspaceStageRejected(
            f"staging prerequisite artifact must be a JSON object: {path.name}"
        )
    return raw


def _write_json_exclusive(path: Path, payload: Mapping[str, object]) -> None:
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
        path.chmod(0o600)
    except FileExistsError as exc:
        raise ConnectivityWorkspaceStageRejected(
            "connectivity staging artifact already exists"
        ) from exc
