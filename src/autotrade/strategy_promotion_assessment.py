from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from autotrade.domain import OrderIntent
from autotrade.paper_execution_lab import PaperExecutionSensitivityReport
from autotrade.persistence import SQLiteRuntime
from autotrade.research.multiple_testing import HolmEvidence
from autotrade.research.tournament import TournamentEvidence
from autotrade.research.trials import SQLiteTrialLedger
from autotrade.strategy_lab_promotion import (
    PERMANENT_W79_PROMOTION_BLOCKERS,
    REQUIRED_W79_GATE_IDS,
    PromotionAssessmentState,
    PromotionGateEvidence,
    PromotionGateStatus,
    SQLiteStrategyPromotionPolicyRegistry,
    StrategyPromotionEvidenceView,
    StrategyPromotionIntegrityError,
    evaluate_strategy_promotion,
)


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ZERO_ASSESSMENT_HASH = "0" * 64
ASSESSMENT_CONTRACT_VERSION = "W80_PROMOTION_ASSESSMENT_V1"


class StrategyPromotionAssessmentError(RuntimeError):
    pass


class StrategyPromotionAssessmentIntegrityError(StrategyPromotionAssessmentError):
    pass


class StrategyPromotionAssessmentConflict(StrategyPromotionAssessmentError):
    pass


