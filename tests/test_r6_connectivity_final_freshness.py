from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_asset import AlpacaPaperEquityAssetAttestation
from autotrade.brokers.alpaca_paper_canary_permit import (
    PaperCanaryPermitStatus,
    SQLitePaperCanaryPermitRegistry,
)
from autotrade.brokers.alpaca_paper_flat_account import PaperFlatAccountAttestation
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation
from autotrade.brokers.alpaca_paper_market_data import AlpacaPaperEquityMarketAttestation
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.connectivity_final_freshness import (
    ConnectivityFinalFreshnessGuard,
    ConnectivityFinalFreshnessIntegrityError,
    ConnectivityFinalFreshnessRejected,
    ConnectivityFinalFreshnessStatus,
    SQLiteConnectivityFinalFreshnessRegistry,
)
from autotrade.domain import MarketSnapshot, OrderStatus
from autotrade.persistence import SQLiteOrderStore, SQLiteRuntime
from test_r6_connectivity_candidate import CREDS, NOW, h
from test_r6_connectivity_operator_decision import evidence, issue


class Clock:
    def __init__(self, *times):
        self._times = list(times)
        self._last = times[-1]
    def __call__(self):
        if self._times:
            self._last = self._times.pop(0)
        return self._last


class FreshAccountGateway:
    def __init__(self, *, account_reference=None, portfolio_value="100000"):
        self.account_reference = account_reference or h("paper-connectivity-account")
        self.portfolio_value = Decimal(portfolio_value)
        self.calls = 0
    def attest_account(self, *, credentials, expected_account_id, now):
        self.calls += 1
        return AlpacaPaperAccountAttestation(
            account_id=expected_account_id,
            account_reference=self.account_reference,
            credential_reference=credentials.credential_reference,
            status="ACTIVE",
            currency="USD",
            buying_power=Decimal("100000"),
            portfolio_value=self.portfolio_value,
            shorting_enabled=False,
            attested_at=now,
            request_id=f"fresh-account-{self.calls}",
            source_host="paper-api.alpaca.markets",
            source_path="/v2/account",
        )


class FreshAssetGateway:
    def __init__(self, *, price_increment="0.01"):
        self.price_increment = Decimal(price_increment)
        self.calls = 0
    def attest_asset(
        self,
        *,
        credentials,
        symbol,
        account_attestation_fingerprint,
        expected_credential_reference,
        now,
    ):
        self.calls += 1
        assert credentials.credential_reference == expected_credential_reference
        return AlpacaPaperEquityAssetAttestation(
            symbol=symbol,
            asset_id="asset-connectivity-five",
            asset_class="us_equity",
            exchange="NASDAQ",
            status="active",
            tradable=True,
            fractionable=True,
            min_order_size=Decimal("0.000001"),
            min_trade_increment=Decimal("0.000001"),
            price_increment=self.price_increment,
            attributes=(),
            account_attestation_fingerprint=account_attestation_fingerprint,
            credential_reference=credentials.credential_reference,
            observed_at=now,
            request_id=f"fresh-asset-{self.calls}",
            response_sha256=h(f"fresh-asset-{self.calls}"),
            source_host="paper-api.alpaca.markets",
            source_path=f"/v2/assets/{symbol}",
        )


class FreshFlatGateway:
    def __init__(self, *, positions=0, open_orders=0):
        self.positions = positions
        self.open_orders = open_orders
        self.calls = 0
    def attest_flatness(
        self,
        *,
        credentials,
        account_attestation_fingerprint,
        expected_credential_reference,
        now,
    ):
        self.calls += 1
        assert credentials.credential_reference == expected_credential_reference
        return PaperFlatAccountAttestation(
            account_attestation_fingerprint=account_attestation_fingerprint,
            credential_reference=credentials.credential_reference,
            position_count=self.positions,
            open_order_count=self.open_orders,
            positions_response_hash=h(f"fresh-positions-{self.calls}"),
            orders_response_hash=h(f"fresh-orders-{self.calls}"),
            positions_request_id=f"fresh-pos-{self.calls}",
            orders_request_id=f"fresh-ord-{self.calls}",
            attested_at=now,
        )


