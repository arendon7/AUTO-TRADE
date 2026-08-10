from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Mapping


class ValidationEvidenceConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ValidationEvidenceSpec:
    strategy_fingerprint: str
    dataset_hashes: tuple[str, ...]
    policy_hash: str
    stage: str
    code_version: str

    def __post_init__(self) -> None:
        if not self.strategy_fingerprint.strip():
            raise ValueError("strategy_fingerprint is required")
        if not self.dataset_hashes or any(not value.strip() for value in self.dataset_hashes):
            raise ValueError("dataset_hashes must be non-empty")
        if len(set(self.dataset_hashes)) != len(self.dataset_hashes):
            raise ValueError("dataset_hashes must be unique")
        if not self.policy_hash.strip():
            raise ValueError("policy_hash is required")
        if self.stage not in {"development", "final_holdout"}:
            raise ValueError("stage must be development or final_holdout")
        if not self.code_version.strip():
            raise ValueError("code_version is required")

    @property
    def fingerprint(self) -> str:
        return sha256(_canonical_json(self.payload).encode("utf-8")).hexdigest()

    @property
    def payload(self) -> dict[str, object]:
        return {
            "strategy_fingerprint": self.strategy_fingerprint,
            "dataset_hashes": list(self.dataset_hashes),
            "policy_hash": self.policy_hash,
            "stage": self.stage,
            "code_version": self.code_version,
        }


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    evidence_id: str
    fingerprint: str
    result_hash: str
    passed: bool
    reason_codes: tuple[str, ...]
    decision_payload: Mapping[str, object]
    created_at: datetime
    spec: ValidationEvidenceSpec


class SQLiteValidationRegistry:
    """Append-only-by-fingerprint validation evidence.

    Re-recording the exact same spec/result is idempotent. If the same spec
    produces a different validation result, the registry fails closed instead
    of silently overwriting history.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._memory_connection: sqlite3.Connection | None = None
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self.path == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
            return self._memory_connection
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _close(self, conn: sqlite3.Connection) -> None:
        if self.path != ":memory:":
            conn.close()

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS validation_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    result_hash TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    decision_payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    spec_json TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            self._close(conn)

    def record(
        self,
        *,
        spec: ValidationEvidenceSpec,
        passed: bool,
        reason_codes: tuple[str, ...],
        decision_payload: Mapping[str, object],
        now: datetime,
    ) -> ValidationEvidence:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("validation evidence timestamp must be timezone-aware")
        if passed and reason_codes:
            raise ValueError("passed validation cannot contain failure reason codes")
        if not passed and not reason_codes:
            raise ValueError("failed validation must contain at least one reason code")
        if len(set(reason_codes)) != len(reason_codes):
            raise ValueError("reason_codes must be unique")

        result_payload = {
            "passed": passed,
            "reason_codes": list(reason_codes),
            "decision_payload": dict(decision_payload),
        }
        result_json = _canonical_json(result_payload)
        result_hash = sha256(result_json.encode("utf-8")).hexdigest()
        fingerprint = spec.fingerprint
        evidence_id = f"validation-{fingerprint[:24]}"

        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT * FROM validation_evidence WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if existing is not None:
                evidence = _from_row(existing)
                if evidence.result_hash != result_hash:
                    raise ValidationEvidenceConflict(
                        "same validation spec produced different evidence"
                    )
                return evidence

            conn.execute(
                """
                INSERT INTO validation_evidence(
                    evidence_id, fingerprint, result_hash, passed,
                    reason_codes_json, decision_payload_json, created_at, spec_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    fingerprint,
                    result_hash,
                    int(passed),
                    _canonical_json(list(reason_codes)),
                    _canonical_json(dict(decision_payload)),
                    now.isoformat(),
                    _canonical_json(spec.payload),
                ),
            )
            conn.commit()
            return ValidationEvidence(
                evidence_id=evidence_id,
                fingerprint=fingerprint,
                result_hash=result_hash,
                passed=passed,
                reason_codes=reason_codes,
                decision_payload=dict(decision_payload),
                created_at=now,
                spec=spec,
            )
        finally:
            self._close(conn)

    def get(self, evidence_id: str) -> ValidationEvidence | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM validation_evidence WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            return _from_row(row) if row is not None else None
        finally:
            self._close(conn)


def _from_row(row: sqlite3.Row) -> ValidationEvidence:
    raw_spec = json.loads(row["spec_json"])
    spec = ValidationEvidenceSpec(
        strategy_fingerprint=raw_spec["strategy_fingerprint"],
        dataset_hashes=tuple(raw_spec["dataset_hashes"]),
        policy_hash=raw_spec["policy_hash"],
        stage=raw_spec["stage"],
        code_version=raw_spec["code_version"],
    )
    return ValidationEvidence(
        evidence_id=row["evidence_id"],
        fingerprint=row["fingerprint"],
        result_hash=row["result_hash"],
        passed=bool(row["passed"]),
        reason_codes=tuple(json.loads(row["reason_codes_json"])),
        decision_payload=json.loads(row["decision_payload_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        spec=spec,
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
