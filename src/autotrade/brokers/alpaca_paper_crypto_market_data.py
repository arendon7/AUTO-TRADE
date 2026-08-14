from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

from autotrade.domain import MarketSnapshot, market_fingerprint

from .alpaca_paper_crypto_asset import CRYPTO_PAIR, normalize_crypto_pair
from .alpaca_paper_gateway import AlpacaPaperCredentials
from .alpaca_paper_market_data import (
    ALPACA_MARKET_DATA_HOST,
    AlpacaPaperMarketDataHttpResponse,
    AlpacaPaperMarketDataRequest,
    AlpacaPaperMarketDataTransport,
    AlpacaPaperMarketDataUnavailable,
    UrllibAlpacaPaperMarketDataTransport,
)


CRYPTO_LOCATION = "us"
LATEST_QUOTE_PATH = "/v1beta3/crypto/us/latest/quotes"
LATEST_TRADE_PATH = "/v1beta3/crypto/us/latest/trades"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class AlpacaPaperCryptoMarketDataError(RuntimeError):
    pass


class AlpacaPaperCryptoMarketDataDisabled(AlpacaPaperCryptoMarketDataError):
    pass


class AlpacaPaperCryptoMarketDataPolicyError(AlpacaPaperCryptoMarketDataError):
    pass


class AlpacaPaperCryptoMarketDataIntegrityError(AlpacaPaperCryptoMarketDataError):
    pass


def crypto_exact_query(symbol: str) -> str:
    return f"symbols={normalize_crypto_pair(symbol)}"


EXACT_QUERY = crypto_exact_query(CRYPTO_PAIR)


@dataclass(frozen=True, slots=True)
class AlpacaPaperCryptoMarketDataConfig:
    enabled: bool = False
    timeout_seconds: float = 5.0
    max_response_bytes: int = 256 * 1024
    max_component_age_seconds: float = 60.0
    max_component_skew_seconds: float = 60.0
    future_tolerance_seconds: float = 3.0

    def __post_init__(self) -> None:
        if not 0 < self.timeout_seconds <= 15:
            raise ValueError("crypto market-data timeout must be >0 and <=15 seconds")
        if not 1 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("crypto market-data response limit is invalid")
        if not 0 < self.max_component_age_seconds <= 120:
            raise ValueError("crypto market-data max age must be >0 and <=120 seconds")
        if not 0 <= self.max_component_skew_seconds <= 120:
            raise ValueError("crypto market-data skew must be between 0 and 120 seconds")
        if not 0 <= self.future_tolerance_seconds <= 5:
            raise ValueError("crypto market-data future tolerance must be between 0 and 5 seconds")


