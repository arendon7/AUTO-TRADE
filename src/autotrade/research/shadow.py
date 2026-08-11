from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterable, Mapping


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_STRATEGY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_GENESIS_HASH = "0" * 64


class ShadowError(RuntimeError):
    pass


class ShadowIntegrityError(ShadowError):
    pass


class ShadowConflict(ShadowError):
    pass


@dataclass(frozen=True, slots=True)
class FrozenShadowConfig:
    config_id: str
    activated_at: datetime
    initial_nav: Decimal
    strategy_weights: Mapping[str, Decimal]
    source_config_hash: str

    def __post_init__(self) -> None:
        if not self.config_id.strip():
            raise ValueError("config_id is required")
        _require_aware(self.activated_at, "activated_at")
        if not _finite_positive(self.initial_nav):
            raise ValueError("initial_nav must be finite and > 0")
        if not _HASH_RE.fullmatch(self.source_config_hash):
            raise ValueError("source_config_hash must be lowercase SHA-256 hex")
        if not self.strategy_weights:
            raise ValueError("strategy_weights cannot be empty")

        total = Decimal("0")
        seen: set[str] = set()
        for strategy_id, weight in self.strategy_weights.items():
            if not isinstance(strategy_id, str) or not _STRATEGY_ID_RE.fullmatch(strategy_id):
                raise ValueError("invalid strategy_id in strategy_weights")
            if strategy_id in seen:
                raise ValueError("duplicate strategy_id in strategy_weights")
            seen.add(strategy_id)
            if not _finite_positive(weight):
                raise ValueError("shadow strategy weights must be finite and > 0")
            total += weight
        if total != Decimal("1"):
            raise ValueError("shadow strategy weights must sum exactly to Decimal('1')")

    @property
    def fingerprint(self) -> str:
        return _hash_payload(_config_payload(self))


@dataclass(frozen=True, slots=True)
class StrategyShadowObservation:
    strategy_id: str
    period_started_at: datetime
    period_ended_at: datetime
    return_fraction: Decimal
    source_fingerprint: str

    def __post_init__(self) -> None:
        if not _STRATEGY_ID_RE.fullmatch(self.strategy_id):
            raise ValueError("invalid strategy_id")
        _require_aware(self.period_started_at, "period_started_at")
        _require_aware(self.period_ended_at, "period_ended_at")
        if _utc(self.period_started_at) >= _utc(self.period_ended_at):
            raise ValueError("shadow observation period must have positive duration")
        if not isinstance(self.return_fraction, Decimal) or not self.return_fraction.is_finite():
            raise ValueError("return_fraction must be a finite Decimal")
        if self.return_fraction <= Decimal("-1"):
            raise ValueError("return_fraction must be greater than -1")
        if not _HASH_RE.fullmatch(self.source_fingerprint):
            raise ValueError("source_fingerprint must be lowercase SHA-256 hex")

    @property
    def fingerprint(self) -> str:
        return _hash_payload(_observation_payload(self))


@dataclass(frozen=True, slots=True)
class ShadowPeriodRecord:
    sequence: int
    config_fingerprint: str
    period_started_at: datetime
    period_ended_at: datetime
    weighted_return: Decimal
    nav_before: Decimal
    nav_after: Decimal
    observation_payloads: Mapping[str, str]
    previous_record_hash: str
    record_hash: str

    @property
    def observation_fingerprints(self) -> Mapping[str, str]:
        return {
            strategy_id: sha256(raw.encode("utf-8")).hexdigest()
            for strategy_id, raw in self.observation_payloads.items()
        }


@dataclass(frozen=True, slots=True)
class ShadowControlState:
    config_fingerprint: str
    sequence: int
    head_hash: str
    nav: Decimal
    control_hash: str


