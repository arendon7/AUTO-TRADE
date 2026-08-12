from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import json

import pytest

from autotrade.brokers.alpaca_paper_asset import AlpacaPaperEquityAssetAttestation
from autotrade.brokers.alpaca_paper_asset_evidence import PaperAssetEvidenceStore
from autotrade.brokers.alpaca_paper_flat_account import PaperFlatAccountAttestation
from autotrade.brokers.alpaca_paper_flat_account_evidence import PaperFlatAccountEvidenceStore
from autotrade.brokers.alpaca_paper_market_data import AlpacaPaperEquityMarketAttestation
from autotrade.brokers.alpaca_paper_market_evidence import market_evidence_payload
from autotrade.brokers.alpaca_paper_market_readiness import (
    ASSET_MAX_AGE_SECONDS,
    ASSET_NEXT_ACTION,
    ASSET_PREFLIGHT_REQUIRED,
    BLOCKED_STALE_ASSET_EVIDENCE,
    BLOCKED_STALE_FLAT_ACCOUNT_EVIDENCE,
    FLAT_ACCOUNT_MAX_AGE_SECONDS,
    FLAT_ACCOUNT_NEXT_ACTION,
    FLAT_ACCOUNT_PREFLIGHT_REQUIRED,
    MARKET_DATA_NEXT_ACTION,
    MARKET_DATA_PREFLIGHT_REQUIRED,
    inspect_market_aware_readiness,
)
from autotrade.brokers.alpaca_paper_operational import PaperOperationalWorkspace
from autotrade.brokers.alpaca_paper_readiness import PaperReadinessIntegrityError
from autotrade.domain import MarketSnapshot
from test_r6_operational_prepare import NOW, build, run_prepare
from test_r6_paper_canary_coordinator import attestation


def equity_market(*, symbol: str = "AAPL", last: str = "189.11") -> AlpacaPaperEquityMarketAttestation:
    trade_at = NOW - timedelta(seconds=1)
    quote_at = NOW - timedelta(milliseconds=500)
    price = Decimal(last)
    return AlpacaPaperEquityMarketAttestation(
        market=MarketSnapshot(symbol=symbol, bid=price - Decimal("0.01"), ask=price + Decimal("0.01"), last=price, observed_at=trade_at),
        feed="iex", currency="USD", quote_observed_at=quote_at, trade_observed_at=trade_at,
        received_at=NOW, response_sha256="a" * 64,
    )


def write_asset(workspace: PaperOperationalWorkspace, *, symbol: str = "AAPL", observed_at=None) -> None:
    account = attestation()
    asset = AlpacaPaperEquityAssetAttestation(
        symbol=symbol,
        asset_id=f"asset-{symbol.lower()}",
        asset_class="us_equity",
        exchange="NASDAQ",
        status="active",
        tradable=True,
        fractionable=True,
        min_order_size=Decimal("0.000001"),
        min_trade_increment=Decimal("0.000001"),
        price_increment=Decimal("0.01"),
        attributes=(),
        account_attestation_fingerprint=account.fingerprint,
        credential_reference=account.credential_reference,
        observed_at=observed_at or NOW,
        request_id=f"req-asset-{symbol.lower()}",
        response_sha256="d" * 64,
        source_host="paper-api.alpaca.markets",
        source_path=f"/v2/assets/{symbol}",
    )
    PaperAssetEvidenceStore(workspace).write(asset)


def write_flat(workspace: PaperOperationalWorkspace, *, positions: int = 0, orders: int = 0, attested_at=None) -> None:
    account = attestation()
    flat = PaperFlatAccountAttestation(
        account_attestation_fingerprint=account.fingerprint,
        credential_reference=account.credential_reference,
        position_count=positions,
        open_order_count=orders,
        positions_response_hash="b" * 64,
        orders_response_hash="c" * 64,
        positions_request_id="req-positions",
        orders_request_id="req-orders",
        attested_at=attested_at or NOW,
    )
    PaperFlatAccountEvidenceStore(workspace).write(flat)


