from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from autotrade.domain import MarketSnapshot

from .alpaca_paper_gateway import AlpacaPaperCredentials
from .alpaca_paper_market_data import (
    ALPACA_BASIC_EQUITY_FEED,
    ALPACA_MARKET_DATA_CURRENCY,
    ALPACA_MARKET_DATA_HOST,
    AlpacaPaperEquityMarketAttestation,
)
from .alpaca_paper_operational import (
    PaperOperationalIntegrityError,
    PaperOperationalWorkspace,
    _read_json_object,
    _write_json_idempotent,
)


_MARKET_ARTIFACT = "market_snapshot.json"


class PaperMarketEvidenceError(RuntimeError):
    pass


class PaperMarketEvidenceIntegrityError(PaperMarketEvidenceError):
    pass


@dataclass(frozen=True, slots=True)
class PaperMarketEvidenceStore:
    workspace: PaperOperationalWorkspace

    @property
    def path(self) -> Path:
        return self.workspace.root / _MARKET_ARTIFACT

    def write(
        self,
        *,
        attestation: AlpacaPaperEquityMarketAttestation,
        credentials: AlpacaPaperCredentials,
    ) -> Path:
        if not isinstance(attestation, AlpacaPaperEquityMarketAttestation):
            raise TypeError("equity market attestation is required")
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise TypeError("Alpaca PAPER credentials are required")
        account_path = self.workspace.account_attestation_path
        if not account_path.is_file():
            raise PaperMarketEvidenceIntegrityError(
                "PAPER account attestation must exist before market evidence"
            )
        try:
            account = _read_json_object(account_path)
        except PaperOperationalIntegrityError as exc:
            raise PaperMarketEvidenceIntegrityError(
                "workspace PAPER account attestation is unreadable"
            ) from exc
        if account.get("environment") != "PAPER":
            raise PaperMarketEvidenceIntegrityError("workspace account evidence is not PAPER")
        if account.get("credentials_persisted") is not False:
            raise PaperMarketEvidenceIntegrityError("workspace account evidence persists credentials")
        if account.get("credential_reference") != credentials.credential_reference:
            raise PaperMarketEvidenceIntegrityError(
                "market-data credentials do not match attested PAPER credential reference"
            )
        payload = market_evidence_payload(attestation)
        _write_json_idempotent(self.path, payload)
        return self.path

    def read(self) -> AlpacaPaperEquityMarketAttestation:
        try:
            raw = _read_json_object(self.path)
        except PaperOperationalIntegrityError as exc:
            raise PaperMarketEvidenceIntegrityError("market evidence is unreadable") from exc
        return market_evidence_from_payload(raw)


def market_evidence_payload(
    attestation: AlpacaPaperEquityMarketAttestation,
) -> dict[str, object]:
    if not isinstance(attestation, AlpacaPaperEquityMarketAttestation):
        raise TypeError("equity market attestation is required")
    market = attestation.market
    return {
        "schema_version": 1,
        "environment": "PAPER",
        "symbol": market.symbol,
        "bid": str(market.bid),
        "ask": str(market.ask),
        "last": str(market.last),
        "market_observed_at": market.observed_at.isoformat(),
        "market_fingerprint": _market_fingerprint(attestation),
        "attestation_fingerprint": attestation.fingerprint,
        "quote_observed_at": attestation.quote_observed_at.isoformat(),
        "trade_observed_at": attestation.trade_observed_at.isoformat(),
        "received_at": attestation.received_at.isoformat(),
        "response_sha256": attestation.response_sha256,
        "source_host": attestation.source_host,
        "source_path": f"/v2/stocks/{market.symbol}/snapshot",
        "feed": attestation.feed,
        "currency": attestation.currency,
        "network_method": "GET",
        "credentials_persisted": False,
        "broker_write_authorized": False,
        "external_order_submitted": False,
        "capital_authority": "NONE",
        "profitability_claim": False,
        "live_trading": "BLOCKED",
    }