class SQLitePortfolioShadowRegistry:
    """Research-only synchronized portfolio shadow evidence.

    One frozen allocation config drives an append-only period chain. Every
    component observation is persisted canonically so weighted return and NAV
    are fully recomputable. A separately hash-protected control row anchors the
    current chain head, detecting tail deletion in addition to row mutation,
    reordering, sequence gaps and previous-hash corruption.
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
                CREATE TABLE IF NOT EXISTS shadow_config (
                    slot INTEGER PRIMARY KEY CHECK(slot = 1),
                    config_fingerprint TEXT NOT NULL,
                    config_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS shadow_records (
                    sequence INTEGER PRIMARY KEY,
                    period_started_at TEXT NOT NULL UNIQUE,
                    period_ended_at TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    previous_record_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL UNIQUE,
                    record_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS shadow_control (
                    slot INTEGER PRIMARY KEY CHECK(slot = 1),
                    config_fingerprint TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    head_hash TEXT NOT NULL,
                    nav TEXT NOT NULL,
                    control_hash TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            self._close_if_file(conn)

    def register_config(self, config: FrozenShadowConfig) -> FrozenShadowConfig:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM shadow_config WHERE slot = 1").fetchone()
            if row is not None:
                existing = _config_from_row(row)
                if existing.fingerprint != config.fingerprint:
                    raise ShadowConflict("portfolio shadow config is already frozen")
                self._verify_all_locked(conn)
                conn.commit()
                return existing

            if conn.execute("SELECT 1 FROM shadow_records LIMIT 1").fetchone() is not None:
                raise ShadowIntegrityError("shadow records exist without a frozen config")
            if conn.execute("SELECT 1 FROM shadow_control LIMIT 1").fetchone() is not None:
                raise ShadowIntegrityError("shadow control exists without a frozen config")

            config_json = _canonical_json(_config_payload(config))
            conn.execute(
                "INSERT INTO shadow_config(slot, config_fingerprint, config_json) VALUES (1, ?, ?)",
                (config.fingerprint, config_json),
            )
            control = _make_control(
                config_fingerprint=config.fingerprint,
                sequence=0,
                head_hash=_GENESIS_HASH,
                nav=config.initial_nav,
            )
            conn.execute(
                """
                INSERT INTO shadow_control(
                    slot, config_fingerprint, sequence, head_hash, nav, control_hash
                ) VALUES (1, ?, ?, ?, ?, ?)
                """,
                (
                    control.config_fingerprint,
                    control.sequence,
                    control.head_hash,
                    str(control.nav),
                    control.control_hash,
                ),
            )
            conn.commit()
            return config
        except Exception:
            conn.rollback()
            raise
        finally:
            self._close_if_file(conn)

    def append_period(
        self, observations: Iterable[StrategyShadowObservation]
    ) -> ShadowPeriodRecord:
        observation_tuple = tuple(observations)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            config, records, control = self._verify_all_locked(conn)
            normalized = _normalize_observations(config, observation_tuple)
            period_start = normalized[0].period_started_at
            if _utc(period_start) < _utc(config.activated_at):
                raise ShadowIntegrityError("shadow period precedes config activation")

            by_start = {_utc(record.period_started_at): record for record in records}
            existing = by_start.get(_utc(period_start))
            if existing is not None:
                candidate = _build_record(
                    config=config,
                    observations=normalized,
                    sequence=existing.sequence,
                    nav_before=existing.nav_before,
                    previous_record_hash=existing.previous_record_hash,
                )
                if candidate.record_hash != existing.record_hash:
                    raise ShadowConflict("conflicting replay for an existing shadow period")
                conn.commit()
                return existing

            if records:
                last = records[-1]
                if _utc(period_start) != _utc(last.period_ended_at):
                    raise ShadowIntegrityError("shadow periods must be strictly contiguous")

            record = _build_record(
                config=config,
                observations=normalized,
                sequence=control.sequence + 1,
                nav_before=control.nav,
                previous_record_hash=control.head_hash,
            )
            conn.execute(
                """
                INSERT INTO shadow_records(
                    sequence, period_started_at, period_ended_at,
                    config_fingerprint, previous_record_hash, record_hash, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.sequence,
                    _iso(record.period_started_at),
                    _iso(record.period_ended_at),
                    record.config_fingerprint,
                    record.previous_record_hash,
                    record.record_hash,
                    _canonical_json(_record_payload_without_hash(record)),
                ),
            )
            new_control = _make_control(
                config_fingerprint=config.fingerprint,
                sequence=record.sequence,
                head_hash=record.record_hash,
                nav=record.nav_after,
            )
            conn.execute(
                """
                UPDATE shadow_control
                SET config_fingerprint = ?, sequence = ?, head_hash = ?, nav = ?, control_hash = ?
                WHERE slot = 1
                """,
                (
                    new_control.config_fingerprint,
                    new_control.sequence,
                    new_control.head_hash,
                    str(new_control.nav),
                    new_control.control_hash,
                ),
            )
            if conn.total_changes < 2:
                raise ShadowIntegrityError("shadow control anchor update failed")
            conn.commit()
            return record
        except Exception:
            conn.rollback()
            raise
        finally:
            self._close_if_file(conn)

    def get_config(self) -> FrozenShadowConfig:
        conn = self._connect()
        try:
            config, _, _ = self._verify_all_locked(conn)
            return config
        finally:
            self._close_if_file(conn)

    def control_state(self) -> ShadowControlState:
        conn = self._connect()
        try:
            _, _, control = self._verify_all_locked(conn)
            return control
        finally:
            self._close_if_file(conn)

    def list_records(self) -> tuple[ShadowPeriodRecord, ...]:
        conn = self._connect()
        try:
            _, records, _ = self._verify_all_locked(conn)
            return records
        finally:
            self._close_if_file(conn)

    def _verify_all_locked(
        self, conn: sqlite3.Connection
    ) -> tuple[FrozenShadowConfig, tuple[ShadowPeriodRecord, ...], ShadowControlState]:
        config_row = conn.execute("SELECT * FROM shadow_config WHERE slot = 1").fetchone()
        if config_row is None:
            if conn.execute("SELECT 1 FROM shadow_records LIMIT 1").fetchone() is not None:
                raise ShadowIntegrityError("shadow records exist without config")
            if conn.execute("SELECT 1 FROM shadow_control LIMIT 1").fetchone() is not None:
                raise ShadowIntegrityError("shadow control exists without config")
            raise ShadowIntegrityError("portfolio shadow config is not initialized")
        config = _config_from_row(config_row)

        control_row = conn.execute("SELECT * FROM shadow_control WHERE slot = 1").fetchone()
        if control_row is None:
            raise ShadowIntegrityError("shadow control anchor is missing")
        control = _control_from_row(control_row)
        if control.config_fingerprint != config.fingerprint:
            raise ShadowIntegrityError("shadow control config fingerprint mismatch")

        rows = conn.execute("SELECT * FROM shadow_records ORDER BY sequence").fetchall()
        records: list[ShadowPeriodRecord] = []
        expected_sequence = 1
        previous_hash = _GENESIS_HASH
        expected_nav = config.initial_nav
        previous_end: datetime | None = None
        for row in rows:
            record = _record_from_row(row)
            if record.sequence != expected_sequence:
                raise ShadowIntegrityError("shadow record sequence gap or reordering detected")
            if record.config_fingerprint != config.fingerprint:
                raise ShadowIntegrityError("shadow record config fingerprint mismatch")
            if record.previous_record_hash != previous_hash:
                raise ShadowIntegrityError("shadow record previous-hash linkage mismatch")
            if record.nav_before != expected_nav:
                raise ShadowIntegrityError("shadow NAV continuity mismatch")
            if previous_end is not None and _utc(record.period_started_at) != _utc(previous_end):
                raise ShadowIntegrityError("shadow period continuity mismatch")
            if record.record_hash != _hash_payload(_record_payload_without_hash(record)):
                raise ShadowIntegrityError("shadow record hash mismatch")

            observations = _observations_from_record(record)
            expected_weighted_return = sum(
                (
                    config.strategy_weights[observation.strategy_id]
                    * observation.return_fraction
                    for observation in observations
                ),
                Decimal("0"),
            )
            if record.weighted_return != expected_weighted_return:
                raise ShadowIntegrityError("shadow weighted return is not reproducible")
            if record.nav_after != record.nav_before * (Decimal("1") + record.weighted_return):
                raise ShadowIntegrityError("shadow record NAV arithmetic mismatch")
            if not _finite_positive(record.nav_after):
                raise ShadowIntegrityError("shadow record NAV must remain finite and positive")

            records.append(record)
            expected_sequence += 1
            previous_hash = record.record_hash
            expected_nav = record.nav_after
            previous_end = record.period_ended_at

        expected_count = len(records)
        expected_head = records[-1].record_hash if records else _GENESIS_HASH
        expected_control_nav = records[-1].nav_after if records else config.initial_nav
        if control.sequence != expected_count:
            raise ShadowIntegrityError("shadow control sequence does not match durable records")
        if control.head_hash != expected_head:
            raise ShadowIntegrityError("shadow control head does not match durable records")
        if control.nav != expected_control_nav:
            raise ShadowIntegrityError("shadow control NAV does not match durable records")
        if control.control_hash != _control_hash(
            config_fingerprint=control.config_fingerprint,
            sequence=control.sequence,
            head_hash=control.head_hash,
            nav=control.nav,
        ):
            raise ShadowIntegrityError("shadow control hash mismatch")
        return config, tuple(records), control


