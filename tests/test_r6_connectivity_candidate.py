from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_asset import AlpacaPaperEquityAssetAttestation
from autotrade.brokers.alpaca_paper_asset_evidence import PaperAssetEvidenceStore
from autotrade.brokers.alpaca_paper_connectivity_candidate import (
    PaperConnectivityCandidateBuilder,
    PaperConnectivityCandidateRejected,
)
from autotrade.brokers.alpaca_paper_flat_account import PaperFlatAccountAttestation
from autotrade.brokers.alpaca_paper_flat_account_evidence import PaperFlatAccountEvidenceStore
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from autotrade.brokers.alpaca_paper_market_data import AlpacaPaperEquityMarketAttestation
from autotrade.brokers.alpaca_paper_market_evidence import PaperMarketEvidenceStore
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.connectivity_canary_authority import (
    CONNECTIVITY_CANARY_STRATEGY_ID,
    SQLiteConnectivityCanaryAuthorityStore,
)
from autotrade.domain import MarketSnapshot, OrderStatus
from autotrade.persistence import SQLiteEventLedger, SQLiteOrderStore, SQLiteRuntime


NOW = datetime(2026, 8, 12, 3, 40, tzinfo=timezone.utc)
CREDS = AlpacaPaperCredentials(key_id="paper-connectivity-key", secret_key="paper-connectivity-secret")


def h(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def account(*, observed_at: datetime | None = None) -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id="paper-connectivity-account",
        account_reference=h("paper-connectivity-account"),
        credential_reference=CREDS.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=False,
        attested_at=observed_at or NOW - timedelta(seconds=2),
        request_id="req-connectivity-account",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )


def asset(current_account: AlpacaPaperAccountAttestation) -> AlpacaPaperEquityAssetAttestation:
    return AlpacaPaperEquityAssetAttestation(
        symbol="FIVE",
        asset_id="asset-connectivity-five",
        asset_class="us_equity",
        exchange="NASDAQ",
        status="active",
        tradable=True,
        fractionable=True,
        min_order_size=Decimal("0.000001"),
        min_trade_increment=Decimal("0.000001"),
        price_increment=Decimal("0.01"),
        attributes=(),
        account_attestation_fingerprint=current_account.fingerprint,
        credential_reference=CREDS.credential_reference,
        observed_at=NOW - timedelta(seconds=1),
        request_id="req-connectivity-asset",
        response_sha256=h("connectivity-asset-response"),
        source_host="paper-api.alpaca.markets",
        source_path="/v2/assets/FIVE",
    )


def flat(current_account: AlpacaPaperAccountAttestation, *, positions: int = 0) -> PaperFlatAccountAttestation:
    return PaperFlatAccountAttestation(
        account_attestation_fingerprint=current_account.fingerprint,
        credential_reference=CREDS.credential_reference,
        position_count=positions,
        open_order_count=0,
        positions_response_hash=h("connectivity-positions"),
        orders_response_hash=h("connectivity-orders"),
        positions_request_id="req-connectivity-positions",
        orders_request_id="req-connectivity-orders",
        attested_at=NOW - timedelta(milliseconds=800),
    )


def market(*, ask: str = "5.01", observed_at: datetime | None = None) -> AlpacaPaperEquityMarketAttestation:
    observed = observed_at or NOW - timedelta(milliseconds=200)
    bid = Decimal(ask) - Decimal("0.01")
    return AlpacaPaperEquityMarketAttestation(
        market=MarketSnapshot(
            symbol="FIVE",
            bid=bid,
            ask=Decimal(ask),
            last=bid,
            observed_at=observed,
        ),
        feed="iex",
        currency="USD",
        quote_observed_at=observed,
        trade_observed_at=observed,
        received_at=observed,
        response_sha256=h(f"market-{ask}-{observed.isoformat()}"),
    )


