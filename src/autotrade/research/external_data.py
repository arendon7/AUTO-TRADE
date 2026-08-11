from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping, Protocol
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError, URLError
import socket

from .market import Bar, InstrumentMetadata, MarketDataset


class ExternalDataError(RuntimeError):
    pass


class ExternalDataDisabled(ExternalDataError):
    pass


class ExternalDataPolicyError(ExternalDataError):
    pass


class ExternalDataUnavailable(ExternalDataError):
    pass


class ExternalDataIntegrityError(ExternalDataError):
    pass


@dataclass(frozen=True, slots=True)
class ReadOnlyRequest:
    method: str
    url: str
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: bytes
    final_url: str
    headers: Mapping[str, str]


class ReadOnlyHttpTransport(Protocol):
    def send(self, request: ReadOnlyRequest) -> HttpResponse: ...


@dataclass(frozen=True, slots=True)
class PublicDataPolicy:
    allowed_host: str
    allowed_paths: frozenset[str]

    def validate(self, request: ReadOnlyRequest) -> None:
        if request.method != "GET":
            raise ExternalDataPolicyError("public market-data transport is GET-only")
        if not request.timeout_seconds > 0 or request.timeout_seconds > 30:
            raise ExternalDataPolicyError("timeout must be > 0 and <= 30 seconds")
        parsed = urlsplit(request.url)
        if parsed.scheme != "https":
            raise ExternalDataPolicyError("public market-data transport requires HTTPS")
        if parsed.username or parsed.password or parsed.fragment:
            raise ExternalDataPolicyError("credentials/fragments are forbidden in public-data URLs")
        if parsed.hostname != self.allowed_host or parsed.port not in (None, 443):
            raise ExternalDataPolicyError("public market-data host is not allowlisted")
        if parsed.path not in self.allowed_paths:
            raise ExternalDataPolicyError("public market-data path is not allowlisted")

    def validate_final_url(self, url: str) -> None:
        self.validate(ReadOnlyRequest(method="GET", url=url, timeout_seconds=1.0))


class _PolicyRedirectHandler(HTTPRedirectHandler):
    def __init__(self, policy: PublicDataPolicy) -> None:
        super().__init__()
        self._policy = policy

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Validate the redirect target BEFORE urllib performs the redirected
        # network request. Cross-host/path redirects therefore fail closed.
        self._policy.validate_final_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibReadOnlyTransport:
    def __init__(self, *, policy: PublicDataPolicy, max_response_bytes: int = 10_000_000) -> None:
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be > 0")
        self._policy = policy
        self._max_response_bytes = max_response_bytes
        self._opener = build_opener(_PolicyRedirectHandler(policy))

    def send(self, request: ReadOnlyRequest) -> HttpResponse:
        self._policy.validate(request)
        raw_request = Request(
            request.url,
            method="GET",
            headers={"Accept": "application/json", "User-Agent": "AUTO-TRADE-R3/1"},
        )
        try:
            with self._opener.open(raw_request, timeout=request.timeout_seconds) as response:  # noqa: S310
                final_url = response.geturl()
                self._policy.validate_final_url(final_url)
                body = response.read(self._max_response_bytes + 1)
                if len(body) > self._max_response_bytes:
                    raise ExternalDataUnavailable("public-data response exceeded size limit")
                return HttpResponse(
                    status_code=int(response.status),
                    body=body,
                    final_url=final_url,
                    headers={key.lower(): value for key, value in response.headers.items()},
                )
        except HTTPError as exc:
            raise ExternalDataUnavailable(f"public-data HTTP error: {exc.code}") from exc
        except URLError as exc:
            raise ExternalDataUnavailable("public-data network request failed") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ExternalDataUnavailable("public-data request timed out") from exc


BINANCE_PUBLIC_DATA_HOST = "data-api.binance.vision"
BINANCE_KLINES_PATH = "/api/v3/klines"
BINANCE_KLINES_MAX_PAGE = 1000
FIXED_INTERVAL_MS: Mapping[str, int] = {
    "1s": 1_000,
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,24}$")


