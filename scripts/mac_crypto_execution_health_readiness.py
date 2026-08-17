from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import json
import re
import sqlite3

from autotrade.health_bridge import HealthBridgeState, HealthRiskMode
from autotrade.research.health import HealthControlState, HealthEntityKind, HealthState


EXECUTION_STRATEGY_ID = "R6_CRYPTO_PAPER_FIRST_CANARY_EXECUTION"
MAX_HEALTH_AGE_SECONDS = 3600
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_TABLES = frozenset({"health_state_v2", "health_bridge_state"})


class CryptoExecutionHealthReadinessError(RuntimeError):
    pass


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CryptoExecutionHealthReadinessError("timestamp is not timezone-aware")
    return value.astimezone(timezone.utc)


def _workspace_root(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TypeError("workspace_path must be pathlib.Path")
    expanded = path.expanduser()
    if not expanded.exists() or not expanded.is_dir() or expanded.is_symlink():
        raise CryptoExecutionHealthReadinessError("workspace is missing, not a directory, or is a symlink")
    return expanded.resolve()


def _connect_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file() or path.is_symlink():
        raise CryptoExecutionHealthReadinessError("core.sqlite3 is missing or unsafe")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def _health_from_row(row: sqlite3.Row) -> HealthControlState:
    try:
        state = HealthControlState(
            entity_id=str(row["entity_id"]),
            entity_kind=HealthEntityKind(str(row["entity_kind"])),
            state=HealthState(str(row["state"])),
            version=int(row["version"]),
            distinct_quarantine_count=int(row["distinct_quarantine_count"]),
            baseline_fingerprint=str(row["baseline_fingerprint"]),
            policy_fingerprint=str(row["policy_fingerprint"]),
            last_assessment_fingerprint=str(row["last_assessment_fingerprint"]),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            recovery_ack_head=str(row["recovery_ack_head"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CryptoExecutionHealthReadinessError("authoritative Health row is malformed") from exc
    stored_hash = str(row["state_hash"])
    if not _SHA256_RE.fullmatch(stored_hash) or stored_hash != state.fingerprint:
        raise CryptoExecutionHealthReadinessError("authoritative Health state hash mismatch")
    return state


def _bridge_from_row(row: sqlite3.Row) -> HealthBridgeState:
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
    except (KeyError, TypeError, ValueError) as exc:
        raise CryptoExecutionHealthReadinessError("Health bridge row is malformed") from exc
    stored_hash = str(row["state_hash"])
    if not _SHA256_RE.fullmatch(stored_hash) or stored_hash != state.fingerprint:
        raise CryptoExecutionHealthReadinessError("Health bridge state hash mismatch")
    return state


def _verify_ack_chain(conn: sqlite3.Connection, state: HealthControlState) -> None:
    tables = _table_names(conn)
    if "health_recovery_acks_v3" not in tables:
        if state.recovery_ack_head != "GENESIS":
            raise CryptoExecutionHealthReadinessError("Health recovery ACK chain table is missing")
        return
    rows = conn.execute(
        """
        SELECT ack_seq,recovery_id,request_fingerprint,confirmed_by,
               applied_at,previous_ack_hash,ack_hash
        FROM health_recovery_acks_v3
        WHERE entity_kind=? AND entity_id=?
        ORDER BY ack_seq ASC
        """,
        (state.entity_kind.value, state.entity_id),
    ).fetchall()
    from hashlib import sha256

    running = "GENESIS"
    expected_seq = 1
    for row in rows:
        seq = int(row["ack_seq"])
        if seq != expected_seq:
            raise CryptoExecutionHealthReadinessError("Health recovery ACK chain sequence mismatch")
        previous = str(row["previous_ack_hash"])
        if previous != running:
            raise CryptoExecutionHealthReadinessError("Health recovery ACK chain previous hash mismatch")
        payload = {
            "entity_kind": state.entity_kind.value,
            "entity_id": state.entity_id,
            "ack_seq": seq,
            "recovery_id": str(row["recovery_id"]),
            "request_fingerprint": str(row["request_fingerprint"]),
            "confirmed_by": str(row["confirmed_by"]),
            "applied_at": str(row["applied_at"]),
            "previous_ack_hash": previous,
        }
        expected = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        actual = str(row["ack_hash"])
        if actual != expected:
            raise CryptoExecutionHealthReadinessError("Health recovery ACK chain hash mismatch")
        running = actual
        expected_seq += 1
    if running != state.recovery_ack_head:
        raise CryptoExecutionHealthReadinessError("Health recovery ACK chain head mismatch")


def _freshness_reason(label: str, at: datetime, now: datetime) -> str | None:
    value = _aware(at)
    instant = _aware(now)
    if value > instant + timedelta(seconds=1):
        return f"{label}_FUTURE"
    if instant - value > timedelta(seconds=MAX_HEALTH_AGE_SECONDS):
        return f"{label}_STALE"
    return None


def _entity_payload(
    *,
    health: HealthControlState,
    bridge: HealthBridgeState,
    now: datetime,
) -> tuple[dict[str, object], list[str]]:
    blockers: list[str] = []
    if bridge.entity_id != health.entity_id or bridge.entity_kind is not health.entity_kind:
        blockers.append(f"{health.entity_kind.value}_IDENTITY_MISMATCH")
    if bridge.health_state_version > health.version:
        blockers.append(f"{health.entity_kind.value}_HEALTH_VERSION_MOVED_BACKWARD")
    if bridge.health_state_version == health.version and bridge.health_state_fingerprint != health.fingerprint:
        blockers.append(f"{health.entity_kind.value}_HEALTH_VERSION_IDENTITY_CONFLICT")
    if bridge.baseline_fingerprint != health.baseline_fingerprint:
        blockers.append(f"{health.entity_kind.value}_BASELINE_BINDING_MISMATCH")
    if bridge.policy_fingerprint != health.policy_fingerprint:
        blockers.append(f"{health.entity_kind.value}_POLICY_BINDING_MISMATCH")
    for label, at in (("AUTHORITATIVE_HEALTH", health.updated_at), ("HEALTH_BRIDGE", bridge.updated_at)):
        reason = _freshness_reason(f"{health.entity_kind.value}_{label}", at, now)
        if reason:
            blockers.append(reason)
    if health.state is not HealthState.HEALTHY:
        blockers.append(f"{health.entity_kind.value}_HEALTH_NOT_HEALTHY")
    if bridge.mode is not HealthRiskMode.NORMAL:
        blockers.append(f"{health.entity_kind.value}_BRIDGE_NOT_NORMAL")
    if bridge.risk_multiplier != Decimal("1"):
        blockers.append(f"{health.entity_kind.value}_BRIDGE_MULTIPLIER_NOT_ONE")
    payload = {
        "entity_kind": health.entity_kind.value,
        "entity_id": health.entity_id,
        "health_state": health.state.value,
        "health_version": health.version,
        "health_fingerprint": health.fingerprint,
        "health_updated_at": health.updated_at.isoformat(),
        "bridge_mode": bridge.mode.value,
        "bridge_version": bridge.bridge_version,
        "bridge_risk_multiplier": str(bridge.risk_multiplier),
        "bridge_fingerprint": bridge.fingerprint,
        "bridge_updated_at": bridge.updated_at.isoformat(),
        "baseline_fingerprint": health.baseline_fingerprint,
        "policy_fingerprint": health.policy_fingerprint,
        "ready": not blockers,
    }
    return payload, blockers


def inspect_health_readiness(
    *,
    workspace_path: Path,
    now: datetime,
    strategy_id: str = EXECUTION_STRATEGY_ID,
) -> dict[str, object]:
    if not isinstance(strategy_id, str) or not strategy_id.strip() or strategy_id != strategy_id.strip():
        raise ValueError("strategy_id must be a non-empty canonical string")
    instant = _aware(now)
    root = _workspace_root(workspace_path)
    core = root / "core.sqlite3"
    base = {
        "status": "HEALTH_R4_EXECUTION_READINESS_BLOCKED",
        "mode": "READ_ONLY_NO_POST",
        "workspace": str(root),
        "core_database": str(core),
        "strategy_id": strategy_id,
        "checked_at": instant.isoformat(),
        "read_only": True,
        "credentials_read": False,
        "broker_network_used": False,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "approval_consumed": False,
        "oms_submitting": False,
        "lifecycle_unknown": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    if not core.is_file() or core.is_symlink():
        return {
            **base,
            "blockers": ["CORE_DB_MISSING"],
            "next_action": "COMMISSION_AUTHORITATIVE_R4_HEALTH_BEFORE_FINAL_GUARD",
        }

    conn = _connect_ro(core)
    try:
        tables = _table_names(conn)
        missing_tables = sorted(_REQUIRED_TABLES - tables)
        if missing_tables:
            return {
                **base,
                "blockers": [f"HEALTH_SCHEMA_MISSING:{name}" for name in missing_tables],
                "next_action": "COMMISSION_AUTHORITATIVE_R4_HEALTH_BEFORE_FINAL_GUARD",
            }

        strategy_row = conn.execute(
            "SELECT * FROM health_state_v2 WHERE entity_kind='STRATEGY' AND entity_id=?",
            (strategy_id,),
        ).fetchone()
        portfolio_rows = conn.execute(
            "SELECT * FROM health_state_v2 WHERE entity_kind='PORTFOLIO' ORDER BY entity_id",
        ).fetchall()
        blockers: list[str] = []
        if strategy_row is None:
            blockers.append("STRATEGY_HEALTH_MISSING")
        if not portfolio_rows:
            blockers.append("PORTFOLIO_HEALTH_MISSING")
        elif len(portfolio_rows) != 1:
            blockers.append("PORTFOLIO_HEALTH_IDENTITY_AMBIGUOUS")
        if blockers:
            return {
                **base,
                "blockers": blockers,
                "portfolio_health_candidates": [str(row["entity_id"]) for row in portfolio_rows],
                "next_action": "COMMISSION_AUTHORITATIVE_R4_HEALTH_BEFORE_FINAL_GUARD",
            }

        assert strategy_row is not None and len(portfolio_rows) == 1
        strategy_health = _health_from_row(strategy_row)
        portfolio_health = _health_from_row(portfolio_rows[0])
        _verify_ack_chain(conn, strategy_health)
        _verify_ack_chain(conn, portfolio_health)

        strategy_bridge_row = conn.execute(
            "SELECT * FROM health_bridge_state WHERE entity_kind='STRATEGY' AND entity_id=?",
            (strategy_id,),
        ).fetchone()
        portfolio_bridge_row = conn.execute(
            "SELECT * FROM health_bridge_state WHERE entity_kind='PORTFOLIO' AND entity_id=?",
            (portfolio_health.entity_id,),
        ).fetchone()
        if strategy_bridge_row is None:
            blockers.append("STRATEGY_HEALTH_BRIDGE_MISSING")
        if portfolio_bridge_row is None:
            blockers.append("PORTFOLIO_HEALTH_BRIDGE_MISSING")
        if blockers:
            return {
                **base,
                "blockers": blockers,
                "portfolio_health_entity_id": portfolio_health.entity_id,
                "next_action": "SYNC_AUTHORITATIVE_R4_HEALTH_BRIDGE_BEFORE_FINAL_GUARD",
            }

        strategy_bridge = _bridge_from_row(strategy_bridge_row)
        portfolio_bridge = _bridge_from_row(portfolio_bridge_row)
        strategy_payload, strategy_blockers = _entity_payload(
            health=strategy_health,
            bridge=strategy_bridge,
            now=instant,
        )
        portfolio_payload, portfolio_blockers = _entity_payload(
            health=portfolio_health,
            bridge=portfolio_bridge,
            now=instant,
        )
        blockers.extend(strategy_blockers)
        blockers.extend(portfolio_blockers)
        ready = not blockers
        return {
            **base,
            "status": (
                "HEALTH_R4_EXECUTION_READINESS_PASS"
                if ready
                else "HEALTH_R4_EXECUTION_READINESS_BLOCKED"
            ),
            "ready": ready,
            "blockers": blockers,
            "strategy": strategy_payload,
            "portfolio": portfolio_payload,
            "portfolio_health_entity_id": portfolio_health.entity_id,
            "next_action": (
                "FINAL_GUARD_PRE_CONSUME_UAT_MAY_BE_PREPARED"
                if ready
                else "REMEDIATE_OR_REFRESH_AUTHORITATIVE_R4_HEALTH_BEFORE_FINAL_GUARD"
            ),
        }
    except CryptoExecutionHealthReadinessError as exc:
        return {
            **base,
            "blockers": ["HEALTH_INTEGRITY_FAILURE"],
            "reason": str(exc),
            "next_action": "HALT_AND_REPAIR_HEALTH_INTEGRITY",
        }
    finally:
        conn.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only R6 crypto execution Health R4 readiness inspection. "
            "No credentials, broker network, approval consumption, OMS staging, lifecycle UNKNOWN, or POST."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--strategy-id", default=EXECUTION_STRATEGY_ID)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = inspect_health_readiness(
            workspace_path=args.workspace,
            now=datetime.now(timezone.utc),
            strategy_id=args.strategy_id,
        )
    except Exception as exc:
        result = {
            "status": "HEALTH_R4_EXECUTION_READINESS_BLOCKED",
            "mode": "READ_ONLY_NO_POST",
            "blockers": ["READINESS_INSPECTION_FAILED"],
            "reason": str(exc),
            "read_only": True,
            "credentials_read": False,
            "broker_network_used": False,
            "broker_write_performed": False,
            "external_post_authorized": False,
            "approval_consumed": False,
            "oms_submitting": False,
            "lifecycle_unknown": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("status") == "HEALTH_R4_EXECUTION_READINESS_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