def write_market(workspace: PaperOperationalWorkspace, market: AlpacaPaperEquityMarketAttestation) -> None:
    workspace.root.joinpath("market_snapshot.json").write_text(
        json.dumps(market_evidence_payload(market), sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def initialized(tmp_path):
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    workspace.write_account_attestation(attestation())
    return workspace


def test_readiness_requires_asset_after_account(tmp_path) -> None:
    workspace = initialized(tmp_path)
    report = inspect_market_aware_readiness(root=workspace.root, now=NOW)
    assert report["phase"] == ASSET_PREFLIGHT_REQUIRED
    assert report["next_action"] == ASSET_NEXT_ACTION
    assert report["asset_evidence_present"] is False
    assert report["execution_authorized"] is False


def test_readiness_requires_flat_only_after_asset(tmp_path) -> None:
    workspace = initialized(tmp_path)
    write_asset(workspace)
    report = inspect_market_aware_readiness(root=workspace.root, now=NOW)
    assert report["phase"] == FLAT_ACCOUNT_PREFLIGHT_REQUIRED
    assert report["next_action"] == FLAT_ACCOUNT_NEXT_ACTION
    assert report["asset_symbol"] == "AAPL"
    assert report["flat_account_evidence_present"] is False


def test_readiness_requires_market_only_after_asset_and_flat(tmp_path) -> None:
    workspace = initialized(tmp_path)
    write_asset(workspace)
    write_flat(workspace)
    report = inspect_market_aware_readiness(root=workspace.root, now=NOW)
    assert report["phase"] == MARKET_DATA_PREFLIGHT_REQUIRED
    assert report["next_action"] == MARKET_DATA_NEXT_ACTION
    assert report["flat_account_clean_for_first_canary"] is True
    assert report["flat_account_age_seconds"] == 0


def test_readiness_blocks_stale_asset(tmp_path) -> None:
    workspace = initialized(tmp_path)
    write_asset(workspace, observed_at=NOW - timedelta(seconds=ASSET_MAX_AGE_SECONDS + 1))
    report = inspect_market_aware_readiness(root=workspace.root, now=NOW)
    assert report["phase"] == BLOCKED_STALE_ASSET_EVIDENCE
    assert report["execution_authorized"] is False


def test_readiness_blocks_stale_flat(tmp_path) -> None:
    workspace = initialized(tmp_path)
    write_asset(workspace)
    write_flat(workspace, attested_at=NOW - timedelta(seconds=FLAT_ACCOUNT_MAX_AGE_SECONDS + 1))
    report = inspect_market_aware_readiness(root=workspace.root, now=NOW)
    assert report["phase"] == BLOCKED_STALE_FLAT_ACCOUNT_EVIDENCE
    assert report["next_action"] == "CREATE_NEW_WORKSPACE_AND_REPEAT_ACCOUNT_ASSET_FLAT_MARKET_PREFLIGHTS"


def test_readiness_blocks_existing_paper_exposure(tmp_path) -> None:
    workspace = initialized(tmp_path)
    write_asset(workspace)
    write_flat(workspace, positions=1)
    report = inspect_market_aware_readiness(root=workspace.root, now=NOW)
    assert report["phase"] == "BLOCKED_EXISTING_PAPER_EXPOSURE"
    assert report["execution_authorized"] is False


def test_readiness_reaches_preparation_only_after_all_four_get_gates(tmp_path) -> None:
    workspace = initialized(tmp_path)
    write_asset(workspace)
    write_flat(workspace)
    write_market(workspace, equity_market())
    report = inspect_market_aware_readiness(root=workspace.root, now=NOW)
    assert report["phase"] == "PREPARATION_REQUIRED"
    assert report["asset_symbol"] == "AAPL"
    assert report["market_symbol"] == "AAPL"
    assert report["execution_authorized"] is False


def test_readiness_rejects_market_symbol_not_bound_to_asset(tmp_path) -> None:
    workspace = initialized(tmp_path)
    write_asset(workspace, symbol="AAPL")
    write_flat(workspace)
    write_market(workspace, equity_market(symbol="MSFT", last="420.00"))
    with pytest.raises(PaperReadinessIntegrityError, match="market symbol does not match"):
        inspect_market_aware_readiness(root=workspace.root, now=NOW)


def test_readiness_rejects_tampered_market_artifact(tmp_path) -> None:
    workspace = initialized(tmp_path)
    write_asset(workspace)
    write_flat(workspace)
    payload = market_evidence_payload(equity_market())
    payload["live_trading"] = "ENABLED"
    workspace.root.joinpath("market_snapshot.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PaperReadinessIntegrityError, match="persisted equity market evidence is invalid"):
        inspect_market_aware_readiness(root=workspace.root, now=NOW)


def test_prepared_workspace_cannot_bypass_missing_asset_gate(tmp_path) -> None:
    preparer, workspace, _, submission, permit = build(tmp_path)
    run_prepare(preparer, submission, permit)
    report = inspect_market_aware_readiness(root=workspace.root, now=NOW + timedelta(seconds=1))
    assert report["phase"] == ASSET_PREFLIGHT_REQUIRED
    assert report["next_action"] == ASSET_NEXT_ACTION
    assert report["execution_authorized"] is False


def test_prepared_workspace_cannot_bypass_missing_flat_gate_after_asset(tmp_path) -> None:
    preparer, workspace, _, submission, permit = build(tmp_path)
    run_prepare(preparer, submission, permit)
    write_asset(workspace)
    report = inspect_market_aware_readiness(root=workspace.root, now=NOW + timedelta(seconds=1))
    assert report["phase"] == FLAT_ACCOUNT_PREFLIGHT_REQUIRED
    assert report["next_action"] == FLAT_ACCOUNT_NEXT_ACTION


def test_prepared_workspace_cannot_bypass_missing_market_gate(tmp_path) -> None:
    preparer, workspace, _, submission, permit = build(tmp_path)
    run_prepare(preparer, submission, permit)
    write_asset(workspace)
    write_flat(workspace)
    report = inspect_market_aware_readiness(root=workspace.root, now=NOW + timedelta(seconds=1))
    assert report["phase"] == MARKET_DATA_PREFLIGHT_REQUIRED
    assert report["next_action"] == MARKET_DATA_NEXT_ACTION


def test_prepared_workspace_still_cross_checks_package_market_fingerprint(tmp_path) -> None:
    preparer, workspace, _, submission, permit = build(tmp_path)
    run_prepare(preparer, submission, permit)
    write_asset(workspace, symbol="MSFT")
    write_flat(workspace)
    write_market(workspace, equity_market(symbol="MSFT", last="420.00"))
    with pytest.raises(PaperReadinessIntegrityError, match="market fingerprint does not match"):
        inspect_market_aware_readiness(root=workspace.root, now=NOW + timedelta(seconds=1))