@dataclass(frozen=True, slots=True)
class BinanceKlineRange:
    instrument: InstrumentMetadata
    interval: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.instrument.venue != "BINANCE_SPOT":
            raise ValueError("instrument venue must be BINANCE_SPOT")
        if not _SYMBOL_RE.fullmatch(self.instrument.symbol):
            raise ValueError("invalid Binance symbol")
        if self.interval not in FIXED_INTERVAL_MS:
            raise ValueError("unsupported/non-fixed Binance interval")
        _require_aware(self.start, "start")
        _require_aware(self.end, "end")
        start_ms = _epoch_ms(self.start)
        end_ms = _epoch_ms(self.end)
        interval_ms = FIXED_INTERVAL_MS[self.interval]
        if start_ms >= end_ms:
            raise ValueError("start must be before end")
        if start_ms % interval_ms or end_ms % interval_ms:
            raise ValueError("range boundaries must align to interval")

    @property
    def expected_bars(self) -> int:
        return (_epoch_ms(self.end) - _epoch_ms(self.start)) // FIXED_INTERVAL_MS[self.interval]


@dataclass(frozen=True, slots=True)
class ExternalDatasetManifest:
    provider_id: str
    provider_version: str
    endpoint: str
    symbol: str
    interval: str
    start: str
    end: str
    expected_bars: int
    received_bars: int
    pages: int
    source_payload_sha256: str
    dataset_hash: str
    provenance: str

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return sha256(raw).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "endpoint": self.endpoint,
            "symbol": self.symbol,
            "interval": self.interval,
            "start": self.start,
            "end": self.end,
            "expected_bars": self.expected_bars,
            "received_bars": self.received_bars,
            "pages": self.pages,
            "source_payload_sha256": self.source_payload_sha256,
            "dataset_hash": self.dataset_hash,
            "provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class ExternalDatasetArtifact:
    dataset: MarketDataset
    manifest: ExternalDatasetManifest
    canonical_rows: tuple[tuple[object, ...], ...]

    def write(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "artifact_version": 1,
            "manifest": self.manifest.to_dict(),
            "manifest_fingerprint": self.manifest.fingerprint,
            "instrument": {
                "symbol": self.dataset.instrument.symbol,
                "venue": self.dataset.instrument.venue,
                "asset_class": self.dataset.instrument.asset_class,
                "quote_currency": self.dataset.instrument.quote_currency,
                "price_increment": str(self.dataset.instrument.price_increment),
                "quantity_increment": str(self.dataset.instrument.quantity_increment),
            },
            "rows": [list(row) for row in self.canonical_rows],
        }
        raw = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(raw + "\n", encoding="utf-8")
        temporary.replace(target)

    @classmethod
    def read(cls, path: str | Path) -> "ExternalDatasetArtifact":
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if document.get("artifact_version") != 1:
            raise ExternalDataIntegrityError("unsupported external dataset artifact version")
        manifest = _manifest_from_dict(document.get("manifest"))
        if document.get("manifest_fingerprint") != manifest.fingerprint:
            raise ExternalDataIntegrityError("manifest fingerprint mismatch")
        instrument_data = document.get("instrument")
        if not isinstance(instrument_data, dict):
            raise ExternalDataIntegrityError("artifact instrument is missing")
        try:
            instrument = InstrumentMetadata(
                symbol=instrument_data["symbol"],
                venue=instrument_data["venue"],
                asset_class=instrument_data["asset_class"],
                quote_currency=instrument_data["quote_currency"],
                price_increment=Decimal(instrument_data["price_increment"]),
                quantity_increment=Decimal(instrument_data["quantity_increment"]),
            )
        except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
            raise ExternalDataIntegrityError("artifact instrument is invalid") from exc
        rows_raw = document.get("rows")
        if not isinstance(rows_raw, list):
            raise ExternalDataIntegrityError("artifact rows are missing")
        rows = tuple(_validate_canonical_row(row) for row in rows_raw)
        payload_hash = _rows_sha256(rows)
        if payload_hash != manifest.source_payload_sha256:
            raise ExternalDataIntegrityError("source payload checksum mismatch")
        bars = tuple(_bar_from_canonical_row(row, manifest.interval) for row in rows)
        try:
            dataset = MarketDataset(
                instrument=instrument,
                timeframe=manifest.interval,
                bars=bars,
                provenance=manifest.provenance,
            )
        except ValueError as exc:
            raise ExternalDataIntegrityError("artifact dataset is invalid") from exc
        if dataset.dataset_hash != manifest.dataset_hash:
            raise ExternalDataIntegrityError("dataset hash mismatch")
        _validate_exact_coverage(
            bars=dataset.bars,
            interval=manifest.interval,
            start=datetime.fromisoformat(manifest.start),
            end=datetime.fromisoformat(manifest.end),
        )
        if len(rows) != manifest.received_bars or manifest.received_bars != manifest.expected_bars:
            raise ExternalDataIntegrityError("artifact bar-count mismatch")
        return cls(dataset=dataset, manifest=manifest, canonical_rows=rows)


