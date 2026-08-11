from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest

from autotrade.research.external_data import (
    BINANCE_KLINES_PATH,
    BINANCE_PUBLIC_DATA_HOST,
    BinanceKlineRange,
    BinanceSpotHistoricalProvider,
    ExternalDataDisabled,
    ExternalDataIntegrityError,
    ExternalDataPolicyError,
    ExternalDataUnavailable,
    HttpResponse,
    PublicDataPolicy,
    ReadOnlyRequest,
)
from autotrade.research.market import InstrumentMetadata


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def send(self, request):
        self.calls.append(request)
        return self.handler(request)


def instrument():
    return InstrumentMetadata(
        symbol="BTCUSDT",
        venue="BINANCE_SPOT",
        asset_class="CRYPTO",
        quote_currency="USDT",
        price_increment=Decimal("0.01"),
        quantity_increment=Decimal("0.00001"),
    )


def aligned_start():
    return datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def row(open_ms: int, interval_ms: int, *, close="100", volume="10"):
    return [
        open_ms,
        "100",
        "102",
        "99",
        close,
        volume,
        open_ms + interval_ms - 1,
        "0",
        1,
        "0",
        "0",
        "0",
    ]


def response_for(request, rows, *, status=200, final_url=None):
    import json

    return HttpResponse(
        status_code=status,
        body=json.dumps(rows, separators=(",", ":")).encode(),
        final_url=final_url or request.url,
        headers={"content-type": "application/json"},
    )


def provider_for_rows(rows, *, enabled=True, final_url=None, status=200, max_total_bars=5000):
    transport = FakeTransport(
        lambda request: response_for(
            request,
            rows(request) if callable(rows) else rows,
            status=status,
            final_url=final_url,
        )
    )
    provider = BinanceSpotHistoricalProvider(
        transport=transport,
        enabled=enabled,
        timeout_seconds=2,
        max_total_bars=max_total_bars,
    )
    return provider, transport


def request(minutes=3):
    start = aligned_start()
    return BinanceKlineRange(
        instrument=instrument(),
        interval="1m",
        start=start,
        end=start + timedelta(minutes=minutes),
    )


def valid_rows(minutes=3):
    start_ms = int(aligned_start().timestamp() * 1000)
    return [
        row(start_ms + i * 60_000, 60_000, close=str(100 + i))
        for i in range(minutes)
    ]


def test_external_provider_is_disabled_by_default_before_transport_call():
    provider, transport = provider_for_rows(valid_rows(), enabled=False)
    with pytest.raises(ExternalDataDisabled):
        provider.fetch(request())
    assert transport.calls == []


def test_public_policy_rejects_non_get_wrong_scheme_host_path_and_credentials():
    policy = PublicDataPolicy(
        allowed_host=BINANCE_PUBLIC_DATA_HOST,
        allowed_paths=frozenset({BINANCE_KLINES_PATH}),
    )
    good = f"https://{BINANCE_PUBLIC_DATA_HOST}{BINANCE_KLINES_PATH}?symbol=BTCUSDT"
    policy.validate(ReadOnlyRequest("GET", good, 1))
    bad = [
        ReadOnlyRequest("POST", good, 1),
        ReadOnlyRequest("GET", good.replace("https://", "http://"), 1),
        ReadOnlyRequest("GET", good.replace(BINANCE_PUBLIC_DATA_HOST, "api.binance.com"), 1),
        ReadOnlyRequest("GET", f"https://{BINANCE_PUBLIC_DATA_HOST}/api/v3/order", 1),
        ReadOnlyRequest("GET", f"https://user:pass@{BINANCE_PUBLIC_DATA_HOST}{BINANCE_KLINES_PATH}", 1),
        ReadOnlyRequest("GET", good, 0),
        ReadOnlyRequest("GET", good, 31),
    ]
    for req in bad:
        with pytest.raises(ExternalDataPolicyError):
            policy.validate(req)


def test_valid_fetch_builds_exact_canonical_dataset_and_manifest():
    provider, transport = provider_for_rows(valid_rows())
    artifact = provider.fetch(request())
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call.method == "GET"
    parsed = urlsplit(call.url)
    assert parsed.hostname == BINANCE_PUBLIC_DATA_HOST
    assert parsed.path == BINANCE_KLINES_PATH
    params = parse_qs(parsed.query)
    assert params["symbol"] == ["BTCUSDT"]
    assert params["interval"] == ["1m"]
    assert params["limit"] == ["3"]
    assert artifact.manifest.expected_bars == 3
    assert artifact.manifest.received_bars == 3
    assert artifact.manifest.pages == 1
    assert len(artifact.manifest.source_payload_sha256) == 64
    assert artifact.manifest.dataset_hash == artifact.dataset.dataset_hash
    assert artifact.dataset.provenance == artifact.manifest.provenance
    assert artifact.dataset.detect_gaps() == ()
    assert [bar.close for bar in artifact.dataset.bars] == [
        Decimal("100"),
        Decimal("101"),
        Decimal("102"),
    ]