def _normalize_observations(
    config: FrozenShadowConfig,
    observations: tuple[StrategyShadowObservation, ...],
) -> tuple[StrategyShadowObservation, ...]:
    if not observations:
        raise ShadowIntegrityError("shadow period requires observations")
    by_strategy: dict[str, StrategyShadowObservation] = {}
    for observation in observations:
        if observation.strategy_id in by_strategy:
            raise ShadowIntegrityError("duplicate strategy observation in shadow period")
        by_strategy[observation.strategy_id] = observation
    if set(by_strategy) != set(config.strategy_weights):
        raise ShadowIntegrityError("shadow period must contain the exact frozen strategy universe")

    first = observations[0]
    start = _utc(first.period_started_at)
    end = _utc(first.period_ended_at)
    for observation in observations[1:]:
        if _utc(observation.period_started_at) != start or _utc(observation.period_ended_at) != end:
            raise ShadowIntegrityError("shadow observations must use exactly synchronized timestamps")
    return tuple(by_strategy[strategy_id] for strategy_id in sorted(by_strategy))


def _observations_from_record(
    record: ShadowPeriodRecord,
) -> tuple[StrategyShadowObservation, ...]:
    observations: list[StrategyShadowObservation] = []
    for strategy_id in sorted(record.observation_payloads):
        raw = record.observation_payloads[strategy_id]
        payload = _strict_json_object(raw)
        try:
            observation = StrategyShadowObservation(
                strategy_id=_required_str(payload, "strategy_id"),
                period_started_at=_parse_datetime(payload.get("period_started_at"), "period_started_at"),
                period_ended_at=_parse_datetime(payload.get("period_ended_at"), "period_ended_at"),
                return_fraction=_parse_decimal(payload.get("return_fraction"), "return_fraction"),
                source_fingerprint=_required_str(payload, "source_fingerprint"),
            )
        except ValueError as exc:
            raise ShadowIntegrityError("invalid persisted shadow component observation") from exc
        if observation.strategy_id != strategy_id:
            raise ShadowIntegrityError("shadow component strategy key mismatch")
        if raw != _canonical_json(_observation_payload(observation)):
            raise ShadowIntegrityError("shadow component observation is not canonical")
        if _utc(observation.period_started_at) != _utc(record.period_started_at):
            raise ShadowIntegrityError("shadow component period start mismatch")
        if _utc(observation.period_ended_at) != _utc(record.period_ended_at):
            raise ShadowIntegrityError("shadow component period end mismatch")
        observations.append(observation)
    return tuple(observations)


