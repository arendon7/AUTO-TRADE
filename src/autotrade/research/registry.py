from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import sqlite3
from typing import Mapping

from .backtest import BacktestResult


class ExperimentConflict(RuntimeError):
    pass


class HoldoutPermitConsumed(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HoldoutPermit:
    permit_id: str
    issued_by: str
    purpose: str = "final_validation"

    def __post_init__(self) -> None:
        if not self.permit_id.strip():
            raise ValueError("permit_id is required")
        if not self.issued_by.strip():
            raise ValueError("issued_by is required")
        if self.purpose != "final_validation":
            raise ValueError("holdout permits are restricted to final_validation")


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    dataset_hash: str
    split_name: str
    strategy_id: str
    strategy_version: str
    strategy_parameters: Mapping[str, str | int | float | bool]
    config_hash: str
    code_version: str

    def __post_init__(self) -> None:
        for name, value in (
            ("dataset_hash", self.dataset_hash),
            ("split_name", self.split_name),
            ("strategy_id", self.strategy_id),
            ("strategy_version", self.strategy_version),
            ("config_hash", self.config_hash),
            ("code_version", self.code_version),
        ):
            if not value.strip():
                raise ValueError(f"{name} is required")
        _canonical_json(dict(self.strategy_parameters))

    @property
    def fingerprint(self) -> str:
        payload = {
            "dataset_hash": self.dataset_hash,
            "split_name": self.split_name,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_parameters": dict(self.strategy_parameters),
            "config_hash": self.config_hash,
            "code_version": self.code_version,
        }
        return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    run_id: str
    fingerprint: str
    result_hash: str
    created_at: datetime
    spec: ExperimentSpec
    metrics: Mapping[str, float | int | str]
    artifacts: Mapping[str, str]


class SQLiteExperimentRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = None
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self.path == ":memory:":
            if self._connection is None:
                self._connection = sqlite3.connect(":memory:")
                self._connection.row_factory = sqlite3.Row
            return self._connection
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _close_if_file(self, conn: sqlite3.Connection) -> None:
        if self.path != ":memory:":
            conn.close()

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    run_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    result_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS holdout_permits (
                    permit_id TEXT PRIMARY KEY,
                    issued_by TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    used_at TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            self._close_if_file(conn)

    def record(
        self,
        *,
        spec: ExperimentSpec,
        result: BacktestResult,
        now: datetime,
        artifacts: Mapping[str, str] | None = None,
    ) -> ExperimentRecord:
        self._validate_result_matches_spec(spec=spec, result=result)
        fingerprint = spec.fingerprint
        result_hash = result.result_hash
        run_id = f"exp-{fingerprint[:24]}"
        metrics = _safe_metrics(asdict(result.metrics))
        artifact_map = dict(artifacts or {})
        spec_json = _canonical_json(_spec_payload(spec))
        metrics_json = _canonical_json(metrics)
        artifacts_json = _canonical_json(artifact_map)

        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT * FROM experiments WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            if existing is not None:
                record = _record_from_row(existing)
                if record.result_hash != result_hash:
                    raise ExperimentConflict(
                        "same experiment spec produced a different result hash"
                    )
                return record

            conn.execute(
                """
                INSERT INTO experiments(
                    run_id, fingerprint, result_hash, created_at,
                    spec_json, metrics_json, artifacts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    fingerprint,
                    result_hash,
                    now.isoformat(),
                    spec_json,
                    metrics_json,
                    artifacts_json,
                ),
            )
            conn.commit()
            return ExperimentRecord(
                run_id=run_id,
                fingerprint=fingerprint,
                result_hash=result_hash,
                created_at=now,
                spec=spec,
                metrics=metrics,
                artifacts=artifact_map,
            )
        finally:
            self._close_if_file(conn)

    def get(self, run_id: str) -> ExperimentRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM experiments WHERE run_id = ?", (run_id,)
            ).fetchone()
            return _record_from_row(row) if row is not None else None
        finally:
            self._close_if_file(conn)

    def list_records(self) -> tuple[ExperimentRecord, ...]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM experiments ORDER BY created_at, run_id"
            ).fetchall()
            return tuple(_record_from_row(row) for row in rows)
        finally:
            self._close_if_file(conn)

    def consume_holdout_permit(self, *, permit: HoldoutPermit, now: datetime) -> None:
        conn = self._connect()
        try:
            try:
                conn.execute(
                    """
                    INSERT INTO holdout_permits(permit_id, issued_by, purpose, used_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (permit.permit_id, permit.issued_by, permit.purpose, now.isoformat()),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise HoldoutPermitConsumed(permit.permit_id) from exc
        finally:
            self._close_if_file(conn)

    @staticmethod
    def _validate_result_matches_spec(
        *, spec: ExperimentSpec, result: BacktestResult
    ) -> None:
        if result.dataset_hash != spec.dataset_hash:
            raise ExperimentConflict("dataset hash does not match experiment spec")
        if result.strategy_id != spec.strategy_id:
            raise ExperimentConflict("strategy_id does not match experiment spec")
        if result.strategy_version != spec.strategy_version:
            raise ExperimentConflict("strategy_version does not match experiment spec")
        if result.config_hash != spec.config_hash:
            raise ExperimentConflict("config hash does not match experiment spec")
        if result.strategy_parameters != dict(spec.strategy_parameters):
            raise ExperimentConflict("strategy parameters do not match experiment spec")


def _spec_payload(spec: ExperimentSpec) -> dict[str, object]:
    return {
        "dataset_hash": spec.dataset_hash,
        "split_name": spec.split_name,
        "strategy_id": spec.strategy_id,
        "strategy_version": spec.strategy_version,
        "strategy_parameters": dict(spec.strategy_parameters),
        "config_hash": spec.config_hash,
        "code_version": spec.code_version,
    }


def _record_from_row(row: sqlite3.Row) -> ExperimentRecord:
    spec_data = json.loads(row["spec_json"])
    spec = ExperimentSpec(
        dataset_hash=spec_data["dataset_hash"],
        split_name=spec_data["split_name"],
        strategy_id=spec_data["strategy_id"],
        strategy_version=spec_data["strategy_version"],
        strategy_parameters=spec_data["strategy_parameters"],
        config_hash=spec_data["config_hash"],
        code_version=spec_data["code_version"],
    )
    return ExperimentRecord(
        run_id=row["run_id"],
        fingerprint=row["fingerprint"],
        result_hash=row["result_hash"],
        created_at=datetime.fromisoformat(row["created_at"]),
        spec=spec,
        metrics=json.loads(row["metrics_json"]),
        artifacts=json.loads(row["artifacts_json"]),
    )


def _safe_metrics(values: Mapping[str, object]) -> dict[str, float | int | str]:
    safe: dict[str, float | int | str] = {}
    for key, value in values.items():
        if isinstance(value, float) and not isfinite(value):
            if value == inf:
                safe[key] = "inf"
            elif value == -inf:
                safe[key] = "-inf"
            else:
                safe[key] = "nan"
        elif isinstance(value, (float, int)):
            safe[key] = value
        else:
            safe[key] = str(value)
    return safe


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
