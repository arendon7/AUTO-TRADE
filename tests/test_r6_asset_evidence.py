from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from autotrade.brokers.alpaca_paper_asset import AlpacaPaperEquityAssetAttestation
from autotrade.brokers.alpaca_paper_asset_evidence import (
    PaperAssetEvidenceError,
    PaperAssetEvidenceStore,
)
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace


NOW = datetime(2026, 8, 12, 3, 5, tzinfo=timezone.utc)


def h(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def account() -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id="7ca57c2a-1b8f-4e18-9414-cb88b80227c7",
        account_reference=h("asset-evidence-account"),
        credential_reference=h("paper-key"),
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
        credential_reference=current.credential_reference,
        observed_at=NOW,
        request_id="req-asset",
        response_sha256=h("asset-response"),
        source_host="paper-api.alpaca.markets",
        source_path="/v2/assets/AAPL",
    )


def setup(tmp_path):
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    workspace.write_account_attestation(account())
    store = PaperAssetEvidenceStore(workspace)
    attestation = asset()
    store.write(attestation)
    return workspace, store, attestation


def test_asset_evidence_requires_account_first(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    with pytest.raises(PaperAssetEvidenceError, match="account attestation"):
        PaperAssetEvidenceStore(workspace).write(asset())


def test_asset_evidence_round_trips_without_secret_or_authority(tmp_path) -> None:
    _, store, attestation = setup(tmp_path)
    assert store.read() == attestation
    raw = store.path.read_text(encoding="utf-8")
    assert '"network_method": "GET"' in raw
    assert '"credentials_persisted": false' in raw
    assert '"broker_mutation_performed": false' in raw
    assert '"execution_authorized": false' in raw
    assert '"capital_authority": "NONE"' in raw
    assert '"profitability_claim": false' in raw
    assert '"live_trading": "BLOCKED"' in raw


def test_asset_evidence_rejects_account_binding_mismatch(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    workspace.write_account_attestation(account())
    mismatched = replace(asset(), account_attestation_fingerprint="b" * 64)
    with pytest.raises(PaperAssetEvidenceError, match="account attestation"):
        PaperAssetEvidenceStore(workspace).write(mismatched)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("environment", "LIVE"),
        ("network_method", "POST"),
        ("credentials_persisted", True),
        ("broker_mutation_performed", True),
        ("execution_authorized", True),
        ("capital_authority", "TRADING"),
        ("profitability_claim", True),
        ("live_trading", "ENABLED"),
    ],
)
def test_asset_evidence_tamper_is_fail_closed(tmp_path, key, value) -> None:
    _, store, _ = setup(tmp_path)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload[key] = value
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PaperAssetEvidenceError):
        store.read()


def test_asset_evidence_rejects_business_field_tamper_via_fingerprint(tmp_path) -> None:
    _, store, _ = setup(tmp_path)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["price_increment"] = "0.02"
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PaperAssetEvidenceError, match="fingerprint"):
        store.read()


def test_asset_evidence_revalidates_denied_attribute_on_read(tmp_path) -> None:
    _, store, _ = setup(tmp_path)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["attributes"] = ["ipo"]
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PaperAssetEvidenceError, match="invalid"):
        store.read()