@dataclass(frozen=True, slots=True)
class StrategyPromotionAssessmentReceipt:
    """Immutable durable receipt for one W79 promotion-evidence observation.

    This receipt records scientific evidence only. It never grants PAPER,
    external execution, capital, broker or LIVE authority.
    """

    assessment_id: str
    contract_version: str
    ordinal: int
    policy_id: str
    policy_hash: str
    threshold_policy_hash: str
    selected_strategy_id: str
    selected_strategy_version: str
    source_view_hash: str
    previous_assessment_hash: str
    assessed_at: datetime
    gates: tuple[PromotionGateEvidence, ...]
    evidence_complete: bool
    assessment_state: PromotionAssessmentState
    promotion_blockers: tuple[str, ...]
    paper_candidate_authorized: bool
    external_execution_authorized: bool
    capital_authority: str
    live_trading: str
    assessment_hash: str

    def __post_init__(self) -> None:
        _require_id(self.assessment_id, "assessment_id")
        if self.contract_version != ASSESSMENT_CONTRACT_VERSION:
            raise StrategyPromotionAssessmentIntegrityError(
                "assessment contract version is not canonical W80"
            )
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise StrategyPromotionAssessmentIntegrityError(
                "assessment ordinal must be a positive integer"
            )
        for label, value in (
            ("policy_id", self.policy_id),
            ("selected_strategy_id", self.selected_strategy_id),
            ("selected_strategy_version", self.selected_strategy_version),
        ):
            _require_id(value, label)
        for label, value in (
            ("policy_hash", self.policy_hash),
            ("threshold_policy_hash", self.threshold_policy_hash),
            ("source_view_hash", self.source_view_hash),
            ("previous_assessment_hash", self.previous_assessment_hash),
            ("assessment_hash", self.assessment_hash),
        ):
            _require_hash(value, label)
        _require_aware(self.assessed_at, "assessed_at")
        if any(not isinstance(item, PromotionGateEvidence) for item in self.gates):
            raise StrategyPromotionAssessmentIntegrityError(
                "assessment gates must use PromotionGateEvidence"
            )
        if tuple(item.gate_id for item in self.gates) != REQUIRED_W79_GATE_IDS:
            raise StrategyPromotionAssessmentIntegrityError(
                "assessment must preserve the exact canonical W79 gate set"
            )
        expected_complete = all(
            item.status is PromotionGateStatus.PASS for item in self.gates
        )
        if self.evidence_complete is not expected_complete:
            raise StrategyPromotionAssessmentIntegrityError(
                "assessment evidence_complete does not match gate states"
            )
        if self.assessment_state is not _assessment_state(self.gates):
            raise StrategyPromotionAssessmentIntegrityError(
                "assessment state does not match gate states"
            )
        if self.promotion_blockers != tuple(sorted(PERMANENT_W79_PROMOTION_BLOCKERS)):
            raise StrategyPromotionAssessmentIntegrityError(
                "assessment blockers must preserve the exact W79 blocker set"
            )
        if self.paper_candidate_authorized is not False:
            raise StrategyPromotionAssessmentIntegrityError(
                "W80 assessment may not authorize PAPER candidate"
            )
        if self.external_execution_authorized is not False:
            raise StrategyPromotionAssessmentIntegrityError(
                "W80 assessment may not authorize external execution"
            )
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise StrategyPromotionAssessmentIntegrityError(
                "W80 assessment may not grant capital or LIVE authority"
            )
        if self.assessment_hash != _hash(_receipt_payload(self, include_hash=False)):
            raise StrategyPromotionAssessmentIntegrityError(
                "promotion assessment hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _receipt_payload(self, include_hash=True)


class SQLiteStrategyPromotionAssessmentRegistry:
    """Append-only, hash-chained W80 assessment journal on canonical core SQLite."""

    def __init__(self, runtime: SQLiteRuntime | str) -> None:
        self._runtime = runtime if isinstance(runtime, SQLiteRuntime) else SQLiteRuntime(runtime)
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            tables = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            required = {
                "strategy_promotion_threshold_policies",
                "strategy_promotion_policies",
            }
            if not required.issubset(tables):
                raise StrategyPromotionAssessmentIntegrityError(
                    "W80 assessment registry requires the initialized W79 promotion schema"
                )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS strategy_promotion_assessments (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    assessment_id TEXT NOT NULL UNIQUE,
                    assessment_hash TEXT NOT NULL UNIQUE,
                    policy_id TEXT NOT NULL,
                    policy_hash TEXT NOT NULL,
                    threshold_policy_hash TEXT NOT NULL,
                    source_view_hash TEXT NOT NULL,
                    previous_assessment_hash TEXT NOT NULL,
                    assessed_at TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    FOREIGN KEY(policy_id)
                        REFERENCES strategy_promotion_policies(policy_id)
                );
                CREATE INDEX IF NOT EXISTS idx_strategy_promotion_assessments_policy_sequence
                    ON strategy_promotion_assessments(policy_id, sequence);
                """
            )
        finally:
            conn.close()

    def assess_and_record(
        self,
        *,
        assessment_id: str,
        policy_registry: SQLiteStrategyPromotionPolicyRegistry,
        policy_id: str,
        trial_ledger: SQLiteTrialLedger,
        tournament: TournamentEvidence,
        now: datetime,
        holm: HolmEvidence | None = None,
        execution_report: PaperExecutionSensitivityReport | None = None,
        execution_intent: OrderIntent | None = None,
    ) -> StrategyPromotionAssessmentReceipt:
        _require_id(assessment_id, "assessment_id")
        _require_id(policy_id, "policy_id")
        _require_aware(now, "now")
        self._require_same_runtime(policy_registry=policy_registry, trial_ledger=trial_ledger)

        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            view = evaluate_strategy_promotion(
                registry=policy_registry,
                policy_id=policy_id,
                trial_ledger=trial_ledger,
                tournament=tournament,
                holm=holm,
                execution_report=execution_report,
                execution_intent=execution_intent,
            )
            existing_row = conn.execute(
                "SELECT * FROM strategy_promotion_assessments WHERE assessment_id = ?",
                (assessment_id,),
            ).fetchone()
            if existing_row is not None:
                existing = _receipt_from_row(existing_row)
                if (
                    existing.policy_id != policy_id
                    or existing.source_view_hash != view.view_hash
                    or _utc(existing.assessed_at) != _utc(now)
                ):
                    raise StrategyPromotionAssessmentConflict(
                        f"assessment identity conflict: {assessment_id}"
                    )
                conn.execute("COMMIT")
                return existing

            previous_row = conn.execute(
                """
                SELECT * FROM strategy_promotion_assessments
                WHERE policy_id = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (policy_id,),
            ).fetchone()
            previous = _receipt_from_row(previous_row) if previous_row is not None else None
            if previous is not None:
                if _utc(now) <= _utc(previous.assessed_at):
                    raise StrategyPromotionAssessmentIntegrityError(
                        "assessment timestamps must advance monotonically per policy"
                    )
                if previous.source_view_hash == view.view_hash:
                    raise StrategyPromotionAssessmentConflict(
                        "unchanged promotion view may not be appended under a new assessment id"
                    )
                _require_non_regressive_evidence(previous, view)

            receipt = _build_receipt(
                assessment_id=assessment_id,
                view=view,
                ordinal=(previous.ordinal + 1 if previous is not None else 1),
                previous_assessment_hash=(
                    previous.assessment_hash if previous is not None else ZERO_ASSESSMENT_HASH
                ),
                assessed_at=now,
            )
            payload = _canonical_json(receipt.to_dict())
            conn.execute(
                """
                INSERT INTO strategy_promotion_assessments(
                    assessment_id, assessment_hash, policy_id, policy_hash,
                    threshold_policy_hash, source_view_hash,
                    previous_assessment_hash, assessed_at, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.assessment_id,
                    receipt.assessment_hash,
                    receipt.policy_id,
                    receipt.policy_hash,
                    receipt.threshold_policy_hash,
                    receipt.source_view_hash,
                    receipt.previous_assessment_hash,
                    _utc(receipt.assessed_at).isoformat(),
                    payload,
                ),
            )
            conn.execute("COMMIT")
            return receipt
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, assessment_id: str) -> StrategyPromotionAssessmentReceipt | None:
        _require_id(assessment_id, "assessment_id")
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT * FROM strategy_promotion_assessments WHERE assessment_id = ?",
                (assessment_id,),
            ).fetchone()
            return _receipt_from_row(row) if row is not None else None
        finally:
            conn.close()

    def list_for_policy(self, policy_id: str) -> tuple[StrategyPromotionAssessmentReceipt, ...]:
        _require_id(policy_id, "policy_id")
        conn = self._runtime.connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM strategy_promotion_assessments
                WHERE policy_id = ?
                ORDER BY sequence
                """,
                (policy_id,),
            ).fetchall()
        finally:
            conn.close()
        receipts = tuple(_receipt_from_row(row) for row in rows)
        _validate_chain(receipts)
        return receipts

    def latest_for_policy(self, policy_id: str) -> StrategyPromotionAssessmentReceipt | None:
        values = self.list_for_policy(policy_id)
        return values[-1] if values else None

    def _require_same_runtime(
        self,
        *,
        policy_registry: SQLiteStrategyPromotionPolicyRegistry,
        trial_ledger: SQLiteTrialLedger,
    ) -> None:
        policy_runtime = getattr(policy_registry, "_runtime", None)
        ledger_runtime = getattr(trial_ledger, "_runtime", None)
        if not isinstance(policy_runtime, SQLiteRuntime) or not isinstance(
            ledger_runtime, SQLiteRuntime
        ):
            raise StrategyPromotionAssessmentIntegrityError(
                "W80 assessment requires durable W79 policy and trial registries"
            )
        expected = _resolved_path(self._runtime.path)
        if _resolved_path(policy_runtime.path) != expected or _resolved_path(ledger_runtime.path) != expected:
            raise StrategyPromotionAssessmentIntegrityError(
                "assessment, promotion policy and trial ledger must share one authoritative SQLite runtime"
            )


