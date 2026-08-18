from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path

from autotrade.brokers.alpaca_paper_flat_account import AlpacaPaperFlatAccountGateway
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountGateway,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.persistence import SQLitePortfolioStore, SQLiteRuntime
from autotrade.risk_state import SQLiteR2SafetyStateStore

import mac_crypto_canary_preview as preview
from mac_crypto_cold_start_portfolio_bootstrap import (
    BOOTSTRAP_MANIFEST_NAME,
    _account_anchor,
    _health_counts,
    _read_manifest as _read_bootstrap_manifest,
)
from mac_crypto_health_commissioning import COMMISSIONING_KILL_REASON


WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
ATTESTATION_DIR = "qualification_cold_start"
ATTESTATION_TTL = timedelta(seconds=30)
BROKER_EVIDENCE_MAX_AGE = timedelta(seconds=5)
EXPECTED_SYMBOL = "BTC/USD"
MAX_NOTIONAL = Decimal("5")
TARGET_NOTIONAL = Decimal("2")
MIN_NOTIONAL = Decimal("1")
_ZERO = Decimal("0")


class CryptoColdStartQualificationAttestationError(RuntimeError):
    pass


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("attestation timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _require_fresh(value: datetime, *, now: datetime, label: str) -> None:
    observed = _aware(value)
    if observed > now + timedelta(seconds=2):
        raise CryptoColdStartQualificationAttestationError(f"{label} evidence timestamp is in the future")
    if now - observed > BROKER_EVIDENCE_MAX_AGE:
        raise CryptoColdStartQualificationAttestationError(f"{label} evidence is stale")


def _credentials() -> AlpacaPaperCredentials:
    key = os.environ.get(KEY_ENV, "")
    secret = os.environ.get(SECRET_ENV, "")
    if not key or not secret:
        raise CryptoColdStartQualificationAttestationError(
            "PAPER Key + Secret are required for cold-start qualification attestation"
        )
    return AlpacaPaperCredentials(key_id=key, secret_key=secret)


def _root(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TypeError("workspace_path must be pathlib.Path")
    raw = path.expanduser()
    if raw.is_symlink() or not raw.is_dir():
        raise CryptoColdStartQualificationAttestationError(
            "existing non-symlink workspace is required"
        )
    return raw.resolve()


def _hash_payload(payload: dict[str, object]) -> str:
    material = {key: value for key, value in payload.items() if key != "attestation_hash"}
    return sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _decimal(value: object, *, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CryptoColdStartQualificationAttestationError(f"{label} is not a valid decimal") from exc
    if not parsed.is_finite():
        raise CryptoColdStartQualificationAttestationError(f"{label} must be finite")
    return parsed


def _require_zero_portfolio(current, *, account_reference: str, portfolio_value: Decimal) -> None:
    if current.version != 1:
        raise CryptoColdStartQualificationAttestationError(
            "cold-start qualification requires durable Portfolio State version 1"
        )
    snapshot = current.snapshot
    expected_prefix = f"r6-crypto-paper-cold-start:{account_reference[:20]}"
    if snapshot.snapshot_id != expected_prefix:
        raise CryptoColdStartQualificationAttestationError(
            "durable Portfolio State is not bound to the fresh PAPER account"
        )
    if snapshot.equity != portfolio_value:
        raise CryptoColdStartQualificationAttestationError(
            "durable Portfolio State equity differs from fresh PAPER portfolio value"
        )
    if snapshot.gross_exposure != _ZERO or snapshot.net_exposure != _ZERO or snapshot.open_orders != 0:
        raise CryptoColdStartQualificationAttestationError(
            "durable Portfolio State is not exactly flat"
        )
    if snapshot.signed_position_notional_by_symbol:
        raise CryptoColdStartQualificationAttestationError(
            "durable Portfolio State contains symbol positions"
        )
    if any(value != _ZERO for value in snapshot.strategy_gross_exposure.values()):
        raise CryptoColdStartQualificationAttestationError(
            "durable Portfolio State contains strategy exposure"
        )
    if any(
        value != _ZERO
        for values in snapshot.strategy_signed_position_notional_by_symbol.values()
        for value in values.values()
    ):
        raise CryptoColdStartQualificationAttestationError(
            "durable Portfolio State contains strategy positions"
        )
    if not snapshot.reconciliation_ok or not snapshot.broker_state_known:
        raise CryptoColdStartQualificationAttestationError(
            "durable Portfolio State is not reconciled and broker-grounded"
        )


def _validate_preview(result: dict[str, object]) -> dict[str, object]:
    if result.get("status") != "CRYPTO_PAPER_QUALIFICATION_PREVIEW_PASS":
        raise CryptoColdStartQualificationAttestationError("certified qualification preview did not PASS")
    if result.get("mode") != "DRY_RUN_NO_POST" or result.get("symbol") != EXPECTED_SYMBOL:
        raise CryptoColdStartQualificationAttestationError("preview scope is not exact BTC/USD dry-run")
    if result.get("broker_reads") != 6 or result.get("account_flat") is not True:
        raise CryptoColdStartQualificationAttestationError(
            "preview did not prove the complete fresh broker-read/flat-account contract"
        )
    entry = result.get("entry")
    operator = result.get("operator")
    protection = result.get("protection")
    ambiguity = result.get("ambiguity_policy")
    if not all(isinstance(value, dict) for value in (entry, operator, protection, ambiguity)):
        raise CryptoColdStartQualificationAttestationError("preview structured contract is incomplete")
    assert isinstance(entry, dict)
    assert isinstance(operator, dict)
    assert isinstance(protection, dict)
    assert isinstance(ambiguity, dict)
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        raise CryptoColdStartQualificationAttestationError("preview entry payload is missing")
    if payload.get("symbol") != EXPECTED_SYMBOL or payload.get("side") != "buy":
        raise CryptoColdStartQualificationAttestationError("preview entry identity/side drifted")
    if payload.get("type") != "limit" or payload.get("time_in_force") != "ioc":
        raise CryptoColdStartQualificationAttestationError("preview entry is not LIMIT IOC")
    notional = _decimal(entry.get("notional"), label="preview notional")
    hard_cap = _decimal(entry.get("safety_hard_cap"), label="preview hard cap")
    target = _decimal(entry.get("target_notional"), label="preview target notional")
    minimum = _decimal(entry.get("minimum_buy_market_value"), label="preview minimum notional")
    if not MIN_NOTIONAL <= notional <= MAX_NOTIONAL:
        raise CryptoColdStartQualificationAttestationError(
            f"preview notional {notional} is outside cold-start qualification bounds"
        )
    if hard_cap <= 0 or hard_cap > MAX_NOTIONAL:
        raise CryptoColdStartQualificationAttestationError("preview Safety hard cap exceeds USD 5")
    if target != TARGET_NOTIONAL or minimum != MIN_NOTIONAL:
        raise CryptoColdStartQualificationAttestationError("preview sizing constants drifted")
    if entry.get("network_write_authorized") is not False:
        raise CryptoColdStartQualificationAttestationError("preview unexpectedly authorizes network write")
    if operator.get("approval_recorded") is not False or operator.get("decision_consumed") is not False:
        raise CryptoColdStartQualificationAttestationError("preview unexpectedly contains approval authority")
    if operator.get("reusable_for_real_execution") is not False:
        raise CryptoColdStartQualificationAttestationError("preview is unexpectedly reusable for execution")
    if protection.get("quantity_rule") != "EXACT_CONFIRMED_NET_LONG_AFTER_RECONCILIATION":
        raise CryptoColdStartQualificationAttestationError("preview protection quantity rule drifted")
    if protection.get("warning") != "STOP_LIMIT_IS_NOT_A_GUARANTEED_EXIT_OR_MAX_LOSS":
        raise CryptoColdStartQualificationAttestationError("preview protection warning drifted")
    if ambiguity.get("blind_retry") is not False or ambiguity.get("unknown_before_io") is not True:
        raise CryptoColdStartQualificationAttestationError("preview ambiguity policy drifted")
    if ambiguity.get("on_timeout_or_ambiguous_ack") != "RECONCILE_ONLY":
        raise CryptoColdStartQualificationAttestationError("preview ambiguous-ack recovery drifted")
    for key, expected in (
        ("broker_write_performed", False),
        ("external_post_authorized", False),
        ("operator_approval_authority", "NONE"),
        ("capital_authority", "NONE"),
        ("live_trading", "BLOCKED"),
        ("profitability_claim", False),
    ):
        if result.get(key) != expected:
            raise CryptoColdStartQualificationAttestationError(f"preview authority binding mismatch: {key}")
    return entry


def _persist_attestation(root: Path, document: dict[str, object]) -> Path:
    directory = root / ATTESTATION_DIR
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise CryptoColdStartQualificationAttestationError("attestation directory is unsafe")
    directory.mkdir(mode=0o700, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError as exc:
        raise CryptoColdStartQualificationAttestationError(
            "cannot restrict attestation directory permissions"
        ) from exc
    attestation_hash = str(document["attestation_hash"])
    path = directory / f"cold_start_{attestation_hash[:24]}.json"
    encoded = json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise CryptoColdStartQualificationAttestationError("existing attestation path is unsafe")
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CryptoColdStartQualificationAttestationError("existing attestation is unreadable") from exc
        if existing != document:
            raise CryptoColdStartQualificationAttestationError("attestation hash collision/content mismatch")
        return path
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
        path.chmod(0o600)
    except OSError as exc:
        raise CryptoColdStartQualificationAttestationError("cannot persist qualification attestation") from exc
    return path


def attest_cold_start_qualification(
    *,
    workspace_path: Path,
    credentials: AlpacaPaperCredentials,
    now: datetime,
    account_gateway=None,
    flat_gateway=None,
    preview_runner=None,
) -> dict[str, object]:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise CryptoColdStartQualificationAttestationError(
            "cold-start qualification attestation refuses R6_EXTERNAL_PAPER_WRITE=ENABLED"
        )
    instant = _aware(now)
    root = _root(workspace_path)
    workspace = PaperOperationalWorkspace(root=root)
    core = workspace.core_db_path
    if core.is_symlink() or not core.is_file():
        raise CryptoColdStartQualificationAttestationError("commissioned core.sqlite3 is required")

    bootstrap_manifest = _read_bootstrap_manifest(root / BOOTSTRAP_MANIFEST_NAME)
    if bootstrap_manifest is None:
        raise CryptoColdStartQualificationAttestationError(
            "verified cold-start Portfolio bootstrap manifest is required"
        )

    runtime = SQLiteRuntime(core)
    safety = SQLiteR2SafetyStateStore(runtime).get()
    if not safety.kill_switch_active or safety.kill_switch_reason != COMMISSIONING_KILL_REASON:
        raise CryptoColdStartQualificationAttestationError(
            "commissioning kill switch must remain active for cold-start qualification"
        )
    health_count, bridge_count = _health_counts(runtime)
    if health_count != 0 or bridge_count != 0:
        raise CryptoColdStartQualificationAttestationError(
            "cold-start qualification requires Strategy/Portfolio Health and bridge to remain absent"
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
    if not flat.clean_for_first_canary:
        raise CryptoColdStartQualificationAttestationError(
            f"fresh PAPER account is not flat; positions={flat.position_count}, open_orders={flat.open_order_count}"
        )
    if flat.account_attestation_fingerprint != account.fingerprint:
        raise CryptoColdStartQualificationAttestationError("flat-account evidence is not bound to fresh account")
    if flat.credential_reference != account.credential_reference:
        raise CryptoColdStartQualificationAttestationError("flat-account credential provenance drifted")
    if bootstrap_manifest.get("account_reference") != account.account_reference:
        raise CryptoColdStartQualificationAttestationError("bootstrap and fresh PAPER account differ")
    if bootstrap_manifest.get("credential_reference") != account.credential_reference:
        raise CryptoColdStartQualificationAttestationError("bootstrap credential provenance differs")

    current = SQLitePortfolioStore(runtime).get()
    _require_zero_portfolio(
        current,
        account_reference=account.account_reference,
        portfolio_value=account.portfolio_value,
    )
    if bootstrap_manifest.get("portfolio_version") != 1:
        raise CryptoColdStartQualificationAttestationError("bootstrap manifest Portfolio version drifted")
    if bootstrap_manifest.get("portfolio_snapshot_id") != current.snapshot.snapshot_id:
        raise CryptoColdStartQualificationAttestationError("bootstrap manifest Portfolio binding drifted")

    runner = preview_runner or preview.run
    preview_result = runner(
        workspace_path=root,
        credentials=credentials,
        now=instant,
        symbol=EXPECTED_SYMBOL,
    )
    if not isinstance(preview_result, dict):
        raise CryptoColdStartQualificationAttestationError("preview runner returned no structured result")
    entry = _validate_preview(preview_result)

    package_hash = entry.get("package_hash")
    payload_hash = entry.get("payload_hash")
    client_order_id = entry.get("dry_run_client_order_id")
    if not all(isinstance(value, str) and value for value in (package_hash, payload_hash, client_order_id)):
        raise CryptoColdStartQualificationAttestationError("preview package identity is incomplete")

    valid_until = instant + ATTESTATION_TTL
    material: dict[str, object] = {
        "schema_version": 1,
        "attestation_type": "R6_CRYPTO_PAPER_COLD_START_QUALIFICATION",
        "environment": "PAPER",
        "symbol": EXPECTED_SYMBOL,
        "scope": "FIRST_TECHNICAL_CANARY_ONLY",
        "issued_at": instant.isoformat(),
        "valid_until": valid_until.isoformat(),
        "broker_reads": 9,
        "account_reference": account.account_reference,
        "credential_reference": account.credential_reference,
        "fresh_account_fingerprint": account.fingerprint,
        "fresh_flat_account_fingerprint": flat.fingerprint,
        "position_count": flat.position_count,
        "open_order_count": flat.open_order_count,
        "portfolio_version": current.version,
        "portfolio_snapshot_id": current.snapshot.snapshot_id,
        "portfolio_equity": str(current.snapshot.equity),
        "portfolio_gross_exposure": "0",
        "portfolio_net_exposure": "0",
        "portfolio_open_orders": 0,
        "portfolio_reconciliation_ok": True,
        "portfolio_broker_state_known": True,
        "bootstrap_manifest_hash": bootstrap_manifest.get("manifest_hash"),
        "kill_switch_active": True,
        "kill_switch_reason": COMMISSIONING_KILL_REASON,
        "kill_switch_reset": False,
        "safety_state_version": safety.version,
        "strategy_health_state_rows": 0,
        "portfolio_health_state_rows": 0,
        "health_bridge_rows": 0,
        "strategy_health_expected_missing": True,
        "portfolio_health_expected_missing": True,
        "health_override_authorized": False,
        "health_normal_path_modified": False,
        "preview_status": preview_result["status"],
        "preview_package_hash": package_hash,
        "preview_payload_hash": payload_hash,
        "preview_client_order_id": client_order_id,
        "preview_notional": entry.get("notional"),
        "preview_safety_hard_cap": entry.get("safety_hard_cap"),
        "preview_network_write_authorized": False,
        "protection_required_after_reconciled_fill": True,
        "ambiguity_policy": "UNKNOWN_BEFORE_IO_RECONCILE_ONLY_NO_BLIND_RETRY",
        "qualification_candidate": True,
        "qualification_completed": False,
        "profitability_evidence": False,
        "approval_consumed": False,
        "new_human_approval_required_for_any_future_execution": True,
        "final_guard_opened": False,
        "oms_submitting": False,
        "lifecycle_unknown": False,
        "credentials_persisted": False,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "execution_authority": "NONE",
        "capital_authority": "NONE",
        "reusable_for_real_execution": False,
        "live_trading": "BLOCKED",
    }
    material["attestation_hash"] = _hash_payload(material)
    path = _persist_attestation(root, material)

    return {
        "status": "CRYPTO_COLD_START_QUALIFICATION_ATTESTED_NO_EXECUTION",
        "mode": "PAPER_READ_LOCAL_ATTESTATION_NO_POST",
        "workspace": str(root),
        "attestation_path": str(path),
        **material,
        "next_action": "RECERTIFY_SEPARATE_COLD_START_FINAL_GUARD_BINDING_WITH_NEW_HUMAN_APPROVAL",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a short-lived, non-executable cold-start qualification attestation for the first BTC/USD "
            "PAPER canary. Requires durable flat Portfolio v1, active commissioning kill switch, absent Health, "
            "fresh flat broker evidence and a certified Safety/OMS dry-run. Does not consume approval or POST."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--allow-paper-crypto-read", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_crypto_read:
        raise SystemExit("cold-start qualification attestation requires explicit --allow-paper-crypto-read")
    try:
        result = attest_cold_start_qualification(
            workspace_path=args.workspace,
            credentials=_credentials(),
            now=datetime.now(timezone.utc),
        )
    except Exception as exc:
        result = {
            "status": "CRYPTO_COLD_START_QUALIFICATION_ATTESTATION_BLOCKED",
            "mode": "PAPER_READ_LOCAL_ATTESTATION_NO_POST",
            "reason": str(exc),
            "credentials_persisted": False,
            "broker_write_performed": False,
            "external_post_authorized": False,
            "approval_consumed": False,
            "final_guard_opened": False,
            "oms_submitting": False,
            "lifecycle_unknown": False,
            "execution_authority": "NONE",
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0 if result.get("status") == "CRYPTO_COLD_START_QUALIFICATION_ATTESTED_NO_EXECUTION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