def _build_record(
    *,
    config: FrozenShadowConfig,
    observations: tuple[StrategyShadowObservation, ...],
    sequence: int,
    nav_before: Decimal,
    previous_record_hash: str,
) -> ShadowPeriodRecord:
    if sequence <= 0:
        raise ShadowIntegrityError("shadow sequence must be positive")
    if not _HASH_RE.fullmatch(previous_record_hash):
        raise ShadowIntegrityError("previous_record_hash is invalid")
    weighted_return = Decimal("0")
    observation_payloads: dict[str, str] = {}
    for observation in observations:
        weighted_return += config.strategy_weights[observation.strategy_id] * observation.return_fraction
        observation_payloads[observation.strategy_id] = _canonical_json(
            _observation_payload(observation)
        )
    if not weighted_return.is_finite() or weighted_return <= Decimal("-1"):
        raise ShadowIntegrityError("weighted shadow return is invalid")
    nav_after = nav_before * (Decimal("1") + weighted_return)
    if not _finite_positive(nav_after):
        raise ShadowIntegrityError("shadow NAV became invalid")
    provisional = ShadowPeriodRecord(
        sequence=sequence,
        config_fingerprint=config.fingerprint,
        period_started_at=observations[0].period_started_at,
        period_ended_at=observations[0].period_ended_at,
        weighted_return=weighted_return,
        nav_before=nav_before,
        nav_after=nav_after,
        observation_payloads=observation_payloads,
        previous_record_hash=previous_record_hash,
        record_hash="",
    )
    record_hash = _hash_payload(_record_payload_without_hash(provisional))
    return ShadowPeriodRecord(
        sequence=provisional.sequence,
        config_fingerprint=provisional.config_fingerprint,
        period_started_at=provisional.period_started_at,
        period_ended_at=provisional.period_ended_at,
        weighted_return=provisional.weighted_return,
        nav_before=provisional.nav_before,
        nav_after=provisional.nav_after,
        observation_payloads=provisional.observation_payloads,
        previous_record_hash=provisional.previous_record_hash,
        record_hash=record_hash,
    )


