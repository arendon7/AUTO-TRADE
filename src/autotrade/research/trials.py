from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping

from autotrade.persistence import SQLiteRuntime


class TrialLedgerError(RuntimeError):
    pass


class TrialConflict(TrialLedgerError):
    pass


class TrialGovernanceError(TrialLedgerError):
    pass


class TrialPhase(StrEnum):
    TRAIN = "TRAIN"
    DEVELOPMENT = "DEVELOPMENT"
    FINAL_HOLDOUT = "FINAL_HOLDOUT"


class TrialStatus(StrEnum):
    PREREGISTERED = "PREREGISTERED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    @property
    def terminal(self) -> bool:
        return self in {TrialStatus.COMPLETED, TrialStatus.FAILED}


@dataclass(frozen=True, slots=True)
class CampaignSpec:
    campaign_id: str
    family_id: str
    expected_trial_ids: tuple[str, ...]
    code_version: str
    purpose: str

    def __post_init__(self) -> None:
        for name, value in (
            ("campaign_id", self.campaign_id),
            ("family_id", self.family_id),
            ("code_version", self.code_version),
            ("purpose", self.purpose),
        ):
            if not value.strip():
                raise ValueError(f"{name} is required")
        if not self.expected_trial_ids:
            raise ValueError("expected_trial_ids cannot be empty")
        if any(not value.strip() for value in self.expected_trial_ids):
            raise ValueError("expected_trial_ids cannot contain blanks")
        if len(set(self.expected_trial_ids)) != len(self.expected_trial_ids):
            raise ValueError("expected_trial_ids must be unique")

    @property
    def fingerprint(self) -> str:
        return _sha256(_canonical_json(_campaign_payload(self)))


@dataclass(frozen=True, slots=True)
class TrialSpec:
    trial_id: str
    campaign_id: str
    hypothesis_id: str
    strategy_id: str
    strategy_version: str
    dataset_hash: str
    split_name: str
    phase: TrialPhase
    parameters: Mapping[str, str | int | float | bool]
    code_version: str
    holdout_authorization_id: str = ""

    def __post_init__(self) -> None:
        for name, value in (
            ("trial_id", self.trial_id),
            ("campaign_id", self.campaign_id),
            ("hypothesis_id", self.hypothesis_id),
            ("strategy_id", self.strategy_id),
            ("strategy_version", self.strategy_version),
            ("dataset_hash", self.dataset_hash),
            ("split_name", self.split_name),
            ("code_version", self.code_version),
        ):
            if not value.strip():
                raise ValueError(f"{name} is required")
        _canonical_json(dict(self.parameters))
        split = self.split_name.strip().lower()
        if self.phase is TrialPhase.FINAL_HOLDOUT:
            if "holdout" not in split:
                raise ValueError("FINAL_HOLDOUT trial must bind a holdout split")
            if not self.holdout_authorization_id.strip():
                raise ValueError("FINAL_HOLDOUT trial requires holdout_authorization_id")
        else:
            if "holdout" in split or self.holdout_authorization_id:
                raise ValueError("iterative trials cannot bind HOLDOUT authorization")

    @property
    def fingerprint(self) -> str:
        return _sha256(_canonical_json(_trial_payload(self)))


@dataclass(frozen=True, slots=True)
class TrialRecord:
    spec: TrialSpec
    status: TrialStatus
    preregistered_at: datetime
    terminal_at: datetime | None
    metrics: Mapping[str, str | int | float]
    p_value: Decimal | None
    failure_code: str
    result_hash: str


@dataclass(frozen=True, slots=True)
class CampaignAccounting:
    campaign_id: str
    expected_trial_ids: tuple[str, ...]
    preregistered_trial_ids: tuple[str, ...]
    completed_trial_ids: tuple[str, ...]
    failed_trial_ids: tuple[str, ...]
    missing_preregistration_ids: tuple[str, ...]
    unterminated_trial_ids: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_preregistration_ids and not self.unterminated_trial_ids