class FreshMarketGateway:
    def __init__(self, *, ask="5.01"):
        self.ask = Decimal(ask)
        self.calls = 0
    def attest_snapshot(self, *, credentials, symbol, now):
        self.calls += 1
        bid = self.ask - Decimal("0.01")
        return AlpacaPaperEquityMarketAttestation(
            market=MarketSnapshot(
                symbol=symbol,
                bid=bid,
                ask=self.ask,
                last=bid,
                observed_at=now,
            ),
            feed="iex",
            currency="USD",
            quote_observed_at=now,
            trade_observed_at=now,
            received_at=now,
            response_sha256=h(f"fresh-market-{self.ask}-{self.calls}"),
        )


def ready_workspace(tmp_path, *, ttl_seconds=60):
    ws, prepared, bridge, context = evidence(tmp_path)
    issued_at = NOW + timedelta(seconds=20)
    state = bridge.issue(
        context=context,
        operator_id="operator:arendon7",
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=ttl_seconds),
    )
    return ws, prepared, context, state


def clock_from(offset=21):
    base = NOW + timedelta(seconds=offset)
    return Clock(*(base + timedelta(milliseconds=100 * i) for i in range(6)))


def guard(
    ws,
    *,
    account_gateway=None,
    asset_gateway=None,
    flat_gateway=None,
    market_gateway=None,
    clock=None,
):
    return ConnectivityFinalFreshnessGuard(
        ws,
        account_gateway=account_gateway or FreshAccountGateway(),
        asset_gateway=asset_gateway or FreshAssetGateway(),
        flat_gateway=flat_gateway or FreshFlatGateway(),
        market_gateway=market_gateway or FreshMarketGateway(),
        clock=clock or clock_from(),
    )


def test_final_freshness_happy_path_is_get_only_and_pre_execution(tmp_path) -> None:
    ws, prepared, context, operator_state = ready_workspace(tmp_path)
    initial_paths = (
        ws.account_attestation_path,
        ws.root / "asset_attestation.json",
        ws.root / "flat_account_attestation.json",
        ws.root / "market_snapshot.json",
    )
    before = {path.name: path.read_bytes() for path in initial_paths}
    result = guard(ws).acquire(credentials=CREDS)

    assert result.state.status is ConnectivityFinalFreshnessStatus.ISSUED
    assert result.permit.operator_context_hash == context.context_hash
    assert result.permit.operator_decision_hash == operator_state.decision.decision_hash
    assert result.permit.preparation_hash == prepared.preparation_hash
    assert result.permit.is_valid_at(result.permit.issued_at) is True
    assert result.permit.expires_at - result.permit.issued_at <= timedelta(seconds=5)
    assert result.fresh_risk_decision.status.value == "APPROVED"
    assert result.fresh_risk_decision.limits_version == "r6-connectivity-final-freshness-v1"

    order = SQLiteOrderStore(SQLiteRuntime(ws.core_db_path)).get_by_order_id(context.order_id)
    assert order is not None and order.status is OrderStatus.VALIDATED
    submission = SQLitePaperSubmissionRegistry(SQLiteRuntime(ws.submission_db_path)).get(context.order_id)
    assert submission.status is PaperSubmissionStatus.PREPARED and submission.attempt_count == 0
    original_permit = SQLitePaperCanaryPermitRegistry(SQLiteRuntime(ws.permit_db_path)).get(context.canary_approval_hash)
    assert original_permit.status is PaperCanaryPermitStatus.ISSUED and original_permit.attempt_id is None

    assert {path.name: path.read_bytes() for path in initial_paths} == before
    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert payload["network_read_count"] == 5
    assert payload["network_methods"] == ["GET"] * 5
    assert payload["credentials_persisted"] is False
    assert payload["initial_preflight_artifacts_modified"] is False
    assert payload["oms_staging_authorized"] is False
    assert payload["external_post_authorized"] is False
    assert payload["external_order_submitted"] is False
    assert payload["strategy_health_required"] is False
    assert payload["strategy_health_created"] is False
    assert payload["strategy_trading_authorized"] is False
    assert payload["capital_authority"] == "NONE"
    assert payload["profitability_claim"] is False
    assert payload["live_trading"] == "BLOCKED"
    assert payload["next_action"] == "EXPLICIT_CONNECTIVITY_EXECUTION_DECISION_REQUIRED"


