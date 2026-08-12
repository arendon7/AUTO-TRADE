from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import json

import pytest

from autotrade.brokers.alpaca_paper_flat_account import PaperFlatAccountAttestation
from autotrade.brokers.alpaca_paper_flat_account_evidence import PaperFlatAccountEvidenceStore
from autotrade.brokers.alpaca_paper_market_data import AlpacaPaperEquityMarketAttestation
from autotrade.brokers.alpaca_paper_market_evidence import market_evidence_payload
from autotrade.brokers.alpaca_paper_market_readiness import (
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
        market=MarketSnapshot(
            symbol=symbol,
            bid=price - Decimal("0.01"),
            ask=price + Decimal("0.01"),
            last=price,
            observed_at=trade_at,
        ),
        feed="iex",
        currency="USD",
        quote_observed_at=quote_at,
        trade_observed_at=trade_at,
        received_at=NOW,
        response_sha256="a" * 64,
    )


def write_flat(workspace: PaperOperationalWorkspace, *, positions: int = 0, orders: int = 0) -> None:
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
        attested_at=NOW,
    )
    PaperFlatAccountEvidenceStore(workspace).write(flat)


def write_market(workspace: PaperOperationalWorkspace, market: AlpacaPaperEquityMarketAttestation) -> None:
    workspace.root.joinpath("market_snapshot.json").write_text(
        json.dumps(
            market_evidence_payload(market),
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_market_aware_readiness_requires_flat_account_after_account_preflight(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    workspace.write_account_attestation(attestation())

    report = inspect_market_aware_readiness(root=workspace.root, now=NOW)

    assert report["phase"] == FLAT_ACCOUNT_PREFLIGHT_REQUIRED
    assert report["next_action"] == FLAT_ACCOUNT_NEXT_ACTION
    assert report["account_attested"] is True
    assert report["flat_account_evidence_present"] is False
    assert report["network_used"] is False
    assert report["broker_write_performed"] is False
    assert report["execution_authorized"] is False
    assert report["live_trading"] == "BLOCKED"


def test_market_aware_readiness_inserts_market_get_only_after_flat_account(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    workspace.write_account_attestation(attestation())
    write_flat(workspace)

    report = inspect_market_aware_readiness(root=workspace.root, now=NOW)

    assert report["phase"] == MARKET_DATA_PREFLIGHT_REQUIRED
    assert report["next_action"] == MARKET_DATA_NEXT_ACTION
    assert report["flat_account_clean_for_first_canary"] is True
    assert report["flat_account_position_count"] == 0
    assert report["flat_account_open_order_count"] == 0
    assert report["market_evidence_present"] is False


def test_market_aware_readiness_blocks_existing_paper_exposure(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    workspace.write_account_attestation(attestation())
    write_flat(workspace, positions=1)

    report = inspect_market_aware_readiness(root=workspace.root, now=NOW)

    assert report["phase"] == "BLOCKED_EXISTING_PAPER_EXPOSURE"
    assert report["next_action"] == "STOP_AND_REVIEW_EXISTING_PAPER_EXPOSURE_MANUALLY"
    assert report["flat_account_clean_for_first_canary"] is False
    assert report["execution_authorized"] is False


def test_market_aware_readiness_allows_offline_preparation_only_after_flat_and_market(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    workspace.write_account_attestation(attestation())
    write_flat(workspace)
    market = equity_market()
    write_market(workspace, market)

    report = inspect_market_aware_readiness(root=workspace.root, now=NOW)

    assert report["phase"] == "PREPARATION_REQUIRED"
    assert report["next_action"] == "RUN_SEPARATE_OFFLINE_CANARY_PREPARATION"
    assert report["flat_account_clean_for_first_canary"] is True
    assert report["market_evidence_present"] is True
    assert report["market_symbol"] == "AAPL"
    assert report["market_feed"] == "iex"
    assert report["market_currency"] == "USD"
    assert isinstance(report["market_fingerprint"], str)
    assert report["execution_authorized"] is False


def test_market_aware_readiness_rejects_tampered_market_artifact(tmp_path) -> None:
    workspace = PaperOperationalWorkspace.initialize(tmp_path / "workspace")
    workspace.write_account_attestation(attestation())
    write_flat(workspace)
    payload = market_evidence_payload(equity_market())
    payload["live_trading"] = "ENABLED"
    workspace.root.joinpath("market_snapshot.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(PaperReadinessIntegrityError, match="persisted equity market evidence is invalid"):
        inspect_market_aware_readiness(root=workspace.root, now=NOW)


def test_market_aware_readiness_cross_checks_prepared_package_market_fingerprint(tmp_path) -> None:
    preparer, workspace, _, submission, permit = build(tmp_path)
    run_prepare(preparer, submission, permit)
    write_market(workspace, equity_market(symbol="MSFT", last="420.00"))

    with pytest.raises(PaperReadinessIntegrityError, match="market fingerprint does not match"):
        inspect_market_aware_readiness(
            root=workspace.root,
            now=NOW + timedelta(seconds=1),
        )


def test_market_aware_readiness_keeps_legacy_prepared_workspace_non_authorizing(tmp_path) -> None:
    preparer, workspace, _, submission, permit = build(tmp_path)
    run_prepare(preparer, submission, permit)

    report = inspect_market_aware_readiness(
        root=workspace.root,
        now=NOW + timedelta(seconds=1),
    )

    assert report["phase"] == "HUMAN_DECISION_REQUIRED"
    assert report["market_evidence_present"] is False
    assert report["execution_authorized"] is False
    assert report["broker_write_performed"] is False
