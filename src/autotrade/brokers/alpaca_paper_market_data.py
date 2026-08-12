from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
import socket
import ssl
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from autotrade.domain import MarketSnapshot, market_fingerprint

from .alpaca_paper_gateway import AlpacaPaperCredentials


ALPACA_MARKET_DATA_HOST = "data.alpaca.markets"
ALPACA_STOCK_SNAPSHOT_PREFIX = "/v2/stocks/"
ALPACA_STOCK_SNAPSHOT_SUFFIX = "/snapshot"
ALPACA_BASIC_EQUITY_FEED = "iex"
ALPACA_MARKET_DATA_CURRENCY = "USD"
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")


class AlpacaPaperMarketDataError(RuntimeError):
    pass


class AlpacaPaperMarketDataDisabled(AlpacaPaperMarketDataError):
    pass


class AlpacaPaperMarketDataPolicyError(AlpacaPaperMarketDataError):
    pass


class AlpacaPaperMarketDataUnavailable(AlpacaPaperMarketDataError):
    pass


class AlpacaPaperMarketDataIntegrityError(AlpacaPaperMarketDataError):
    pass


@dataclass(frozen=True, slots=True)
class AlpacaPaperMarketDataConfig:
    enabled: bool = False
    base_url: str = f"https://{ALPACA_MARKET_DATA_HOST}"
    feed: str = ALPACA_BASIC_EQUITY_FEED
    currency: str = ALPACA_MARKET_DATA_CURRENCY
    timeout_seconds: float = 5.0
    max_response_bytes: int = 256 * 1024
    max_component_age_seconds: float = 30.0
    max_quote_trade_skew_seconds: float = 30.0
    future_tolerance_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.base_url != f"https://{ALPACA_MARKET_DATA_HOST}":
            raise ValueError("R6 equity market data base_url must be exact Alpaca data host")
        if self.feed != ALPACA_BASIC_EQUITY_FEED:
            raise ValueError("R6 equity market data is pinned to IEX")
        if self.currency != ALPACA_MARKET_DATA_CURRENCY:
            raise ValueError("R6 equity market data currency must be USD")
        if not 0 < self.timeout_seconds <= 15:
            raise ValueError("market data timeout must be > 0 and <= 15 seconds")
        if not 1 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("market data max_response_bytes must be between 1 and 1048576")
        if not 0 < self.max_component_age_seconds <= 120:
            raise ValueError("market data max_component_age_seconds must be > 0 and <= 120")
        if not 0 <= self.max_quote_trade_skew_seconds <= 120:
            raise ValueError("market data max_quote_trade_skew_seconds must be between 0 and 120")
        if not 0 <= self.future_tolerance_seconds <= 5:
            raise ValueError("market data future_tolerance_seconds must be between 0 and 5")


@dataclass(frozen=True, slots=True)
class AlpacaPaperMarketDataRequest:
    method: str
    url: str
    timeout_seconds: float
    headers: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True, slots=True)
class AlpacaPaperMarketDataHttpResponse:
    status_code: int
    body: bytes
    final_url: str
    headers: Mapping[str, str]


class AlpacaPaperMarketDataTransport(Protocol):
    def read(
        self, request: AlpacaPaperMarketDataRequest
    ) -> AlpacaPaperMarketDataHttpResponse: ...


@dataclass(frozen=True, slots=True)
class AlpacaPaperEquityMarketAttestation:
    market: MarketSnapshot
    feed: str
    currency: str
    quote_observed_at: datetime
    trade_observed_at: datetime
    received_at: datetime
    response_sha256: str
    source_host: str = ALPACA_MARKET_DATA_HOST

    def __post_init__(self) -> None:
        if self.feed != ALPACA_BASIC_EQUITY_FEED:
            raise ValueError("market attestation feed must be IEX")
        if self.currency != ALPACA_MARKET_DATA_CURRENCY:
            raise ValueError("market attestation currency must be USD")
        if self.source_host != ALPACA_MARKET_DATA_HOST:
            raise ValueError("market attestation source host mismatch")
        for value, label in (
            (self.quote_observed_at, "quote_observed_at"),
            (self.trade_observed_at, "trade_observed_at"),
            (self.received_at, "received_at"),
            (self.market.observed_at, "market.observed_at"),
        ):
            _require_aware(value, label)
        if not re.fullmatch(r"[0-9a-f]{64}", self.response_sha256):
            raise ValueError("response_sha256 must be lowercase SHA-256")
        expected_market_time = min(
            self.quote_observed_at.astimezone(timezone.utc),
            self.trade_observed_at.astimezone(timezone.utc),
        )
        if self.market.observed_at.astimezone(timezone.utc) != expected_market_time:
            raise ValueError("MarketSnapshot observed_at must be oldest component timestamp")

    @property
    def fingerprint(self) -> str:
        payload = {
            "market_fingerprint": market_fingerprint(self.market),
            "feed": self.feed,
            "currency": self.currency,
            "quote_observed_at": self.quote_observed_at.astimezone(timezone.utc).isoformat(),
            "trade_observed_at": self.trade_observed_at.astimezone(timezone.utc).isoformat(),
            "received_at": self.received_at.astimezone(timezone.utc).isoformat(),
            "response_sha256": self.response_sha256,
            "source_host": self.source_host,
        }
        return _hash_json(payload)