def _config_payload(config: FrozenShadowConfig) -> dict[str, object]:
    return {
        "config_id": config.config_id,
        "activated_at": _iso(config.activated_at),
        "initial_nav": str(config.initial_nav),
        "strategy_weights": {
            strategy_id: str(config.strategy_weights[strategy_id])
            for strategy_id in sorted(config.strategy_weights)
        },
        "source_config_hash": config.source_config_hash,
    }


def _observation_payload(observation: StrategyShadowObservation) -> dict[str, object]:
    return {
        "strategy_id": observation.strategy_id,
        "period_started_at": _iso(observation.period_started_at),
        "period_ended_at": _iso(observation.period_ended_at),
        "return_fraction": str(observation.return_fraction),
        "source_fingerprint": observation.source_fingerprint,
    }


def _record_payload_without_hash(record: ShadowPeriodRecord) -> dict[str, object]:
    return {
        "sequence": record.sequence,
        "config_fingerprint": record.config_fingerprint,
        "period_started_at": _iso(record.period_started_at),
        "period_ended_at": _iso(record.period_ended_at),
        "weighted_return": str(record.weighted_return),
        "nav_before": str(record.nav_before),
        "nav_after": str(record.nav_after),
        "observation_payloads": {
            strategy_id: record.observation_payloads[strategy_id]
            for strategy_id in sorted(record.observation_payloads)
        },
        "previous_record_hash": record.previous_record_hash,
    }