class BinanceSpotHistoricalProvider:
    provider_id = "BINANCE_SPOT_PUBLIC_KLINES"
    provider_version = "r3-v1"

    def __init__(
        self,
        *,
        transport: ReadOnlyHttpTransport,
        enabled: bool = False,
        timeout_seconds: float = 10.0,
        max_total_bars: int = 5_000,
    ) -> None:
        if not 0 < timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be > 0 and <= 30")
        if not 1 <= max_total_bars <= 50_000:
            raise ValueError("max_total_bars must be between 1 and 50000")
        self._transport = transport
        self._enabled = enabled
        self._timeout_seconds = timeout_seconds
        self._max_total_bars = max_total_bars
        self._policy = PublicDataPolicy(
            allowed_host=BINANCE_PUBLIC_DATA_HOST,
            allowed_paths=frozenset({BINANCE_KLINES_PATH}),
        )

    def fetch(self, request: BinanceKlineRange) -> ExternalDatasetArtifact:
        if not self._enabled:
            raise ExternalDataDisabled("external public market data is disabled by default")
        if request.expected_bars > self._max_total_bars:
            raise ExternalDataPolicyError("requested range exceeds bounded max_total_bars")

        interval_ms = FIXED_INTERVAL_MS[request.interval]
        start_ms = _epoch_ms(request.start)
        end_ms = _epoch_ms(request.end)
        all_rows: list[tuple[object, ...]] = []
        pages = 0
        cursor = start_ms
        while cursor < end_ms:
            remaining = (end_ms - cursor) // interval_ms
            page_count = min(remaining, BINANCE_KLINES_MAX_PAGE)
            page_end = cursor + page_count * interval_ms
            url = self._url(
                symbol=request.instrument.symbol,
                interval=request.interval,
                start_ms=cursor,
                end_exclusive_ms=page_end,
                limit=page_count,
            )
            public_request = ReadOnlyRequest(
                method="GET", url=url, timeout_seconds=self._timeout_seconds
            )
            self._policy.validate(public_request)
            try:
                response = self._transport.send(public_request)
            except ExternalDataError:
                raise
            except Exception as exc:
                raise ExternalDataUnavailable("public-data transport failed") from exc
            self._policy.validate_final_url(response.final_url)
            if response.status_code != 200:
                raise ExternalDataUnavailable(
                    f"public-data response status is {response.status_code}"
                )
            content_type = response.headers.get("content-type", "").lower()
            if not content_type.startswith("application/json"):
                raise ExternalDataIntegrityError(
                    "public-data response content-type is not application/json"
                )
            rows = _decode_binance_rows(response.body)
            _validate_page_rows(
                rows=rows,
                interval=request.interval,
                start_ms=cursor,
                end_exclusive_ms=page_end,
                expected_count=page_count,
            )
            all_rows.extend(rows)
            pages += 1
            cursor = page_end

        rows_tuple = tuple(all_rows)
        if len(rows_tuple) != request.expected_bars:
            raise ExternalDataIntegrityError("external dataset bar-count mismatch")
        source_hash = _rows_sha256(rows_tuple)
        provenance = (
            f"{self.provider_id}:{self.provider_version}:"
            f"sha256={source_hash}:range={start_ms}-{end_ms}:interval={request.interval}"
        )
        bars = tuple(_bar_from_canonical_row(row, request.interval) for row in rows_tuple)
        _validate_exact_coverage(
            bars=bars, interval=request.interval, start=request.start, end=request.end
        )
        dataset = MarketDataset(
            instrument=request.instrument,
            timeframe=request.interval,
            bars=bars,
            provenance=provenance,
        )
        if dataset.detect_gaps():
            raise ExternalDataIntegrityError("external dataset contains gaps")
        manifest = ExternalDatasetManifest(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            endpoint=f"https://{BINANCE_PUBLIC_DATA_HOST}{BINANCE_KLINES_PATH}",
            symbol=request.instrument.symbol,
            interval=request.interval,
            start=request.start.astimezone(timezone.utc).isoformat(),
            end=request.end.astimezone(timezone.utc).isoformat(),
            expected_bars=request.expected_bars,
            received_bars=len(rows_tuple),
            pages=pages,
            source_payload_sha256=source_hash,
            dataset_hash=dataset.dataset_hash,
            provenance=provenance,
        )
        return ExternalDatasetArtifact(
            dataset=dataset, manifest=manifest, canonical_rows=rows_tuple
        )

    @staticmethod
    def _url(
        *, symbol: str, interval: str, start_ms: int, end_exclusive_ms: int, limit: int
    ) -> str:
        query = urlencode(
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": str(start_ms),
                "endTime": str(end_exclusive_ms - 1),
                "limit": str(limit),
            }
        )
        return f"https://{BINANCE_PUBLIC_DATA_HOST}{BINANCE_KLINES_PATH}?{query}"


