from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from autotrade.brokers.alpaca_paper_asset_evidence import (
    PaperAssetEvidenceError,
    PaperAssetEvidenceStore,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_market_data import (
    AlpacaPaperEquityMarketDataGateway,
    AlpacaPaperMarketDataConfig,
)
from autotrade.brokers.alpaca_paper_market_evidence import (
    PaperMarketEvidenceStore,
    market_evidence_payload,
)
from autotrade.brokers.alpaca_paper_market_readiness import (
    ASSET_PREFLIGHT_REQUIRED,
    MARKET_DATA_PREFLIGHT_REQUIRED,
    inspect_market_aware_readiness,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace


_KEY_ENV = "APCA_API_KEY_ID"
_SECRET_ENV = "APCA_API_SECRET_KEY"
_WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Explicit GET-only Alpaca IEX market-data preflight for one R6 PAPER workspace. "
            "Requires account-bound asset evidence plus clean first-canary account evidence and exposes no order API."
        )
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--symbol", required=True)
    parser.add_argument(
        "--allow-paper-market-read",
        action="store_true",
        help="Required explicit opt-in to the single IEX snapshot GET.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_market_read:
        raise SystemExit(
            "Refusing network access: pass --allow-paper-market-read for the explicit GET-only IEX snapshot."
        )
    if os.environ.get(_WRITE_ENV) == "ENABLED":
        raise SystemExit(
            "Refusing market preflight while R6_EXTERNAL_PAPER_WRITE=ENABLED; disable the write gate first."
        )

    workspace_root = args.workspace.expanduser().resolve()
    if not workspace_root.is_dir():
        raise SystemExit("Workspace does not exist; create it with Mac Safe Start first.")
    now = datetime.now(timezone.utc)
    readiness = inspect_market_aware_readiness(root=workspace_root, now=now)
    phase = readiness.get("phase")
    if phase == ASSET_PREFLIGHT_REQUIRED:
        raise SystemExit(
            "Market preflight requires valid GET-only PAPER asset evidence first."
        )
    if phase != MARKET_DATA_PREFLIGHT_REQUIRED:
        raise SystemExit(
            "Market preflight is not the allowed next step for this workspace; run readiness and satisfy earlier gates first."
        )

    key_id = os.environ.get(_KEY_ENV)
    secret_key = os.environ.get(_SECRET_ENV)
    if not key_id or not secret_key:
        raise SystemExit(
            f"Missing Alpaca PAPER credentials in {_KEY_ENV}/{_SECRET_ENV}; credentials are never accepted as CLI arguments."
        )

    credentials = AlpacaPaperCredentials(key_id=key_id, secret_key=secret_key)
    workspace = PaperOperationalWorkspace(root=workspace_root)
    try:
        asset = PaperAssetEvidenceStore(workspace).read()
    except PaperAssetEvidenceError as exc:
        raise SystemExit(
            "Market preflight requires valid GET-only PAPER asset evidence first."
        ) from exc
    symbol = args.symbol.strip().upper()
    if asset.symbol != symbol:
        raise SystemExit(
            f"Market symbol {symbol} does not match attested PAPER asset {asset.symbol}."
        )

    gateway = AlpacaPaperEquityMarketDataGateway(
        AlpacaPaperMarketDataConfig(enabled=True)
    )
    attestation = gateway.attest_snapshot(
        credentials=credentials,
        symbol=symbol,
        now=now,
    )
    path = PaperMarketEvidenceStore(workspace).write(
        attestation=attestation,
        credentials=credentials,
    )
    payload = market_evidence_payload(attestation)
    result = {
        "status": "PAPER_MARKET_PREFLIGHT_COMPLETE",
        "artifact": str(path),
        "symbol": attestation.market.symbol,
        "asset_attestation_fingerprint": asset.fingerprint,
        "feed": attestation.feed,
        "currency": attestation.currency,
        "market_fingerprint": payload["market_fingerprint"],
        "attestation_fingerprint": attestation.fingerprint,
        "network_method": "GET",
        "network_host": attestation.source_host,
        "network_path": payload["source_path"],
        "credentials_persisted": False,
        "broker_write_authorized": False,
        "external_order_submitted": False,
        "capital_authority": "NONE",
        "profitability_claim": False,
        "live_trading": "BLOCKED",
        "next_action": "BUILD_AUTHORITATIVE_R6_CANDIDATE_OFFLINE",
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
