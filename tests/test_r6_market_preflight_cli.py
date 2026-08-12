from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import runpy

import pytest

from autotrade.brokers.alpaca_paper_asset import AlpacaPaperEquityAssetAttestation
from autotrade.brokers.alpaca_paper_asset_evidence import PaperAssetEvidenceStore
from autotrade.brokers.alpaca_paper_flat_account import PaperFlatAccountAttestation
from autotrade.brokers.alpaca_paper_flat_account_evidence import PaperFlatAccountEvidenceStore
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from autotrade.brokers.alpaca_paper_market_data import AlpacaPaperEquityMarketAttestation
from autotrade.brokers.alpaca_paper_market_evidence import PaperMarketEvidenceStore
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.domain import MarketSnapshot


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/r6_external_paper_market_preflight.py"
NOW = datetime(2026, 8, 11, 20, 0, tzinfo=timezone.utc)
KEY = "paper-key-id"
SECRET = "paper-secret-key"
CREDS = AlpacaPaperCredentials(key_id=KEY, secret_key=SECRET)


def h(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def account() -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id="paper-account-001",
        account_reference="paper:account:001",
        credential_reference=CREDS.credential_reference,
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


def asset() -> AlpacaPaperEquityAssetAttestation:
    current = account()
    return AlpacaPaperEquityAssetAttestation(
        symbol="AAPL",
        asset_id="b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
        asset_class="us_equity",
        exchange="NASDAQ",
        status="active",
        tradable=True,
        fractionable=True,
        min_order_size=Decimal("0.000001"),
        min_trade_increment=Decimal("0.000001"),
        price_increment=Decimal("0.01"),
        attributes=("has_options",),
        account_attestation_fingerprint=current.fingerprint,
        credential_reference=CREDS.credential_reference,
        observed_at=NOW - timedelta(seconds=1),
        request_id="req-asset",
        response_sha256=h("asset-response"),
        source_host=ALPACA_PAPER_TRADING_HOST,
        source_path="/v2/assets/AAPL",
    )


def flat() -> PaperFlatAccountAttestation:
    current = account()
    return PaperFlatAccountAttestation(
        account_attestation_fingerprint=current.fingerprint,
        credential_reference=current.credential_reference,
        position_count=0,
        open_order_count=0,
        positions_response_hash="b" * 64,
        orders_response_hash="c" * 64,
        positions_request_id="req-positions",
        orders_request_id="req-orders",
        attested_at=datetime.now(timezone.utc),
    )


def market() -> AlpacaPaperEquityMarketAttestation:
    trade_at = NOW - timedelta(seconds=1)
    quote_at = NOW - timedelta(milliseconds=500)
    return AlpacaPaperEquityMarketAttestation(
        market=MarketSnapshot(
            symbol="AAPL",
            bid=Decimal("189.10"),
            ask=Decimal("189.12"),
            last=Decimal("189.11"),
            observed_at=trade_at,
        ),
        feed="iex",
        currency="USD",
        quote_observed_at=quote_at,
        trade_observed_at=trade_at,
        received_at=NOW,
        response_sha256="a" * 64,
    )


class FakeGateway:
    def __init__(self) -> None:
        self.calls = []

    def attest_snapshot(self, *, credentials, symbol, now):
        self.calls.append((credentials, symbol, now))
        return market()


def namespace():
    ns = runpy.run_path(str(SCRIPT))
    return ns, ns["main"]


def setup_workspace(tmp_path) -> PaperOperationalWorkspace:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    workspace.write_account_attestation(account())
    PaperAssetEvidenceStore(workspace).write(asset())
    PaperFlatAccountEvidenceStore(workspace).write(flat())
    return workspace


def test_market_preflight_requires_explicit_read_opt_in(tmp_path, monkeypatch) -> None:
    workspace = setup_workspace(tmp_path)
    monkeypatch.setenv("APCA_API_KEY_ID", KEY)
    monkeypatch.setenv("APCA_API_SECRET_KEY", SECRET)
    _, main = namespace()
    with pytest.raises(SystemExit, match="allow-paper-market-read"):
        main(["--workspace", str(workspace.root), "--symbol", "AAPL"])


def test_market_preflight_rejects_enabled_write_gate_before_network(tmp_path, monkeypatch) -> None:
    workspace = setup_workspace(tmp_path)
    monkeypatch.setenv("APCA_API_KEY_ID", KEY)
    monkeypatch.setenv("APCA_API_SECRET_KEY", SECRET)
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "ENABLED")
    _, main = namespace()
    fake = FakeGateway()
    main.__globals__["AlpacaPaperEquityMarketDataGateway"] = lambda config: fake
    with pytest.raises(SystemExit, match="disable the write gate"):
        main(
            [
                "--workspace",
                str(workspace.root),
                "--symbol",
                "AAPL",
                "--allow-paper-market-read",
            ]
        )
    assert fake.calls == []