def _decode_binance_rows(body: bytes) -> tuple[tuple[object, ...], ...]:
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalDataIntegrityError("public-data response is not valid JSON") from exc
    if not isinstance(decoded, list):
        raise ExternalDataIntegrityError("Binance klines payload must be an array")
    return tuple(_validate_binance_row(row) for row in decoded)


def _validate_binance_row(row: object) -> tuple[object, ...]:
    if not isinstance(row, list) or len(row) < 12:
        raise ExternalDataIntegrityError("Binance kline row must contain 12 fields")
    open_time, open_raw, high_raw, low_raw, close_raw, volume_raw, close_time = row[:7]
    if isinstance(open_time, bool) or not isinstance(open_time, int):
        raise ExternalDataIntegrityError("kline open time must be integer milliseconds")
    if isinstance(close_time, bool) or not isinstance(close_time, int):
        raise ExternalDataIntegrityError("kline close time must be integer milliseconds")
    decimals: list[str] = []
    for name, raw in (
        ("open", open_raw),
        ("high", high_raw),
        ("low", low_raw),
        ("close", close_raw),
        ("volume", volume_raw),
    ):
        if not isinstance(raw, str):
            raise ExternalDataIntegrityError(f"kline {name} must be encoded as string")
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise ExternalDataIntegrityError(f"kline {name} is not a decimal") from exc
        if not value.is_finite():
            raise ExternalDataIntegrityError(f"kline {name} must be finite")
        decimals.append(str(value))
    return (
        open_time,
        decimals[0],
        decimals[1],
        decimals[2],
        decimals[3],
        decimals[4],
        close_time,
    )


