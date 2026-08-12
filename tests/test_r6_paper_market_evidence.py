from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from autotrade.brokers.alpaca_paper_market_data import AlpacaPaperEquityMarketAttestation
from autotrade.brokers.alpaca_paper_market_evidence import (
    PaperMarketEvidenceIntegrityError,
    PaperMarketEvidenceStore,
)
from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalConflict,
    PaperOperationalWorkspace,
)
from autotrade.domain import MarketSnapshot


NOW = datetime(2026, 8, 11, 19, 45, tzinfo=timezone.utc)
CREDS = AlpacaPaperCredentials(key_id="paper-key-id", secret_key="paper-secret-key")


def account(credentials: AlpacaPaperCredentials = CREDS) -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id="paper-account-001",
        account_reference="paper:account:001",
        credential_reference=credentials.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=False,
        attested_at=NOW - timedelta(seconds=2),
        request_id="request-account-001",
        source_host=ALPACA_PAPER_TRADING_HOST,
        source_path=ALPACA_PAPER_ACCOUNT_PATH,
    )


def market(*, last: Decimal = Decimal("189.11")) -> AlpacaPaperEquityMarketAttestation:
    quote_at = NOW - timedelta(milliseconds=500)
    trade_at = NOW - timedelta(seconds=1)
    return AlpacaPaperEquityMarketAttestation(
        market=MarketSnapshot(
            symbol="AAPL",
            bid=Decimal("189.10"),
            ask=Decimal("189.12"),
            last=last,
            observed_at=trade_at,
        ),
        feed="iex",
        currency="USD",
        quote_observed_at=quote_at,
        trade_observed_at=trade_at,
        received_at=NOW,
        response_sha256="a" * 64,
    )


def workspace(tmp_path):
    ws = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    ws.write_account_attestation(account())
    return ws


def test_market_evidence_round_trip_is_sanitized_and_idempotent(tmp_path) -> None:
    ws = workspace(tmp_path)
    store = PaperMarketEvidenceStore(ws)
    attestation = market()

    path = store.write(attestation=attestation, credentials=CREDS)
    assert store.read() == attestation
    assert store.write(attestation=attestation, credentials=CREDS) == path

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["network_method"] == "GET"
    assert raw["feed"] == "iex"
    assert raw["currency"] == "USD"
    assert raw["credentials_persisted"] is False
    assert raw["broker_write_authorized"] is False
    assert raw["external_order_submitted"] is False
    assert raw["capital_authority"] == "NONE"
    assert raw["profitability_claim"] is False
    assert raw["live_trading"] == "BLOCKED"
    text = path.read_text(encoding="utf-8")
    assert CREDS.key_id not in text
    assert CREDS.secret_key not in text


def test_market_evidence_requires_prior_account_attestation(tmp_path) -> None:
    ws = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    with pytest.raises(PaperMarketEvidenceIntegrityError, match="account attestation must exist"):
        PaperMarketEvidenceStore(ws).write(attestation=market(), credentials=CREDS)


def test_market_evidence_requires_same_credential_reference_as_account(tmp_path) -> None:
    ws = workspace(tmp_path)
    other = AlpacaPaperCredentials(key_id="other-key", secret_key="other-secret")
    with pytest.raises(PaperMarketEvidenceIntegrityError, match="do not match attested"):
        PaperMarketEvidenceStore(ws).write(attestation=market(), credentials=other)


def test_market_evidence_refuses_conflicting_overwrite(tmp_path) -> None:
    ws = workspace(tmp_path)
    store = PaperMarketEvidenceStore(ws)
    store.write(attestation=market(), credentials=CREDS)
    changed = market(last=Decimal("189.20"))
    with pytest.raises(PaperOperationalConflict, match="refusing to overwrite"):
        store.write(attestation=changed, credentials=CREDS)


def test_market_evidence_tamper_is_fail_closed(tmp_path) -> None:
    ws = workspace(tmp_path)
    store = PaperMarketEvidenceStore(ws)
    path = store.write(attestation=market(), credentials=CREDS)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["live_trading"] = "ENABLED"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PaperMarketEvidenceIntegrityError, match="unsafe market evidence field"):
        store.read()


def test_market_evidence_fingerprint_tamper_is_fail_closed(tmp_path) -> None:
    ws = workspace(tmp_path)
    store = PaperMarketEvidenceStore(ws)
    path = store.write(attestation=market(), credentials=CREDS)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["attestation_fingerprint"] = "0" * 64
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PaperMarketEvidenceIntegrityError, match="attestation fingerprint mismatch"):
        store.read()


def test_market_evidence_account_policy_tamper_is_fail_closed(tmp_path) -> None:
    ws = workspace(tmp_path)
    raw = json.loads(ws.account_attestation_path.read_text(encoding="utf-8"))
    raw["credentials_persisted"] = True
    ws.account_attestation_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PaperMarketEvidenceIntegrityError, match="persists credentials"):
        PaperMarketEvidenceStore(ws).write(attestation=market(), credentials=CREDS)