class SQLiteTrialLedger:
    def __init__(self, runtime: SQLiteRuntime | str | Path) -> None:
        self._runtime = runtime if isinstance(runtime, SQLiteRuntime) else SQLiteRuntime(runtime)
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS research_trials (
                    trial_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    preregistered_at TEXT NOT NULL,
                    terminal_at TEXT,
                    result_json TEXT,
                    result_hash TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(campaign_id) REFERENCES research_campaigns(campaign_id)
                );
                CREATE INDEX IF NOT EXISTS idx_research_trials_campaign
                    ON research_trials(campaign_id, trial_id);
                """
            )
        finally:
            conn.close()

    def create_campaign(self, spec: CampaignSpec, *, now: datetime) -> CampaignSpec:
        _require_aware(now)
        payload = _canonical_json(_campaign_payload(spec))
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT fingerprint, spec_json FROM research_campaigns WHERE campaign_id = ?",
                (spec.campaign_id,),
            ).fetchone()
            if row is not None:
                if row["fingerprint"] != spec.fingerprint or row["spec_json"] != payload:
                    raise TrialConflict(f"campaign identity conflict: {spec.campaign_id}")
                conn.execute("COMMIT")
                return spec
            conn.execute(
                """
                INSERT INTO research_campaigns(campaign_id, fingerprint, spec_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (spec.campaign_id, spec.fingerprint, payload, now.isoformat()),
            )
            conn.execute("COMMIT")
            return spec
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def preregister(self, spec: TrialSpec, *, now: datetime) -> TrialRecord:
        _require_aware(now)
        payload = _canonical_json(_trial_payload(spec))
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            campaign = conn.execute(
                "SELECT spec_json FROM research_campaigns WHERE campaign_id = ?",
                (spec.campaign_id,),
            ).fetchone()
            if campaign is None:
                raise TrialGovernanceError("trial campaign does not exist")
            campaign_spec = _campaign_from_json(campaign["spec_json"])
            if spec.trial_id not in campaign_spec.expected_trial_ids:
                raise TrialGovernanceError(
                    f"trial {spec.trial_id} is not in frozen campaign universe"
                )
            existing = conn.execute(
                "SELECT * FROM research_trials WHERE trial_id = ?", (spec.trial_id,)
            ).fetchone()
            if existing is not None:
                record = _trial_record_from_row(existing)
                if record.spec.fingerprint != spec.fingerprint:
                    raise TrialConflict(f"trial identity conflict: {spec.trial_id}")
                conn.execute("COMMIT")
                return record
            conn.execute(
                """
                INSERT INTO research_trials(
                    trial_id, campaign_id, fingerprint, spec_json, status,
                    preregistered_at, terminal_at, result_json, result_hash
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, '')
                """,
                (
                    spec.trial_id,
                    spec.campaign_id,
                    spec.fingerprint,
                    payload,
                    TrialStatus.PREREGISTERED.value,
                    now.isoformat(),
                ),
            )
            conn.execute("COMMIT")
            return TrialRecord(
                spec=spec,
                status=TrialStatus.PREREGISTERED,
                preregistered_at=now,
                terminal_at=None,
                metrics={},
                p_value=None,
                failure_code="",
                result_hash="",
            )
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def record_completed(
        self,
        *,
        trial_id: str,
        metrics: Mapping[str, str | int | float],
        p_value: Decimal | None,
        now: datetime,
    ) -> TrialRecord:
        if p_value is not None and (
            not p_value.is_finite() or p_value < 0 or p_value > 1
        ):
            raise ValueError("p_value must be finite and between 0 and 1")
        result = {
            "status": TrialStatus.COMPLETED.value,
            "metrics": dict(metrics),
            "p_value": str(p_value) if p_value is not None else None,
            "failure_code": "",
        }
        return self._record_terminal(trial_id=trial_id, result=result, now=now)

    def record_failed(
        self, *, trial_id: str, failure_code: str, now: datetime
    ) -> TrialRecord:
        if not failure_code.strip():
            raise ValueError("failure_code is required")
        result = {
            "status": TrialStatus.FAILED.value,
            "metrics": {},
            "p_value": None,
            "failure_code": failure_code,
        }
        return self._record_terminal(trial_id=trial_id, result=result, now=now)

    def _record_terminal(
        self, *, trial_id: str, result: Mapping[str, object], now: datetime
    ) -> TrialRecord:
        _require_aware(now)
        result_json = _canonical_json(dict(result))
        result_hash = _sha256(result_json)
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM research_trials WHERE trial_id = ?", (trial_id,)
            ).fetchone()
            if row is None:
                raise TrialGovernanceError("result cannot be recorded before preregistration")
            existing = _trial_record_from_row(row)
            if existing.status.terminal:
                if existing.result_hash != result_hash:
                    raise TrialConflict(f"terminal trial result conflict: {trial_id}")
                conn.execute("COMMIT")
                return existing
            if now < existing.preregistered_at:
                raise TrialGovernanceError("trial result cannot predate preregistration")
            conn.execute(
                """
                UPDATE research_trials
                SET status = ?, terminal_at = ?, result_json = ?, result_hash = ?
                WHERE trial_id = ?
                """,
                (result["status"], now.isoformat(), result_json, result_hash, trial_id),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        record = self.get_trial(trial_id)
        assert record is not None
        return record

    def get_trial(self, trial_id: str) -> TrialRecord | None:
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT * FROM research_trials WHERE trial_id = ?", (trial_id,)
            ).fetchone()
            return _trial_record_from_row(row) if row is not None else None
        finally:
            conn.close()

    def list_trials(self, campaign_id: str) -> tuple[TrialRecord, ...]:
        conn = self._runtime.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM research_trials WHERE campaign_id = ? ORDER BY trial_id",
                (campaign_id,),
            ).fetchall()
            return tuple(_trial_record_from_row(row) for row in rows)
        finally:
            conn.close()

    def campaign_accounting(self, campaign_id: str) -> CampaignAccounting:
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT spec_json FROM research_campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            if row is None:
                raise TrialGovernanceError(f"unknown campaign: {campaign_id}")
            campaign = _campaign_from_json(row["spec_json"])
        finally:
            conn.close()
        records = self.list_trials(campaign_id)
        by_id = {record.spec.trial_id: record for record in records}
        expected = campaign.expected_trial_ids
        preregistered = tuple(trial_id for trial_id in expected if trial_id in by_id)
        completed = tuple(
            trial_id
            for trial_id in expected
            if trial_id in by_id and by_id[trial_id].status is TrialStatus.COMPLETED
        )
        failed = tuple(
            trial_id
            for trial_id in expected
            if trial_id in by_id and by_id[trial_id].status is TrialStatus.FAILED
        )
        missing = tuple(trial_id for trial_id in expected if trial_id not in by_id)
        unterminated = tuple(
            trial_id
            for trial_id in expected
            if trial_id in by_id and not by_id[trial_id].status.terminal
        )
        return CampaignAccounting(
            campaign_id=campaign_id,
            expected_trial_ids=expected,
            preregistered_trial_ids=preregistered,
            completed_trial_ids=completed,
            failed_trial_ids=failed,
            missing_preregistration_ids=missing,
            unterminated_trial_ids=unterminated,
        )

    def require_complete_campaign(self, campaign_id: str) -> CampaignAccounting:
        accounting = self.campaign_accounting(campaign_id)
        if not accounting.complete:
            raise TrialGovernanceError(
                "campaign accounting incomplete: "
                f"missing={accounting.missing_preregistration_ids}, "
                f"unterminated={accounting.unterminated_trial_ids}"
            )
        return accounting