def _build_receipt(
    *,
    assessment_id: str,
    view: StrategyPromotionEvidenceView,
    ordinal: int,
    previous_assessment_hash: str,
    assessed_at: datetime,
) -> StrategyPromotionAssessmentReceipt:
    values = {
        "assessment_id": assessment_id,
        "contract_version": ASSESSMENT_CONTRACT_VERSION,
        "ordinal": ordinal,
        "policy_id": view.policy_id,
        "policy_hash": view.policy_hash,
        "threshold_policy_hash": view.threshold_policy_hash,
        "selected_strategy_id": view.selected_strategy_id,
        "selected_strategy_version": view.selected_strategy_version,
        "source_view_hash": view.view_hash,
        "previous_assessment_hash": previous_assessment_hash,
        "assessed_at": assessed_at,
        "gates": view.gates,
        "evidence_complete": view.evidence_complete,
        "assessment_state": view.assessment_state,
        "promotion_blockers": tuple(sorted(PERMANENT_W79_PROMOTION_BLOCKERS)),
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return StrategyPromotionAssessmentReceipt(
        **values,
        assessment_hash=_hash(_receipt_payload_from_values(values)),
    )


def _require_non_regressive_evidence(
    previous: StrategyPromotionAssessmentReceipt,
    view: StrategyPromotionEvidenceView,
) -> None:
    if previous.policy_hash != view.policy_hash or previous.threshold_policy_hash != view.threshold_policy_hash:
        raise StrategyPromotionAssessmentIntegrityError(
            "assessment chain may not change frozen policy identity"
        )
    if (
        previous.selected_strategy_id != view.selected_strategy_id
        or previous.selected_strategy_version != view.selected_strategy_version
    ):
        raise StrategyPromotionAssessmentIntegrityError(
            "assessment chain may not change frozen strategy identity"
        )
    previous_by_gate = {gate.gate_id: gate for gate in previous.gates}
    current_by_gate = {gate.gate_id: gate for gate in view.gates}
    for gate_id in REQUIRED_W79_GATE_IDS:
        prior = previous_by_gate[gate_id]
        current = current_by_gate[gate_id]
        if not set(prior.evidence_hashes).issubset(current.evidence_hashes):
            raise StrategyPromotionAssessmentIntegrityError(
                f"assessment evidence hashes may not regress for gate: {gate_id}"
            )
        if prior.status is not PromotionGateStatus.MISSING and current.status is PromotionGateStatus.MISSING:
            raise StrategyPromotionAssessmentIntegrityError(
                f"assessment gate may not regress to MISSING: {gate_id}"
            )


def _validate_chain(receipts: tuple[StrategyPromotionAssessmentReceipt, ...]) -> None:
    previous: StrategyPromotionAssessmentReceipt | None = None
    for expected_ordinal, receipt in enumerate(receipts, start=1):
        if receipt.ordinal != expected_ordinal:
            raise StrategyPromotionAssessmentIntegrityError(
                "assessment chain ordinal discontinuity"
            )
        expected_previous = (
            previous.assessment_hash if previous is not None else ZERO_ASSESSMENT_HASH
        )
        if receipt.previous_assessment_hash != expected_previous:
            raise StrategyPromotionAssessmentIntegrityError(
                "assessment predecessor hash discontinuity"
            )
        if previous is not None:
            if _utc(receipt.assessed_at) <= _utc(previous.assessed_at):
                raise StrategyPromotionAssessmentIntegrityError(
                    "assessment chain timestamp regression"
                )
            _require_non_regressive_evidence(previous, _view_from_receipt(receipt))
        previous = receipt


def _view_from_receipt(receipt: StrategyPromotionAssessmentReceipt) -> StrategyPromotionEvidenceView:
    values = {
        "policy_id": receipt.policy_id,
        "policy_hash": receipt.policy_hash,
        "threshold_policy_hash": receipt.threshold_policy_hash,
        "selected_strategy_id": receipt.selected_strategy_id,
        "selected_strategy_version": receipt.selected_strategy_version,
        "gates": receipt.gates,
        "evidence_complete": receipt.evidence_complete,
        "assessment_state": receipt.assessment_state,
        "promotion_blockers": receipt.promotion_blockers,
        "paper_candidate_authorized": False,
        "external_execution_authorized": False,
        "live_trading": "BLOCKED",
    }
    return StrategyPromotionEvidenceView(
        **values,
        view_hash=receipt.source_view_hash,
    )


def _receipt_from_row(row: sqlite3.Row) -> StrategyPromotionAssessmentReceipt:
    raw = row["receipt_json"]
    if not isinstance(raw, str):
        raise StrategyPromotionAssessmentIntegrityError("assessment receipt JSON must be text")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StrategyPromotionAssessmentIntegrityError("assessment receipt JSON is invalid") from exc
    if not isinstance(value, dict):
        raise StrategyPromotionAssessmentIntegrityError("assessment receipt JSON must be an object")
    receipt = _receipt_from_dict(value)
    expected = {
        "assessment_id": receipt.assessment_id,
        "assessment_hash": receipt.assessment_hash,
        "policy_id": receipt.policy_id,
        "policy_hash": receipt.policy_hash,
        "threshold_policy_hash": receipt.threshold_policy_hash,
        "source_view_hash": receipt.source_view_hash,
        "previous_assessment_hash": receipt.previous_assessment_hash,
        "assessed_at": _utc(receipt.assessed_at).isoformat(),
        "receipt_json": _canonical_json(receipt.to_dict()),
    }
    for key, expected_value in expected.items():
        if str(row[key]) != expected_value:
            raise StrategyPromotionAssessmentIntegrityError(
                f"assessment SQLite column mismatch: {key}"
            )
    return receipt


def _receipt_from_dict(value: dict[str, object]) -> StrategyPromotionAssessmentReceipt:
    gates_raw = value.get("gates")
    if not isinstance(gates_raw, list):
        raise StrategyPromotionAssessmentIntegrityError("assessment gates must be a list")
    gates = tuple(_gate_from_dict(item) for item in gates_raw)
    assessed_raw = value.get("assessed_at")
    if not isinstance(assessed_raw, str):
        raise StrategyPromotionAssessmentIntegrityError("assessed_at must be text")
    try:
        assessed_at = datetime.fromisoformat(assessed_raw)
    except ValueError as exc:
        raise StrategyPromotionAssessmentIntegrityError("assessed_at is invalid") from exc
    _require_aware(assessed_at, "assessed_at")
    blockers_raw = value.get("promotion_blockers")
    if not isinstance(blockers_raw, list) or any(not isinstance(item, str) for item in blockers_raw):
        raise StrategyPromotionAssessmentIntegrityError("promotion blockers must be a string list")
    try:
        assessment_state = PromotionAssessmentState(_string(value, "assessment_state"))
    except ValueError as exc:
        raise StrategyPromotionAssessmentIntegrityError("assessment state is invalid") from exc
    return StrategyPromotionAssessmentReceipt(
        assessment_id=_string(value, "assessment_id"),
        contract_version=_string(value, "contract_version"),
        ordinal=_integer(value, "ordinal"),
        policy_id=_string(value, "policy_id"),
        policy_hash=_string(value, "policy_hash"),
        threshold_policy_hash=_string(value, "threshold_policy_hash"),
        selected_strategy_id=_string(value, "selected_strategy_id"),
        selected_strategy_version=_string(value, "selected_strategy_version"),
        source_view_hash=_string(value, "source_view_hash"),
        previous_assessment_hash=_string(value, "previous_assessment_hash"),
        assessed_at=assessed_at,
        gates=gates,
        evidence_complete=_boolean(value, "evidence_complete"),
        assessment_state=assessment_state,
        promotion_blockers=tuple(blockers_raw),
        paper_candidate_authorized=_false(value, "paper_candidate_authorized"),
        external_execution_authorized=_false(value, "external_execution_authorized"),
        capital_authority=_string(value, "capital_authority"),
        live_trading=_string(value, "live_trading"),
        assessment_hash=_string(value, "assessment_hash"),
    )


def _gate_from_dict(value: object) -> PromotionGateEvidence:
    if not isinstance(value, dict):
        raise StrategyPromotionAssessmentIntegrityError("gate receipt must be an object")
    reasons = value.get("reason_codes")
    hashes = value.get("evidence_hashes")
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        raise StrategyPromotionAssessmentIntegrityError("gate reason_codes must be a string list")
    if not isinstance(hashes, list) or any(not isinstance(item, str) for item in hashes):
        raise StrategyPromotionAssessmentIntegrityError("gate evidence_hashes must be a string list")
    try:
        status = PromotionGateStatus(_string(value, "status"))
    except ValueError as exc:
        raise StrategyPromotionAssessmentIntegrityError("gate status is invalid") from exc
    return PromotionGateEvidence(
        gate_id=_string(value, "gate_id"),
        status=status,
        reason_codes=tuple(reasons),
        evidence_hashes=tuple(hashes),
    )


def _receipt_payload(
    receipt: StrategyPromotionAssessmentReceipt,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload = _receipt_payload_from_values(
        {
            "assessment_id": receipt.assessment_id,
            "contract_version": receipt.contract_version,
            "ordinal": receipt.ordinal,
            "policy_id": receipt.policy_id,
            "policy_hash": receipt.policy_hash,
            "threshold_policy_hash": receipt.threshold_policy_hash,
            "selected_strategy_id": receipt.selected_strategy_id,
            "selected_strategy_version": receipt.selected_strategy_version,
            "source_view_hash": receipt.source_view_hash,
            "previous_assessment_hash": receipt.previous_assessment_hash,
            "assessed_at": receipt.assessed_at,
            "gates": receipt.gates,
            "evidence_complete": receipt.evidence_complete,
            "assessment_state": receipt.assessment_state,
            "promotion_blockers": receipt.promotion_blockers,
            "paper_candidate_authorized": receipt.paper_candidate_authorized,
            "external_execution_authorized": receipt.external_execution_authorized,
            "capital_authority": receipt.capital_authority,
            "live_trading": receipt.live_trading,
        }
    )
    if include_hash:
        payload["assessment_hash"] = receipt.assessment_hash
    return payload


def _receipt_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload = dict(values)
    assessed_at = payload["assessed_at"]
    if not isinstance(assessed_at, datetime):
        raise StrategyPromotionAssessmentIntegrityError("assessed_at must be datetime")
    payload["assessed_at"] = _utc(assessed_at).isoformat()
    gates = payload["gates"]
    if not isinstance(gates, tuple):
        raise StrategyPromotionAssessmentIntegrityError("assessment gates must be tuple")
    payload["gates"] = [item.to_dict() for item in gates]
    state = payload["assessment_state"]
    if not isinstance(state, PromotionAssessmentState):
        raise StrategyPromotionAssessmentIntegrityError("assessment state type is invalid")
    payload["assessment_state"] = state.value
    blockers = payload["promotion_blockers"]
    if not isinstance(blockers, tuple):
        raise StrategyPromotionAssessmentIntegrityError("promotion blockers must be tuple")
    payload["promotion_blockers"] = list(blockers)
    return payload


def _assessment_state(gates: tuple[PromotionGateEvidence, ...]) -> PromotionAssessmentState:
    statuses = {item.status for item in gates}
    if PromotionGateStatus.BLOCKED in statuses:
        return PromotionAssessmentState.BLOCKED
    if PromotionGateStatus.FAIL in statuses:
        return PromotionAssessmentState.REJECTED
    if PromotionGateStatus.MISSING in statuses:
        return PromotionAssessmentState.INCOMPLETE
    return PromotionAssessmentState.EVIDENCE_QUALIFIED


def _string(value: dict[str, object], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str):
        raise StrategyPromotionAssessmentIntegrityError(f"{key} must be string")
    return raw


def _integer(value: dict[str, object], key: str) -> int:
    raw = value.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise StrategyPromotionAssessmentIntegrityError(f"{key} must be integer")
    return raw


def _boolean(value: dict[str, object], key: str) -> bool:
    raw = value.get(key)
    if not isinstance(raw, bool):
        raise StrategyPromotionAssessmentIntegrityError(f"{key} must be boolean")
    return raw


def _false(value: dict[str, object], key: str) -> bool:
    raw = _boolean(value, key)
    if raw is not False:
        raise StrategyPromotionAssessmentIntegrityError(f"{key} must remain false")
    return False


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise StrategyPromotionAssessmentIntegrityError(
            f"{label} must be a canonical identifier"
        )


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise StrategyPromotionAssessmentIntegrityError(
            f"{label} must be lowercase sha256"
        )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StrategyPromotionAssessmentIntegrityError(
            f"{label} must be timezone-aware datetime"
        )


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _resolved_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "ASSESSMENT_CONTRACT_VERSION",
    "ZERO_ASSESSMENT_HASH",
    "SQLiteStrategyPromotionAssessmentRegistry",
    "StrategyPromotionAssessmentConflict",
    "StrategyPromotionAssessmentError",
    "StrategyPromotionAssessmentIntegrityError",
    "StrategyPromotionAssessmentReceipt",
]
