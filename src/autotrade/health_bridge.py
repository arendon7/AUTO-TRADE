from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import re
import sqlite3
from typing import Protocol

from .persistence import SQLiteRuntime, _ledger_hash
from .research.health import HealthControlState, HealthEntityKind, HealthState


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ZERO = Decimal("0")
_ONE = Decimal("1")


class HealthBridgeError(RuntimeError):
    pass


class HealthBridgeEvidenceMissing(HealthBridgeError):
    pass


class HealthBridgeConflict(HealthBridgeError):
    pass


class HealthBridgeRecoveryRejected(HealthBridgeError):
    pass


class HealthRiskMode(StrEnum):
    NORMAL = "NORMAL"
    REDUCED = "REDUCED"
    NO_NEW_RISK = "NO_NEW_RISK"


_SEVERITY = {
    HealthRiskMode.NORMAL: 0,
    HealthRiskMode.REDUCED: 1,
    HealthRiskMode.NO_NEW_RISK: 2,
}
_MODE_BY_SEVERITY = {value: key for key, value in _SEVERITY.items()}


class HealthStateReader(Protocol):
    def get(
        self,
        entity_id: str,
        entity_kind: HealthEntityKind,
    ) -> HealthControlState | None: ...


@dataclass(frozen=True, slots=True)
class HealthBridgePolicy:
    degraded_risk_multiplier: Decimal = Decimal("0.50")
    max_state_age_seconds: int = 3600
    require_strategy_state: bool = True
    require_portfolio_state: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.degraded_risk_multiplier, Decimal)
            or not self.degraded_risk_multiplier.is_finite()
            or not _ZERO < self.degraded_risk_multiplier < _ONE
        ):
            raise ValueError("degraded_risk_multiplier must be finite Decimal in (0,1)")
        if (
            isinstance(self.max_state_age_seconds, bool)
            or not isinstance(self.max_state_age_seconds, int)
            or self.max_state_age_seconds <= 0
        ):
            raise ValueError("max_state_age_seconds must be integer > 0")
        if not isinstance(self.require_strategy_state, bool):
            raise ValueError("require_strategy_state must be bool")
        if not isinstance(self.require_portfolio_state, bool):
            raise ValueError("require_portfolio_state must be bool")

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "degraded_risk_multiplier": str(self.degraded_risk_multiplier),
                "max_state_age_seconds": self.max_state_age_seconds,
                "require_strategy_state": self.require_strategy_state,
                "require_portfolio_state": self.require_portfolio_state,
            }
        )


@dataclass(frozen=True, slots=True)
class HealthBridgeState:
    entity_id: str
    entity_kind: HealthEntityKind
    mode: HealthRiskMode
    risk_multiplier: Decimal
    health_state_version: int
    health_state_fingerprint: str
    baseline_fingerprint: str
    policy_fingerprint: str
    bridge_version: int
    updated_at: datetime

    def __post_init__(self) -> None:
        _identity(self.entity_id, "entity_id")
        if not isinstance(self.entity_kind, HealthEntityKind):
            raise ValueError("entity_kind must be HealthEntityKind")
        if not isinstance(self.mode, HealthRiskMode):
            raise ValueError("mode must be HealthRiskMode")
        if (
            not isinstance(self.risk_multiplier, Decimal)
            or not self.risk_multiplier.is_finite()
            or not _ZERO <= self.risk_multiplier <= _ONE
        ):
            raise ValueError("risk_multiplier must be finite Decimal in [0,1]")
        if self.mode is HealthRiskMode.NORMAL and self.risk_multiplier != _ONE:
            raise ValueError("NORMAL mode requires multiplier 1")
        if self.mode is HealthRiskMode.NO_NEW_RISK and self.risk_multiplier != _ZERO:
            raise ValueError("NO_NEW_RISK mode requires multiplier 0")
        if self.mode is HealthRiskMode.REDUCED and not _ZERO < self.risk_multiplier < _ONE:
            raise ValueError("REDUCED mode requires multiplier in (0,1)")
        if (
            isinstance(self.health_state_version, bool)
            or not isinstance(self.health_state_version, int)
            or self.health_state_version <= 0
        ):
            raise ValueError("health_state_version must be integer > 0")
        if (
            isinstance(self.bridge_version, bool)
            or not isinstance(self.bridge_version, int)
            or self.bridge_version <= 0
        ):
            raise ValueError("bridge_version must be integer > 0")
        for name, value in (
            ("health_state_fingerprint", self.health_state_fingerprint),
            ("baseline_fingerprint", self.baseline_fingerprint),
            ("policy_fingerprint", self.policy_fingerprint),
        ):
            _hash_value(value, name)
        if not _aware(self.updated_at):
            raise ValueError("updated_at must be timezone-aware")

    @property
    def entity_key(self) -> str:
        return f"{self.entity_kind.value}:{self.entity_id}"

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "entity_id": self.entity_id,
                "entity_kind": self.entity_kind.value,
                "mode": self.mode.value,
                "risk_multiplier": str(self.risk_multiplier),
                "health_state_version": self.health_state_version,
                "health_state_fingerprint": self.health_state_fingerprint,
                "baseline_fingerprint": self.baseline_fingerprint,
                "policy_fingerprint": self.policy_fingerprint,
                "bridge_version": self.bridge_version,
                "updated_at": self.updated_at.isoformat(),
            }
        )