class _RejectMarketDataRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AlpacaPaperMarketDataPolicyError("Alpaca market-data redirects are forbidden")


class UrllibAlpacaPaperMarketDataTransport:
    def __init__(self, *, max_response_bytes: int = 256 * 1024) -> None:
        if not 1 <= max_response_bytes <= 1_048_576:
            raise ValueError("market-data response limit is invalid")
        self._max_response_bytes = max_response_bytes
        context = ssl.create_default_context()
        self._opener = build_opener(
            ProxyHandler({}),
            HTTPSHandler(context=context),
            _RejectMarketDataRedirectHandler(),
        )

    def read(
        self, request: AlpacaPaperMarketDataRequest
    ) -> AlpacaPaperMarketDataHttpResponse:
        try:
            wire = Request(
                request.url,
                headers=dict(request.headers),
                method="GET",
            )
            response = self._opener.open(wire, timeout=request.timeout_seconds)
            try:
                body = response.read(self._max_response_bytes + 1)
                if len(body) > self._max_response_bytes:
                    raise AlpacaPaperMarketDataIntegrityError(
                        "Alpaca market-data response exceeds configured limit"
                    )
                return AlpacaPaperMarketDataHttpResponse(
                    status_code=int(response.status),
                    body=body,
                    final_url=str(response.geturl()),
                    headers={str(k): str(v) for k, v in response.headers.items()},
                )
            finally:
                response.close()
        except HTTPError as exc:
            raise AlpacaPaperMarketDataUnavailable(
                f"Alpaca market-data HTTP error {exc.code}"
            ) from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise AlpacaPaperMarketDataUnavailable("Alpaca market-data request failed") from exc