def workspace(tmp_path, *, dirty: bool = False, ask: str = "5.01", market_time: datetime | None = None):
    ws = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    current_account = account()
    ws.write_account_attestation(current_account)
    PaperAssetEvidenceStore(ws).write(asset(current_account))
    PaperFlatAccountEvidenceStore(ws).write(flat(current_account, positions=1 if dirty else 0))
    PaperMarketEvidenceStore(ws).write(
        attestation=market(ask=ask, observed_at=market_time),
        credentials=CREDS,
    )
    return ws


def test_connectivity_candidate_builds_real_safety_and_oms_without_health(tmp_path) -> None:
    ws = workspace(tmp_path)
    result = PaperConnectivityCandidateBuilder(ws).build(now=NOW)

    assert result.quantity == Decimal("1")
    assert result.limit_price == Decimal("5.01")
    assert result.effective_notional_cap == Decimal("10")
    assert result.artifact_path.is_file()
    runtime = SQLiteRuntime(ws.core_db_path)
    orders = SQLiteOrderStore(runtime).all_orders()
    assert len(orders) == 1
    order = orders[0]
    assert order.status is OrderStatus.VALIDATED
    assert order.intent.strategy_id == CONNECTIVITY_CANARY_STRATEGY_ID
    assert order.intent.quantity == Decimal("1")
    assert order.intent.limit_price == Decimal("5.01")

    authority = SQLiteConnectivityCanaryAuthorityStore(runtime).get_for_order(order.order_id)
    assert authority is not None
    assert authority.authority_id == result.authority_id
    assert authority.max_quantity == Decimal("1")
    assert authority.max_notional == Decimal("10")
    assert SQLiteEventLedger(runtime).verify_integrity() is True

    conn = sqlite3.connect(ws.core_db_path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "health_state_v2" not in tables
    assert "health_bridge_state" not in tables
    assert "submission_state" not in tables

    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert payload["purpose"] == "CONNECTIVITY_CANARY"
    assert payload["order_status"] == "VALIDATED"
    assert payload["risk_decision"]["status"] == "APPROVED"
    assert payload["risk_decision"]["reason_code"] == "APPROVED"
    assert payload["portfolio_baseline"]["performance_scope"] == "CONNECTIVITY_SESSION_ONLY"
    assert payload["portfolio_baseline"]["strategy_performance_claim"] is False
    assert payload["strategy_health_required"] is False
    assert payload["strategy_health_created"] is False
    assert payload["strategy_trading_authorized"] is False
    assert payload["external_post_authorized"] is False
    assert payload["capital_authority"] == "NONE"
    assert payload["profitability_claim"] is False
    assert payload["live_trading"] == "BLOCKED"
    assert payload["next_action"] == "CONNECTIVITY_PREPARATION_BRIDGE_REQUIRED"


def test_connectivity_candidate_rejects_expensive_whole_share_before_core_creation(tmp_path) -> None:
    ws = workspace(tmp_path, ask="11.00")
    with pytest.raises(PaperConnectivityCandidateRejected, match="exceeds strict cap"):
        PaperConnectivityCandidateBuilder(ws).build(now=NOW)
    assert not ws.core_db_path.exists()


def test_connectivity_candidate_rejects_dirty_flat_account_before_core_creation(tmp_path) -> None:
    ws = workspace(tmp_path, dirty=True)
    with pytest.raises(PaperConnectivityCandidateRejected, match="zero positions"):
        PaperConnectivityCandidateBuilder(ws).build(now=NOW)
    assert not ws.core_db_path.exists()


def test_connectivity_candidate_rejects_stale_market_before_core_creation(tmp_path) -> None:
    ws = workspace(tmp_path, market_time=NOW - timedelta(seconds=6))
    with pytest.raises(PaperConnectivityCandidateRejected, match="market evidence is stale"):
        PaperConnectivityCandidateBuilder(ws).build(now=NOW)
    assert not ws.core_db_path.exists()


def test_connectivity_candidate_requires_fresh_workspace(tmp_path) -> None:
    ws = workspace(tmp_path)
    SQLiteRuntime(ws.core_db_path)
    with pytest.raises(PaperConnectivityCandidateRejected, match="core.sqlite3 already exists"):
        PaperConnectivityCandidateBuilder(ws).build(now=NOW)