def test_market_preflight_requires_flat_account_before_network(tmp_path, monkeypatch) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    workspace.write_account_attestation(account())
    PaperAssetEvidenceStore(workspace).write(asset())
    monkeypatch.setenv("APCA_API_KEY_ID", KEY)
    monkeypatch.setenv("APCA_API_SECRET_KEY", SECRET)
    _, main = namespace()
    fake = FakeGateway()
    main.__globals__["AlpacaPaperEquityMarketDataGateway"] = lambda config: fake
    with pytest.raises(SystemExit, match="not the allowed next step"):
        main(
            [
                "--workspace",
                str(workspace.root),
                "--symbol",
                "AAPL",
                "--allow-paper-market-read",
            ]
        )
    assert fake.calls == []


def test_market_preflight_requires_asset_evidence_before_network(tmp_path, monkeypatch) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    workspace.write_account_attestation(account())
    PaperFlatAccountEvidenceStore(workspace).write(flat())
    monkeypatch.setenv("APCA_API_KEY_ID", KEY)
    monkeypatch.setenv("APCA_API_SECRET_KEY", SECRET)
    _, main = namespace()
    fake = FakeGateway()
    main.__globals__["AlpacaPaperEquityMarketDataGateway"] = lambda config: fake
    with pytest.raises(SystemExit, match="asset evidence"):
        main(
            [
                "--workspace",
                str(workspace.root),
                "--symbol",
                "AAPL",
                "--allow-paper-market-read",
            ]
        )
    assert fake.calls == []


def test_market_preflight_requires_credentials_only_from_environment(tmp_path, monkeypatch) -> None:
    workspace = setup_workspace(tmp_path)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    _, main = namespace()
    with pytest.raises(SystemExit, match="credentials are never accepted as CLI arguments"):
        main(
            [
                "--workspace",
                str(workspace.root),
                "--symbol",
                "AAPL",
                "--allow-paper-market-read",
            ]
        )


def test_market_preflight_rejects_symbol_not_bound_to_asset(tmp_path, monkeypatch) -> None:
    workspace = setup_workspace(tmp_path)
    monkeypatch.setenv("APCA_API_KEY_ID", KEY)
    monkeypatch.setenv("APCA_API_SECRET_KEY", SECRET)
    _, main = namespace()
    fake = FakeGateway()
    main.__globals__["AlpacaPaperEquityMarketDataGateway"] = lambda config: fake
    with pytest.raises(SystemExit, match="does not match attested PAPER asset"):
        main(
            [
                "--workspace",
                str(workspace.root),
                "--symbol",
                "MSFT",
                "--allow-paper-market-read",
            ]
        )
    assert fake.calls == []


def test_market_preflight_happy_path_is_one_get_and_sanitized_artifact(
    tmp_path, monkeypatch, capsys
) -> None:
    workspace = setup_workspace(tmp_path)
    monkeypatch.setenv("APCA_API_KEY_ID", KEY)
    monkeypatch.setenv("APCA_API_SECRET_KEY", SECRET)
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "DISABLED")
    _, main = namespace()
    fake = FakeGateway()
    main.__globals__["AlpacaPaperEquityMarketDataGateway"] = lambda config: fake

    assert (
        main(
            [
                "--workspace",
                str(workspace.root),
                "--symbol",
                "AAPL",
                "--allow-paper-market-read",
            ]
        )
        == 0
    )
    assert len(fake.calls) == 1
    credentials, symbol, _ = fake.calls[0]
    assert credentials.credential_reference == CREDS.credential_reference
    assert symbol == "AAPL"

    persisted = PaperMarketEvidenceStore(workspace).read()
    assert persisted == market()
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "PAPER_MARKET_PREFLIGHT_COMPLETE"
    assert output["asset_attestation_fingerprint"] == asset().fingerprint
    assert output["network_method"] == "GET"
    assert output["network_host"] == "data.alpaca.markets"
    assert output["broker_write_authorized"] is False
    assert output["external_order_submitted"] is False
    assert output["capital_authority"] == "NONE"
    assert output["profitability_claim"] is False
    assert output["live_trading"] == "BLOCKED"
    serialized = json.dumps(output)
    assert KEY not in serialized
    assert SECRET not in serialized
