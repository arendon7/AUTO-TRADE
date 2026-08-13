from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
    AlpacaPaperHttpResponse,
    AlpacaPaperReadPolicy,
    AlpacaPaperReadRequest,
    AlpacaPaperReadTransport,
    UrllibAlpacaPaperReadTransport,
)


KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"
_ACCOUNT_ID_RE = re.compile(r"^[0-9a-fA-F-]{16,64}$")
_REQUEST_ID_RE = re.compile(r"^[\x21-\x7e]{1,256}$")


class PaperAccountDiscoveryError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Identify which Alpaca PAPER account the supplied PAPER credentials resolve to. "
            "This is one GET /v2/account, creates no durable attestation and exposes no order-write API."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument(
        "--allow-paper-account-discovery-read",
        action="store_true",
        help="Explicitly permit the single non-persistent GET /v2/account discovery read.",
    )
    return parser


def _credentials_from_environment() -> AlpacaPaperCredentials:
    key_id = os.environ.get(KEY_ENV, "")
    secret_key = os.environ.get(SECRET_ENV, "")
    if not key_id or not secret_key:
        raise SystemExit(
            f"PAPER credentials must exist only in environment variables {KEY_ENV} and {SECRET_ENV}"
        )
    return AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)


def _require_workspace(workspace: Path) -> Path:
    if workspace.is_symlink():
        raise PaperAccountDiscoveryError("workspace may not be a symlink")
    if not workspace.is_dir():
        raise PaperAccountDiscoveryError("workspace must already exist")
    return workspace


def _strict_object(response: AlpacaPaperHttpResponse) -> dict[str, object]:
    if response.status_code != 200:
        raise PaperAccountDiscoveryError(
            f"unexpected PAPER account discovery status: {response.status_code}"
        )
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise PaperAccountDiscoveryError("PAPER account discovery response must be application/json")
    request_id = response.headers.get("x-request-id", "").strip()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise PaperAccountDiscoveryError("PAPER account discovery response is missing a valid X-Request-ID")
    try:
        value = json.loads(
            response.body.decode("utf-8", errors="strict"),
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PaperAccountDiscoveryError("PAPER account discovery response is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PaperAccountDiscoveryError("PAPER account discovery response root must be an object")
    return value


def _required_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PaperAccountDiscoveryError(f"PAPER account field {key} is required")
    text = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise PaperAccountDiscoveryError(f"PAPER account field {key} contains control characters")
    return text


def run_account_discovery(
    *,
    workspace: Path,
    credentials: AlpacaPaperCredentials,
    transport: AlpacaPaperReadTransport | None = None,
) -> dict[str, object]:
    _require_workspace(workspace)
    policy = AlpacaPaperReadPolicy()
    reader = transport or UrllibAlpacaPaperReadTransport(policy=policy)
    request = AlpacaPaperReadRequest(
        method="GET",
        url=f"https://{ALPACA_PAPER_TRADING_HOST}{ALPACA_PAPER_ACCOUNT_PATH}",
        timeout_seconds=5.0,
        headers={
            "Accept": "application/json",
            "User-Agent": "AUTO-TRADE-R6/0.28R",
            "APCA-API-KEY-ID": credentials.key_id,
            "APCA-API-SECRET-KEY": credentials.secret_key,
        },
    )
    policy.validate(request)
    response = reader.read(request)
    policy.validate_final_url(response.final_url)
    payload = _strict_object(response)

    account_id = _required_str(payload, "id")
    if not _ACCOUNT_ID_RE.fullmatch(account_id):
        raise PaperAccountDiscoveryError("PAPER account id is malformed")
    account_number = _required_str(payload, "account_number")
    status = _required_str(payload, "status")
    currency = _required_str(payload, "currency")

    if status != "ACTIVE":
        raise PaperAccountDiscoveryError("PAPER account is not ACTIVE")
    if currency != "USD":
        raise PaperAccountDiscoveryError("PAPER account currency must be USD")

    return {
        "status": "PAPER_ACCOUNT_DISCOVERED",
        "environment": "PAPER",
        "account_id": account_id,
        "account_number_hint": f"…{account_number[-4:]}",
        "account_status": status,
        "currency": currency,
        "network_method": "GET",
        "network_path": ALPACA_PAPER_ACCOUNT_PATH,
        "persistent_evidence_created": False,
        "account_attested": False,
        "operator_confirmation_required": True,
        "order_write_authorized": False,
        "external_order_submitted": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_account_discovery_read:
        raise SystemExit(
            "PAPER account discovery read is disabled unless "
            "--allow-paper-account-discovery-read is explicit"
        )
    result = run_account_discovery(
        workspace=args.workspace,
        credentials=_credentials_from_environment(),
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
