from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrade.research.market import InstrumentMetadata
from autotrade.research.streaming import (
    ClosedKlineStream,
    ClosedKlineSubscription,
    StreamOpenRequest,
    StreamState,
    StreamUnavailable,
)


class NoIoTransport:
    def __init__(self) -> None:
        self.open_calls = 0

    def open(self, request: StreamOpenRequest):
        self.open_calls += 1
        raise AssertionError("transport must not be opened")


def test_direct_ingest_cannot_bypass_disabled_stream() -> None:
    instrument = InstrumentMetadata(
        symbol="BTCUSDT",
        venue="BINANCE_SPOT",
        quote_currency="USDT",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.00001"),
    )
    transport = NoIoTransport()
    stream = ClosedKlineStream(
        subscription=ClosedKlineSubscription(instrument=instrument, interval="1m"),
        transport=transport,
    )

    with pytest.raises(StreamUnavailable, match="ACTIVE"):
        stream.ingest("{}", received_at=datetime(2026, 8, 11, 6, 1, tzinfo=timezone.utc))

    assert stream.state == StreamState.DISABLED
    assert stream.last_bar is None
    assert transport.open_calls == 0
