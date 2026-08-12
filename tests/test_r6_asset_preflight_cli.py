from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import runpy

import pytest

from autotrade.brokers.alpaca_paper_asset import AlpacaPaperEquityAssetAttestation
from autotrade.brokers.alpaca_paper_asset_evidence import PaperAssetEvidenceStore
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/r6_external_paper_asset_preflight.py"
NOW = datetime(2026, 8, 12, 3, 10, tzinfo=timezone.utc)
KEY = "paper-key"
SECRET = "paper-secret"
CREDS = AlpacaPaperCredentials(key_id=KEY, secret_key=SECRET)


def h(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def account() -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id="7ca57c2a-1b8f-4e18-9414-cb88b80227c7",
        account_reference=h("asset-cli-account"),
        credential_reference=CREDS.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal("100000"),
        shorting_enabled=False,
        attested_at=NOW,
        request_id="req-account",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
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
        observed_at=NOW,
        request_id="req-asset",
        response_sha256=h("asset-response"),
        source_host="paper-api.alpaca.markets",
        source_path="/v2/assets/AAPL",
    )


class FakeGateway:
    def __init__(self):
        self.calls = []

    def attest_asset(self, **kwargs):
        self.calls.append(kwargs)
        return asset()


def namespace():
    ns = runpy.run_path(str(SCRIPT))
    return ns, ns["main"]


def workspace(tmp_path) -> PaperOperationalWorkspace:
    current = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    current.write_account_attestation(account())
    return current


def test_asset_cli_requires_explicit_opt_in(tmp_path) -> None:
    current = workspace(tmp_path)
    _, main = namespace()
    with pytest.raises(SystemExit, match="allow-paper-asset-read"):
        main(["--workspace", str(current.root), "--symbol", "AAPL"])


def test_asset_cli_rejects_enabled_write_gate_before_network(tmp_path, monkeypatch) -> None:
    current = workspace(tmp_path)
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "ENABLED")
    monkeypatch.setenv("APCA_API_KEY_ID", KEY)
    monkeypatch.setenv("APCA_API_SECRET_KEY", SECRET)
    _, main = namespace()
    fake = FakeGateway()
    main.__globals__["AlpacaPaperEquityAssetGateway"] = lambda config: fake
    with pytest.raises(SystemExit, match="disable the write gate"):
        main(
            [
                "--workspace",
                str(current.root),
                "--symbol",
                "AAPL",
                "--allow-paper-asset-read",
            ]
        )
    assert fake.calls == []


def test_asset_cli_requires_environment_credentials_only(tmp_path, monkeypatch) -> None:
    current = workspace(tmp_path)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "DISABLED")
    _, main = namespace()
    with pytest.raises(SystemExit, match="never accepted as CLI arguments"):
        main(
            [
                "--workspace",
                str(current.root),
                "--symbol",
                "AAPL",
                "--allow-paper-asset-read",
            ]
        )


def test_asset_cli_happy_path_persists_sanitized_non_authorizing_evidence(
    tmp_path, monkeypatch, capsys
) -> None:
    current = workspace(tmp_path)
    monkeypatch.setenv("APCA_API_KEY_ID", KEY)
    monkeypatch.setenv("APCA_API_SECRET_KEY", SECRET)
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "DISABLED")
    _, main = namespace()
    fake = FakeGateway()
    main.__globals__["AlpacaPaperEquityAssetGateway"] = lambda config: fake

    assert (
        main(
            [
                "--workspace",
                str(current.root),
                "--symbol",
                "AAPL",
                "--allow-paper-asset-read",
            ]
        )
        == 0
    )
    assert len(fake.calls) == 1
    persisted = PaperAssetEvidenceStore(current).read()
    assert persisted == asset()
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "PAPER_ASSET_PREFLIGHT_COMPLETE"
    assert output["asset_class"] == "us_equity"
    assert output["whole_share_canary_supported"] is True
    assert output["network_method"] == "GET"
    assert output["broker_write_authorized"] is False
    assert output["external_order_submitted"] is False
    assert output["capital_authority"] == "NONE"
    assert output["profitability_claim"] is False
    assert output["live_trading"] == "BLOCKED"
    serialized = json.dumps(output)
    assert KEY not in serialized
    assert SECRET not in serialized


def test_asset_cli_must_precede_flat_or_market_artifacts(tmp_path, monkeypatch) -> None:
    current = workspace(tmp_path)
    (current.root / "flat_account_attestation.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("APCA_API_KEY_ID", KEY)
    monkeypatch.setenv("APCA_API_SECRET_KEY", SECRET)
    _, main = namespace()
    with pytest.raises(SystemExit, match="must precede"):
        main(
            [
                "--workspace",
                str(current.root),
                "--symbol",
                "AAPL",
                "--allow-paper-asset-read",
            ]
        )
