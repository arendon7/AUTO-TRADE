from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

from autotrade.strategy_lab_promotion import (
    PERMANENT_W79_PROMOTION_BLOCKERS,
    REQUIRED_W79_GATE_IDS,
    PromotionAssessmentState,
    PromotionGateStatus,
)


ASSESSMENT_CONTRACT_VERSION = "W80_PROMOTION_ASSESSMENT_V1"
ZERO_ASSESSMENT_HASH = "0" * 64
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_RECEIPT_KEYS = {
    "assessment_id",
    "contract_version",
    "ordinal",
    "policy_id",
    "policy_hash",
    "threshold_policy_hash",
    "selected_strategy_id",
    "selected_strategy_version",
    "source_view_hash",
    "previous_assessment_hash",
    "assessed_at",
    "gates",
    "evidence_complete",
    "assessment_state",
    "promotion_blockers",
    "paper_candidate_authorized",
    "external_execution_authorized",
    "capital_authority",
    "live_trading",
    "assessment_hash",
}
_GATE_KEYS = {"gate_id", "status", "reason_codes", "evidence_hashes"}


class PromotionAssessmentReadError(RuntimeError):
    pass


class PromotionAssessmentReadMissing(PromotionAssessmentReadError):
    pass


class PromotionAssessmentReadIntegrityError(PromotionAssessmentReadError):
    pass


@dataclass(frozen=True, slots=True)
class PromotionGateReadView:
    gate_id: str
    status: PromotionGateStatus
    reason_codes: tuple[str, ...]
    evidence_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_id(self.gate_id, "gate_id")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise PromotionAssessmentReadIntegrityError("gate reason codes must be unique sorted order")
        if self.evidence_hashes != tuple(sorted(set(self.evidence_hashes))):
            raise PromotionAssessmentReadIntegrityError("gate evidence hashes must be unique sorted order")
        for value in self.evidence_hashes:
            _require_hash(value, "gate evidence hash")
        if self.status is PromotionGateStatus.PASS:
            if self.reason_codes:
                raise PromotionAssessmentReadIntegrityError("PASS gate may not carry reasons")
        elif not self.reason_codes:
            raise PromotionAssessmentReadIntegrityError("non-PASS gate requires an explicit reason")

    def to_dict(self) -> dict[str, object]:
        return {
            "gate_id": self.gate_id,
            "status": self.status.value,
            "reason_codes": list(self.reason_codes),
            "evidence_hashes": list(self.evidence_hashes),
        }


@dataclass(frozen=True, slots=True)
class PromotionAssessmentReadView:
    sequence: int
    assessment_id: str
    ordinal: int
    policy_id: str
    policy_hash: str
    threshold_policy_hash: str
    selected_strategy_id: str
    selected_strategy_version: str
    source_view_hash: str
    previous_assessment_hash: str
    assessed_at: datetime
    gates: tuple[PromotionGateReadView, ...]
    evidence_complete: bool
    assessment_state: PromotionAssessmentState
    promotion_blockers: tuple[str, ...]
    assessment_hash: str

    def __post_init__(self) -> None:
        _positive_int(self.sequence, "sequence")
        _positive_int(self.ordinal, "ordinal")
        for label, value in (
            ("assessment_id", self.assessment_id),
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
        if tuple(item.gate_id for item in self.gates) != REQUIRED_W79_GATE_IDS:
            raise PromotionAssessmentReadIntegrityError("assessment gate set/order is not canonical W79")
        complete = all(item.status is PromotionGateStatus.PASS for item in self.gates)
        if self.evidence_complete is not complete:
            raise PromotionAssessmentReadIntegrityError("assessment evidence_complete does not match gates")
        if self.assessment_state is not _assessment_state(self.gates):
            raise PromotionAssessmentReadIntegrityError("assessment state does not match gates")
        if self.promotion_blockers != tuple(sorted(PERMANENT_W79_PROMOTION_BLOCKERS)):
            raise PromotionAssessmentReadIntegrityError("assessment blocker set is not canonical W79")

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "assessment_id": self.assessment_id,
            "ordinal": self.ordinal,
            "policy_id": self.policy_id,
            "policy_hash": self.policy_hash,
            "threshold_policy_hash": self.threshold_policy_hash,
            "selected_strategy_id": self.selected_strategy_id,
            "selected_strategy_version": self.selected_strategy_version,
            "source_view_hash": self.source_view_hash,
            "previous_assessment_hash": self.previous_assessment_hash,
            "assessed_at": _utc(self.assessed_at).isoformat(),
            "gates": [item.to_dict() for item in self.gates],
            "evidence_complete": self.evidence_complete,
            "assessment_state": self.assessment_state.value,
            "promotion_blockers": list(self.promotion_blockers),
            "paper_candidate_authorized": False,
            "external_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
            "assessment_hash": self.assessment_hash,
        }


