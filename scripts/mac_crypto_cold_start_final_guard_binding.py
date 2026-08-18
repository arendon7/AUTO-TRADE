from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path

from autotrade.brokers.alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecisionContext,
    crypto_operator_confirmation_challenge,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials

import mac_crypto_approval_prepare as approval_prepare
import mac_crypto_cold_start_qualification_attestation as qualification


WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
EXPECTED_SYMBOL = "BTC/USD"
BINDING_DIR = "qualification_cold_start/final_guard_binding"
BINDING_TTL = timedelta(seconds=30)
MIN_REMAINING_LIFE = timedelta(seconds=5)
MAX_NOTIONAL = Decimal("5")
MIN_NOTIONAL = Decimal("1")


class CryptoColdStartFinalGuardBindingError(RuntimeError):
    pass


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("binding timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal(value: object, *, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CryptoColdStartFinalGuardBindingError(f"{label} is not a valid decimal") from exc
    if not parsed.is_finite():
        raise CryptoColdStartFinalGuardBindingError(f"{label} must be finite")
    return parsed


def _hash_payload(payload: dict[str, object], *, hash_key: str) -> str:
    material = {key: value for key, value in payload.items() if key != hash_key}
    return sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _credentials() -> AlpacaPaperCredentials:
    key = os.environ.get(KEY_ENV, "")
    secret = os.environ.get(SECRET_ENV, "")
    if not key or not secret:
        raise CryptoColdStartFinalGuardBindingError(
            "PAPER Key + Secret are required for cold-start Final Guard binding"
        )
    return AlpacaPaperCredentials(key_id=key, secret_key=secret)


def _root(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TypeError("workspace_path must be pathlib.Path")
    raw = path.expanduser()
    if raw.is_symlink() or not raw.is_dir():
        raise CryptoColdStartFinalGuardBindingError("existing non-symlink workspace is required")
    return raw.resolve()


def _persist(root: Path, *, prefix: str, hash_value: str, document: dict[str, object]) -> Path:
    directory = root / BINDING_DIR
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise CryptoColdStartFinalGuardBindingError("Final Guard binding directory is unsafe")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError as exc:
        raise CryptoColdStartFinalGuardBindingError("cannot restrict binding directory permissions") from exc
    path = directory / f"{prefix}_{hash_value[:24]}.json"
    encoded = json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise CryptoColdStartFinalGuardBindingError("existing binding path is unsafe")
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CryptoColdStartFinalGuardBindingError("existing binding document is unreadable") from exc
        if existing != document:
            raise CryptoColdStartFinalGuardBindingError("binding hash collision/content mismatch")
        return path
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
        path.chmod(0o600)
    except OSError as exc:
        raise CryptoColdStartFinalGuardBindingError("cannot persist binding document") from exc
    return path


def _validate_qualification(result: dict[str, object], *, now: datetime, credentials: AlpacaPaperCredentials) -> None:
    if result.get("status") != "CRYPTO_COLD_START_QUALIFICATION_ATTESTED_NO_EXECUTION":
        raise CryptoColdStartFinalGuardBindingError("fresh cold-start qualification attestation did not PASS")
    expected = {
        "symbol": EXPECTED_SYMBOL,
        "scope": "FIRST_TECHNICAL_CANARY_ONLY",
        "broker_reads": 9,
        "portfolio_version": 1,
        "portfolio_gross_exposure": "0",
        "portfolio_net_exposure": "0",
        "portfolio_open_orders": 0,
        "portfolio_reconciliation_ok": True,
        "portfolio_broker_state_known": True,
        "kill_switch_active": True,
        "kill_switch_reset": False,
        "strategy_health_state_rows": 0,
        "portfolio_health_state_rows": 0,
        "health_bridge_rows": 0,
        "strategy_health_expected_missing": True,
        "portfolio_health_expected_missing": True,
        "health_override_authorized": False,
        "health_normal_path_modified": False,
        "qualification_candidate": True,
        "qualification_completed": False,
        "profitability_evidence": False,
        "approval_consumed": False,
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
    for key, value in expected.items():
        if result.get(key) != value:
            raise CryptoColdStartFinalGuardBindingError(f"qualification attestation contract mismatch: {key}")
    credential_reference = getattr(credentials, "credential_reference", None)
    if not isinstance(credential_reference, str) or credential_reference != result.get("credential_reference"):
        raise CryptoColdStartFinalGuardBindingError("qualification attestation credential binding mismatch")
    valid_until_raw = result.get("valid_until")
    if not isinstance(valid_until_raw, str):
        raise CryptoColdStartFinalGuardBindingError("qualification attestation expiry is missing")
    try:
        valid_until = datetime.fromisoformat(valid_until_raw)
    except ValueError as exc:
        raise CryptoColdStartFinalGuardBindingError("qualification attestation expiry is invalid") from exc
    valid_until = _aware(valid_until)
    if valid_until <= now + MIN_REMAINING_LIFE:
        raise CryptoColdStartFinalGuardBindingError("qualification attestation is too close to expiry")
    supplied_hash = result.get("attestation_hash")
    if not isinstance(supplied_hash, str) or len(supplied_hash) != 64:
        raise CryptoColdStartFinalGuardBindingError("qualification attestation hash is missing")


def _approval_contract(result: dict[str, object]) -> tuple[dict[str, object], dict[str, object], CryptoOperatorDecisionContext, str]:
    if result.get("status") != "CRYPTO_PAPER_ONE_SHOT_APPROVAL_PREPARED":
        raise CryptoColdStartFinalGuardBindingError("fresh approval package did not prepare")
    if result.get("mode") != "ONE_SHOT_APPROVAL_REHEARSAL_NO_POST":
        raise CryptoColdStartFinalGuardBindingError("approval package is not NO-POST rehearsal mode")
    for key, expected in (
        ("broker_reads", 6),
        ("broker_write_performed", False),
        ("external_post_authorized", False),
        ("capital_authority", "NONE"),
        ("live_trading", "BLOCKED"),
        ("profitability_claim", False),
    ):
        if result.get(key) != expected:
            raise CryptoColdStartFinalGuardBindingError(f"approval package authority mismatch: {key}")
    entry = result.get("entry")
    operator = result.get("operator")
    protection = result.get("protection")
    ambiguity = result.get("ambiguity_policy")
    if not all(isinstance(value, dict) for value in (entry, operator, protection, ambiguity)):
        raise CryptoColdStartFinalGuardBindingError("approval package structured contract is incomplete")
    assert isinstance(entry, dict)
    assert isinstance(operator, dict)
    assert isinstance(protection, dict)
    assert isinstance(ambiguity, dict)
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        raise CryptoColdStartFinalGuardBindingError("approval package payload is missing")
    if payload.get("symbol") != EXPECTED_SYMBOL or payload.get("side") != "buy":
        raise CryptoColdStartFinalGuardBindingError("approval package identity/side drifted")
    if payload.get("type") != "limit" or payload.get("time_in_force") != "ioc":
        raise CryptoColdStartFinalGuardBindingError("approval package is not BUY LIMIT IOC")
    notional = _decimal(entry.get("notional"), label="approval package notional")
    hard_cap = _decimal(entry.get("safety_hard_cap"), label="approval package hard cap")
    if not MIN_NOTIONAL <= notional <= MAX_NOTIONAL or hard_cap <= 0 or hard_cap > MAX_NOTIONAL:
        raise CryptoColdStartFinalGuardBindingError("approval package exceeds cold-start USD 1-5 bounds")
    if entry.get("network_write_authorized") is not False:
        raise CryptoColdStartFinalGuardBindingError("approval package unexpectedly authorizes network write")
    if operator.get("approval_recorded") is not False or operator.get("decision_consumed") is not False:
        raise CryptoColdStartFinalGuardBindingError("approval package already contains decision authority")
    if operator.get("uat_only") is not True or operator.get("reusable_for_real_execution") is not False:
        raise CryptoColdStartFinalGuardBindingError("approval package is not UAT-only/non-reusable")
    if protection.get("quantity_rule") != "EXACT_CONFIRMED_NET_LONG_AFTER_RECONCILIATION":
        raise CryptoColdStartFinalGuardBindingError("protection quantity rule drifted")
    if ambiguity.get("blind_retry") is not False or ambiguity.get("unknown_before_io") is not True:
        raise CryptoColdStartFinalGuardBindingError("ambiguity policy drifted")
    if ambiguity.get("on_timeout_or_ambiguous_ack") != "RECONCILE_ONLY":
        raise CryptoColdStartFinalGuardBindingError("ambiguous-ack policy drifted")
    context_payload = operator.get("approval_context")
    challenge = operator.get("approval_challenge")
    if not isinstance(context_payload, dict) or not isinstance(challenge, str) or not challenge:
        raise CryptoColdStartFinalGuardBindingError("approval context/challenge is missing")
    context = CryptoOperatorDecisionContext.from_dict(context_payload)
    if context.symbol != EXPECTED_SYMBOL or not context.attempt_id.startswith("approval-uat-"):
        raise CryptoColdStartFinalGuardBindingError("approval context is outside BTC/USD one-shot UAT scope")
    if crypto_operator_confirmation_challenge(context) != challenge:
        raise CryptoColdStartFinalGuardBindingError("approval challenge does not match its context")
    package_hash = entry.get("package_hash")
    payload_hash = entry.get("payload_hash")
    client_order_id = entry.get("dry_run_client_order_id")
    if not all(isinstance(value, str) and value for value in (package_hash, payload_hash, client_order_id)):
        raise CryptoColdStartFinalGuardBindingError("approval package identity is incomplete")
    if context.prepared_package_hash != package_hash or context.client_order_id != client_order_id:
        raise CryptoColdStartFinalGuardBindingError("approval context/package identity mismatch")
    if context.crypto_order_payload_hash != payload_hash:
        raise CryptoColdStartFinalGuardBindingError("approval context/payload hash mismatch")
    return entry, operator, context, challenge


def prepare_binding(
    *,
    workspace_path: Path,
    credentials: AlpacaPaperCredentials,
    now: datetime,
    qualification_runner=None,
    approval_runner=None,
) -> dict[str, object]:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise CryptoColdStartFinalGuardBindingError(
            "cold-start Final Guard binding refuses R6_EXTERNAL_PAPER_WRITE=ENABLED"
        )
    instant = _aware(now)
    root = _root(workspace_path)
    q_runner = qualification_runner or qualification.attest_cold_start_qualification
    a_runner = approval_runner or approval_prepare.run
    qualification_result = q_runner(workspace_path=root, credentials=credentials, now=instant)
    if not isinstance(qualification_result, dict):
        raise CryptoColdStartFinalGuardBindingError("qualification runner returned no structured result")
    _validate_qualification(qualification_result, now=instant, credentials=credentials)
    approval_result = a_runner(
        workspace_path=root,
        credentials=credentials,
        now=instant,
        symbol=EXPECTED_SYMBOL,
    )
    if not isinstance(approval_result, dict):
        raise CryptoColdStartFinalGuardBindingError("approval runner returned no structured result")
    entry, operator, context, challenge = _approval_contract(approval_result)
    execution_deadline = _aware(context.execution_deadline)
    qualification_valid_until = _aware(datetime.fromisoformat(str(qualification_result["valid_until"])))
    valid_until = min(instant + BINDING_TTL, execution_deadline, qualification_valid_until)
    if valid_until <= instant + MIN_REMAINING_LIFE:
        raise CryptoColdStartFinalGuardBindingError("binding package is too close to expiry")
    material: dict[str, object] = {
        "schema_version": 1,
        "binding_type": "R6_CRYPTO_PAPER_COLD_START_FINAL_GUARD_BINDING_UAT",
        "environment": "PAPER",
        "symbol": EXPECTED_SYMBOL,
        "scope": "FIRST_TECHNICAL_CANARY_ONLY",
        "issued_at": instant.isoformat(),
        "valid_until": valid_until.isoformat(),
        "broker_reads": 15,
        "qualification_attestation_hash": qualification_result["attestation_hash"],
        "qualification_attestation_package_hash": qualification_result["preview_package_hash"],
        "qualification_attestation_valid_until": qualification_result["valid_until"],
        "account_reference": qualification_result["account_reference"],
        "credential_reference": qualification_result["credential_reference"],
        "portfolio_version": qualification_result["portfolio_version"],
        "portfolio_snapshot_id": qualification_result["portfolio_snapshot_id"],
        "portfolio_equity": qualification_result["portfolio_equity"],
        "portfolio_gross_exposure": "0",
        "portfolio_net_exposure": "0",
        "kill_switch_active": True,
        "kill_switch_reason": qualification_result["kill_switch_reason"],
        "kill_switch_reset": False,
        "strategy_health_state_rows": 0,
        "portfolio_health_state_rows": 0,
        "health_bridge_rows": 0,
        "health_missing_expected": True,
        "health_override_authorized": False,
        "normal_health_path_modified": False,
        "binding_package_hash": entry["package_hash"],
        "binding_payload_hash": entry["payload_hash"],
        "binding_client_order_id": entry["dry_run_client_order_id"],
        "binding_notional": entry["notional"],
        "binding_safety_hard_cap": entry["safety_hard_cap"],
        "binding_payload": entry["payload"],
        "operator_attempt_id": context.attempt_id,
        "operator_preparation_hash": context.preparation_hash,
        "operator_context": context.to_dict(),
        "operator_challenge": challenge,
        "operator_decision_recorded": False,
        "operator_decision_consumed": False,
        "protection_required_after_reconciled_fill": True,
        "ambiguity_policy": "UNKNOWN_BEFORE_IO_RECONCILE_ONLY_NO_BLIND_RETRY",
        "qualification_and_binding_packages_are_distinct": (
            qualification_result["preview_package_hash"] != entry["package_hash"]
        ),
        "cold_start_binding_candidate": True,
        "cold_start_binding_sealed": False,
        "normal_final_guard_opened": False,
        "cold_start_final_guard_opened": False,
        "oms_submitting": False,
        "lifecycle_unknown": False,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "execution_authority": "NONE",
        "capital_authority": "NONE",
        "reusable_for_real_execution": False,
        "profitability_evidence": False,
        "live_trading": "BLOCKED",
    }
    material["binding_preparation_hash"] = _hash_payload(material, hash_key="binding_preparation_hash")
    path = _persist(
        root,
        prefix="prepared",
        hash_value=str(material["binding_preparation_hash"]),
        document=material,
    )
    return {
        "status": "CRYPTO_COLD_START_FINAL_GUARD_BINDING_PREPARED_NO_EXECUTION",
        "mode": "PAPER_READ_LOCAL_BINDING_NO_POST",
        "workspace": str(root),
        "preparation_path": str(path),
        **material,
        "next_action": "TYPE_EXACT_CHALLENGE_TO_SEAL_UAT_BINDING_WITH_CANONICAL_ISSUER",
    }


def seal_binding(
    *,
    workspace_path: Path,
    preparation: dict[str, object],
    approval_receipt: dict[str, object],
    now: datetime,
) -> dict[str, object]:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise CryptoColdStartFinalGuardBindingError(
            "cold-start Final Guard binding seal refuses R6_EXTERNAL_PAPER_WRITE=ENABLED"
        )
    instant = _aware(now)
    root = _root(workspace_path)
    supplied_hash = preparation.get("binding_preparation_hash")
    if not isinstance(supplied_hash, str) or supplied_hash != _hash_payload(
        preparation, hash_key="binding_preparation_hash"
    ):
        raise CryptoColdStartFinalGuardBindingError("binding preparation hash is invalid or tampered")
    if preparation.get("binding_type") != "R6_CRYPTO_PAPER_COLD_START_FINAL_GUARD_BINDING_UAT":
        raise CryptoColdStartFinalGuardBindingError("binding preparation type mismatch")
    if preparation.get("cold_start_binding_candidate") is not True:
        raise CryptoColdStartFinalGuardBindingError("binding preparation is not a cold-start candidate")
    valid_until_raw = preparation.get("valid_until")
    if not isinstance(valid_until_raw, str):
        raise CryptoColdStartFinalGuardBindingError("binding preparation expiry is missing")
    valid_until = _aware(datetime.fromisoformat(valid_until_raw))
    if instant >= valid_until:
        raise CryptoColdStartFinalGuardBindingError("binding preparation expired before sealing")
    expected_receipt = {
        "status": "CRYPTO_PAPER_ONE_SHOT_APPROVAL_RECORDED_UAT",
        "decision_status": "ISSUED",
        "decision_consumed": False,
        "uat_only": True,
        "reusable_for_real_execution": False,
        "execution_authority": "NONE",
        "broker_write_performed": False,
        "external_post_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    for key, value in expected_receipt.items():
        if approval_receipt.get(key) != value:
            raise CryptoColdStartFinalGuardBindingError(f"approval receipt contract mismatch: {key}")
    if approval_receipt.get("attempt_id") != preparation.get("operator_attempt_id"):
        raise CryptoColdStartFinalGuardBindingError("approval receipt attempt does not match binding")
    if approval_receipt.get("preparation_hash") != preparation.get("operator_preparation_hash"):
        raise CryptoColdStartFinalGuardBindingError("approval receipt preparation hash does not match binding")
    receipt_expires_raw = approval_receipt.get("expires_at")
    if not isinstance(receipt_expires_raw, str):
        raise CryptoColdStartFinalGuardBindingError("approval receipt expiry is missing")
    receipt_expires = _aware(datetime.fromisoformat(receipt_expires_raw))
    if instant >= receipt_expires:
        raise CryptoColdStartFinalGuardBindingError("human approval expired before binding seal")
    sealed_valid_until = min(valid_until, receipt_expires)
    material: dict[str, object] = {
        "schema_version": 1,
        "binding_receipt_type": "R6_CRYPTO_PAPER_COLD_START_FINAL_GUARD_BINDING_SEALED_UAT",
        "environment": "PAPER",
        "symbol": EXPECTED_SYMBOL,
        "scope": "FIRST_TECHNICAL_CANARY_ONLY",
        "sealed_at": instant.isoformat(),
        "valid_until": sealed_valid_until.isoformat(),
        "binding_preparation_hash": supplied_hash,
        "qualification_attestation_hash": preparation["qualification_attestation_hash"],
        "binding_package_hash": preparation["binding_package_hash"],
        "binding_payload_hash": preparation["binding_payload_hash"],
        "binding_client_order_id": preparation["binding_client_order_id"],
        "binding_notional": preparation["binding_notional"],
        "binding_safety_hard_cap": preparation["binding_safety_hard_cap"],
        "account_reference": preparation["account_reference"],
        "credential_reference": preparation["credential_reference"],
        "portfolio_version": preparation["portfolio_version"],
        "portfolio_snapshot_id": preparation["portfolio_snapshot_id"],
        "operator_id": approval_receipt["operator_id"],
        "operator_attempt_id": approval_receipt["attempt_id"],
        "operator_decision_hash": approval_receipt["decision_hash"],
        "operator_event_hash": approval_receipt["event_hash"],
        "operator_decision_status": "ISSUED",
        "operator_decision_consumed": False,
        "health_missing_expected": True,
        "health_override_authorized": False,
        "kill_switch_active": True,
        "kill_switch_reset": False,
        "protection_required_after_reconciled_fill": True,
        "ambiguity_policy": "UNKNOWN_BEFORE_IO_RECONCILE_ONLY_NO_BLIND_RETRY",
        "cold_start_final_guard_binding": True,
        "normal_final_guard_opened": False,
        "cold_start_final_guard_opened": False,
        "final_guard_pre_consume_authorized": False,
        "oms_submitting": False,
        "lifecycle_unknown": False,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "execution_authority": "NONE",
        "capital_authority": "NONE",
        "reusable_for_real_execution": False,
        "new_execution_approval_required": True,
        "profitability_evidence": False,
        "live_trading": "BLOCKED",
    }
    material["binding_receipt_hash"] = _hash_payload(material, hash_key="binding_receipt_hash")
    path = _persist(
        root,
        prefix="sealed",
        hash_value=str(material["binding_receipt_hash"]),
        document=material,
    )
    return {
        "status": "CRYPTO_COLD_START_FINAL_GUARD_BINDING_SEALED_UAT_NO_EXECUTION",
        "mode": "LOCAL_BINDING_RECEIPT_NO_POST",
        "workspace": str(root),
        "receipt_path": str(path),
        **material,
        "next_action": "BUILD_SEPARATE_EXECUTION_GATE_REGENERATING_EVERYTHING_AND_REQUIRING_NEW_EXPLICIT_APPROVAL",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a non-executable cold-start Final Guard binding candidate. Regenerates fresh cold-start "
            "qualification evidence and a fresh one-shot approval package, but never consumes approval, opens "
            "Final Guard, changes OMS/lifecycle, resets the kill switch or performs broker POST."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--allow-paper-crypto-read", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_crypto_read:
        raise SystemExit("cold-start Final Guard binding requires explicit --allow-paper-crypto-read")
    try:
        result = prepare_binding(
            workspace_path=args.workspace,
            credentials=_credentials(),
            now=datetime.now(timezone.utc),
        )
    except Exception as exc:
        blocked = {
            "status": "CRYPTO_COLD_START_FINAL_GUARD_BINDING_BLOCKED",
            "mode": "PAPER_READ_LOCAL_BINDING_NO_POST",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "broker_write_performed": False,
            "external_post_authorized": False,
            "execution_authority": "NONE",
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        print(json.dumps(blocked, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
