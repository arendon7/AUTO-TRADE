from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path

from autotrade.brokers.alpaca_paper_flat_account import (
    AlpacaPaperFlatAccountGateway,
    PaperFlatAccountAttestation,
)
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountAttestation,
    AlpacaPaperAccountGateway,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.domain import PortfolioSnapshot
from autotrade.ledger import LedgerEvent
from autotrade.persistence import SQLiteEventLedger, SQLitePortfolioStore, SQLiteRuntime
from autotrade.risk_state import SQLiteR2SafetyStateStore
from autotrade.state import PortfolioNotInitialized

from mac_crypto_health_commissioning import (
    COMMISSIONING_KILL_REASON,
    MANIFEST_NAME as HEALTH_COMMISSIONING_MANIFEST_NAME,
    _read_existing_manifest as _read_health_commissioning_manifest,
)


WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
BOOTSTRAP_MANIFEST_NAME = "crypto_cold_start_portfolio_bootstrap_manifest.json"
BOOTSTRAP_EVENT_ID = "r6-crypto-cold-start-portfolio-bootstrap-v1"
MAX_EVIDENCE_AGE = timedelta(seconds=5)
_ZERO = Decimal("0")


class CryptoColdStartPortfolioBootstrapError(RuntimeError):
    pass


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("bootstrap timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _workspace_root(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TypeError("workspace_path must be pathlib.Path")
    expanded = path.expanduser()
    if not expanded.exists() or not expanded.is_dir() or expanded.is_symlink():
        raise CryptoColdStartPortfolioBootstrapError(
            "workspace is missing, not a directory, or is a symlink"
        )
    return expanded.resolve()


def _credentials() -> AlpacaPaperCredentials:
    key = os.environ.get(KEY_ENV, "")
    secret = os.environ.get(SECRET_ENV, "")
    if not key or not secret:
        raise CryptoColdStartPortfolioBootstrapError(
            "PAPER Key + Secret are required for cold-start portfolio bootstrap"
        )
    return AlpacaPaperCredentials(key_id=key, secret_key=secret)


def _account_anchor(workspace: PaperOperationalWorkspace) -> str:
    path = workspace.account_attestation_path
    if path.is_symlink() or not path.is_file():
        raise CryptoColdStartPortfolioBootstrapError(
            "verified PAPER account is missing; verify the PAPER account in the Control Center first"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoColdStartPortfolioBootstrapError(
            "verified PAPER account evidence cannot be read"
        ) from exc
    if not isinstance(raw, dict) or raw.get("environment") != "PAPER":
        raise CryptoColdStartPortfolioBootstrapError("workspace account evidence is not PAPER")
    if raw.get("credentials_persisted") is not False:
        raise CryptoColdStartPortfolioBootstrapError(
            "workspace account evidence violates credential persistence policy"
        )
    account_id = raw.get("account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        raise CryptoColdStartPortfolioBootstrapError("workspace account ID is missing")
    return account_id.strip()


def _manifest_hash(payload: dict[str, object]) -> str:
    material = {key: value for key, value in payload.items() if key != "manifest_hash"}
    return sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


def _read_manifest(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise CryptoColdStartPortfolioBootstrapError(
            "cold-start portfolio bootstrap manifest is not a safe regular file"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoColdStartPortfolioBootstrapError(
            "cold-start portfolio bootstrap manifest is unreadable"
        ) from exc
    if not isinstance(payload, dict):
        raise CryptoColdStartPortfolioBootstrapError(
            "cold-start portfolio bootstrap manifest must be a JSON object"
        )
    expected = payload.get("manifest_hash")
    if not isinstance(expected, str) or expected != _manifest_hash(payload):
        raise CryptoColdStartPortfolioBootstrapError(
            "cold-start portfolio bootstrap manifest hash mismatch"
        )
    for key, expected_value in (
        ("schema_version", 1),
        ("environment", "PAPER"),
        ("flat_account_required", True),
        ("health_created", False),
        ("health_bridge_created", False),
        ("kill_switch_reset", False),
        ("broker_write_performed", False),
        ("external_post_authorized", False),
        ("approval_consumed", False),
        ("capital_authority", "NONE"),
        ("live_trading", "BLOCKED"),
    ):
        if payload.get(key) != expected_value:
            raise CryptoColdStartPortfolioBootstrapError(
                f"cold-start portfolio bootstrap manifest binding mismatch: {key}"
            )
    return payload


def _write_manifest_once(path: Path, payload: dict[str, object]) -> dict[str, object]:
    existing = _read_manifest(path)
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
        existing = _read_manifest(path)
        if existing is None:
            raise CryptoColdStartPortfolioBootstrapError(
                "cold-start portfolio bootstrap manifest race produced no readable manifest"
            )
        return existing
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise CryptoColdStartPortfolioBootstrapError(
            "cannot restrict cold-start portfolio bootstrap manifest permissions"
        ) from exc
    return document


def _health_counts(runtime: SQLiteRuntime) -> tuple[int, int]:
    conn = runtime.connect()
    try:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        health_count = (
            int(conn.execute("SELECT COUNT(*) FROM health_state_v2").fetchone()[0])
            if "health_state_v2" in tables
            else -1
        )
        bridge_count = (
            int(conn.execute("SELECT COUNT(*) FROM health_bridge_state").fetchone()[0])
            if "health_bridge_state" in tables
            else -1
        )
        return health_count, bridge_count
    finally:
        conn.close()


def _require_fresh(timestamp: datetime, *, now: datetime, label: str) -> None:
    observed = _aware(timestamp)
    if observed > now + timedelta(seconds=2):
        raise CryptoColdStartPortfolioBootstrapError(f"{label} evidence timestamp is in the future")
    if now - observed > MAX_EVIDENCE_AGE:
        raise CryptoColdStartPortfolioBootstrapError(f"{label} evidence is stale")


def _zero_snapshot(*, account: AlpacaPaperAccountAttestation) -> PortfolioSnapshot:
    if account.portfolio_value <= 0:
        raise CryptoColdStartPortfolioBootstrapError(
            "PAPER portfolio value must be positive for durable Portfolio State"
        )
    return PortfolioSnapshot(
        snapshot_id=f"r6-crypto-paper-cold-start:{account.account_reference[:20]}",
        equity=account.portfolio_value,
        gross_exposure=_ZERO,
        net_exposure=_ZERO,
        daily_pnl=_ZERO,
        drawdown=_ZERO,
        open_orders=0,
        signed_position_notional_by_symbol={},
        strategy_gross_exposure={},
        strategy_signed_position_notional_by_symbol={},
        reconciliation_ok=True,
        broker_state_known=True,
    )


def _verify_existing_zero_portfolio(
    current,
    *,
    expected: PortfolioSnapshot,
) -> None:
    snapshot = current.snapshot
    if current.version != 1:
        raise CryptoColdStartPortfolioBootstrapError(
            "cold-start bootstrap refuses Portfolio State beyond version 1"
        )
    if snapshot.snapshot_id != expected.snapshot_id:
        raise CryptoColdStartPortfolioBootstrapError(
            "existing Portfolio State belongs to a different broker/account bootstrap"
        )
    if snapshot.equity != expected.equity:
        raise CryptoColdStartPortfolioBootstrapError(
            "existing Portfolio State equity differs from fresh flat PAPER account"
        )
    if not snapshot.reconciliation_ok or not snapshot.broker_state_known:
        raise CryptoColdStartPortfolioBootstrapError(
            "existing Portfolio State is not broker-grounded and reconciled"
        )
    if snapshot.open_orders != 0:
        raise CryptoColdStartPortfolioBootstrapError(
            "existing Portfolio State has open orders"
        )
    if snapshot.gross_exposure != _ZERO or snapshot.net_exposure != _ZERO:
        raise CryptoColdStartPortfolioBootstrapError(
            "existing Portfolio State is not flat"
        )
    if any(value != _ZERO for value in snapshot.signed_position_notional_by_symbol.values()):
        raise CryptoColdStartPortfolioBootstrapError(
            "existing Portfolio State has nonzero symbol exposure"
        )
    if any(value != _ZERO for value in snapshot.strategy_gross_exposure.values()):
        raise CryptoColdStartPortfolioBootstrapError(
            "existing Portfolio State has nonzero strategy exposure"
        )
    if any(
        value != _ZERO
        for positions in snapshot.strategy_signed_position_notional_by_symbol.values()
        for value in positions.values()
    ):
        raise CryptoColdStartPortfolioBootstrapError(
            "existing Portfolio State has nonzero strategy positions"
        )


def _append_event_once(
    runtime: SQLiteRuntime,
    *,
    now: datetime,
    account: AlpacaPaperAccountAttestation,
    flat: PaperFlatAccountAttestation,
    portfolio_version: int,
    snapshot_id: str,
) -> None:
    conn = runtime.connect()
    try:
        existing = conn.execute(
            "SELECT 1 FROM ledger_events WHERE event_id=?", (BOOTSTRAP_EVENT_ID,)
        ).fetchone()
    finally:
        conn.close()
    if existing is not None:
        return
    SQLiteEventLedger(runtime).append(
        LedgerEvent(
            event_id=BOOTSTRAP_EVENT_ID,
            event_type="R6_CRYPTO_COLD_START_PORTFOLIO_BOOTSTRAPPED",
            occurred_at=now,
            payload={
                "account_reference": account.account_reference,
                "account_attestation_fingerprint": account.fingerprint,
                "flat_account_fingerprint": flat.fingerprint,
                "portfolio_version": str(portfolio_version),
                "portfolio_snapshot_id": snapshot_id,
                "gross_exposure": "0",
                "net_exposure": "0",
                "open_orders": "0",
                "health_created": "false",
                "health_bridge_created": "false",
                "kill_switch_reset": "false",
                "broker_write_performed": "false",
                "external_post_authorized": "false",
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
            },
        )
    )


def bootstrap_cold_start_portfolio(
    *,
    workspace_path: Path,
    credentials: AlpacaPaperCredentials,
    now: datetime,
    account_gateway=None,
    flat_gateway=None,
) -> dict[str, object]:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise CryptoColdStartPortfolioBootstrapError(
            "cold-start portfolio bootstrap refuses R6_EXTERNAL_PAPER_WRITE=ENABLED"
        )
    instant = _aware(now)
    root = _workspace_root(workspace_path)
    workspace = PaperOperationalWorkspace(root=root)
    core = workspace.core_db_path
    if core.is_symlink() or not core.is_file():
        raise CryptoColdStartPortfolioBootstrapError(
            "commissioned core.sqlite3 is required before cold-start portfolio bootstrap"
        )
    health_manifest = _read_health_commissioning_manifest(root / HEALTH_COMMISSIONING_MANIFEST_NAME)
    if health_manifest is None:
        raise CryptoColdStartPortfolioBootstrapError(
            "verified Health R4 commissioning manifest is required before portfolio bootstrap"
        )

    runtime = SQLiteRuntime(core)
    safety = SQLiteR2SafetyStateStore(runtime).get()
    if not safety.kill_switch_active or safety.kill_switch_reason != COMMISSIONING_KILL_REASON:
        raise CryptoColdStartPortfolioBootstrapError(
            "commissioning kill switch must remain active during cold-start portfolio bootstrap"
        )
    health_count, bridge_count = _health_counts(runtime)
    if health_count != 0 or bridge_count != 0:
        raise CryptoColdStartPortfolioBootstrapError(
            "cold-start portfolio bootstrap requires Health and bridge to remain absent"
        )

    expected_account_id = _account_anchor(workspace)
    config = AlpacaPaperGatewayConfig(enabled=True)
    account_reader = account_gateway or AlpacaPaperAccountGateway(config=config)
    flat_reader = flat_gateway or AlpacaPaperFlatAccountGateway(config=config)
    account = account_reader.attest_account(
        credentials=credentials,
        expected_account_id=expected_account_id,
        now=instant,
    )
    flat = flat_reader.attest_flatness(
        credentials=credentials,
        account_attestation_fingerprint=account.fingerprint,
        expected_credential_reference=account.credential_reference,
        now=instant,
    )
    _require_fresh(account.attested_at, now=instant, label="account")
    _require_fresh(flat.attested_at, now=instant, label="flat-account")
    if flat.account_attestation_fingerprint != account.fingerprint:
        raise CryptoColdStartPortfolioBootstrapError(
            "flat-account evidence is not bound to the fresh PAPER account"
        )
    if flat.credential_reference != account.credential_reference:
        raise CryptoColdStartPortfolioBootstrapError(
            "flat-account credential provenance differs from fresh PAPER account"
        )
    if not flat.clean_for_first_canary:
        raise CryptoColdStartPortfolioBootstrapError(
            f"PAPER account is not flat; positions={flat.position_count}, open_orders={flat.open_order_count}"
        )

    portfolio_store = SQLitePortfolioStore(runtime)
    expected_snapshot = _zero_snapshot(account=account)
    created = False
    try:
        current = portfolio_store.get()
    except PortfolioNotInitialized:
        current = portfolio_store.initialize(expected_snapshot, now=instant)
        created = True
    _verify_existing_zero_portfolio(current, expected=expected_snapshot)

    manifest_path = root / BOOTSTRAP_MANIFEST_NAME
    existing_manifest = _read_manifest(manifest_path)
    if existing_manifest is not None:
        if existing_manifest.get("account_reference") != account.account_reference:
            raise CryptoColdStartPortfolioBootstrapError(
                "existing cold-start bootstrap manifest belongs to a different PAPER account"
            )
        if existing_manifest.get("credential_reference") != account.credential_reference:
            raise CryptoColdStartPortfolioBootstrapError(
                "existing cold-start bootstrap manifest credential provenance changed"
            )
        if existing_manifest.get("portfolio_snapshot_id") != current.snapshot.snapshot_id:
            raise CryptoColdStartPortfolioBootstrapError(
                "existing cold-start bootstrap manifest Portfolio State binding changed"
            )
    else:
        existing_manifest = _write_manifest_once(
            manifest_path,
            {
                "schema_version": 1,
                "environment": "PAPER",
                "bootstrapped_at": instant.isoformat(),
                "account_reference": account.account_reference,
                "credential_reference": account.credential_reference,
                "account_attestation_fingerprint": account.fingerprint,
                "flat_account_fingerprint": flat.fingerprint,
                "positions_response_hash": flat.positions_response_hash,
                "orders_response_hash": flat.orders_response_hash,
                "portfolio_version": current.version,
                "portfolio_snapshot_id": current.snapshot.snapshot_id,
                "portfolio_equity": str(current.snapshot.equity),
                "gross_exposure": "0",
                "net_exposure": "0",
                "open_orders": 0,
                "reconciliation_ok": True,
                "broker_state_known": True,
                "flat_account_required": True,
                "health_created": False,
                "health_bridge_created": False,
                "kill_switch_reset": False,
                "kill_switch_reason": COMMISSIONING_KILL_REASON,
                "broker_reads": 3,
                "credentials_persisted": False,
                "broker_write_performed": False,
                "external_post_authorized": False,
                "approval_consumed": False,
                "oms_submitting": False,
                "lifecycle_unknown": False,
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
            },
        )

    _append_event_once(
        runtime,
        now=instant,
        account=account,
        flat=flat,
        portfolio_version=current.version,
        snapshot_id=current.snapshot.snapshot_id,
    )

    safety_after = SQLiteR2SafetyStateStore(runtime).get()
    if (
        not safety_after.kill_switch_active
        or safety_after.kill_switch_reason != COMMISSIONING_KILL_REASON
        or safety_after.version != safety.version
    ):
        raise CryptoColdStartPortfolioBootstrapError(
            "cold-start portfolio bootstrap must not reset or mutate the commissioning kill switch"
        )
    final_health_count, final_bridge_count = _health_counts(runtime)
    if final_health_count != 0 or final_bridge_count != 0:
        raise CryptoColdStartPortfolioBootstrapError(
            "cold-start portfolio bootstrap unexpectedly created Health authority"
        )

    return {
        "status": "CRYPTO_COLD_START_PORTFOLIO_BOOTSTRAPPED_HEALTH_STILL_REQUIRED",
        "mode": "PAPER_READ_LOCAL_PORTFOLIO_BOOTSTRAP_NO_POST",
        "workspace": str(root),
        "core_database": str(core),
        "manifest": str(manifest_path),
        "manifest_hash": existing_manifest["manifest_hash"],
        "portfolio_created": created,
        "portfolio_version": current.version,
        "portfolio_snapshot_id": current.snapshot.snapshot_id,
        "portfolio_equity": str(current.snapshot.equity),
        "gross_exposure": "0",
        "net_exposure": "0",
        "open_orders": 0,
        "reconciliation_ok": True,
        "broker_state_known": True,
        "account_reference": account.account_reference,
        "credential_reference": account.credential_reference,
        "fresh_account_fingerprint": account.fingerprint,
        "fresh_flat_account_fingerprint": flat.fingerprint,
        "broker_reads": 3,
        "position_count": flat.position_count,
        "broker_open_order_count": flat.open_order_count,
        "health_state_rows": 0,
        "health_bridge_rows": 0,
        "kill_switch_active": True,
        "kill_switch_reason": COMMISSIONING_KILL_REASON,
        "kill_switch_reset": False,
        "credentials_read": True,
        "credentials_persisted": False,
        "local_state_write_performed": created or existing_manifest is not None,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "approval_consumed": False,
        "oms_submitting": False,
        "lifecycle_unknown": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "profitability_claim": False,
        "next_action": "COLD_START_QUALIFICATION_GUARD_UAT_WITH_HEALTH_STILL_ABSENT",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bootstrap version-1 durable Portfolio State from fresh flat Alpaca PAPER account evidence. "
            "Uses GET-only broker reads and local SQLite write; does not create Health, reset kill switch, "
            "consume approval, invoke Final Guard, or expose broker POST."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--allow-paper-crypto-read", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_crypto_read:
        raise SystemExit("cold-start portfolio bootstrap requires explicit --allow-paper-crypto-read")
    try:
        result = bootstrap_cold_start_portfolio(
            workspace_path=args.workspace,
            credentials=_credentials(),
            now=datetime.now(timezone.utc),
        )
    except Exception as exc:
        result = {
            "status": "CRYPTO_COLD_START_PORTFOLIO_BOOTSTRAP_BLOCKED",
            "mode": "PAPER_READ_LOCAL_PORTFOLIO_BOOTSTRAP_NO_POST",
            "reason": str(exc),
            "credentials_persisted": False,
            "broker_write_performed": False,
            "external_post_authorized": False,
            "approval_consumed": False,
            "oms_submitting": False,
            "lifecycle_unknown": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return (
        0
        if result.get("status")
        == "CRYPTO_COLD_START_PORTFOLIO_BOOTSTRAPPED_HEALTH_STILL_REQUIRED"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