@dataclass(frozen=True, slots=True)
class PromotionAssessmentReadSnapshot:
    assessment_evidence_state: str
    journal_schema_present: bool
    assessments: tuple[PromotionAssessmentReadView, ...]
    latest_assessments: tuple[PromotionAssessmentReadView, ...]
    observed_at: datetime
    provenance_hash: str

    def __post_init__(self) -> None:
        expected_state = "DURABLE_W80_ASSESSMENT" if self.assessments else "NO_DURABLE_W80_ASSESSMENT"
        if self.assessment_evidence_state != expected_state:
            raise PromotionAssessmentReadIntegrityError("assessment evidence state does not match journal")
        if self.assessments != tuple(sorted(self.assessments, key=lambda item: item.sequence)):
            raise PromotionAssessmentReadIntegrityError("assessment history must be sequence-sorted")
        latest: dict[str, PromotionAssessmentReadView] = {}
        for item in self.assessments:
            latest[item.policy_id] = item
        expected_latest = tuple(sorted(latest.values(), key=lambda item: item.policy_id))
        if self.latest_assessments != expected_latest:
            raise PromotionAssessmentReadIntegrityError("latest assessments do not match history")
        _require_aware(self.observed_at, "observed_at")
        _require_hash(self.provenance_hash, "provenance_hash")
        if self.provenance_hash != _hash(self._payload()):
            raise PromotionAssessmentReadIntegrityError("assessment snapshot provenance hash mismatch")

    def _payload(self) -> dict[str, object]:
        return {
            "assessment_evidence_state": self.assessment_evidence_state,
            "journal_schema_present": self.journal_schema_present,
            "assessments": [item.to_dict() for item in self.assessments],
            "latest_assessments": [item.to_dict() for item in self.latest_assessments],
            "paper_candidate_authorized": False,
            "external_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }

    def to_dict(self) -> dict[str, object]:
        value = self._payload()
        value.update(
            assessment_count=len(self.assessments),
            policy_count=len(self.latest_assessments),
            observed_at=_utc(self.observed_at).isoformat(),
            provenance_hash=self.provenance_hash,
            broker_network_used=False,
            broker_write_performed=False,
            credentials_used=False,
        )
        return value


class PromotionAssessmentReadModel:
    """Independent immutable W80 assessment reader.

    The writer module is intentionally not imported. The database is opened
    mode=ro with query_only=ON, and every receipt/side-column/hash-chain is
    independently revalidated before any assessment truth is exposed.
    """

    def __init__(self, core_db_path: str | Path) -> None:
        path = Path(core_db_path).expanduser()
        if path.is_symlink():
            raise PromotionAssessmentReadIntegrityError("core.sqlite3 may not be a symlink")
        if not path.is_file():
            raise PromotionAssessmentReadMissing("core.sqlite3 is missing")
        self._path = path.resolve()

    def snapshot(self, *, now: datetime | None = None) -> PromotionAssessmentReadSnapshot:
        observed_at = now or datetime.now(timezone.utc)
        _require_aware(observed_at, "now")
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            table_present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                ("strategy_promotion_assessments",),
            ).fetchone() is not None
            rows = conn.execute(
                "SELECT * FROM strategy_promotion_assessments ORDER BY sequence"
            ).fetchall() if table_present else []
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

        assessments = tuple(_assessment_from_row(row) for row in rows)
        _validate_chains(assessments)
        latest: dict[str, PromotionAssessmentReadView] = {}
        for item in assessments:
            latest[item.policy_id] = item
        latest_assessments = tuple(sorted(latest.values(), key=lambda item: item.policy_id))
        state = "DURABLE_W80_ASSESSMENT" if assessments else "NO_DURABLE_W80_ASSESSMENT"
        payload = {
            "assessment_evidence_state": state,
            "journal_schema_present": table_present,
            "assessments": [item.to_dict() for item in assessments],
            "latest_assessments": [item.to_dict() for item in latest_assessments],
            "paper_candidate_authorized": False,
            "external_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        return PromotionAssessmentReadSnapshot(
            assessment_evidence_state=state,
            journal_schema_present=table_present,
            assessments=assessments,
            latest_assessments=latest_assessments,
            observed_at=observed_at,
            provenance_hash=_hash(payload),
        )

    def _connect(self) -> sqlite3.Connection:
        encoded = quote(str(self._path), safe="/")
        conn = sqlite3.connect(f"file:{encoded}?mode=ro", uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


def _assessment_from_row(row: sqlite3.Row) -> PromotionAssessmentReadView:
    raw = row["receipt_json"]
    if not isinstance(raw, str):
        raise PromotionAssessmentReadIntegrityError("assessment receipt JSON must be text")
    try:
        receipt = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PromotionAssessmentReadIntegrityError("assessment receipt JSON is invalid") from exc
    if not isinstance(receipt, dict) or set(receipt) != _RECEIPT_KEYS:
        raise PromotionAssessmentReadIntegrityError("assessment receipt fields are not canonical W80")
    if _string(receipt, "contract_version") != ASSESSMENT_CONTRACT_VERSION:
        raise PromotionAssessmentReadIntegrityError("assessment contract version is not canonical W80")
    assessment_hash = _string(receipt, "assessment_hash")
    _require_hash(assessment_hash, "assessment_hash")
    payload = dict(receipt)
    payload.pop("assessment_hash")
    if assessment_hash != _hash(payload):
        raise PromotionAssessmentReadIntegrityError("assessment receipt hash mismatch")

    gates_raw = receipt["gates"]
    if not isinstance(gates_raw, list):
        raise PromotionAssessmentReadIntegrityError("assessment gates must be a list")
    gates = tuple(_gate(value) for value in gates_raw)
    blockers = _strings(receipt, "promotion_blockers")
    if blockers != tuple(sorted(PERMANENT_W79_PROMOTION_BLOCKERS)):
        raise PromotionAssessmentReadIntegrityError("assessment blocker set is not canonical W79")
    if _boolean(receipt, "paper_candidate_authorized") or _boolean(receipt, "external_execution_authorized"):
        raise PromotionAssessmentReadIntegrityError("durable assessment may not authorize PAPER/execution")
    if _string(receipt, "capital_authority") != "NONE" or _string(receipt, "live_trading") != "BLOCKED":
        raise PromotionAssessmentReadIntegrityError("durable assessment may not grant capital/LIVE authority")
    try:
        state = PromotionAssessmentState(_string(receipt, "assessment_state"))
    except ValueError as exc:
        raise PromotionAssessmentReadIntegrityError("assessment state is invalid") from exc

    view = PromotionAssessmentReadView(
        sequence=_positive_int(row["sequence"], "sequence"),
        assessment_id=_string(receipt, "assessment_id"),
        ordinal=_positive_int(receipt["ordinal"], "ordinal"),
        policy_id=_string(receipt, "policy_id"),
        policy_hash=_string(receipt, "policy_hash"),
        threshold_policy_hash=_string(receipt, "threshold_policy_hash"),
        selected_strategy_id=_string(receipt, "selected_strategy_id"),
        selected_strategy_version=_string(receipt, "selected_strategy_version"),
        source_view_hash=_string(receipt, "source_view_hash"),
        previous_assessment_hash=_string(receipt, "previous_assessment_hash"),
        assessed_at=_aware_datetime(receipt["assessed_at"], "assessed_at"),
        gates=gates,
        evidence_complete=_boolean(receipt, "evidence_complete"),
        assessment_state=state,
        promotion_blockers=blockers,
        assessment_hash=assessment_hash,
    )
    expected_columns = {
        "assessment_id": view.assessment_id,
        "assessment_hash": view.assessment_hash,
        "policy_id": view.policy_id,
        "policy_hash": view.policy_hash,
        "threshold_policy_hash": view.threshold_policy_hash,
        "source_view_hash": view.source_view_hash,
        "previous_assessment_hash": view.previous_assessment_hash,
        "assessed_at": _utc(view.assessed_at).isoformat(),
        "receipt_json": _canonical_json(receipt),
    }
    for key, expected in expected_columns.items():
        if str(row[key]) != expected:
            raise PromotionAssessmentReadIntegrityError(f"assessment SQLite column mismatch: {key}")
    return view


def _gate(value: object) -> PromotionGateReadView:
    if not isinstance(value, dict) or set(value) != _GATE_KEYS:
        raise PromotionAssessmentReadIntegrityError("assessment gate fields are not canonical W80")
    try:
        status = PromotionGateStatus(_string(value, "status"))
    except ValueError as exc:
        raise PromotionAssessmentReadIntegrityError("assessment gate status is invalid") from exc
    return PromotionGateReadView(
        gate_id=_string(value, "gate_id"),
        status=status,
        reason_codes=_strings(value, "reason_codes"),
        evidence_hashes=_strings(value, "evidence_hashes"),
    )


def _validate_chains(assessments: tuple[PromotionAssessmentReadView, ...]) -> None:
    ids: set[str] = set()
    hashes: set[str] = set()
    previous: dict[str, PromotionAssessmentReadView] = {}
    for item in assessments:
        if item.assessment_id in ids or item.assessment_hash in hashes:
            raise PromotionAssessmentReadIntegrityError("duplicate durable assessment identity")
        ids.add(item.assessment_id)
        hashes.add(item.assessment_hash)
        prior = previous.get(item.policy_id)
        expected_ordinal = 1 if prior is None else prior.ordinal + 1
        expected_hash = ZERO_ASSESSMENT_HASH if prior is None else prior.assessment_hash
        if item.ordinal != expected_ordinal:
            raise PromotionAssessmentReadIntegrityError("assessment chain ordinal discontinuity")
        if item.previous_assessment_hash != expected_hash:
            raise PromotionAssessmentReadIntegrityError("assessment predecessor hash discontinuity")
        if prior is not None:
            if _utc(item.assessed_at) <= _utc(prior.assessed_at):
                raise PromotionAssessmentReadIntegrityError("assessment chain timestamp regression")
            if (
                item.policy_hash != prior.policy_hash
                or item.threshold_policy_hash != prior.threshold_policy_hash
                or item.selected_strategy_id != prior.selected_strategy_id
                or item.selected_strategy_version != prior.selected_strategy_version
            ):
                raise PromotionAssessmentReadIntegrityError("assessment chain changed frozen identity")
            prior_gates = {gate.gate_id: gate for gate in prior.gates}
            for gate in item.gates:
                old = prior_gates[gate.gate_id]
                if not set(old.evidence_hashes).issubset(gate.evidence_hashes):
                    raise PromotionAssessmentReadIntegrityError(f"assessment evidence regressed: {gate.gate_id}")
                if old.status is not PromotionGateStatus.MISSING and gate.status is PromotionGateStatus.MISSING:
                    raise PromotionAssessmentReadIntegrityError(f"assessment gate regressed to MISSING: {gate.gate_id}")
        previous[item.policy_id] = item


def _assessment_state(gates: tuple[PromotionGateReadView, ...]) -> PromotionAssessmentState:
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
        raise PromotionAssessmentReadIntegrityError(f"{key} must be string")
    return raw


def _strings(value: dict[str, object], key: str) -> tuple[str, ...]:
    raw = value.get(key)
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise PromotionAssessmentReadIntegrityError(f"{key} must be a string list")
    return tuple(raw)


def _boolean(value: dict[str, object], key: str) -> bool:
    raw = value.get(key)
    if not isinstance(raw, bool):
        raise PromotionAssessmentReadIntegrityError(f"{key} must be boolean")
    return raw


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PromotionAssessmentReadIntegrityError(f"{label} must be a positive integer")
    return value


def _aware_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise PromotionAssessmentReadIntegrityError(f"{label} must be text")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PromotionAssessmentReadIntegrityError(f"{label} is invalid") from exc
    _require_aware(result, label)
    return result


def _require_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PromotionAssessmentReadIntegrityError(f"{label} must be a canonical identifier")


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PromotionAssessmentReadIntegrityError(f"{label} must be lowercase sha256")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PromotionAssessmentReadIntegrityError(f"{label} must be timezone-aware datetime")


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "PromotionAssessmentReadError",
    "PromotionAssessmentReadIntegrityError",
    "PromotionAssessmentReadMissing",
    "PromotionAssessmentReadModel",
    "PromotionAssessmentReadSnapshot",
    "PromotionAssessmentReadView",
    "PromotionGateReadView",
]
