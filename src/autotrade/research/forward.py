from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping

from .shadow import SQLitePortfolioShadowRegistry, ShadowPeriodRecord


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_GENESIS_HASH = "0" * 64
_FORWARD_DOMAIN = "FORWARD_POST_ACTIVATION"


class ForwardEvidenceError(RuntimeError):
    pass


class ForwardEvidenceIntegrityError(ForwardEvidenceError):
    pass


class ForwardEvidenceConflict(ForwardEvidenceError):
    pass


@dataclass(frozen=True, slots=True)
class FrozenForwardPolicy:
    campaign_id: str
    activated_at: datetime
    shadow_config_fingerprint: str
    frozen_parameters_hash: str
    source_code_hash: str

    def __post_init__(self) -> None:
        if not self.campaign_id.strip():
            raise ValueError("campaign_id is required")
        _require_aware(self.activated_at, "activated_at")
        for label, value in (
            ("shadow_config_fingerprint", self.shadow_config_fingerprint),
            ("frozen_parameters_hash", self.frozen_parameters_hash),
            ("source_code_hash", self.source_code_hash),
        ):
            if not _HASH_RE.fullmatch(value):
                raise ValueError(f"{label} must be lowercase SHA-256 hex")

    @property
    def fingerprint(self) -> str:
        return _hash_payload(_policy_payload(self))


@dataclass(frozen=True, slots=True)
class ForwardPeriodEvidence:
    sequence: int
    policy_fingerprint: str
    domain: str
    period_started_at: datetime
    period_ended_at: datetime
    shadow_record_hash: str
    shadow_config_fingerprint: str
    portfolio_return: Decimal
    nav_after: Decimal
    previous_evidence_hash: str
    evidence_hash: str


@dataclass(frozen=True, slots=True)
class ForwardControlState:
    policy_fingerprint: str
    sequence: int
    head_hash: str
    control_hash: str


