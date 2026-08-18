from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

from autotrade.brokers.alpaca_paper_flat_account import PaperFlatAccountAttestation
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperAccountAttestation,
    AlpacaPaperCredentials,
)
from autotrade.domain import PortfolioSnapshot
from autotrade.persistence import SQLitePortfolioStore, SQLiteRuntime
from autotrade.risk_state import SQLiteR2SafetyStateStore

from scripts.mac_crypto_cold_start_portfolio_bootstrap import (
    BOOTSTRAP_MANIFEST_NAME,
    CryptoColdStartPortfolioBootstrapError,
    bootstrap_cold_start_portfolio,
)
from scripts.mac_crypto_health_commissioning import (
    COMMISSIONING_KILL_REASON,
    commission_health_core,
)


NOW = datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc)
ACCOUNT_ID = "11111111-2222-3333-4444-555555555555"
ACCOUNT_REFERENCE = "a" * 64
KEY_ID = "PKTESTCOLDSTART1234567890"
SECRET = "cold-start-secret-not-persisted"
CREDENTIALS = AlpacaPaperCredentials(key_id=KEY_ID, secret_key=SECRET)


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "account_attestation.json").write_text(
        json.dumps(
            {
                "environment": "PAPER",
                "account_id": ACCOUNT_ID,
                "credentials_persisted": False,
            }
        ),
        encoding="utf-8",
    )
    commission_health_core(workspace_path=root, now=NOW)
    return root


def _account(*, credential_reference: str | None = None, portfolio_value: str = "100000"):
    return AlpacaPaperAccountAttestation(
        account_id=ACCOUNT_ID,
        account_reference=ACCOUNT_REFERENCE,
        credential_reference=credential_reference or CREDENTIALS.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal(portfolio_value),
        shorting_enabled=False,
        attested_at=NOW,
        request_id="req-account-cold-start",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )


def _flat(account: AlpacaPaperAccountAttestation, *, positions=0, open_orders=0, credential_reference=None):
    return PaperFlatAccountAttestation(
        account_attestation_fingerprint=account.fingerprint,
        credential_reference=credential_reference or account.credential_reference,
        position_count=positions,
        open_order_count=open_orders,
        positions_response_hash="b" * 64,
        orders_response_hash="c" * 64,
        positions_request_id="req-positions-cold-start",
        orders_request_id="req-orders-cold-start",
        attested_at=NOW,
    )


class _AccountGateway:
    def __init__(self, attestation):
        self.attestation = attestation
        self.calls = 0

    def attest_account(self, **kwargs):
        self.calls += 1
        assert kwargs["expected_account_id"] == ACCOUNT_ID
        assert kwargs["credentials"] == CREDENTIALS
        return self.attestation


class _FlatGateway:
    def __init__(self, attestation):
        self.attestation = attestation
        self.calls = 0

    def attest_flatness(self, **kwargs):
        self.calls += 1
        return self.attestation


def _run(root: Path, *, account=None, flat=None):
    account = account or _account()
    flat = flat or _flat(account)
    account_gateway = _AccountGateway(account)
    flat_gateway = _FlatGateway(flat)
    result = bootstrap_cold_start_portfolio(
        workspace_path=root,
        credentials=CREDENTIALS,
        now=NOW,
        account_gateway=account_gateway,
        flat_gateway=flat_gateway,
    )
    return result, account_gateway, flat_gateway