def test_fetch_paginates_bounded_range_with_no_overlap():
    start_ms = int(aligned_start().timestamp() * 1000)

    def page_rows(req):
        params = parse_qs(urlsplit(req.url).query)
        start = int(params["startTime"][0])
        limit = int(params["limit"][0])
        return [row(start + i * 60_000, 60_000) for i in range(limit)]

    provider, transport = provider_for_rows(page_rows, max_total_bars=1001)
    artifact = provider.fetch(request(minutes=1001))
    assert len(transport.calls) == 2
    assert artifact.manifest.pages == 2
    assert artifact.manifest.received_bars == 1001
    first = parse_qs(urlsplit(transport.calls[0].url).query)
    second = parse_qs(urlsplit(transport.calls[1].url).query)
    assert first["limit"] == ["1000"]
    assert second["limit"] == ["1"]
    assert int(second["startTime"][0]) == start_ms + 1000 * 60_000


def test_range_contract_rejects_unaligned_invalid_venue_symbol_interval_and_oversize():
    start = aligned_start()
    with pytest.raises(ValueError, match="align"):
        BinanceKlineRange(
            instrument(), "1m", start + timedelta(seconds=1), start + timedelta(minutes=2)
        )
    with pytest.raises(ValueError, match="venue"):
        BinanceKlineRange(
            replace(instrument(), venue="OTHER"), "1m", start, start + timedelta(minutes=1)
        )
    with pytest.raises(ValueError, match="symbol"):
        BinanceKlineRange(
            replace(instrument(), symbol="BTC/USDT"),
            "1m",
            start,
            start + timedelta(minutes=1),
        )
    with pytest.raises(ValueError, match="unsupported"):
        BinanceKlineRange(instrument(), "1M", start, start + timedelta(days=31))
    provider, transport = provider_for_rows([], max_total_bars=2)
    with pytest.raises(ExternalDataPolicyError, match="max_total_bars"):
        provider.fetch(request(minutes=3))
    assert transport.calls == []


@pytest.mark.parametrize("payload", [b"not-json", b"{}", b"[1]", b"[[1,2,3]]"])
def test_malformed_payloads_fail_closed(payload):
    transport = FakeTransport(lambda req: HttpResponse(200, payload, req.url, {}))
    provider = BinanceSpotHistoricalProvider(transport=transport, enabled=True)
    with pytest.raises(ExternalDataIntegrityError):
        provider.fetch(request(minutes=1))


def test_partial_duplicate_out_of_order_gap_and_bad_close_time_fail_closed():
    start_ms = int(aligned_start().timestamp() * 1000)
    cases = [
        [row(start_ms, 60_000), row(start_ms + 60_000, 60_000)],
        [row(start_ms, 60_000), row(start_ms, 60_000), row(start_ms + 120_000, 60_000)],
        [row(start_ms + 60_000, 60_000), row(start_ms, 60_000), row(start_ms + 120_000, 60_000)],
        [row(start_ms, 60_000), row(start_ms + 120_000, 60_000), row(start_ms + 180_000, 60_000)],
    ]
    bad_close = valid_rows()
    bad_close[1][6] += 1
    cases.append(bad_close)
    for rows in cases:
        provider, _ = provider_for_rows(rows)
        with pytest.raises(ExternalDataIntegrityError):
            provider.fetch(request())


def test_transport_status_redirect_and_exception_fail_closed():
    provider, _ = provider_for_rows(valid_rows(), status=503)
    with pytest.raises(ExternalDataUnavailable):
        provider.fetch(request())

    provider, _ = provider_for_rows(
        valid_rows(), final_url="https://evil.example/api/v3/klines"
    )
    with pytest.raises(ExternalDataPolicyError):
        provider.fetch(request())

    class Broken:
        def send(self, request):
            raise TimeoutError("boom")

    provider = BinanceSpotHistoricalProvider(transport=Broken(), enabled=True)
    with pytest.raises(ExternalDataUnavailable):
        provider.fetch(request())


def test_artifact_roundtrip_and_tamper_detection(tmp_path):
    provider, _ = provider_for_rows(valid_rows())
    artifact = provider.fetch(request())
    target = tmp_path / "dataset.json"
    artifact.write(target)
    restored = type(artifact).read(target)
    assert restored.manifest == artifact.manifest
    assert restored.dataset.dataset_hash == artifact.dataset.dataset_hash

    import json

    document = json.loads(target.read_text())
    document["rows"][0][4] = "999"
    target.write_text(json.dumps(document))
    with pytest.raises(ExternalDataIntegrityError, match="checksum"):
        type(artifact).read(target)
