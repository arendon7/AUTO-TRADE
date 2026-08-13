from __future__ import annotations

import argparse
import json
import os

from autotrade.brokers.alpaca_paper_crypto_catalog import AlpacaPaperCryptoCatalogGateway
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials, AlpacaPaperGatewayConfig


WRITE_ENV = "R6_EXTERNAL_PAPER_WRITE"
KEY_ENV = "APCA_API_KEY_ID"
SECRET_ENV = "APCA_API_SECRET_KEY"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List eligible Alpaca PAPER crypto pairs. GET-only; no order surface.")
    parser.add_argument("--allow-paper-crypto-catalog-read", action="store_true")
    return parser


def _credentials() -> AlpacaPaperCredentials:
    key = os.environ.get(KEY_ENV, "")
    secret = os.environ.get(SECRET_ENV, "")
    if not key or not secret:
        raise SystemExit("PAPER Key + Secret are required for crypto catalog")
    return AlpacaPaperCredentials(key_id=key, secret_key=secret)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_paper_crypto_catalog_read:
        raise SystemExit("crypto catalog requires explicit --allow-paper-crypto-catalog-read")
    if os.environ.get(WRITE_ENV) == "ENABLED":
        raise SystemExit("crypto catalog refuses R6_EXTERNAL_PAPER_WRITE=ENABLED")
    items = AlpacaPaperCryptoCatalogGateway(
        config=AlpacaPaperGatewayConfig(enabled=True)
    ).list_pairs(credentials=_credentials())
    print(
        json.dumps(
            {
                "status": "CRYPTO_PAPER_CATALOG_PASS",
                "environment": "PAPER",
                "pairs": [item.to_dict() for item in items],
                "pair_count": len(items),
                "broker_reads": 1,
                "broker_write_performed": False,
                "external_post_authorized": False,
                "capital_authority": "NONE",
                "live_trading": "BLOCKED",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