class AlpacaPaperEquityMarketDataGateway:
    """One-shot, GET-only IEX snapshot reader for R6 US-equity market evidence."""

    def __init__(
        self,
        config: AlpacaPaperMarketDataConfig | None = None,
        *,
        transport: AlpacaPaperMarketDataTransport | None = None,
    ) -> None:
        self._config = config or AlpacaPaperMarketDataConfig()
        self._transport = transport or UrllibAlpacaPaperMarketDataTransport(
            max_response_bytes=self._config.max_response_bytes
        )

    def attest_snapshot(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        symbol: str,
        now: datetime,
    ) -> AlpacaPaperEquityMarketAttestation:
        if not self._config.enabled:
            raise AlpacaPaperMarketDataDisabled("R6 equity market-data gateway is disabled")
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise TypeError("Alpaca PAPER credentials are required")
        _require_aware(now, "now")
        canonical_symbol = _canonical_symbol(symbol)
        url = self._snapshot_url(canonical_symbol)
        request = AlpacaPaperMarketDataRequest(
            method="GET",
            url=url,
            timeout_seconds=self._config.timeout_seconds,
            headers={
                "APCA-API-KEY-ID": credentials.key_id,
                "APCA-API-SECRET-KEY": credentials.secret_key,
                "Accept": "application/json",
            },
        )
        self._validate_request(request, symbol=canonical_symbol)
        response = self._transport.read(request)
        self._validate_final_url(response.final_url, symbol=canonical_symbol)
        if response.status_code != 200:
            raise AlpacaPaperMarketDataUnavailable(
                f"Alpaca market-data returned HTTP {response.status_code}"
            )
        if len(response.body) > self._config.max_response_bytes:
            raise AlpacaPaperMarketDataIntegrityError("Alpaca market-data body exceeds limit")
        return self._parse_snapshot(
            response.body,
            symbol=canonical_symbol,
            received_at=now.astimezone(timezone.utc),
        )

    def _snapshot_url(self, symbol: str) -> str:
        return (
            f"{self._config.base_url}{ALPACA_STOCK_SNAPSHOT_PREFIX}"
            f"{quote(symbol, safe='.-')}{ALPACA_STOCK_SNAPSHOT_SUFFIX}"
            f"?feed={self._config.feed}&currency={self._config.currency}"
        )

    def _validate_request(
        self, request: AlpacaPaperMarketDataRequest, *, symbol: str
    ) -> None:
        if request.method != "GET":
            raise AlpacaPaperMarketDataPolicyError("R6 equity market data is GET-only")
        if not 0 < request.timeout_seconds <= 15:
            raise AlpacaPaperMarketDataPolicyError("market-data timeout is invalid")
        parsed = urlsplit(request.url)
        expected_path = f"{ALPACA_STOCK_SNAPSHOT_PREFIX}{symbol}{ALPACA_STOCK_SNAPSHOT_SUFFIX}"
        if parsed.scheme != "https" or parsed.hostname != ALPACA_MARKET_DATA_HOST:
            raise AlpacaPaperMarketDataPolicyError("market-data host is not exact allowlist")
        if parsed.port not in (None, 443) or parsed.path != expected_path:
            raise AlpacaPaperMarketDataPolicyError("market-data path/port is not allowlisted")
        if parsed.username or parsed.password or parsed.fragment:
            raise AlpacaPaperMarketDataPolicyError("market-data URL credentials/fragment forbidden")
        if parsed.query != f"feed={ALPACA_BASIC_EQUITY_FEED}&currency={ALPACA_MARKET_DATA_CURRENCY}":
            raise AlpacaPaperMarketDataPolicyError("market-data query must be exact IEX/USD")
        if set(request.headers) != {
            "APCA-API-KEY-ID",
            "APCA-API-SECRET-KEY",
            "Accept",
        }:
            raise AlpacaPaperMarketDataPolicyError("market-data headers are non-canonical")
        if request.headers.get("Accept") != "application/json":
            raise AlpacaPaperMarketDataPolicyError("market-data Accept header is invalid")

    def _validate_final_url(self, url: str, *, symbol: str) -> None:
        expected = self._snapshot_url(symbol)
        if url != expected:
            raise AlpacaPaperMarketDataPolicyError("market-data final URL changed")

    def _parse_snapshot(
        self,
        body: bytes,
        *,
        symbol: str,
        received_at: datetime,
    ) -> AlpacaPaperEquityMarketAttestation:
        try:
            raw = json.loads(body.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError, InvalidOperation) as exc:
            raise AlpacaPaperMarketDataIntegrityError("market-data response is invalid JSON") from exc
        if not isinstance(raw, dict):
            raise AlpacaPaperMarketDataIntegrityError("market-data snapshot root must be object")
        if raw.get("symbol") != symbol:
            raise AlpacaPaperMarketDataIntegrityError("market-data snapshot symbol mismatch")
        quote_payload = raw.get("latestQuote")
        trade_payload = raw.get("latestTrade")
        if not isinstance(quote_payload, dict) or not isinstance(trade_payload, dict):
            raise AlpacaPaperMarketDataIntegrityError(
                "market-data snapshot requires latestQuote and latestTrade"
            )
        bid = _positive_decimal(quote_payload.get("bp"), "latestQuote.bp")
        ask = _positive_decimal(quote_payload.get("ap"), "latestQuote.ap")
        last = _positive_decimal(trade_payload.get("p"), "latestTrade.p")
        if bid > ask:
            raise AlpacaPaperMarketDataIntegrityError("market-data bid exceeds ask")
        quote_time = _rfc3339(quote_payload.get("t"), "latestQuote.t")
        trade_time = _rfc3339(trade_payload.get("t"), "latestTrade.t")
        self._validate_component_time(quote_time, received_at, "latestQuote")
        self._validate_component_time(trade_time, received_at, "latestTrade")
        skew = abs((quote_time - trade_time).total_seconds())
        if skew > self._config.max_quote_trade_skew_seconds:
            raise AlpacaPaperMarketDataIntegrityError("market-data quote/trade skew exceeds policy")
        market = MarketSnapshot(
            symbol=symbol,
            bid=bid,
            ask=ask,
            last=last,
            observed_at=min(quote_time, trade_time),
        )
        return AlpacaPaperEquityMarketAttestation(
            market=market,
            feed=self._config.feed,
            currency=self._config.currency,
            quote_observed_at=quote_time,
            trade_observed_at=trade_time,
            received_at=received_at,
            response_sha256=sha256(body).hexdigest(),
        )

    def _validate_component_time(
        self, observed: datetime, received_at: datetime, label: str
    ) -> None:
        delta = (received_at - observed).total_seconds()
        if delta < -self._config.future_tolerance_seconds:
            raise AlpacaPaperMarketDataIntegrityError(f"{label} timestamp is in the future")
        if delta > self._config.max_component_age_seconds:
            raise AlpacaPaperMarketDataIntegrityError(f"{label} is stale")


def _canonical_symbol(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("equity symbol must be string")
    if value != value.strip() or value != value.upper() or not _SYMBOL_RE.fullmatch(value):
        raise ValueError("equity symbol must be canonical uppercase US-equity symbol")
    return value


def _positive_decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise AlpacaPaperMarketDataIntegrityError(f"{label} must be positive decimal")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AlpacaPaperMarketDataIntegrityError(f"{label} must be positive decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise AlpacaPaperMarketDataIntegrityError(f"{label} must be positive decimal")
    return parsed


def _rfc3339(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise AlpacaPaperMarketDataIntegrityError(f"{label} must be RFC3339 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AlpacaPaperMarketDataIntegrityError(f"{label} must be RFC3339 timestamp") from exc
    _require_aware(parsed, label)
    return parsed.astimezone(timezone.utc)


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _hash_json(payload: Mapping[str, object]) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()