@dataclass(frozen=True, slots=True)
class EffectiveHealthControl:
    mode: HealthRiskMode
    order_multiplier: Decimal
    strategy_multiplier: Decimal
    portfolio_multiplier: Decimal
    reason: str
    strategy_state_fingerprint: str = ""
    portfolio_state_fingerprint: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.mode, HealthRiskMode):
            raise ValueError("mode must be HealthRiskMode")
        for name, value in (
            ("order_multiplier", self.order_multiplier),
            ("strategy_multiplier", self.strategy_multiplier),
            ("portfolio_multiplier", self.portfolio_multiplier),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or not _ZERO <= value <= _ONE:
                raise ValueError(f"{name} must be finite Decimal in [0,1]")
        _identity(self.reason, "reason")
        for value in (self.strategy_state_fingerprint, self.portfolio_state_fingerprint):
            if value and not _SHA256_RE.fullmatch(value):
                raise ValueError("state fingerprints must be empty or SHA-256 hex")

    @property
    def blocks_new_risk(self) -> bool:
        return self.mode is HealthRiskMode.NO_NEW_RISK


class HealthBridgeControlProvider(Protocol):
    def effective_control(
        self,
        *,
        strategy_id: str,
        portfolio_entity_id: str,
        now: datetime,
    ) -> EffectiveHealthControl: ...


class SQLiteHealthBridgeStore:
    """Durable reduce-only bridge from authoritative health state to capital controls.

    The bridge never consumes caller-supplied health objects. It reads the
    authoritative HealthControlState through ``HealthStateReader``. Automatic
    sync is monotone: it can maintain or tighten risk but cannot relax it.
    Relaxation requires an explicit acknowledgement and fresh authoritative
    health evidence. Every distinct bridge state change increments the global
    safety-state version in the same SQLite transaction so an already-approved
    RiskDecision becomes stale before OMS submission.
    """

    def __init__(
        self,
        runtime: SQLiteRuntime,
        *,
        health_reader: HealthStateReader,
        policy: HealthBridgePolicy | None = None,
    ) -> None:
        self._runtime = runtime
        self._health_reader = health_reader
        self._policy = policy or HealthBridgePolicy()
        self._initialize_schema()

    @property
    def policy(self) -> HealthBridgePolicy:
        return self._policy

    def _initialize_schema(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS health_bridge_state (
                    entity_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    risk_multiplier TEXT NOT NULL,
                    health_state_version INTEGER NOT NULL CHECK(health_state_version > 0),
                    health_state_fingerprint TEXT NOT NULL,
                    baseline_fingerprint TEXT NOT NULL,
                    policy_fingerprint TEXT NOT NULL,
                    bridge_version INTEGER NOT NULL CHECK(bridge_version > 0),
                    updated_at TEXT NOT NULL,
                    state_hash TEXT NOT NULL,
                    PRIMARY KEY(entity_kind, entity_id)
                );
                CREATE TABLE IF NOT EXISTS health_bridge_applied (
                    entity_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    health_state_fingerprint TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    PRIMARY KEY(entity_kind, entity_id, health_state_fingerprint)
                );
                CREATE TABLE IF NOT EXISTS health_bridge_transitions (
                    transition_id TEXT PRIMARY KEY,
                    entity_kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    from_mode TEXT NOT NULL,
                    to_mode TEXT NOT NULL,
                    health_state_fingerprint TEXT NOT NULL,
                    action TEXT NOT NULL,
                    confirmed_by TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                """
            )
        finally:
            conn.close()

    def get(
        self,
        entity_id: str,
        entity_kind: HealthEntityKind,
    ) -> HealthBridgeState | None:
        _identity(entity_id, "entity_id")
        if not isinstance(entity_kind, HealthEntityKind):
            raise ValueError("entity_kind must be HealthEntityKind")
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT * FROM health_bridge_state WHERE entity_kind=? AND entity_id=?",
                (entity_kind.value, entity_id),
            ).fetchone()
        finally:
            conn.close()
        return None if row is None else _state_from_row(row)

    def sync_from_health(
        self,
        *,
        entity_id: str,
        entity_kind: HealthEntityKind,
        now: datetime,
    ) -> HealthBridgeState:
        health = self._authoritative_health(entity_id, entity_kind)
        _validate_time(now)
        if health.updated_at > now:
            raise HealthBridgeConflict("health state cannot be from the future")
        desired_mode, desired_multiplier = self._mapped_control(health.state)

        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = self._current_tx(conn, entity_id, entity_kind)
            if current is not None:
                self._assert_binding(current, health)

            seen = conn.execute(
                """
                SELECT 1 FROM health_bridge_applied
                WHERE entity_kind=? AND entity_id=? AND health_state_fingerprint=?
                """,
                (entity_kind.value, entity_id, health.fingerprint),
            ).fetchone()
            if seen is not None:
                if current is None:
                    raise HealthBridgeConflict("applied health evidence exists without bridge state")
                conn.execute("COMMIT")
                return current

            if current is not None:
                if health.version < current.health_state_version:
                    raise HealthBridgeConflict("health state version moved backward")
                if (
                    health.version == current.health_state_version
                    and health.fingerprint != current.health_state_fingerprint
                ):
                    raise HealthBridgeConflict("health state version identity conflict")
                target_mode = _stricter(current.mode, desired_mode)
                target_multiplier = min(current.risk_multiplier, desired_multiplier)
                bridge_version = current.bridge_version + 1
                from_mode = current.mode
            else:
                target_mode = desired_mode
                target_multiplier = desired_multiplier
                bridge_version = 1
                from_mode = HealthRiskMode.NORMAL

            target_multiplier = self._multiplier_for_mode(target_mode, target_multiplier)
            updated = HealthBridgeState(
                entity_id=health.entity_id,
                entity_kind=health.entity_kind,
                mode=target_mode,
                risk_multiplier=target_multiplier,
                health_state_version=health.version,
                health_state_fingerprint=health.fingerprint,
                baseline_fingerprint=health.baseline_fingerprint,
                policy_fingerprint=health.policy_fingerprint,
                bridge_version=bridge_version,
                updated_at=now,
            )
            self._upsert_state_tx(conn, updated)
            conn.execute(
                """
                INSERT INTO health_bridge_applied(
                    entity_kind,entity_id,health_state_fingerprint,applied_at
                ) VALUES(?,?,?,?)
                """,
                (entity_kind.value, entity_id, health.fingerprint, now.isoformat()),
            )
            self._append_transition_tx(
                conn,
                state=updated,
                from_mode=from_mode,
                action="AUTOMATIC_HEALTH_SYNC",
                confirmed_by="",
                now=now,
            )
            safety_version = self._bump_safety_version_tx(conn, now=now)
            _append_ledger_tx(
                conn,
                event_id=f"health-bridge-sync:{entity_kind.value}:{entity_id}:{health.fingerprint}",
                event_type="HEALTH_BRIDGE_APPLIED",
                occurred_at=now,
                payload={
                    "entity_kind": entity_kind.value,
                    "entity_id": entity_id,
                    "mode": updated.mode.value,
                    "health_state_version": str(health.version),
                    "health_state_fingerprint": health.fingerprint,
                    "bridge_version": str(updated.bridge_version),
                    "safety_state_version": str(safety_version),
                    "action": "AUTOMATIC_HEALTH_SYNC",
                },
            )
            conn.execute("COMMIT")
            return updated
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def acknowledge_recovery(
        self,
        *,
        entity_id: str,
        entity_kind: HealthEntityKind,
        confirmed_by: str,
        now: datetime,
    ) -> HealthBridgeState:
        _identity(confirmed_by, "confirmed_by")
        _validate_time(now)
        health = self._authoritative_health(entity_id, entity_kind)
        self._require_fresh_health(health, now=now)
        desired_mode, _ = self._mapped_control(health.state)

        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = self._current_tx(conn, entity_id, entity_kind)
            if current is None:
                raise HealthBridgeRecoveryRejected("no bridge state exists for entity")
            self._assert_binding(current, health)
            if health.version < current.health_state_version:
                raise HealthBridgeRecoveryRejected("recovery health state is older than bridge evidence")
            if (
                health.version == current.health_state_version
                and health.fingerprint != current.health_state_fingerprint
            ):
                raise HealthBridgeConflict("health state version identity conflict")
            if _SEVERITY[desired_mode] >= _SEVERITY[current.mode]:
                raise HealthBridgeRecoveryRejected(
                    "recovery requires authoritative health evidence supporting a less restrictive mode"
                )

            target_severity = max(_SEVERITY[desired_mode], _SEVERITY[current.mode] - 1)
            target_mode = _MODE_BY_SEVERITY[target_severity]
            target_multiplier = self._multiplier_for_mode(target_mode, None)
            updated = HealthBridgeState(
                entity_id=current.entity_id,
                entity_kind=current.entity_kind,
                mode=target_mode,
                risk_multiplier=target_multiplier,
                health_state_version=health.version,
                health_state_fingerprint=health.fingerprint,
                baseline_fingerprint=current.baseline_fingerprint,
                policy_fingerprint=current.policy_fingerprint,
                bridge_version=current.bridge_version + 1,
                updated_at=now,
            )
            self._upsert_state_tx(conn, updated)
            conn.execute(
                """
                INSERT OR IGNORE INTO health_bridge_applied(
                    entity_kind,entity_id,health_state_fingerprint,applied_at
                ) VALUES(?,?,?,?)
                """,
                (entity_kind.value, entity_id, health.fingerprint, now.isoformat()),
            )
            self._append_transition_tx(
                conn,
                state=updated,
                from_mode=current.mode,
                action="ACKNOWLEDGED_RECOVERY",
                confirmed_by=confirmed_by,
                now=now,
            )
            safety_version = self._bump_safety_version_tx(conn, now=now)
            _append_ledger_tx(
                conn,
                event_id=(
                    f"health-bridge-recovery:{entity_kind.value}:{entity_id}:"
                    f"{updated.bridge_version}"
                ),
                event_type="HEALTH_BRIDGE_RECOVERY_ACKNOWLEDGED",
                occurred_at=now,
                payload={
                    "entity_kind": entity_kind.value,
                    "entity_id": entity_id,
                    "from_mode": current.mode.value,
                    "to_mode": updated.mode.value,
                    "confirmed_by": confirmed_by,
                    "health_state_version": str(health.version),
                    "health_state_fingerprint": health.fingerprint,
                    "bridge_version": str(updated.bridge_version),
                    "safety_state_version": str(safety_version),
                },
            )
            conn.execute("COMMIT")
            return updated
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def effective_control(
        self,
        *,
        strategy_id: str,
        portfolio_entity_id: str,
        now: datetime,
    ) -> EffectiveHealthControl:
        _identity(strategy_id, "strategy_id")
        if portfolio_entity_id:
            _identity(portfolio_entity_id, "portfolio_entity_id")
        _validate_time(now)

        strategy = self.get(strategy_id, HealthEntityKind.STRATEGY)
        portfolio = (
            self.get(portfolio_entity_id, HealthEntityKind.PORTFOLIO)
            if portfolio_entity_id
            else None
        )

        strategy_mode, strategy_multiplier, strategy_reason = self._effective_entity(
            strategy,
            required=self._policy.require_strategy_state,
            label="STRATEGY",
            now=now,
        )
        if self._policy.require_portfolio_state and not portfolio_entity_id:
            portfolio_mode = HealthRiskMode.NO_NEW_RISK
            portfolio_multiplier = _ZERO
            portfolio_reason = "MISSING_PORTFOLIO_HEALTH_ID"
        else:
            portfolio_mode, portfolio_multiplier, portfolio_reason = self._effective_entity(
                portfolio,
                required=self._policy.require_portfolio_state,
                label="PORTFOLIO",
                now=now,
            )
        effective_mode = _stricter(strategy_mode, portfolio_mode)
        order_multiplier = min(strategy_multiplier, portfolio_multiplier)
        reason = ";".join(
            value for value in (strategy_reason, portfolio_reason) if value
        ) or "HEALTH_CONTROLS_NORMAL"
        return EffectiveHealthControl(
            mode=effective_mode,
            order_multiplier=order_multiplier,
            strategy_multiplier=strategy_multiplier,
            portfolio_multiplier=portfolio_multiplier,
            reason=reason,
            strategy_state_fingerprint=strategy.fingerprint if strategy is not None else "",
            portfolio_state_fingerprint=portfolio.fingerprint if portfolio is not None else "",
        )

    def _effective_entity(
        self,
        state: HealthBridgeState | None,
        *,
        required: bool,
        label: str,
        now: datetime,
    ) -> tuple[HealthRiskMode, Decimal, str]:
        if state is None:
            if required:
                return HealthRiskMode.NO_NEW_RISK, _ZERO, f"MISSING_{label}_HEALTH_CONTROL"
            return HealthRiskMode.NORMAL, _ONE, ""
        if state.updated_at > now:
            return HealthRiskMode.NO_NEW_RISK, _ZERO, f"FUTURE_{label}_HEALTH_CONTROL"
        if now - state.updated_at > timedelta(seconds=self._policy.max_state_age_seconds):
            return HealthRiskMode.NO_NEW_RISK, _ZERO, f"STALE_{label}_HEALTH_CONTROL"
        return state.mode, state.risk_multiplier, f"{label}_{state.mode.value}"

    def _authoritative_health(
        self,
        entity_id: str,
        entity_kind: HealthEntityKind,
    ) -> HealthControlState:
        _identity(entity_id, "entity_id")
        if not isinstance(entity_kind, HealthEntityKind):
            raise ValueError("entity_kind must be HealthEntityKind")
        health = self._health_reader.get(entity_id, entity_kind)
        if health is None:
            raise HealthBridgeEvidenceMissing(f"missing authoritative health state: {entity_kind.value}:{entity_id}")
        if not isinstance(health, HealthControlState):
            raise HealthBridgeConflict("health reader returned invalid state type")
        if health.entity_id != entity_id or health.entity_kind is not entity_kind:
            raise HealthBridgeConflict("health reader returned mismatched entity identity")
        return health

    def _mapped_control(self, state: HealthState) -> tuple[HealthRiskMode, Decimal]:
        if state is HealthState.HEALTHY:
            return HealthRiskMode.NORMAL, _ONE
        if state is HealthState.DEGRADED:
            return HealthRiskMode.REDUCED, self._policy.degraded_risk_multiplier
        if state in {HealthState.QUARANTINED, HealthState.RETIRED}:
            return HealthRiskMode.NO_NEW_RISK, _ZERO
        raise HealthBridgeConflict(f"unsupported health state: {state}")

    def _multiplier_for_mode(
        self,
        mode: HealthRiskMode,
        candidate: Decimal | None,
    ) -> Decimal:
        if mode is HealthRiskMode.NORMAL:
            return _ONE
        if mode is HealthRiskMode.NO_NEW_RISK:
            return _ZERO
        if candidate is not None and _ZERO < candidate < _ONE:
            return candidate
        return self._policy.degraded_risk_multiplier

    def _require_fresh_health(self, health: HealthControlState, *, now: datetime) -> None:
        if health.updated_at > now:
            raise HealthBridgeRecoveryRejected("recovery health state cannot be from the future")
        if now - health.updated_at > timedelta(seconds=self._policy.max_state_age_seconds):
            raise HealthBridgeRecoveryRejected("recovery requires fresh health state")

    def _assert_binding(
        self,
        current: HealthBridgeState,
        health: HealthControlState,
    ) -> None:
        if current.baseline_fingerprint != health.baseline_fingerprint:
            raise HealthBridgeConflict("health baseline fingerprint mismatch")
        if current.policy_fingerprint != health.policy_fingerprint:
            raise HealthBridgeConflict("health policy fingerprint mismatch")

    def _current_tx(
        self,
        conn: sqlite3.Connection,
        entity_id: str,
        entity_kind: HealthEntityKind,
    ) -> HealthBridgeState | None:
        row = conn.execute(
            "SELECT * FROM health_bridge_state WHERE entity_kind=? AND entity_id=?",
            (entity_kind.value, entity_id),
        ).fetchone()
        return None if row is None else _state_from_row(row)

    def _upsert_state_tx(
        self,
        conn: sqlite3.Connection,
        state: HealthBridgeState,
    ) -> None:
        conn.execute(
            """
            INSERT INTO health_bridge_state(
                entity_kind,entity_id,mode,risk_multiplier,health_state_version,
                health_state_fingerprint,baseline_fingerprint,policy_fingerprint,
                bridge_version,updated_at,state_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(entity_kind,entity_id) DO UPDATE SET
                mode=excluded.mode,
                risk_multiplier=excluded.risk_multiplier,
                health_state_version=excluded.health_state_version,
                health_state_fingerprint=excluded.health_state_fingerprint,
                baseline_fingerprint=excluded.baseline_fingerprint,
                policy_fingerprint=excluded.policy_fingerprint,
                bridge_version=excluded.bridge_version,
                updated_at=excluded.updated_at,
                state_hash=excluded.state_hash
            """,
            (
                state.entity_kind.value,
                state.entity_id,
                state.mode.value,
                str(state.risk_multiplier),
                state.health_state_version,
                state.health_state_fingerprint,
                state.baseline_fingerprint,
                state.policy_fingerprint,
                state.bridge_version,
                state.updated_at.isoformat(),
                state.fingerprint,
            ),
        )

    def _append_transition_tx(
        self,
        conn: sqlite3.Connection,
        *,
        state: HealthBridgeState,
        from_mode: HealthRiskMode,
        action: str,
        confirmed_by: str,
        now: datetime,
    ) -> None:
        transition_id = _hash(
            {
                "entity_key": state.entity_key,
                "bridge_version": state.bridge_version,
                "from_mode": from_mode.value,
                "to_mode": state.mode.value,
                "health_state_fingerprint": state.health_state_fingerprint,
                "action": action,
                "confirmed_by": confirmed_by,
                "occurred_at": now.isoformat(),
            }
        )
        conn.execute(
            """
            INSERT INTO health_bridge_transitions(
                transition_id,entity_kind,entity_id,from_mode,to_mode,
                health_state_fingerprint,action,confirmed_by,occurred_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                transition_id,
                state.entity_kind.value,
                state.entity_id,
                from_mode.value,
                state.mode.value,
                state.health_state_fingerprint,
                action,
                confirmed_by,
                now.isoformat(),
            ),
        )

    def _bump_safety_version_tx(
        self,
        conn: sqlite3.Connection,
        *,
        now: datetime,
    ) -> int:
        row = conn.execute(
            "SELECT version FROM safety_state WHERE singleton_id=1"
        ).fetchone()
        if row is None:
            raise HealthBridgeConflict("safety state is not initialized")
        version = int(row["version"]) + 1
        conn.execute(
            "UPDATE safety_state SET version=?, updated_at=? WHERE singleton_id=1",
            (version, now.isoformat()),
        )
        return version


def _state_from_row(row: sqlite3.Row) -> HealthBridgeState:
    stored_hash = row["state_hash"]
    if not isinstance(stored_hash, str) or not _SHA256_RE.fullmatch(stored_hash):
        raise HealthBridgeConflict("stored health bridge state hash is invalid")

    # Verify the commitment over the raw persisted representation before
    # semantic parsing. This distinguishes simple row tamper from a row whose
    # attacker also recomputed the commitment; the latter is then rejected by
    # HealthBridgeState semantic invariants below.
    try:
        raw_fingerprint = _hash(
            {
                "entity_id": str(row["entity_id"]),
                "entity_kind": str(row["entity_kind"]),
                "mode": str(row["mode"]),
                "risk_multiplier": str(row["risk_multiplier"]),
                "health_state_version": int(row["health_state_version"]),
                "health_state_fingerprint": str(row["health_state_fingerprint"]),
                "baseline_fingerprint": str(row["baseline_fingerprint"]),
                "policy_fingerprint": str(row["policy_fingerprint"]),
                "bridge_version": int(row["bridge_version"]),
                "updated_at": str(row["updated_at"]),
            }
        )
    except (TypeError, ValueError) as exc:
        raise HealthBridgeConflict("stored health bridge state is invalid") from exc
    if raw_fingerprint != stored_hash:
        raise HealthBridgeConflict("stored health bridge state hash mismatch")

    try:
        state = HealthBridgeState(
            entity_id=str(row["entity_id"]),
            entity_kind=HealthEntityKind(str(row["entity_kind"])),
            mode=HealthRiskMode(str(row["mode"])),
            risk_multiplier=Decimal(str(row["risk_multiplier"])),
            health_state_version=int(row["health_state_version"]),
            health_state_fingerprint=str(row["health_state_fingerprint"]),
            baseline_fingerprint=str(row["baseline_fingerprint"]),
            policy_fingerprint=str(row["policy_fingerprint"]),
            bridge_version=int(row["bridge_version"]),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HealthBridgeConflict("stored health bridge state is invalid") from exc
    return state


def _append_ledger_tx(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    payload: dict[str, str],
) -> None:
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    occurred_raw = occurred_at.isoformat()
    row = conn.execute(
        "SELECT event_hash FROM ledger_events ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prev_hash = row["event_hash"] if row is not None else "GENESIS"
    event_hash = _ledger_hash(
        prev_hash=prev_hash,
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_raw,
        payload_json=payload_json,
    )
    try:
        conn.execute(
            """
            INSERT INTO ledger_events(
                event_id,event_type,occurred_at,payload_json,prev_hash,event_hash
            ) VALUES(?,?,?,?,?,?)
            """,
            (event_id, event_type, occurred_raw, payload_json, prev_hash, event_hash),
        )
    except sqlite3.IntegrityError as exc:
        raise HealthBridgeConflict(f"ledger event identity conflict: {event_id}") from exc


def _stricter(left: HealthRiskMode, right: HealthRiskMode) -> HealthRiskMode:
    return left if _SEVERITY[left] >= _SEVERITY[right] else right


def _identity(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be non-empty canonical text")


def _hash_value(value: object, name: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be lowercase SHA-256 hex")


def _validate_time(value: datetime) -> None:
    if not _aware(value):
        raise ValueError("timestamp must be timezone-aware")


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: object) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
