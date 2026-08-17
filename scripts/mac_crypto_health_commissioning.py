from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from autotrade.health_bridge import HealthBridgePolicy, SQLiteHealthBridgeStore
from autotrade.ledger import LedgerEvent
from autotrade.persistence import SQLiteEventLedger, SQLiteRuntime
from autotrade.research.health import SQLiteHealthStateStore
from autotrade.risk_state import SQLiteR2SafetyStateStore

from mac_crypto_execution_health_readiness import (
    EXECUTION_STRATEGY_ID,
    inspect_health_readiness,
)


PORTFOLIO_HEALTH_ENTITY_ID = "R6_CRYPTO_PAPER_PORTFOLIO"
COMMISSIONING_KILL_REASON = "R6_HEALTH_R4_EVIDENCE_REQUIRED"
MANIFEST_NAME = "health_r4_commissioning_manifest.json"
COMMISSIONING_EVENT_ID = "r6-health-r4-core-commissioned-v1"


class CryptoHealthCommissioningError(RuntimeError):
    pass


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("commissioning timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _workspace_root(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TypeError("workspace_path must be pathlib.Path")
    expanded = path.expanduser()
    if not expanded.exists() or not expanded.is_dir() or expanded.is_symlink():
        raise CryptoHealthCommissioningError("workspace is missing, not a directory, or is a symlink")
    return expanded.resolve()


def _existing_tables(path: Path) -> set[str]:
    if not path.exists():
        return set()
    if not path.is_file() or path.is_symlink():
        raise CryptoHealthCommissioningError("core.sqlite3 exists but is not a safe regular file")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        conn.close()


def _health_row_count(path: Path) -> int:
    tables = _existing_tables(path)
    if "health_state_v2" not in tables:
        return 0
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM health_state_v2").fetchone()[0])
    finally:
        conn.close()


def _manifest_hash(payload: dict[str, object]) -> str:
    material = {key: value for key, value in payload.items() if key != "manifest_hash"}
    return sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _read_existing_manifest(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise CryptoHealthCommissioningError("commissioning manifest is not a safe regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoHealthCommissioningError("commissioning manifest is unreadable") from exc
    if not isinstance(payload, dict):
        raise CryptoHealthCommissioningError("commissioning manifest must be a JSON object")
    expected = payload.get("manifest_hash")
    if not isinstance(expected, str) or expected != _manifest_hash(payload):
        raise CryptoHealthCommissioningError("commissioning manifest hash mismatch")
    for key, expected_value in (
        ("schema_version", 1),
        ("strategy_id", EXECUTION_STRATEGY_ID),
        ("portfolio_health_entity_id", PORTFOLIO_HEALTH_ENTITY_ID),
        ("schema_only", True),
        ("fabricated_health", False),
        ("broker_write_performed", False),
        ("external_post_authorized", False),
        ("approval_consumed", False),
        ("capital_authority", "NONE"),
        ("live_trading", "BLOCKED"),
    ):
        if payload.get(key) != expected_value:
            raise CryptoHealthCommissioningError(f"commissioning manifest binding mismatch: {key}")
    return payload


def _write_manifest_once(path: Path, payload: dict[str, object]) -> dict[str, object]:
    existing = _read_existing_manifest(path)
    if existing is not None:
        return existing
    document = dict(payload)
    document["manifest_hash"] = _manifest_hash(document)
    encoded = json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
    except FileExistsError:
        return _read_existing_manifest(path) or document
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise CryptoHealthCommissioningError("cannot restrict commissioning manifest permissions") from exc
    return document


def _append_commissioning_event_once(runtime: SQLiteRuntime, *, now: datetime) -> None:
    conn = runtime.connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM ledger_events WHERE event_id=?",
            (COMMISSIONING_EVENT_ID,),
        ).fetchone()
    finally:
        conn.close()
    if row is not None:
        return
    SQLiteEventLedger(runtime).append(
        LedgerEvent(
            event_id=COMMISSIONING_EVENT_ID,
            event_type="R6_HEALTH_R4_CORE_COMMISSIONED",
            occurred_at=now,
            payload={
                "strategy_id": EXECUTION_STRATEGY_ID,
                "portfolio_health_entity_id": PORTFOLIO_HEALTH_ENTITY_ID,
                "schema_only": "true",
                "health_evidence_created": "false",
                "health_bridge_state_created": "false",
                "kill_switch_reason": COMMISSIONING_KILL_REASON,
                "broker_write_performed": "false",
                "external_post_authorized": "false",
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
            },
        )
    )


def commission_health_core(*, workspace_path: Path, now: datetime) -> dict[str, object]:
    instant = _aware(now)
    root = _workspace_root(workspace_path)
    core = root / "core.sqlite3"
    manifest_path = root / MANIFEST_NAME

    existing_manifest = _read_existing_manifest(manifest_path)
    core_preexisted = core.exists()
    tables_before = _existing_tables(core)
    if core_preexisted and tables_before and not ({"safety_state", "ledger_events"} & tables_before):
        raise CryptoHealthCommissioningError(
            "pre-existing core.sqlite3 is not recognized as an AUTO-TRADE runtime"
        )
    if core_preexisted and _health_row_count(core) != 0:
        raise CryptoHealthCommissioningError(
            "authoritative Health evidence already exists; schema commissioning refuses to mutate it"
        )

    runtime = SQLiteRuntime(core)
    safety_store = SQLiteR2SafetyStateStore(runtime)
    health_store = SQLiteHealthStateStore(core)
    SQLiteHealthBridgeStore(
        runtime,
        health_reader=health_store,
        policy=HealthBridgePolicy(
            require_strategy_state=True,
            require_portfolio_state=True,
        ),
    )

    safety = safety_store.activate(reason=COMMISSIONING_KILL_REASON, now=instant)
    if not safety.kill_switch_active or safety.kill_switch_reason != COMMISSIONING_KILL_REASON:
        raise CryptoHealthCommissioningError("commissioning failed to establish fail-closed kill switch")

    conn = runtime.connect()
    try:
        authoritative_count = int(conn.execute("SELECT COUNT(*) FROM health_state_v2").fetchone()[0])
        bridge_count = int(conn.execute("SELECT COUNT(*) FROM health_bridge_state").fetchone()[0])
    finally:
        conn.close()
    if authoritative_count != 0 or bridge_count != 0:
        raise CryptoHealthCommissioningError(
            "schema commissioning must not create authoritative Health or bridge state"
        )

    _append_commissioning_event_once(runtime, now=instant)

    if existing_manifest is None:
        existing_manifest = _write_manifest_once(
            manifest_path,
            {
                "schema_version": 1,
                "commissioned_at": instant.isoformat(),
                "strategy_id": EXECUTION_STRATEGY_ID,
                "portfolio_health_entity_id": PORTFOLIO_HEALTH_ENTITY_ID,
                "core_database": str(core),
                "core_created_on_first_commission": not core_preexisted,
                "schema_only": True,
                "health_state_rows_created": 0,
                "health_bridge_rows_created": 0,
                "strategy_return_evidence_imported": False,
                "portfolio_return_evidence_imported": False,
                "fabricated_health": False,
                "kill_switch_active": True,
                "kill_switch_reason": COMMISSIONING_KILL_REASON,
                "broker_network_used": False,
                "credentials_read": False,
                "local_state_write_performed": True,
                "broker_write_performed": False,
                "external_post_authorized": False,
                "approval_consumed": False,
                "oms_submitting": False,
                "lifecycle_unknown": False,
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
            },
        )

    readiness = inspect_health_readiness(
        workspace_path=root,
        now=instant,
        strategy_id=EXECUTION_STRATEGY_ID,
    )
    blockers = list(readiness.get("blockers") or [])
    if "CORE_DB_MISSING" in blockers:
        raise CryptoHealthCommissioningError("commissioned core was not visible to readiness inspector")

    return {
        "status": "HEALTH_R4_CORE_COMMISSIONED_EVIDENCE_REQUIRED",
        "mode": "LOCAL_COMMISSIONING_NO_POST",
        "workspace": str(root),
        "core_database": str(core),
        "manifest": str(manifest_path),
        "manifest_hash": existing_manifest["manifest_hash"],
        "strategy_id": EXECUTION_STRATEGY_ID,
        "portfolio_health_entity_id": PORTFOLIO_HEALTH_ENTITY_ID,
        "kill_switch_active": safety.kill_switch_active,
        "kill_switch_reason": safety.kill_switch_reason,
        "safety_state_version": safety.version,
        "health_state_rows_created": 0,
        "health_bridge_rows_created": 0,
        "fabricated_health": False,
        "readiness_status": readiness.get("status"),
        "readiness_blockers": blockers,
        "next_action": "PRODUCE_AND_VALIDATE_REAL_STRATEGY_AND_PORTFOLIO_HEALTH_EVIDENCE",
        "broker_network_used": False,
        "credentials_read": False,
        "local_state_write_performed": True,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "approval_consumed": False,
        "oms_submitting": False,
        "lifecycle_unknown": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Commission the local durable R6 Health R4 core schema in fail-closed mode. "
            "Creates no Health evidence and has no broker/POST/LIVE authority."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = commission_health_core(
            workspace_path=args.workspace,
            now=datetime.now(timezone.utc),
        )
    except Exception as exc:
        result = {
            "status": "HEALTH_R4_CORE_COMMISSIONING_BLOCKED",
            "mode": "LOCAL_COMMISSIONING_NO_POST",
            "reason": str(exc),
            "fabricated_health": False,
            "broker_network_used": False,
            "credentials_read": False,
            "broker_write_performed": False,
            "external_post_authorized": False,
            "approval_consumed": False,
            "oms_submitting": False,
            "lifecycle_unknown": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0 if result.get("status") == "HEALTH_R4_CORE_COMMISSIONED_EVIDENCE_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