def _config_from_row(row: sqlite3.Row) -> FrozenShadowConfig:
    payload = _strict_json_object(row["config_json"])
    try:
        config = FrozenShadowConfig(
            config_id=_required_str(payload, "config_id"),
            activated_at=_parse_datetime(payload.get("activated_at"), "activated_at"),
            initial_nav=_parse_decimal(payload.get("initial_nav"), "initial_nav"),
            strategy_weights=_parse_weights(payload.get("strategy_weights")),
            source_config_hash=_required_str(payload, "source_config_hash"),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise ShadowIntegrityError("invalid persisted shadow config") from exc
    if row["config_fingerprint"] != config.fingerprint:
        raise ShadowIntegrityError("persisted shadow config fingerprint mismatch")
    if row["config_json"] != _canonical_json(_config_payload(config)):
        raise ShadowIntegrityError("persisted shadow config is not canonical")
    return config


def _record_from_row(row: sqlite3.Row) -> ShadowPeriodRecord:
    payload = _strict_json_object(row["record_json"])
    try:
        payloads_raw = payload.get("observation_payloads")
        if not isinstance(payloads_raw, dict):
            raise ValueError("observation_payloads must be an object")
        observation_payloads: dict[str, str] = {}
        for strategy_id, raw in payloads_raw.items():
            if not isinstance(strategy_id, str) or not isinstance(raw, str):
                raise ValueError("invalid observation payload entry")
            observation_payloads[strategy_id] = raw
        record = ShadowPeriodRecord(
            sequence=_parse_int(payload.get("sequence"), "sequence"),
            config_fingerprint=_required_str(payload, "config_fingerprint"),
            period_started_at=_parse_datetime(payload.get("period_started_at"), "period_started_at"),
            period_ended_at=_parse_datetime(payload.get("period_ended_at"), "period_ended_at"),
            weighted_return=_parse_decimal(payload.get("weighted_return"), "weighted_return"),
            nav_before=_parse_decimal(payload.get("nav_before"), "nav_before"),
            nav_after=_parse_decimal(payload.get("nav_after"), "nav_after"),
            observation_payloads=observation_payloads,
            previous_record_hash=_required_str(payload, "previous_record_hash"),
            record_hash=str(row["record_hash"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise ShadowIntegrityError("invalid persisted shadow record") from exc

    if row["sequence"] != record.sequence:
        raise ShadowIntegrityError("shadow sequence column mismatch")
    if row["period_started_at"] != _iso(record.period_started_at):
        raise ShadowIntegrityError("shadow period-start column mismatch")
    if row["period_ended_at"] != _iso(record.period_ended_at):
        raise ShadowIntegrityError("shadow period-end column mismatch")
    if row["config_fingerprint"] != record.config_fingerprint:
        raise ShadowIntegrityError("shadow config-fingerprint column mismatch")
    if row["previous_record_hash"] != record.previous_record_hash:
        raise ShadowIntegrityError("shadow previous-hash column mismatch")
    if row["record_json"] != _canonical_json(_record_payload_without_hash(record)):
        raise ShadowIntegrityError("persisted shadow record is not canonical")
    if not _HASH_RE.fullmatch(record.record_hash):
        raise ShadowIntegrityError("persisted shadow record hash is invalid")
    return record


def _control_from_row(row: sqlite3.Row) -> ShadowControlState:
    try:
        config_fingerprint = str(row["config_fingerprint"])
        sequence_raw = row["sequence"]
        if isinstance(sequence_raw, bool) or not isinstance(sequence_raw, int):
            raise ValueError("sequence must be integer")
        sequence = sequence_raw
        head_hash = str(row["head_hash"])
        nav = Decimal(str(row["nav"]))
        control_hash = str(row["control_hash"])
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise ShadowIntegrityError("invalid shadow control row") from exc
    if sequence < 0 or not _HASH_RE.fullmatch(config_fingerprint) or not _HASH_RE.fullmatch(head_hash):
        raise ShadowIntegrityError("shadow control identity is invalid")
    if not _finite_positive(nav) or not _HASH_RE.fullmatch(control_hash):
        raise ShadowIntegrityError("shadow control values are invalid")
    return ShadowControlState(
        config_fingerprint=config_fingerprint,
        sequence=sequence,
        head_hash=head_hash,
        nav=nav,
        control_hash=control_hash,
    )


def _make_control(
    *, config_fingerprint: str, sequence: int, head_hash: str, nav: Decimal
) -> ShadowControlState:
    return ShadowControlState(
        config_fingerprint=config_fingerprint,
        sequence=sequence,
        head_hash=head_hash,
        nav=nav,
        control_hash=_control_hash(
            config_fingerprint=config_fingerprint,
            sequence=sequence,
            head_hash=head_hash,
            nav=nav,
        ),
    )


def _control_hash(
    *, config_fingerprint: str, sequence: int, head_hash: str, nav: Decimal
) -> str:
    return _hash_payload(
        {
            "config_fingerprint": config_fingerprint,
            "sequence": sequence,
            "head_hash": head_hash,
            "nav": str(nav),
        }
    )


def _parse_weights(value: object) -> dict[str, Decimal]:
    if not isinstance(value, dict) or not value:
        raise ValueError("strategy_weights must be a non-empty object")
    parsed: dict[str, Decimal] = {}
    for strategy_id, raw_weight in value.items():
        if not isinstance(strategy_id, str):
            raise ValueError("strategy id must be a string")
        parsed[strategy_id] = _parse_decimal(raw_weight, "weight")
    return parsed


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


def _parse_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _parse_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    _require_aware(parsed, label)
    return parsed


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def _strict_json_object(raw: str) -> Mapping[str, object]:
    try:
        value = json.loads(raw, parse_constant=lambda token: _raise_json_constant(token))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ShadowIntegrityError("persisted shadow JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ShadowIntegrityError("persisted shadow JSON root must be an object")
    return value


def _raise_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


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


def _finite_positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0
