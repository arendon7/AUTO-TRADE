from datetime import datetime, timedelta, timezone
from decimal import Decimal
import socket

import pytest

from autotrade.research.external_data import (
    BINANCE_KLINES_PATH,
    BINANCE_PUBLIC_DATA_HOST,
    BinanceKlineRange,
    BinanceSpotHistoricalProvider,
    ExternalDataIntegrityError,
    ExternalDataPolicyError,
    ExternalDataUnavailable,
    HttpResponse,
    PublicDataPolicy,
    ReadOnlyRequest,
    UrllibReadOnlyTransport,
    _PolicyRedirectHandler,
)
from autotrade.research.market import InstrumentMetadata


def instrument():
    return InstrumentMetadata(
        symbol="BTCUSDT",
        venue="BINANCE_SPOT",
        quote_currency="USDT",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.00001"),
    )


def policy():
    return PublicDataPolicy(
        allowed_host=BINANCE_PUBLIC_DATA_HOST,
        allowed_paths=frozenset({BINANCE_KLINES_PATH}),
    )


def test_cross_host_redirect_is_rejected_before_redirect_request_is_created():
    handler = _PolicyRedirectHandler(policy())
    with pytest.raises(ExternalDataPolicyError, match="host"):
        handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {},
            "https://evil.example/api/v3/klines",
        )
    with pytest.raises(ExternalDataPolicyError, match="path"):
        handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {},
            f"https://{BINANCE_PUBLIC_DATA_HOST}/api/v3/order",
        )


def test_submillisecond_range_boundary_is_not_silently_truncated():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc).replace(microsecond=500)
    with pytest.raises(ValueError, match="millisecond precision"):
        BinanceKlineRange(
            instrument=instrument(),
            interval="1m",
            start=start,
            end=start + timedelta(minutes=1),
        )


def test_provider_requires_json_content_type():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    request_range = BinanceKlineRange(
        instrument=instrument(),
        interval="1m",
        start=start,
        end=start + timedelta(minutes=1),
    )

    class Transport:
        def send(self, request):
            return HttpResponse(
                status_code=200,
                body=b"[]",
                final_url=request.url,
                headers={"content-type": "text/html"},
            )

    with pytest.raises(ExternalDataIntegrityError, match="content-type"):
        BinanceSpotHistoricalProvider(transport=Transport(), enabled=True).fetch(
            request_range
        )


def test_urllib_transport_normalizes_socket_timeout_to_unavailable():
    transport = UrllibReadOnlyTransport(policy=policy())

    class BrokenOpener:
        def open(self, *args, **kwargs):
            raise socket.timeout("timed out")

    transport._opener = BrokenOpener()
    req = ReadOnlyRequest(
        method="GET",
        url=f"https://{BINANCE_PUBLIC_DATA_HOST}{BINANCE_KLINES_PATH}?symbol=BTCUSDT",
        timeout_seconds=1,
    )
    with pytest.raises(ExternalDataUnavailable, match="timed out"):
        transport.send(req)