def test_final_freshness_refuses_expired_human_decision_before_network(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path, ttl_seconds=10)
    account_gateway = FreshAccountGateway()
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="human decision is expired"):
        guard(
            ws,
            account_gateway=account_gateway,
            clock=clock_from(offset=31),
        ).acquire(credentials=CREDS)
    assert account_gateway.calls == 0
    assert not (ws.root / "connectivity_final_freshness.json").exists()


def test_final_freshness_refuses_human_expiry_during_gets(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path, ttl_seconds=2)
    times = [
        NOW + timedelta(seconds=21),
        NOW + timedelta(seconds=21, milliseconds=100),
        NOW + timedelta(seconds=21, milliseconds=200),
        NOW + timedelta(seconds=21, milliseconds=300),
        NOW + timedelta(seconds=21, milliseconds=400),
        NOW + timedelta(seconds=22, milliseconds=100),
    ]
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="expired during"):
        guard(ws, clock=Clock(*times)).acquire(credentials=CREDS)
    assert not (ws.root / "connectivity_final_freshness.sqlite3").exists()


def test_final_freshness_refuses_core_drift_before_network(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    conn = sqlite3.connect(ws.core_db_path)
    try:
        conn.execute("CREATE TABLE final_guard_drift(x INTEGER)")
        conn.commit()
    finally:
        conn.close()
    account_gateway = FreshAccountGateway()
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="core.sqlite3 changed"):
        guard(ws, account_gateway=account_gateway).acquire(credentials=CREDS)
    assert account_gateway.calls == 0


def test_final_freshness_refuses_account_identity_drift(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="identity/credential reference drifted"):
        guard(ws, account_gateway=FreshAccountGateway(account_reference=h("different-account"))).acquire(credentials=CREDS)


def test_final_freshness_refuses_asset_venue_drift(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="venue metadata drifted"):
        guard(ws, asset_gateway=FreshAssetGateway(price_increment="0.05")).acquire(credentials=CREDS)


def test_final_freshness_refuses_nonflat_fresh_account(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="not flat"):
        guard(ws, flat_gateway=FreshFlatGateway(positions=1)).acquire(credentials=CREDS)


def test_final_freshness_refuses_fresh_account_cap_below_prepared_notional(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="freshly revalidated account cap"):
        guard(ws, account_gateway=FreshAccountGateway(portfolio_value="1000")).acquire(credentials=CREDS)


def test_final_freshness_price_move_is_rejected_by_real_capital_safety(tmp_path) -> None:
    ws, _, context, _ = ready_workspace(tmp_path)
    core_before = ws.core_db_path.read_bytes()
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="fresh Capital Safety rejected.*PRICE_SANITY_BAND"):
        guard(ws, market_gateway=FreshMarketGateway(ask="5.50")).acquire(credentials=CREDS)
    assert ws.core_db_path.read_bytes() != core_before
    order = SQLiteOrderStore(SQLiteRuntime(ws.core_db_path)).get_by_order_id(context.order_id)
    assert order is not None and order.status is OrderStatus.VALIDATED
    assert not (ws.root / "connectivity_final_freshness.json").exists()
    assert not (ws.root / "connectivity_final_freshness.sqlite3").exists()


def test_final_freshness_registry_detects_tamper(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    result = guard(ws).acquire(credentials=CREDS)
    registry = SQLiteConnectivityFinalFreshnessRegistry(SQLiteRuntime(ws.root / "connectivity_final_freshness.sqlite3"))
    assert registry.get(result.permit.permit_hash) == result.state
    conn = sqlite3.connect(ws.root / "connectivity_final_freshness.sqlite3")
    try:
        conn.execute(
            "UPDATE connectivity_final_freshness_control SET event_head_hash=? WHERE singleton=1",
            ("f" * 64,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ConnectivityFinalFreshnessIntegrityError, match="control hash"):
        registry.get(result.permit.permit_hash)


def test_final_freshness_never_refreshes_in_place(tmp_path) -> None:
    ws, _, _, _ = ready_workspace(tmp_path)
    first = guard(ws).acquire(credentials=CREDS)
    assert first.artifact_path.exists()
    with pytest.raises(ConnectivityFinalFreshnessRejected, match="never refresh in-place"):
        guard(ws).acquire(credentials=CREDS)