class SQLiteForwardEvidenceRegistry:
    """Append-only post-activation evidence sourced only from verified shadow rows.

    The API accepts no dataset split, holdout permit, training result or tuning
    parameters. A source shadow record is retrieved through a verified
    SQLitePortfolioShadowRegistry chain, then referenced by its durable hash.
    Frozen policy hashes can be observed but never recalibrated by this module.
    """

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
                CREATE TABLE IF NOT EXISTS forward_policy (
                    slot INTEGER PRIMARY KEY CHECK(slot = 1),
                    policy_fingerprint TEXT NOT NULL,
                    policy_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forward_records (
                    sequence INTEGER PRIMARY KEY,
                    period_started_at TEXT NOT NULL UNIQUE,
                    period_ended_at TEXT NOT NULL,
                    shadow_record_hash TEXT NOT NULL UNIQUE,
                    previous_evidence_hash TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL UNIQUE,
                    evidence_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS forward_control (
                    slot INTEGER PRIMARY KEY CHECK(slot = 1),
                    policy_fingerprint TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    head_hash TEXT NOT NULL,
                    control_hash TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            self._close_if_file(conn)

    def register_policy(self, policy: FrozenForwardPolicy) -> FrozenForwardPolicy:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM forward_policy WHERE slot = 1").fetchone()
            if row is not None:
                existing = _policy_from_row(row)
                if existing.fingerprint != policy.fingerprint:
                    raise ForwardEvidenceConflict("forward policy is already frozen")
                self._verify_all_locked(conn)
                conn.commit()
                return existing

            if conn.execute("SELECT 1 FROM forward_records LIMIT 1").fetchone() is not None:
                raise ForwardEvidenceIntegrityError("forward records exist without policy")
            if conn.execute("SELECT 1 FROM forward_control LIMIT 1").fetchone() is not None:
                raise ForwardEvidenceIntegrityError("forward control exists without policy")

            conn.execute(
                "INSERT INTO forward_policy(slot, policy_fingerprint, policy_json) VALUES (1, ?, ?)",
                (policy.fingerprint, _canonical_json(_policy_payload(policy))),
            )
            control = _make_control(
                policy_fingerprint=policy.fingerprint,
                sequence=0,
                head_hash=_GENESIS_HASH,
            )
            conn.execute(
                """
                INSERT INTO forward_control(
                    slot, policy_fingerprint, sequence, head_hash, control_hash
                ) VALUES (1, ?, ?, ?, ?)
                """,
                (
                    control.policy_fingerprint,
                    control.sequence,
                    control.head_hash,
                    control.control_hash,
                ),
            )
            conn.commit()
            return policy
        except Exception:
            conn.rollback()
            raise
        finally:
            self._close_if_file(conn)

    def append_shadow_record(
        self,
        *,
        shadow_registry: SQLitePortfolioShadowRegistry,
        shadow_record_hash: str,
    ) -> ForwardPeriodEvidence:
        if not _HASH_RE.fullmatch(shadow_record_hash):
            raise ForwardEvidenceIntegrityError("shadow_record_hash must be lowercase SHA-256 hex")

        # list_records() verifies the complete shadow chain and its anchored head
        # before this module accepts any source evidence.
        shadow_records = shadow_registry.list_records()
        matches = [record for record in shadow_records if record.record_hash == shadow_record_hash]
        if len(matches) != 1:
            raise ForwardEvidenceIntegrityError("shadow record is not present in verified shadow evidence")
        shadow_record = matches[0]
        shadow_config = shadow_registry.get_config()

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            policy, records, control = self._verify_all_locked(conn)
            if shadow_config.fingerprint != policy.shadow_config_fingerprint:
                raise ForwardEvidenceIntegrityError("shadow config does not match frozen forward policy")
            if shadow_record.config_fingerprint != policy.shadow_config_fingerprint:
                raise ForwardEvidenceIntegrityError("shadow record config does not match frozen forward policy")
            if _utc(shadow_record.period_started_at) < _utc(policy.activated_at):
                raise ForwardEvidenceIntegrityError("forward evidence cannot predate activation")

            by_start = {_utc(record.period_started_at): record for record in records}
            existing = by_start.get(_utc(shadow_record.period_started_at))
            if existing is not None:
                candidate = _build_evidence(
                    policy=policy,
                    shadow_record=shadow_record,
                    sequence=existing.sequence,
                    previous_evidence_hash=existing.previous_evidence_hash,
                )
                if candidate.evidence_hash != existing.evidence_hash:
                    raise ForwardEvidenceConflict("conflicting forward evidence for existing period")
                conn.commit()
                return existing

            if records and _utc(shadow_record.period_started_at) != _utc(records[-1].period_ended_at):
                raise ForwardEvidenceIntegrityError("forward evidence periods must be strictly contiguous")

            evidence = _build_evidence(
                policy=policy,
                shadow_record=shadow_record,
                sequence=control.sequence + 1,
                previous_evidence_hash=control.head_hash,
            )
            conn.execute(
                """
                INSERT INTO forward_records(
                    sequence, period_started_at, period_ended_at, shadow_record_hash,
                    previous_evidence_hash, evidence_hash, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.sequence,
                    _iso(evidence.period_started_at),
                    _iso(evidence.period_ended_at),
                    evidence.shadow_record_hash,
                    evidence.previous_evidence_hash,
                    evidence.evidence_hash,
                    _canonical_json(_evidence_payload_without_hash(evidence)),
                ),
            )
            new_control = _make_control(
                policy_fingerprint=policy.fingerprint,
                sequence=evidence.sequence,
                head_hash=evidence.evidence_hash,
            )
            conn.execute(
                """
                UPDATE forward_control
                SET policy_fingerprint = ?, sequence = ?, head_hash = ?, control_hash = ?
                WHERE slot = 1
                """,
                (
                    new_control.policy_fingerprint,
                    new_control.sequence,
                    new_control.head_hash,
                    new_control.control_hash,
                ),
            )
            conn.commit()
            return evidence
        except Exception:
            conn.rollback()
            raise
        finally:
            self._close_if_file(conn)

    def get_policy(self) -> FrozenForwardPolicy:
        conn = self._connect()
        try:
            policy, _, _ = self._verify_all_locked(conn)
            return policy
        finally:
            self._close_if_file(conn)

    def list_records(self) -> tuple[ForwardPeriodEvidence, ...]:
        conn = self._connect()
        try:
            _, records, _ = self._verify_all_locked(conn)
            return records
        finally:
            self._close_if_file(conn)

    def control_state(self) -> ForwardControlState:
        conn = self._connect()
        try:
            _, _, control = self._verify_all_locked(conn)
            return control
        finally:
            self._close_if_file(conn)

    def _verify_all_locked(
        self, conn: sqlite3.Connection
    ) -> tuple[FrozenForwardPolicy, tuple[ForwardPeriodEvidence, ...], ForwardControlState]:
        policy_row = conn.execute("SELECT * FROM forward_policy WHERE slot = 1").fetchone()
        if policy_row is None:
            if conn.execute("SELECT 1 FROM forward_records LIMIT 1").fetchone() is not None:
                raise ForwardEvidenceIntegrityError("forward records exist without policy")
            if conn.execute("SELECT 1 FROM forward_control LIMIT 1").fetchone() is not None:
                raise ForwardEvidenceIntegrityError("forward control exists without policy")
            raise ForwardEvidenceIntegrityError("forward policy is not initialized")
        policy = _policy_from_row(policy_row)

        control_row = conn.execute("SELECT * FROM forward_control WHERE slot = 1").fetchone()
        if control_row is None:
            raise ForwardEvidenceIntegrityError("forward control anchor is missing")
        control = _control_from_row(control_row)
        if control.policy_fingerprint != policy.fingerprint:
            raise ForwardEvidenceIntegrityError("forward control policy fingerprint mismatch")

        rows = conn.execute("SELECT * FROM forward_records ORDER BY sequence").fetchall()
        records: list[ForwardPeriodEvidence] = []
        expected_sequence = 1
        previous_hash = _GENESIS_HASH
        previous_end: datetime | None = None
        for row in rows:
            record = _evidence_from_row(row)
            if record.sequence != expected_sequence:
                raise ForwardEvidenceIntegrityError("forward evidence sequence gap or reordering detected")
            if record.policy_fingerprint != policy.fingerprint:
                raise ForwardEvidenceIntegrityError("forward evidence policy fingerprint mismatch")
            if record.domain != _FORWARD_DOMAIN:
                raise ForwardEvidenceIntegrityError("forward evidence domain mismatch")
            if record.previous_evidence_hash != previous_hash:
                raise ForwardEvidenceIntegrityError("forward previous-hash linkage mismatch")
            if _utc(record.period_started_at) < _utc(policy.activated_at):
                raise ForwardEvidenceIntegrityError("persisted forward evidence predates activation")
            if previous_end is not None and _utc(record.period_started_at) != _utc(previous_end):
                raise ForwardEvidenceIntegrityError("forward period continuity mismatch")
            if record.shadow_config_fingerprint != policy.shadow_config_fingerprint:
                raise ForwardEvidenceIntegrityError("forward shadow config fingerprint mismatch")
            if not _finite_decimal(record.portfolio_return) or not _finite_positive(record.nav_after):
                raise ForwardEvidenceIntegrityError("forward numeric evidence is invalid")
            if record.evidence_hash != _hash_payload(_evidence_payload_without_hash(record)):
                raise ForwardEvidenceIntegrityError("forward evidence hash mismatch")

            records.append(record)
            expected_sequence += 1
            previous_hash = record.evidence_hash
            previous_end = record.period_ended_at

        expected_head = records[-1].evidence_hash if records else _GENESIS_HASH
        if control.sequence != len(records):
            raise ForwardEvidenceIntegrityError("forward control sequence does not match durable records")
        if control.head_hash != expected_head:
            raise ForwardEvidenceIntegrityError("forward control head does not match durable records")
        if control.control_hash != _control_hash(
            policy_fingerprint=control.policy_fingerprint,
            sequence=control.sequence,
            head_hash=control.head_hash,
        ):
            raise ForwardEvidenceIntegrityError("forward control hash mismatch")
        return policy, tuple(records), control


def _build_evidence(
    *,
    policy: FrozenForwardPolicy,
    shadow_record: ShadowPeriodRecord,
    sequence: int,
    previous_evidence_hash: str,
) -> ForwardPeriodEvidence:
    if sequence <= 0:
        raise ForwardEvidenceIntegrityError("forward sequence must be positive")
    if not _HASH_RE.fullmatch(previous_evidence_hash):
        raise ForwardEvidenceIntegrityError("previous_evidence_hash is invalid")
    if not _HASH_RE.fullmatch(shadow_record.record_hash):
        raise ForwardEvidenceIntegrityError("shadow record hash is invalid")
    provisional = ForwardPeriodEvidence(
        sequence=sequence,
        policy_fingerprint=policy.fingerprint,
        domain=_FORWARD_DOMAIN,
        period_started_at=shadow_record.period_started_at,
        period_ended_at=shadow_record.period_ended_at,
        shadow_record_hash=shadow_record.record_hash,
        shadow_config_fingerprint=shadow_record.config_fingerprint,
        portfolio_return=shadow_record.weighted_return,
        nav_after=shadow_record.nav_after,
        previous_evidence_hash=previous_evidence_hash,
        evidence_hash="",
    )
    evidence_hash = _hash_payload(_evidence_payload_without_hash(provisional))
    return ForwardPeriodEvidence(
        sequence=provisional.sequence,
        policy_fingerprint=provisional.policy_fingerprint,
        domain=provisional.domain,
        period_started_at=provisional.period_started_at,
        period_ended_at=provisional.period_ended_at,
        shadow_record_hash=provisional.shadow_record_hash,
        shadow_config_fingerprint=provisional.shadow_config_fingerprint,
        portfolio_return=provisional.portfolio_return,
        nav_after=provisional.nav_after,
        previous_evidence_hash=provisional.previous_evidence_hash,
        evidence_hash=evidence_hash,
    )


def _policy_payload(policy: FrozenForwardPolicy) -> dict[str, object]:
    return {
        "campaign_id": policy.campaign_id,
        "activated_at": _iso(policy.activated_at),
        "shadow_config_fingerprint": policy.shadow_config_fingerprint,
        "frozen_parameters_hash": policy.frozen_parameters_hash,
        "source_code_hash": policy.source_code_hash,
    }


def _evidence_payload_without_hash(record: ForwardPeriodEvidence) -> dict[str, object]:
    return {
        "sequence": record.sequence,
        "policy_fingerprint": record.policy_fingerprint,
        "domain": record.domain,
        "period_started_at": _iso(record.period_started_at),
        "period_ended_at": _iso(record.period_ended_at),
        "shadow_record_hash": record.shadow_record_hash,
        "shadow_config_fingerprint": record.shadow_config_fingerprint,
        "portfolio_return": str(record.portfolio_return),
        "nav_after": str(record.nav_after),
        "previous_evidence_hash": record.previous_evidence_hash,
    }


def _policy_from_row(row: sqlite3.Row) -> FrozenForwardPolicy:
    payload = _strict_json_object(row["policy_json"])
    try:
        policy = FrozenForwardPolicy(
            campaign_id=_required_str(payload, "campaign_id"),
            activated_at=_parse_datetime(payload.get("activated_at"), "activated_at"),
            shadow_config_fingerprint=_required_str(payload, "shadow_config_fingerprint"),
            frozen_parameters_hash=_required_str(payload, "frozen_parameters_hash"),
            source_code_hash=_required_str(payload, "source_code_hash"),
        )
    except ValueError as exc:
        raise ForwardEvidenceIntegrityError("invalid persisted forward policy") from exc
    if row["policy_fingerprint"] != policy.fingerprint:
        raise ForwardEvidenceIntegrityError("persisted forward policy fingerprint mismatch")
    if row["policy_json"] != _canonical_json(_policy_payload(policy)):
        raise ForwardEvidenceIntegrityError("persisted forward policy is not canonical")
    return policy


def _evidence_from_row(row: sqlite3.Row) -> ForwardPeriodEvidence:
    payload = _strict_json_object(row["evidence_json"])
    try:
        record = ForwardPeriodEvidence(
            sequence=_parse_int(payload.get("sequence"), "sequence"),
            policy_fingerprint=_required_str(payload, "policy_fingerprint"),
            domain=_required_str(payload, "domain"),
            period_started_at=_parse_datetime(payload.get("period_started_at"), "period_started_at"),
            period_ended_at=_parse_datetime(payload.get("period_ended_at"), "period_ended_at"),
            shadow_record_hash=_required_str(payload, "shadow_record_hash"),
            shadow_config_fingerprint=_required_str(payload, "shadow_config_fingerprint"),
            portfolio_return=_parse_decimal(payload.get("portfolio_return"), "portfolio_return"),
            nav_after=_parse_decimal(payload.get("nav_after"), "nav_after"),
            previous_evidence_hash=_required_str(payload, "previous_evidence_hash"),
            evidence_hash=str(row["evidence_hash"]),
        )
    except ValueError as exc:
        raise ForwardEvidenceIntegrityError("invalid persisted forward evidence") from exc
    if row["sequence"] != record.sequence:
        raise ForwardEvidenceIntegrityError("forward sequence column mismatch")
    if row["period_started_at"] != _iso(record.period_started_at):
        raise ForwardEvidenceIntegrityError("forward period-start column mismatch")
    if row["period_ended_at"] != _iso(record.period_ended_at):
        raise ForwardEvidenceIntegrityError("forward period-end column mismatch")
    if row["shadow_record_hash"] != record.shadow_record_hash:
        raise ForwardEvidenceIntegrityError("forward shadow-record column mismatch")
    if row["previous_evidence_hash"] != record.previous_evidence_hash:
        raise ForwardEvidenceIntegrityError("forward previous-hash column mismatch")
    if row["evidence_json"] != _canonical_json(_evidence_payload_without_hash(record)):
        raise ForwardEvidenceIntegrityError("persisted forward evidence is not canonical")
    if not _HASH_RE.fullmatch(record.evidence_hash):
        raise ForwardEvidenceIntegrityError("persisted forward evidence hash is invalid")
    if not _HASH_RE.fullmatch(record.shadow_record_hash):
        raise ForwardEvidenceIntegrityError("persisted shadow record hash is invalid")
    return record


def _control_from_row(row: sqlite3.Row) -> ForwardControlState:
    try:
        policy_fingerprint = str(row["policy_fingerprint"])
        sequence_raw = row["sequence"]
        if isinstance(sequence_raw, bool) or not isinstance(sequence_raw, int):
            raise ValueError("sequence must be integer")
        sequence = sequence_raw
        head_hash = str(row["head_hash"])
        control_hash = str(row["control_hash"])
    except (ValueError, TypeError) as exc:
        raise ForwardEvidenceIntegrityError("invalid forward control row") from exc
    if sequence < 0 or not _HASH_RE.fullmatch(policy_fingerprint):
        raise ForwardEvidenceIntegrityError("forward control identity is invalid")
    if not _HASH_RE.fullmatch(head_hash) or not _HASH_RE.fullmatch(control_hash):
        raise ForwardEvidenceIntegrityError("forward control hashes are invalid")
    return ForwardControlState(
        policy_fingerprint=policy_fingerprint,
        sequence=sequence,
        head_hash=head_hash,
        control_hash=control_hash,
    )


def _make_control(*, policy_fingerprint: str, sequence: int, head_hash: str) -> ForwardControlState:
    return ForwardControlState(
        policy_fingerprint=policy_fingerprint,
        sequence=sequence,
        head_hash=head_hash,
        control_hash=_control_hash(
            policy_fingerprint=policy_fingerprint,
            sequence=sequence,
            head_hash=head_hash,
        ),
    )


def _control_hash(*, policy_fingerprint: str, sequence: int, head_hash: str) -> str:
    return _hash_payload(
        {
            "policy_fingerprint": policy_fingerprint,
            "sequence": sequence,
            "head_hash": head_hash,
        }
    )


def _strict_json_object(raw: str) -> Mapping[str, object]:
    try:
        value = json.loads(raw, parse_constant=lambda token: _raise_json_constant(token))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ForwardEvidenceIntegrityError("persisted forward JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ForwardEvidenceIntegrityError("persisted forward JSON root must be an object")
    return value


def _raise_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def _parse_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _parse_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not parsed.is_finite():
        raise ValueError(f"{label} must be finite")
    return parsed


def _parse_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    _require_aware(parsed, label)
    return parsed


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash_payload(payload: object) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _utc(value: datetime) -> datetime:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _finite_decimal(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


def _finite_positive(value: Decimal) -> bool:
    return _finite_decimal(value) and value > 0