@dataclass(frozen=True, slots=True)
class AlpacaPaperCryptoMarketAttestation:
    market: MarketSnapshot
    location: str
    quote_observed_at: datetime
    trade_observed_at: datetime
    received_at: datetime
    quote_response_sha256: str
    trade_response_sha256: str
    source_host: str = ALPACA_MARKET_DATA_HOST

    def __post_init__(self) -> None:
        normalize_crypto_pair(self.market.symbol)
        if self.location != CRYPTO_LOCATION:
            raise ValueError("crypto market attestation must use the exact Alpaca US location")
        if self.source_host != ALPACA_MARKET_DATA_HOST:
            raise ValueError("crypto market-data source host mismatch")
        for value in (self.quote_observed_at, self.trade_observed_at, self.received_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("crypto market timestamps must be timezone-aware")
        for value in (self.quote_response_sha256, self.trade_response_sha256):
            if not _HASH_RE.fullmatch(value):
                raise ValueError("crypto market response hash must be SHA-256")
        expected = min(
            self.quote_observed_at.astimezone(timezone.utc),
            self.trade_observed_at.astimezone(timezone.utc),
        )
        if self.market.observed_at.astimezone(timezone.utc) != expected:
            raise ValueError("crypto MarketSnapshot observed_at must be oldest component")

    @property
    def fingerprint(self) -> str:
        payload = {
            "market_fingerprint": market_fingerprint(self.market),
            "location": self.location,
            "quote_observed_at": self.quote_observed_at.astimezone(timezone.utc).isoformat(),
            "trade_observed_at": self.trade_observed_at.astimezone(timezone.utc).isoformat(),
            "received_at": self.received_at.astimezone(timezone.utc).isoformat(),
            "quote_response_sha256": self.quote_response_sha256,
            "trade_response_sha256": self.trade_response_sha256,
            "source_host": self.source_host,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class AlpacaPaperCryptoMarketDataGateway:
    """Two exact GETs for one canonical crypto pair latest quote/latest trade; no write surface."""

    def __init__(
        self,
        config: AlpacaPaperCryptoMarketDataConfig | None = None,
        *,
        transport: AlpacaPaperMarketDataTransport | None = None,
    ) -> None:
        self._config = config or AlpacaPaperCryptoMarketDataConfig()
        self._transport = transport or UrllibAlpacaPaperMarketDataTransport(
            max_response_bytes=self._config.max_response_bytes
        )

    def attest_snapshot(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        now: datetime,
        symbol: str = CRYPTO_PAIR,
    ) -> AlpacaPaperCryptoMarketAttestation:
        if not self._config.enabled:
            raise AlpacaPaperCryptoMarketDataDisabled("PAPER crypto market-data gateway is disabled")
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise TypeError("Alpaca PAPER credentials are required")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        canonical = normalize_crypto_pair(symbol)
        received_at = now.astimezone(timezone.utc)
        quote = self._read(credentials=credentials, path=LATEST_QUOTE_PATH, symbol=canonical)
        trade = self._read(credentials=credentials, path=LATEST_TRADE_PATH, symbol=canonical)
        bid, ask, quote_time = self._parse_quote(quote.body, symbol=canonical)
        last, trade_time = self._parse_trade(trade.body, symbol=canonical)
        self._validate_time(quote_time, received_at, "crypto latest quote")
        self._validate_time(trade_time, received_at, "crypto latest trade")
        if abs((quote_time - trade_time).total_seconds()) > self._config.max_component_skew_seconds:
            raise AlpacaPaperCryptoMarketDataIntegrityError("crypto quote/trade skew exceeds policy")
        if bid > ask:
            raise AlpacaPaperCryptoMarketDataIntegrityError("crypto bid exceeds ask")
        market = MarketSnapshot(
            symbol=canonical,
            bid=bid,
            ask=ask,
            last=last,
            observed_at=min(quote_time, trade_time),
        )
        return AlpacaPaperCryptoMarketAttestation(
            market=market,
            location=CRYPTO_LOCATION,
            quote_observed_at=quote_time,
            trade_observed_at=trade_time,
            received_at=received_at,
            quote_response_sha256=sha256(quote.body).hexdigest(),
            trade_response_sha256=sha256(trade.body).hexdigest(),
        )

    def _read(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        path: str,
        symbol: str,
    ) -> AlpacaPaperMarketDataHttpResponse:
        query = crypto_exact_query(symbol)
        url = "https" + "://" + ALPACA_MARKET_DATA_HOST + path + "?" + query
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
        self._validate_request(request, path=path, symbol=symbol)
        response = self._transport.read(request)
        self._validate_final_url(response.final_url, path=path, symbol=symbol)
        if response.status_code != 200:
            raise AlpacaPaperMarketDataUnavailable(
                f"Alpaca crypto market-data returned HTTP {response.status_code}"
            )
        if len(response.body) > self._config.max_response_bytes:
            raise AlpacaPaperCryptoMarketDataIntegrityError("crypto market-data body exceeds limit")
        return response

    def _validate_request(self, request: AlpacaPaperMarketDataRequest, *, path: str, symbol: str) -> None:
        expected = "https" + "://" + ALPACA_MARKET_DATA_HOST + path + "?" + crypto_exact_query(symbol)
        if request.method != "GET" or request.url != expected:
            raise AlpacaPaperCryptoMarketDataPolicyError("crypto market-data request is not exact GET allowlist")
        if set(request.headers) != {
            "APCA-API-KEY-ID",
            "APCA-API-SECRET-KEY",
            "Accept",
        } or request.headers.get("Accept") != "application/json":
            raise AlpacaPaperCryptoMarketDataPolicyError("crypto market-data headers are non-canonical")

    def _validate_final_url(self, url: str, *, path: str, symbol: str) -> None:
        expected = "https" + "://" + ALPACA_MARKET_DATA_HOST + path + "?" + crypto_exact_query(symbol)
        if url != expected:
            raise AlpacaPaperCryptoMarketDataPolicyError("crypto market-data final URL changed")

    def _parse_quote(self, body: bytes, *, symbol: str) -> tuple[Decimal, Decimal, datetime]:
        root = _json_object(body, "crypto latest quote")
        quotes = root.get("quotes")
        if not isinstance(quotes, dict):
            raise AlpacaPaperCryptoMarketDataIntegrityError("crypto quotes object is required")
        quote = quotes.get(symbol)
        if not isinstance(quote, dict):
            raise AlpacaPaperCryptoMarketDataIntegrityError(f"{symbol} latest quote is missing")
        bid = _positive_decimal(quote.get("bp"), "crypto bid")
        ask = _positive_decimal(quote.get("ap"), "crypto ask")
        observed = _rfc3339(quote.get("t"), "crypto latest quote timestamp")
        return bid, ask, observed

    def _parse_trade(self, body: bytes, *, symbol: str) -> tuple[Decimal, datetime]:
        root = _json_object(body, "crypto latest trade")
        trades = root.get("trades")
        if not isinstance(trades, dict):
            raise AlpacaPaperCryptoMarketDataIntegrityError("crypto trades object is required")
        trade = trades.get(symbol)
        if not isinstance(trade, dict):
            raise AlpacaPaperCryptoMarketDataIntegrityError(f"{symbol} latest trade is missing")
        return (
            _positive_decimal(trade.get("p"), "crypto latest trade price"),
            _rfc3339(trade.get("t"), "crypto latest trade timestamp"),
        )

    def _validate_time(self, observed: datetime, received_at: datetime, label: str) -> None:
        delta = (received_at - observed.astimezone(timezone.utc)).total_seconds()
        if delta < -self._config.future_tolerance_seconds:
            raise AlpacaPaperCryptoMarketDataIntegrityError(f"{label} timestamp is in the future")
        if delta > self._config.max_component_age_seconds:
            raise AlpacaPaperCryptoMarketDataIntegrityError(f"{label} is stale")


def _json_object(body: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(body.decode("utf-8", errors="strict"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError, InvalidOperation) as exc:
        raise AlpacaPaperCryptoMarketDataIntegrityError(f"{label} response is invalid JSON") from exc
    if not isinstance(value, dict):
        raise AlpacaPaperCryptoMarketDataIntegrityError(f"{label} response root must be object")
    return value


def _positive_decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise AlpacaPaperCryptoMarketDataIntegrityError(f"{label} must be positive decimal")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AlpacaPaperCryptoMarketDataIntegrityError(f"{label} must be positive decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise AlpacaPaperCryptoMarketDataIntegrityError(f"{label} must be positive decimal")
    return parsed


def _rfc3339(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AlpacaPaperCryptoMarketDataIntegrityError(f"{label} must be RFC3339")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AlpacaPaperCryptoMarketDataIntegrityError(f"{label} must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AlpacaPaperCryptoMarketDataIntegrityError(f"{label} must be timezone-aware")
    return parsed


__all__ = [
    "AlpacaPaperCryptoMarketAttestation",
    "AlpacaPaperCryptoMarketDataConfig",
    "AlpacaPaperCryptoMarketDataError",
    "AlpacaPaperCryptoMarketDataGateway",
    "LATEST_QUOTE_PATH",
    "LATEST_TRADE_PATH",
    "crypto_exact_query",
]