def _campaign_payload(spec: CampaignSpec) -> dict[str, object]:
    return {
        "campaign_id": spec.campaign_id,
        "family_id": spec.family_id,
        "expected_trial_ids": list(spec.expected_trial_ids),
        "code_version": spec.code_version,
        "purpose": spec.purpose,
    }


def _trial_payload(spec: TrialSpec) -> dict[str, object]:
    return {
        "trial_id": spec.trial_id,
        "campaign_id": spec.campaign_id,
        "hypothesis_id": spec.hypothesis_id,
        "strategy_id": spec.strategy_id,
        "strategy_version": spec.strategy_version,
        "dataset_hash": spec.dataset_hash,
        "split_name": spec.split_name,
        "phase": spec.phase.value,
        "parameters": dict(spec.parameters),
        "code_version": spec.code_version,
        "holdout_authorization_id": spec.holdout_authorization_id,
    }


def _campaign_from_json(raw: str) -> CampaignSpec:
    value = json.loads(raw)
    return CampaignSpec(
        campaign_id=value["campaign_id"],
        family_id=value["family_id"],
        expected_trial_ids=tuple(value["expected_trial_ids"]),
        code_version=value["code_version"],
        purpose=value["purpose"],
    )


def _trial_from_json(raw: str) -> TrialSpec:
    value = json.loads(raw)
    return TrialSpec(
        trial_id=value["trial_id"],
        campaign_id=value["campaign_id"],
        hypothesis_id=value["hypothesis_id"],
        strategy_id=value["strategy_id"],
        strategy_version=value["strategy_version"],
        dataset_hash=value["dataset_hash"],
        split_name=value["split_name"],
        phase=TrialPhase(value["phase"]),
        parameters=value["parameters"],
        code_version=value["code_version"],
        holdout_authorization_id=value["holdout_authorization_id"],
    )


def _trial_record_from_row(row) -> TrialRecord:
    spec = _trial_from_json(row["spec_json"])
    result = json.loads(row["result_json"]) if row["result_json"] else {}
    p_value_raw = result.get("p_value")
    return TrialRecord(
        spec=spec,
        status=TrialStatus(row["status"]),
        preregistered_at=datetime.fromisoformat(row["preregistered_at"]),
        terminal_at=datetime.fromisoformat(row["terminal_at"]) if row["terminal_at"] else None,
        metrics=result.get("metrics", {}),
        p_value=Decimal(p_value_raw) if p_value_raw is not None else None,
        failure_code=result.get("failure_code", ""),
        result_hash=row["result_hash"],
    )


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("trial/campaign payload must be canonical JSON") from exc


def _sha256(raw: str) -> str:
    return sha256(raw.encode("utf-8")).hexdigest()


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
