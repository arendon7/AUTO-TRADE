from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy

from autotrade.first_canary_prepared_evidence import FirstCanaryPreparedEvidence
from autotrade.brokers.alpaca_paper_crypto_first_canary_attempt import (
    ATTEMPT_ID_RE,
    FirstCanaryAttemptWorkspace,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials


ROOT = Path(__file__).resolve().parents[1]
BASE_PREPARE = ROOT / "scripts/mac_crypto_first_canary_prepare.py"
WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
EVIDENCE_FILENAME = "prepared_evidence.json"
HUMAN_STAGING_TTL_MS = 120_000


class RestartSafePreparationError(RuntimeError):
    pass


def _credentials() -> AlpacaPaperCredentials:
    key = os.environ.get(KEY_ENV, "")
    secret = os.environ.get(SECRET_ENV, "")
    if not key or not secret:
        raise RestartSafePreparationError(
            "PAPER Key + Secret are required for restart-safe first-canary preparation"
        )
    return AlpacaPaperCredentials(key_id=key, secret_key=secret)


def prepare_restart_safe(
    *,
    workspace_path: Path,
    attempt_id: str,
    credentials: AlpacaPaperCredentials,
    now: datetime,
    prepare_callable=None,
) -> dict[str, object]:
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise RestartSafePreparationError(
            "restart-safe preparation refuses broker-write enabled environment"
        )
    if not ATTEMPT_ID_RE.fullmatch(attempt_id):
        raise RestartSafePreparationError("execution attempt_id is invalid")
    if not isinstance(credentials, AlpacaPaperCredentials):
        raise TypeError("ephemeral PAPER credentials are required")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    instant = now.astimezone(timezone.utc)

    raw = workspace_path.expanduser()
    if raw.is_symlink() or not raw.is_dir():
        raise RestartSafePreparationError(
            "existing non-symlink PAPER workspace is required"
        )
    workspace = raw.resolve()

    if prepare_callable is None:
        namespace = runpy.run_path(str(BASE_PREPARE))
        prepare_callable = namespace["prepare_first_canary"]
        prepare_callable.__globals__["DECISION_TTL_MS"] = HUMAN_STAGING_TTL_MS
        if prepare_callable.__globals__.get("DECISION_TTL_MS") != HUMAN_STAGING_TTL_MS:
            raise RestartSafePreparationError("first-canary human staging TTL override failed closed")
    session = prepare_callable(
        workspace_path=workspace,
        attempt_id=attempt_id,
        credentials=credentials,
        now=instant,
    )

    if session.attempt.attempt_id != attempt_id:
        raise RestartSafePreparationError("prepared session attempt identity drifted")
    if session.attempt.workspace_root != workspace:
        raise RestartSafePreparationError("prepared session workspace drifted")
    if session.credentials.credential_reference != credentials.credential_reference:
        raise RestartSafePreparationError(
            "prepared session credential reference differs from effective PAPER key"
        )

    evidence = FirstCanaryPreparedEvidence(
        account=session.account,
        asset=session.asset,
        product_profile=session.product_profile,
        market=session.market_attestation,
        risk_decision=session.risk_decision,
    )
    evidence_document = evidence.document()
    preparation = session.preparation_document
    package = session.preparation.package

    bindings = (
        (preparation.get("prepared_account_fingerprint"), evidence.account.fingerprint, "account"),
        (preparation.get("prepared_asset_fingerprint"), evidence.asset.fingerprint, "asset"),
        (
            preparation.get("prepared_product_profile_fingerprint"),
            evidence.product_profile.fingerprint,
            "ProductCapabilities",
        ),
        (
            preparation.get("prepared_market_attestation_fingerprint"),
            evidence.market.fingerprint,
            "market attestation",
        ),
        (package.account_attestation_fingerprint, evidence.account.fingerprint, "package account"),
        (package.asset_attestation_fingerprint, evidence.asset.fingerprint, "package asset"),
        (
            package.product_profile_fingerprint,
            evidence.product_profile.fingerprint,
            "package ProductCapabilities",
        ),
        (
            package.market_attestation_fingerprint,
            evidence.market.fingerprint,
            "package market",
        ),
    )
    for supplied, expected, label in bindings:
        if supplied != expected:
            raise RestartSafePreparationError(
                f"restart-safe prepared evidence {label} binding mismatch"
            )
    if evidence.risk_decision.decision_id != package.risk_decision_id:
        raise RestartSafePreparationError(
            "restart-safe prepared RiskDecision id differs from package"
        )
    if evidence.risk_decision.valid_until != package.risk_decision_valid_until:
        raise RestartSafePreparationError(
            "restart-safe prepared RiskDecision expiry differs from package"
        )

    document: dict[str, object] = {
        "schema_version": 1,
        "document_type": "R6_CRYPTO_PAPER_FIRST_CANARY_RESTART_SAFE_PREPARATION",
        "attempt_id": attempt_id,
        "package_hash": package.package_hash,
        "client_order_id": package.client_order_id,
        "preparation_hash": preparation.get("preparation_hash"),
        "authority_state_fingerprint": session.authority_state_fingerprint,
        "credential_reference": credentials.credential_reference,
        "prepared_evidence": evidence_document,
        "prepared_evidence_hash": evidence_document["prepared_evidence_hash"],
        "created_at": instant.isoformat(),
        "credentials_persisted": False,
        "secret_persisted": False,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "live_trading": "BLOCKED",
    }
    document["restart_safe_hash"] = sha256(
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    attempt = FirstCanaryAttemptWorkspace.open(
        workspace_path=workspace,
        attempt_id=attempt_id,
    )
    attempt.write_once(
        path=attempt.attempt_root / EVIDENCE_FILENAME,
        document=document,
    )
    verified = attempt.read(path=attempt.attempt_root / EVIDENCE_FILENAME)
    if verified != document:
        raise RestartSafePreparationError(
            "restart-safe prepared evidence did not round-trip from durable storage"
        )

    return {
        "status": "CRYPTO_PAPER_FIRST_CANARY_RESTART_SAFE_PREPARED_NO_POST",
        "attempt_id": attempt_id,
        "package_hash": package.package_hash,
        "client_order_id": package.client_order_id,
        "prepared_evidence_hash": evidence_document["prepared_evidence_hash"],
        "restart_safe_hash": document["restart_safe_hash"],
        "prepared_evidence_path": (
            f"first_canary_execution/{attempt_id}/{EVIDENCE_FILENAME}"
        ),
        "preparation": preparation,
        "credentials_persisted": False,
        "secret_persisted": False,
        "broker_write_performed": False,
        "external_post_authorized": False,
        "live_trading": "BLOCKED",
        "next_action": "NEW_HUMAN_APPROVAL_FOR_EXACT_RESTART_SAFE_PACKAGE",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare one fresh BTC/USD PAPER first-canary attempt and persist sanitized typed evidence "
            "for restart-safe later execution. No approval is minted and no POST is possible."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--allow-paper-crypto-read", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_crypto_read:
        raise SystemExit(
            "restart-safe first-canary preparation requires explicit --allow-paper-crypto-read"
        )
    try:
        result = prepare_restart_safe(
            workspace_path=args.workspace,
            attempt_id=str(args.attempt_id),
            credentials=_credentials(),
            now=datetime.now(timezone.utc),
        )
    except Exception as exc:
        result = {
            "status": "CRYPTO_PAPER_FIRST_CANARY_RESTART_SAFE_PREPARATION_BLOCKED",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "credentials_persisted": False,
            "secret_persisted": False,
            "broker_write_performed": False,
            "external_post_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())