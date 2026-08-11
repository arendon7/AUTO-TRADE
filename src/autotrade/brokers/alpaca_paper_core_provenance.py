from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from autotrade.domain import OrderStatus, intent_fingerprint, risk_decision_fingerprint
from autotrade.health_bridge import (
    HealthBridgeConflict,
    HealthRiskMode,
    _state_from_row as _health_bridge_state_from_row,
)
from autotrade.persistence import (
    _order_from_json,
    _order_to_json,
    _portfolio_from_storage,
)
from autotrade.research.health import (
    HealthEntityKind,
    HealthState,
    HealthStateConflict,
    _state_from_row as _health_state_from_row,
)
from autotrade.state import SafetyControlState

from .alpaca_paper_operational import (
    PaperOperationalWorkspace,
    read_prepared_package,
)
from .alpaca_paper_preparation_snapshot import read_preparation_snapshot


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PaperCoreProvenanceError(RuntimeError):
    pass


class PaperCoreProvenanceMissing(PaperCoreProvenanceError):
    pass


class PaperCoreProvenanceConflict(PaperCoreProvenanceError):
    pass


@dataclass(frozen=True, slots=True)
class PaperCoreProvenance:
    order_id: str
    order_status: str
    order_record_fingerprint: str
    strategy_id: str
    intent_fingerprint: str
    risk_decision_fingerprint: str
    safety_version: int
    safety_observed_fingerprint: str
    portfolio_version: int
    portfolio_snapshot_hash: str
    strategy_health_version: int
    strategy_health_fingerprint: str
    health_bridge_version: int
    health_bridge_fingerprint: str
    core_db_sha256: str
    verified_at: datetime
    provenance_hash: str

    def __post_init__(self) -> None:
        for name, value in (
            ("order_record_fingerprint", self.order_record_fingerprint),
            ("intent_fingerprint", self.intent_fingerprint),
            ("risk_decision_fingerprint", self.risk_decision_fingerprint),
            ("safety_observed_fingerprint", self.safety_observed_fingerprint),
            ("portfolio_snapshot_hash", self.portfolio_snapshot_hash),
            ("strategy_health_fingerprint", self.strategy_health_fingerprint),
            ("health_bridge_fingerprint", self.health_bridge_fingerprint),
            ("core_db_sha256", self.core_db_sha256),
            ("provenance_hash", self.provenance_hash),
        ):
            if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                raise ValueError(f"{name} must be lowercase SHA-256")
        if self.order_status != OrderStatus.VALIDATED.value:
            raise ValueError("core provenance requires VALIDATED order")
        for name, value in (
            ("safety_version", self.safety_version),
            ("portfolio_version", self.portfolio_version),
            ("strategy_health_version", self.strategy_health_version),
            ("health_bridge_version", self.health_bridge_version),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative integer")
        if self.portfolio_version <= 0 or self.strategy_health_version <= 0 or self.health_bridge_version <= 0:
            raise ValueError("portfolio/health/bridge versions must be > 0")
        if self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None:
            raise ValueError("verified_at must be timezone-aware")
        if self.provenance_hash != _provenance_hash(self._payload_without_hash()):
            raise ValueError("core provenance hash mismatch")

    def _payload_without_hash(self) -> dict[str, object]:
        return {
            "core_db_sha256": self.core_db_sha256,
            "health_bridge_fingerprint": self.health_bridge_fingerprint,
            "health_bridge_version": self.health_bridge_version,
            "intent_fingerprint": self.intent_fingerprint,
            "order_id": self.order_id,
            "order_record_fingerprint": self.order_record_fingerprint,
            "order_status": self.order_status,
            "portfolio_snapshot_hash": self.portfolio_snapshot_hash,
            "portfolio_version": self.portfolio_version,
            "risk_decision_fingerprint": self.risk_decision_fingerprint,
            "safety_observed_fingerprint": self.safety_observed_fingerprint,
            "safety_version": self.safety_version,
            "strategy_health_fingerprint": self.strategy_health_fingerprint,
            "strategy_health_version": self.strategy_health_version,
            "strategy_id": self.strategy_id,
            "verified_at": self.verified_at.isoformat(),
        }


class PaperOperationalCoreProvenanceReader:
    """Verify the prepared PAPER package against one durable core DB, read-only.

    This class intentionally does not instantiate ``SQLiteRuntime`` or any
    durable store because their constructors may initialize/migrate schema. It
    opens the existing database with SQLite ``mode=ro`` and ``query_only``.
    It has no OMS mutation, operator-decision, execution-bridge, permit, or
    broker-network surface.
    """

    def __init__(self, workspace: PaperOperationalWorkspace) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("operational workspace is required")
        self._workspace = workspace

    def verify(self, *, now: datetime) -> PaperCoreProvenance:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("provenance verification time must be timezone-aware")
        package = read_prepared_package(self._workspace.prepared_package_path)
        decision, market, approval = read_preparation_snapshot(
            self._workspace,
            package=package,
        )
        del market, approval
        if risk_decision_fingerprint(decision) != package.risk_decision_fingerprint:
            raise PaperCoreProvenanceConflict(
                "preparation snapshot RiskDecision differs from prepared package"
            )

        db_path = self._workspace.core_db_path
        self._require_database_file(db_path)
        before_hash = _file_sha256(db_path)
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, isolation_level=None)
        except sqlite3.Error as exc:
            raise PaperCoreProvenanceMissing("cannot open core SQLite database read-only") from exc
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            order, order_record_fingerprint = self._read_order(conn, package, decision)
            safety, safety_observed_fingerprint = self._read_safety(conn, package)
            portfolio_version, portfolio_hash = self._read_portfolio(conn)
            health = self._read_strategy_health(conn, strategy_id=order.intent.strategy_id)
            bridge = self._read_strategy_bridge(
                conn,
                strategy_id=order.intent.strategy_id,
                health=health,
            )
        except (sqlite3.Error, KeyError, TypeError, ValueError, ArithmeticError) as exc:
            if isinstance(exc, PaperCoreProvenanceError):
                raise
            raise PaperCoreProvenanceConflict("core SQLite provenance is invalid") from exc
        finally:
            conn.close()

        after_hash = _file_sha256(db_path)
        if after_hash != before_hash:
            raise PaperCoreProvenanceConflict(
                "core database bytes changed during read-only provenance verification"
            )

        values = {
            "core_db_sha256": before_hash,
            "health_bridge_fingerprint": bridge.fingerprint,
            "health_bridge_version": bridge.bridge_version,
            "intent_fingerprint": intent_fingerprint(order.intent),
            "order_id": order.order_id,
            "order_record_fingerprint": order_record_fingerprint,
            "order_status": order.status.value,
            "portfolio_snapshot_hash": portfolio_hash,
            "portfolio_version": portfolio_version,
            "risk_decision_fingerprint": package.risk_decision_fingerprint,
            "safety_observed_fingerprint": safety_observed_fingerprint,
            "safety_version": safety.version,
            "strategy_health_fingerprint": health.fingerprint,
            "strategy_health_version": health.version,
            "strategy_id": order.intent.strategy_id,
            "verified_at": now.isoformat(),
        }
        return PaperCoreProvenance(
            order_id=order.order_id,
            order_status=order.status.value,
            order_record_fingerprint=order_record_fingerprint,
            strategy_id=order.intent.strategy_id,
            intent_fingerprint=intent_fingerprint(order.intent),
            risk_decision_fingerprint=package.risk_decision_fingerprint,
            safety_version=safety.version,
            safety_observed_fingerprint=safety_observed_fingerprint,
            portfolio_version=portfolio_version,
            portfolio_snapshot_hash=portfolio_hash,
            strategy_health_version=health.version,
            strategy_health_fingerprint=health.fingerprint,
            health_bridge_version=bridge.bridge_version,
            health_bridge_fingerprint=bridge.fingerprint,
            core_db_sha256=before_hash,
            verified_at=now,
            provenance_hash=_provenance_hash(values),
        )

    @staticmethod
    def _require_database_file(path: Path) -> None:
        if path.is_symlink():
            raise PaperCoreProvenanceMissing("core database cannot be a symlink")
        if not path.is_file():
            raise PaperCoreProvenanceMissing("core database does not exist")

    @staticmethod
    def _read_order(conn: sqlite3.Connection, package, decision):
        row = conn.execute(
            "SELECT idempotency_key, order_id, record_json FROM orders WHERE order_id=?",
            (package.order_id,),
        ).fetchone()
        if row is None:
            raise PaperCoreProvenanceMissing("prepared order is missing from durable OMS state")
        raw = row["record_json"]
        if not isinstance(raw, str):
            raise PaperCoreProvenanceConflict("durable order payload is not text")
        order = _order_from_json(raw)
        if _order_to_json(order) != raw:
            raise PaperCoreProvenanceConflict("durable order payload is not canonical")
        if row["order_id"] != order.order_id or row["idempotency_key"] != order.intent.idempotency_key:
            raise PaperCoreProvenanceConflict("durable order row identity mismatch")
        if order.order_id != package.order_id:
            raise PaperCoreProvenanceConflict("durable order does not match prepared package")
        if order.status is not OrderStatus.VALIDATED:
            raise PaperCoreProvenanceConflict("durable order must remain VALIDATED before execution")
        if order.risk_decision_id != package.risk_decision_id:
            raise PaperCoreProvenanceConflict("durable order RiskDecision id mismatch")
        if order.intent.intent_id != decision.intent_id:
            raise PaperCoreProvenanceConflict("durable order intent id mismatch")
        if intent_fingerprint(order.intent) != package.intent_fingerprint:
            raise PaperCoreProvenanceConflict("durable order intent fingerprint mismatch")
        return order, sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _read_safety(conn: sqlite3.Connection, package) -> tuple[SafetyControlState, str]:
        row = conn.execute(
            "SELECT kill_switch_active, kill_switch_reason, version, updated_at "
            "FROM safety_state WHERE singleton_id=1"
        ).fetchone()
        if row is None:
            raise PaperCoreProvenanceMissing("durable Safety state is missing")
        active = row["kill_switch_active"]
        if isinstance(active, bool):
            active_value = active
        elif isinstance(active, int) and active in (0, 1):
            active_value = bool(active)
        else:
            raise PaperCoreProvenanceConflict("durable kill-switch flag is invalid")
        reason = row["kill_switch_reason"]
        if not isinstance(reason, str):
            raise PaperCoreProvenanceConflict("durable kill-switch reason is invalid")
        updated_raw = row["updated_at"]
        if updated_raw is not None and not isinstance(updated_raw, str):
            raise PaperCoreProvenanceConflict("durable Safety timestamp is invalid")
        updated_at = datetime.fromisoformat(updated_raw) if updated_raw else None
        state = SafetyControlState(
            kill_switch_active=active_value,
            kill_switch_reason=reason,
            version=int(row["version"]),
            updated_at=updated_at,
        )
        if state.kill_switch_active:
            raise PaperCoreProvenanceConflict("durable kill switch is engaged")
        if state.version != package.risk_decision_safety_state_version:
            raise PaperCoreProvenanceConflict("durable Safety version differs from prepared RiskDecision")
        observed = {
            "kill_switch_active": state.kill_switch_active,
            "kill_switch_reason": state.kill_switch_reason,
            "updated_at": state.updated_at.isoformat() if state.updated_at else None,
            "version": state.version,
        }
        return state, _provenance_hash(observed)

    @staticmethod
    def _read_portfolio(conn: sqlite3.Connection) -> tuple[int, str]:
        row = conn.execute(
            "SELECT version, snapshot_json, snapshot_hash FROM portfolio_state WHERE singleton_id=1"
        ).fetchone()
        if row is None:
            raise PaperCoreProvenanceMissing("durable Portfolio state is missing")
        snapshot = _portfolio_from_storage(
            snapshot_json=row["snapshot_json"],
            snapshot_hash=row["snapshot_hash"],
        )
        if not snapshot.reconciliation_ok:
            raise PaperCoreProvenanceConflict("portfolio reconciliation is not clean")
        if not snapshot.broker_state_known:
            raise PaperCoreProvenanceConflict("portfolio broker state is unknown")
        version = int(row["version"])
        if version <= 0:
            raise PaperCoreProvenanceConflict("portfolio version must be > 0")
        stored_hash = row["snapshot_hash"]
        if not isinstance(stored_hash, str) or not _HASH_RE.fullmatch(stored_hash):
            raise PaperCoreProvenanceConflict("portfolio snapshot hash is invalid")
        return version, stored_hash

    @staticmethod
    def _read_strategy_health(conn: sqlite3.Connection, *, strategy_id: str):
        row = conn.execute(
            "SELECT * FROM health_state_v2 WHERE entity_kind=? AND entity_id=?",
            (HealthEntityKind.STRATEGY.value, strategy_id),
        ).fetchone()
        if row is None:
            raise PaperCoreProvenanceMissing("strategy Health state is missing")
        try:
            health = _health_state_from_row(row)
        except HealthStateConflict as exc:
            raise PaperCoreProvenanceConflict("strategy Health state integrity failed") from exc
        if health.state is not HealthState.HEALTHY:
            raise PaperCoreProvenanceConflict("strategy Health state is not HEALTHY")
        return health

    @staticmethod
    def _read_strategy_bridge(conn: sqlite3.Connection, *, strategy_id: str, health):
        row = conn.execute(
            "SELECT * FROM health_bridge_state WHERE entity_kind=? AND entity_id=?",
            (HealthEntityKind.STRATEGY.value, strategy_id),
        ).fetchone()
        if row is None:
            raise PaperCoreProvenanceMissing("strategy Health Bridge state is missing")
        try:
            bridge = _health_bridge_state_from_row(row)
        except HealthBridgeConflict as exc:
            raise PaperCoreProvenanceConflict("strategy Health Bridge integrity failed") from exc
        if bridge.mode is not HealthRiskMode.NORMAL or bridge.risk_multiplier != Decimal("1"):
            raise PaperCoreProvenanceConflict("strategy Health Bridge does not allow full new exposure")
        if bridge.health_state_version != health.version:
            raise PaperCoreProvenanceConflict("Health Bridge version is not bound to authoritative Health")
        if bridge.health_state_fingerprint != health.fingerprint:
            raise PaperCoreProvenanceConflict("Health Bridge fingerprint is not bound to authoritative Health")
        if bridge.baseline_fingerprint != health.baseline_fingerprint:
            raise PaperCoreProvenanceConflict("Health Bridge baseline binding mismatch")
        if bridge.policy_fingerprint != health.policy_fingerprint:
            raise PaperCoreProvenanceConflict("Health Bridge policy binding mismatch")
        return bridge


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _provenance_hash(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode("utf-8")).hexdigest()