def market_evidence_from_payload(
    raw: Mapping[str, object],
) -> AlpacaPaperEquityMarketAttestation:
    expected = {
        "schema_version",
        "environment",
        "symbol",
        "bid",
        "ask",
        "last",
        "market_observed_at",
        "market_fingerprint",
        "attestation_fingerprint",
        "quote_observed_at",
        "trade_observed_at",
        "received_at",
        "response_sha256",
        "source_host",
        "source_path",
        "feed",
        "currency",
        "network_method",
        "credentials_persisted",
        "broker_write_authorized",
        "external_order_submitted",
        "capital_authority",
        "profitability_claim",
        "live_trading",
    }
    if set(raw) != expected:
        raise PaperMarketEvidenceIntegrityError("market evidence payload is non-canonical")
    if raw.get("schema_version") != 1 or raw.get("environment") != "PAPER":
        raise PaperMarketEvidenceIntegrityError("market evidence schema/environment is invalid")
    symbol = _string(raw, "symbol")
    if raw.get("source_host") != ALPACA_MARKET_DATA_HOST:
        raise PaperMarketEvidenceIntegrityError("market evidence source host mismatch")
    if raw.get("source_path") != f"/v2/stocks/{symbol}/snapshot":
        raise PaperMarketEvidenceIntegrityError("market evidence source path mismatch")
    if raw.get("feed") != ALPACA_BASIC_EQUITY_FEED:
        raise PaperMarketEvidenceIntegrityError("market evidence feed must be IEX")
    if raw.get("currency") != ALPACA_MARKET_DATA_CURRENCY:
        raise PaperMarketEvidenceIntegrityError("market evidence currency must be USD")
    if raw.get("network_method") != "GET":
        raise PaperMarketEvidenceIntegrityError("market evidence network method must be GET")
    for key, expected_value in (
        ("credentials_persisted", False),
        ("broker_write_authorized", False),
        ("external_order_submitted", False),
        ("profitability_claim", False),
        ("capital_authority", "NONE"),
        ("live_trading", "BLOCKED"),
    ):
        if raw.get(key) != expected_value:
            raise PaperMarketEvidenceIntegrityError(f"unsafe market evidence field: {key}")

    market = MarketSnapshot(
        symbol=symbol,
        bid=_decimal(raw, "bid"),
        ask=_decimal(raw, "ask"),
        last=_decimal(raw, "last"),
        observed_at=_datetime(raw, "market_observed_at"),
    )
    attestation = AlpacaPaperEquityMarketAttestation(
        market=market,
        feed=_string(raw, "feed"),
        currency=_string(raw, "currency"),
        quote_observed_at=_datetime(raw, "quote_observed_at"),
        trade_observed_at=_datetime(raw, "trade_observed_at"),
        received_at=_datetime(raw, "received_at"),
        response_sha256=_string(raw, "response_sha256"),
        source_host=_string(raw, "source_host"),
    )
    if _string(raw, "attestation_fingerprint") != attestation.fingerprint:
        raise PaperMarketEvidenceIntegrityError("market attestation fingerprint mismatch")
    if _string(raw, "market_fingerprint") != _market_fingerprint(attestation):
        raise PaperMarketEvidenceIntegrityError("market fingerprint mismatch")
    if market_evidence_payload(attestation) != dict(raw):
        raise PaperMarketEvidenceIntegrityError("market evidence is not canonical")
    return attestation


def _market_fingerprint(attestation: AlpacaPaperEquityMarketAttestation) -> str:
    from autotrade.domain import market_fingerprint

    return market_fingerprint(attestation.market)


def _string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise PaperMarketEvidenceIntegrityError(f"market evidence {key} must be string")
    return value


def _decimal(raw: Mapping[str, object], key: str) -> Decimal:
    value = raw.get(key)
    if not isinstance(value, str):
        raise PaperMarketEvidenceIntegrityError(f"market evidence {key} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PaperMarketEvidenceIntegrityError(f"market evidence {key} is invalid decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise PaperMarketEvidenceIntegrityError(f"market evidence {key} must be positive")
    return parsed


def _datetime(raw: Mapping[str, object], key: str) -> datetime:
    value = raw.get(key)
    if not isinstance(value, str):
        raise PaperMarketEvidenceIntegrityError(f"market evidence {key} must be datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PaperMarketEvidenceIntegrityError(f"market evidence {key} is invalid datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PaperMarketEvidenceIntegrityError(f"market evidence {key} must be timezone-aware")
    return parsed
