from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from autotrade.brokers.alpaca_paper_asset import AlpacaPaperEquityAssetGateway
from autotrade.brokers.alpaca_paper_asset_evidence import PaperAssetEvidenceStore
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalWorkspace,
    _read_json_object,
)


_KEY_ENV = "APCA_API_KEY_ID"
_SECRET_ENV = "APCA_API_SECRET_KEY"
_WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Explicit GET-only Alpaca PAPER asset/venue preflight for one R6 workspace. "
            "It validates exact us_equity/tradable/whole-share venue constraints and exposes no order API."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--symbol", required=True)
    parser.add_argument(
        "--allow-paper-asset-read",
        action="store_true",
        help="Required explicit opt-in to the single PAPER /v2/assets/{symbol} GET.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_asset_read:
        raise SystemExit(
            "Refusing network access: pass --allow-paper-asset-read for the explicit GET-only asset preflight."
        )
    if os.environ.get(_WRITE_ENV) == "ENABLED":
        raise SystemExit(
            "Refusing asset preflight while R6_EXTERNAL_PAPER_WRITE=ENABLED; disable the write gate first."
        )

    root = args.workspace.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit("Workspace does not exist; create it with Mac Safe Start first.")
    workspace = PaperOperationalWorkspace(root=root)
    if not workspace.account_attestation_path.is_file():
        raise SystemExit("Asset preflight requires the GET-only PAPER account preflight first.")
    if (root / "flat_account_attestation.json").exists() or (root / "market_snapshot.json").exists():
        raise SystemExit(
            "Asset preflight must precede flat-account and market preflights; use a fresh workspace."
        )
    if workspace.prepared_package_path.exists():
        raise SystemExit("Asset preflight cannot modify an already prepared canary workspace.")

    account = _read_json_object(workspace.account_attestation_path)
    if account.get("environment") != "PAPER" or account.get("credentials_persisted") is not False:
        raise SystemExit("Persisted account evidence is not a safe PAPER attestation.")
    account_fingerprint = account.get("attestation_fingerprint")
    expected_credential_reference = account.get("credential_reference")
    if not isinstance(account_fingerprint, str) or len(account_fingerprint) != 64:
        raise SystemExit("Persisted account attestation fingerprint is invalid.")
    if not isinstance(expected_credential_reference, str) or len(expected_credential_reference) != 64:
        raise SystemExit("Persisted PAPER credential reference is invalid.")

    key_id = os.environ.get(_KEY_ENV)
    secret_key = os.environ.get(_SECRET_ENV)
    if not key_id or not secret_key:
        raise SystemExit(
            f"Missing Alpaca PAPER credentials in {_KEY_ENV}/{_SECRET_ENV}; credentials are never accepted as CLI arguments."
        )
    credentials = AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)
    now = datetime.now(timezone.utc)
    gateway = AlpacaPaperEquityAssetGateway(
        config=AlpacaPaperGatewayConfig(enabled=True)
    )
    attestation = gateway.attest_asset(
        credentials=credentials,
        symbol=args.symbol,
        account_attestation_fingerprint=account_fingerprint,
        expected_credential_reference=expected_credential_reference,
        now=now,
    )
    path = PaperAssetEvidenceStore(workspace).write(attestation)
    result = {
        "status": "PAPER_ASSET_PREFLIGHT_COMPLETE",
        "artifact": str(path),
        "symbol": attestation.symbol,
        "asset_class": attestation.asset_class,
        "exchange": attestation.exchange,
        "asset_status": attestation.status,
        "tradable": attestation.tradable,
        "fractionable": attestation.fractionable,
        "min_order_size": str(attestation.min_order_size),
        "min_trade_increment": str(attestation.min_trade_increment),
        "price_increment": str(attestation.price_increment),
        "constraint_source": attestation.constraint_source,
        "attributes": list(attestation.attributes),
        "whole_share_canary_supported": True,
        "attestation_fingerprint": attestation.fingerprint,
        "network_method": "GET",
        "network_host": attestation.source_host,
        "network_path": attestation.source_path,
        "credentials_persisted": False,
        "broker_write_authorized": False,
        "external_order_submitted": False,
        "capital_authority": "NONE",
        "profitability_claim": False,
        "live_trading": "BLOCKED",
        "next_action": "RUN_SEPARATE_GET_ONLY_FLAT_ACCOUNT_PREFLIGHT",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