def test_flat_paper_account_bootstraps_version_one_zero_portfolio_and_keeps_kill_switch(tmp_path) -> None:
    root = _workspace(tmp_path)
    runtime = SQLiteRuntime(root / "core.sqlite3")
    safety_before = SQLiteR2SafetyStateStore(runtime).get()

    result, account_gateway, flat_gateway = _run(root)

    assert result["status"] == "CRYPTO_COLD_START_PORTFOLIO_BOOTSTRAPPED_HEALTH_STILL_REQUIRED"
    assert result["portfolio_created"] is True
    assert result["portfolio_version"] == 1
    assert result["portfolio_equity"] == "100000"
    assert result["gross_exposure"] == "0"
    assert result["net_exposure"] == "0"
    assert result["open_orders"] == 0
    assert result["position_count"] == 0
    assert result["broker_open_order_count"] == 0
    assert result["broker_reads"] == 3
    assert result["credentials_read"] is True
    assert result["credentials_persisted"] is False
    assert result["broker_write_performed"] is False
    assert result["external_post_authorized"] is False
    assert result["approval_consumed"] is False
    assert result["oms_submitting"] is False
    assert result["lifecycle_unknown"] is False
    assert result["capital_authority"] == "NONE"
    assert result["live_trading"] == "BLOCKED"
    assert account_gateway.calls == 1
    assert flat_gateway.calls == 1

    portfolio = SQLitePortfolioStore(runtime).get()
    assert portfolio.version == 1
    assert portfolio.snapshot.equity == Decimal("100000")
    assert portfolio.snapshot.gross_exposure == 0
    assert portfolio.snapshot.net_exposure == 0
    assert portfolio.snapshot.open_orders == 0
    assert portfolio.snapshot.reconciliation_ok is True
    assert portfolio.snapshot.broker_state_known is True

    safety_after = SQLiteR2SafetyStateStore(runtime).get()
    assert safety_after == safety_before
    assert safety_after.kill_switch_active is True
    assert safety_after.kill_switch_reason == COMMISSIONING_KILL_REASON

    conn = runtime.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM health_state_v2").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM health_bridge_state").fetchone()[0] == 0
    finally:
        conn.close()
    manifest = json.loads((root / BOOTSTRAP_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["credentials_persisted"] is False
    assert KEY_ID not in json.dumps(manifest)
    assert SECRET not in json.dumps(manifest)


def test_bootstrap_is_idempotent_with_same_flat_account_and_does_not_version_portfolio(tmp_path) -> None:
    root = _workspace(tmp_path)
    first, _, _ = _run(root)
    second, _, _ = _run(root)
    assert first["portfolio_version"] == second["portfolio_version"] == 1
    assert first["portfolio_snapshot_id"] == second["portfolio_snapshot_id"]
    runtime = SQLiteRuntime(root / "core.sqlite3")
    conn = runtime.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM portfolio_state").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM ledger_events WHERE event_id='r6-crypto-cold-start-portfolio-bootstrap-v1'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_nonflat_broker_evidence_blocks_before_portfolio_creation(tmp_path) -> None:
    root = _workspace(tmp_path)
    account = _account()
    with pytest.raises(CryptoColdStartPortfolioBootstrapError, match="not flat"):
        _run(root, account=account, flat=_flat(account, positions=1))
    runtime = SQLiteRuntime(root / "core.sqlite3")
    conn = runtime.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM portfolio_state").fetchone()[0] == 0
    finally:
        conn.close()


def test_flat_evidence_with_wrong_credential_provenance_blocks(tmp_path) -> None:
    root = _workspace(tmp_path)
    account = _account()
    flat = _flat(account, credential_reference="d" * 64)
    with pytest.raises(CryptoColdStartPortfolioBootstrapError, match="credential provenance"):
        _run(root, account=account, flat=flat)


def test_preexisting_nonzero_portfolio_blocks_without_overwrite(tmp_path) -> None:
    root = _workspace(tmp_path)
    runtime = SQLiteRuntime(root / "core.sqlite3")
    SQLitePortfolioStore(runtime).initialize(
        PortfolioSnapshot(
            snapshot_id=f"r6-crypto-paper-cold-start:{ACCOUNT_REFERENCE[:20]}",
            equity=Decimal("100000"),
            gross_exposure=Decimal("10"),
            net_exposure=Decimal("10"),
            daily_pnl=Decimal("0"),
            drawdown=Decimal("0"),
            open_orders=0,
            signed_position_notional_by_symbol={"BTC/USD": Decimal("10")},
            strategy_gross_exposure={"legacy": Decimal("10")},
            strategy_signed_position_notional_by_symbol={"legacy": {"BTC/USD": Decimal("10")}},
            reconciliation_ok=True,
            broker_state_known=True,
        ),
        now=NOW,
    )
    with pytest.raises(CryptoColdStartPortfolioBootstrapError, match="not flat"):
        _run(root)
    current = SQLitePortfolioStore(runtime).get()
    assert current.snapshot.gross_exposure == Decimal("10")


def test_tampered_bootstrap_manifest_fails_closed(tmp_path) -> None:
    root = _workspace(tmp_path)
    _run(root)
    path = root / BOOTSTRAP_MANIFEST_NAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["health_created"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CryptoColdStartPortfolioBootstrapError, match="manifest hash mismatch"):
        _run(root)


def test_bootstrap_requires_verified_health_commissioning(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "account_attestation.json").write_text(
        json.dumps(
            {
                "environment": "PAPER",
                "account_id": ACCOUNT_ID,
                "credentials_persisted": False,
            }
        ),
        encoding="utf-8",
    )
    SQLiteRuntime(root / "core.sqlite3")
    with pytest.raises(CryptoColdStartPortfolioBootstrapError, match="commissioning manifest"):
        _run(root)
