from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path

from autotrade.research.external_data import (
    BINANCE_KLINES_PATH,
    BINANCE_PUBLIC_DATA_HOST,
    BinanceKlineRange,
    BinanceSpotHistoricalProvider,
    ExternalDatasetArtifact,
    PublicDataPolicy,
    UrllibReadOnlyTransport,
)
from autotrade.research.market import InstrumentMetadata


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "knowledge" / "60_EVIDENCE" / "R3_REAL_DATA_BTCUSDT_1M_20260101.json"
EVIDENCE = ROOT / "knowledge" / "60_EVIDENCE" / "R3_REAL_DATA_CAMPAIGN.json"


def main() -> int:
    if os.environ.get("AUTOTRADE_ENABLE_R3_REAL_DATA") != "1":
        raise SystemExit(
            "real-data campaign is disabled; set AUTOTRADE_ENABLE_R3_REAL_DATA=1 explicitly"
        )

    policy = PublicDataPolicy(
        allowed_host=BINANCE_PUBLIC_DATA_HOST,
        allowed_paths=frozenset({BINANCE_KLINES_PATH}),
    )
    transport = UrllibReadOnlyTransport(
        policy=policy,
        max_response_bytes=1_000_000,
    )
    provider = BinanceSpotHistoricalProvider(
        transport=transport,
        enabled=True,
        timeout_seconds=10,
        max_total_bars=10,
    )
    instrument = InstrumentMetadata(
        symbol="BTCUSDT",
        venue="BINANCE_SPOT",
        asset_class="CRYPTO",
        quote_currency="USDT",
        # Research serialization resolution only; this R3 campaign does not
        # certify exchange trading filters or execution precision.
        price_increment=Decimal("0.00000001"),
        quantity_increment=Decimal("0.00000001"),
    )
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    request = BinanceKlineRange(
        instrument=instrument,
        interval="1m",
        start=start,
        end=start + timedelta(minutes=10),
    )

    first = provider.fetch(request)
    second = provider.fetch(request)
    if first.manifest.source_payload_sha256 != second.manifest.source_payload_sha256:
        raise SystemExit("real-data source checksum changed between independent fetches")
    if first.dataset.dataset_hash != second.dataset.dataset_hash:
        raise SystemExit("real-data canonical dataset hash changed between independent fetches")
    if first.manifest.fingerprint != second.manifest.fingerprint:
        raise SystemExit("real-data manifest changed between independent fetches")

    first.write(OUTPUT)
    restored = ExternalDatasetArtifact.read(OUTPUT)
    if restored.manifest != first.manifest:
        raise SystemExit("written real-data artifact failed manifest roundtrip")

    evidence = {
        "track": "R3",
        "campaign_id": "r3-binance-btcusdt-1m-20260101-10bars",
        "purpose": "validate bounded read-only intake/reproducibility; not profitability evidence",
        "network_opt_in": True,
        "provider_id": first.manifest.provider_id,
        "provider_version": first.manifest.provider_version,
        "endpoint": first.manifest.endpoint,
        "symbol": first.manifest.symbol,
        "interval": first.manifest.interval,
        "start": first.manifest.start,
        "end": first.manifest.end,
        "bars": first.manifest.received_bars,
        "pages": first.manifest.pages,
        "source_payload_sha256": first.manifest.source_payload_sha256,
        "dataset_hash": first.manifest.dataset_hash,
        "manifest_fingerprint": first.manifest.fingerprint,
        "independent_fetches_equal": True,
        "artifact_roundtrip_verified": True,
        "api_keys_used": False,
        "trading_endpoints_used": False,
        "execution_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    EVIDENCE.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