def _validate_canonical_row(row: object) -> tuple[object, ...]:
    if not isinstance(row, list) or len(row) != 7:
        raise ExternalDataIntegrityError("canonical kline row must have seven fields")
    return _validate_binance_row(list(row) + ["0", "0", "0", "0", "0"])


def _validate_page_rows(
    *,
    rows: tuple[tuple[object, ...], ...],
    interval: str,
    start_ms: int,
    end_exclusive_ms: int,
    expected_count: int,
) -> None:
    if len(rows) != expected_count:
        raise ExternalDataIntegrityError(
            f"incomplete kline page: expected={expected_count}, received={len(rows)}"
        )
    interval_ms = FIXED_INTERVAL_MS[interval]
    for index, row in enumerate(rows):
        expected_open = start_ms + index * interval_ms
        if row[0] != expected_open:
            raise ExternalDataIntegrityError(
                f"unexpected kline open time at index {index}: {row[0]} != {expected_open}"
            )
        if row[6] != expected_open + interval_ms - 1:
            raise ExternalDataIntegrityError("unexpected kline close time")
    if rows and rows[-1][0] + interval_ms != end_exclusive_ms:
        raise ExternalDataIntegrityError("kline page does not cover requested end")


def _validate_exact_coverage(
    *, bars: tuple[Bar, ...], interval: str, start: datetime, end: datetime
) -> None:
    interval_ms = FIXED_INTERVAL_MS[interval]
    expected = (_epoch_ms(end) - _epoch_ms(start)) // interval_ms
    if len(bars) != expected:
        raise ExternalDataIntegrityError("dataset does not contain expected bar count")
    for index, bar in enumerate(bars):
        expected_ms = _epoch_ms(start) + index * interval_ms
        if _epoch_ms(bar.timestamp) != expected_ms:
            raise ExternalDataIntegrityError("dataset timestamp coverage mismatch")


def _bar_from_canonical_row(row: tuple[object, ...], interval: str) -> Bar:
    return Bar(
        timestamp=datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),
        open=Decimal(str(row[1])),
        high=Decimal(str(row[2])),
        low=Decimal(str(row[3])),
        close=Decimal(str(row[4])),
        volume=Decimal(str(row[5])),
        timeframe=interval,
    )


def _rows_sha256(rows: tuple[tuple[object, ...], ...]) -> str:
    raw = json.dumps(rows, sort_keys=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    return sha256(raw).hexdigest()


def _manifest_from_dict(raw: object) -> ExternalDatasetManifest:
    if not isinstance(raw, dict):
        raise ExternalDataIntegrityError("artifact manifest is missing")
    try:
        manifest = ExternalDatasetManifest(
            provider_id=str(raw["provider_id"]),
            provider_version=str(raw["provider_version"]),
            endpoint=str(raw["endpoint"]),
            symbol=str(raw["symbol"]),
            interval=str(raw["interval"]),
            start=str(raw["start"]),
            end=str(raw["end"]),
            expected_bars=int(raw["expected_bars"]),
            received_bars=int(raw["received_bars"]),
            pages=int(raw["pages"]),
            source_payload_sha256=str(raw["source_payload_sha256"]),
            dataset_hash=str(raw["dataset_hash"]),
            provenance=str(raw["provenance"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExternalDataIntegrityError("artifact manifest is invalid") from exc
    if manifest.provider_id != BinanceSpotHistoricalProvider.provider_id:
        raise ExternalDataIntegrityError("artifact provider mismatch")
    if manifest.interval not in FIXED_INTERVAL_MS:
        raise ExternalDataIntegrityError("artifact interval is unsupported")
    if manifest.expected_bars <= 0 or manifest.received_bars <= 0 or manifest.pages <= 0:
        raise ExternalDataIntegrityError("artifact counts must be positive")
    return manifest


def _epoch_ms(value: datetime) -> int:
    _require_aware(value, "timestamp")
    normalized = value.astimezone(timezone.utc)
    if normalized.microsecond % 1000:
        raise ValueError("timestamp must have exact millisecond precision")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = normalized - epoch
    return (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
