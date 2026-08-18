from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from autotrade.brokers.alpaca_paper_flat_account import PaperFlatAccountAttestation
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperAccountAttestation, AlpacaPaperCredentials
from autotrade.persistence import SQLitePortfolioStore, SQLiteRuntime
from autotrade.risk_state import SQLiteR2SafetyStateStore

from scripts.mac_crypto_cold_start_portfolio_bootstrap import bootstrap_cold_start_portfolio
from scripts.mac_crypto_cold_start_qualification_attestation import (
    ATTESTATION_DIR,
    CryptoColdStartQualificationAttestationError,
    attest_cold_start_qualification,
)
from scripts.mac_crypto_health_commissioning import COMMISSIONING_KILL_REASON, commission_health_core


NOW = datetime(2026, 8, 18, 1, 30, tzinfo=timezone.utc)
ACCOUNT_ID = "11111111-2222-3333-4444-555555555555"
ACCOUNT_REFERENCE = "4" * 64
KEY_ID = "PKTESTATTEST123456789"
SECRET = "attestation-secret-never-persisted"
CREDENTIALS = AlpacaPaperCredentials(key_id=KEY_ID, secret_key=SECRET)


def _account(
    *,
    account_reference: str = ACCOUNT_REFERENCE,
    credential_reference: str | None = None,
    portfolio_value: str = "100000",
    observed_at: datetime = NOW,
) -> AlpacaPaperAccountAttestation:
    return AlpacaPaperAccountAttestation(
        account_id=ACCOUNT_ID,
        account_reference=account_reference,
        credential_reference=credential_reference or CREDENTIALS.credential_reference,
        status="ACTIVE",
        currency="USD",
        buying_power=Decimal("100000"),
        portfolio_value=Decimal(portfolio_value),
        shorting_enabled=False,
        attested_at=observed_at,
        request_id="req-account-attestation",
        source_host="paper-api.alpaca.markets",
        source_path="/v2/account",
    )


def _flat(
    account: AlpacaPaperAccountAttestation,
    *,
    positions: int = 0,
    open_orders: int = 0,
    credential_reference: str | None = None,
    observed_at: datetime = NOW,
) -> PaperFlatAccountAttestation:
    return PaperFlatAccountAttestation(
        account_attestation_fingerprint=account.fingerprint,
        credential_reference=credential_reference or account.credential_reference,
        position_count=positions,
        open_order_count=open_orders,
        positions_response_hash="5" * 64,
        orders_response_hash="6" * 64,
        positions_request_id="req-positions-attestation",
        orders_request_id="req-orders-attestation",
        attested_at=observed_at,
    )


class _AccountGateway:
    def __init__(self, attestation: AlpacaPaperAccountAttestation):
        self.attestation = attestation
        self.calls = 0

    def attest_account(self, **kwargs):
        self.calls += 1
        assert kwargs["expected_account_id"] == ACCOUNT_ID
        assert kwargs["credentials"] == CREDENTIALS
        return self.attestation


class _FlatGateway:
    def __init__(self, attestation: PaperFlatAccountAttestation):
        self.attestation = attestation
        self.calls = 0

    def attest_flatness(self, **kwargs):
        self.calls += 1
        return self.attestation


def _preview_result(*, notional: str = "2.0001") -> dict[str, object]:
    return {
        "status": "CRYPTO_PAPER_QUALIFICATION_PREVIEW_PASS",
        "environment": "PAPER",
        "mode": "DRY_RUN_NO_POST",
        "symbol": "BTC/USD",
        "broker_reads": 6,
        "account_flat": True,
        "entry": {
            "payload": {
                "symbol": "BTC/USD",
                "side": "buy",
                "type": "limit",
                "time_in_force": "ioc",
                "qty": "0.000031",
                "limit_price": "64520",
                "client_order_id": "atr6-entry-test",
            },
            "quantity": "0.000031",
            "limit_price": "64520",
            "notional": notional,
            "target_notional": "2",
            "minimum_buy_market_value": "1",
            "broker_min_order_size": "0.00001",
            "broker_min_trade_increment": "0.000001",
            "safety_hard_cap": "5",
            "coordinator_effective_cap": "5",
            "dry_run_client_order_id": "atr6-entry-test",
            "payload_hash": "7" * 64,
            "package_hash": "8" * 64,
            "oms_status": "PENDING_SUBMIT",
            "network_write_authorized": False,
        },
        "operator": {
            "approval_recorded": False,
            "decision_consumed": False,
            "dry_run_attempt_id": "preview-test",
            "dry_run_challenge": "APPROVE CRYPTO PAPER BTC/USD deadbeef0000",
            "execution_deadline": "2026-08-18T01:31:00+00:00",
            "reusable_for_real_execution": False,
        },
        "protection": {
            "model": "STOP_LIMIT",
            "time_in_force": "gtc",
            "dry_run_client_order_id": "atr6-protection-test",
            "qualification_stop_bps_below_fill": "100",
            "qualification_limit_bps_below_fill": "150",
            "quantity_rule": "EXACT_CONFIRMED_NET_LONG_AFTER_RECONCILIATION",
            "warning": "STOP_LIMIT_IS_NOT_A_GUARANTEED_EXIT_OR_MAX_LOSS",
        },
        "ambiguity_policy": {
            "unknown_before_io": True,
            "blind_retry": False,
            "on_timeout_or_ambiguous_ack": "RECONCILE_ONLY",
            "order_404_retry_permission": False,
        },
        "capital_safety": "APPROVED",
        "broker_write_performed": False,
        "external_post_authorized": False,
        "operator_approval_authority": "NONE",
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "profitability_claim": False,
    }


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir(parents=True)
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
    account = _account()
    bootstrap_cold_start_portfolio(
        workspace_path=root,
        credentials=CREDENTIALS,
        now=NOW,
        account_gateway=_AccountGateway(account),
        flat_gateway=_FlatGateway(_flat(account)),
    )
    return root


def _attest(
    root: Path,
    *,
    account: AlpacaPaperAccountAttestation | None = None,
    flat: PaperFlatAccountAttestation | None = None,
    preview_result: dict[str, object] | None = None,
):
    account = account or _account()
    flat = flat or _flat(account)
    account_gateway = _AccountGateway(account)
    flat_gateway = _FlatGateway(flat)
    preview_calls: list[dict[str, object]] = []

    def runner(**kwargs):
        preview_calls.append(kwargs)
        return preview_result or _preview_result()

    result = attest_cold_start_qualification(
        workspace_path=root,
        credentials=CREDENTIALS,
        now=NOW,
        account_gateway=account_gateway,
        flat_gateway=flat_gateway,
        preview_runner=runner,
    )
    return result, account_gateway, flat_gateway, preview_calls


def test_nominal_attestation_binds_flat_portfolio_preview_and_keeps_all_execution_closed(tmp_path) -> None:
    root = _workspace(tmp_path)
    safety_before = SQLiteR2SafetyStateStore(SQLiteRuntime(root / "core.sqlite3")).get()

    result, account_gateway, flat_gateway, preview_calls = _attest(root)

    assert result["status"] == "CRYPTO_COLD_START_QUALIFICATION_ATTESTED_NO_EXECUTION"
    assert result["scope"] == "FIRST_TECHNICAL_CANARY_ONLY"
    assert result["symbol"] == "BTC/USD"
    assert result["broker_reads"] == 9
    assert account_gateway.calls == 1
    assert flat_gateway.calls == 1
    assert len(preview_calls) == 1
    assert preview_calls[0]["symbol"] == "BTC/USD"
    assert result["portfolio_version"] == 1
    assert result["portfolio_equity"] == "100000"
    assert result["portfolio_gross_exposure"] == "0"
    assert result["portfolio_net_exposure"] == "0"
    assert result["portfolio_open_orders"] == 0
    assert result["strategy_health_state_rows"] == 0
    assert result["portfolio_health_state_rows"] == 0
    assert result["health_bridge_rows"] == 0
    assert result["strategy_health_expected_missing"] is True
    assert result["portfolio_health_expected_missing"] is True
    assert result["health_override_authorized"] is False
    assert result["kill_switch_active"] is True
    assert result["kill_switch_reset"] is False
    assert result["preview_notional"] == "2.0001"
    assert result["preview_safety_hard_cap"] == "5"
    assert result["qualification_candidate"] is True
    assert result["qualification_completed"] is False
    assert result["profitability_evidence"] is False
    assert result["protection_required_after_reconciled_fill"] is True
    assert result["new_human_approval_required_for_any_future_execution"] is True
    assert result["approval_consumed"] is False
    assert result["final_guard_opened"] is False
    assert result["oms_submitting"] is False
    assert result["lifecycle_unknown"] is False
    assert result["credentials_persisted"] is False
    assert result["broker_write_performed"] is False
    assert result["external_post_authorized"] is False
    assert result["execution_authority"] == "NONE"
    assert result["capital_authority"] == "NONE"
    assert result["reusable_for_real_execution"] is False
    assert result["live_trading"] == "BLOCKED"

    safety_after = SQLiteR2SafetyStateStore(SQLiteRuntime(root / "core.sqlite3")).get()
    assert safety_after == safety_before
    attestation_path = Path(str(result["attestation_path"]))
    assert attestation_path.is_file()
    document = attestation_path.read_text(encoding="utf-8")
    assert KEY_ID not in document
    assert SECRET not in document
    payload = json.loads(document)
    assert payload["attestation_hash"] == result["attestation_hash"]
    assert payload["execution_authority"] == "NONE"
    assert payload["health_override_authorized"] is False


def test_attestation_refuses_nonflat_fresh_broker_account(tmp_path) -> None:
    root = _workspace(tmp_path)
    account = _account()
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="not flat"):
        _attest(root, account=account, flat=_flat(account, positions=1))


def test_attestation_refuses_stale_freshness_evidence(tmp_path) -> None:
    root = _workspace(tmp_path)
    stale_time = NOW - timedelta(seconds=6)
    stale_account = _account(observed_at=stale_time)
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="account evidence is stale"):
        _attest(root, account=stale_account, flat=_flat(stale_account, observed_at=stale_time))

    account = _account()
    stale_flat = _flat(account, observed_at=stale_time)
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="flat-account evidence is stale"):
        _attest(root, account=account, flat=stale_flat)


def test_attestation_refuses_account_or_credential_rebinding(tmp_path) -> None:
    root = _workspace(tmp_path)
    changed_account = _account(account_reference="9" * 64)
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="bootstrap and fresh PAPER account differ"):
        _attest(root, account=changed_account, flat=_flat(changed_account))

    changed_credential = _account(credential_reference="a" * 64)
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="bootstrap credential provenance differs"):
        _attest(root, account=changed_credential, flat=_flat(changed_credential))


def test_attestation_refuses_kill_switch_or_health_boundary_drift(monkeypatch, tmp_path) -> None:
    root = _workspace(tmp_path)
    runtime = SQLiteRuntime(root / "core.sqlite3")
    SQLiteR2SafetyStateStore(runtime).activate(reason="UNRELATED_REASON", now=NOW)
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="kill switch"):
        _attest(root)

    root2 = _workspace(tmp_path / "second")
    import scripts.mac_crypto_cold_start_qualification_attestation as module
    monkeypatch.setattr(module, "_health_counts", lambda _runtime: (1, 0))
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="Health and bridge"):
        _attest(root2)


def test_attestation_refuses_portfolio_version_or_exposure_drift(monkeypatch, tmp_path) -> None:
    root = _workspace(tmp_path)
    store = SQLitePortfolioStore(SQLiteRuntime(root / "core.sqlite3"))
    current = store.get()
    updated = store.compare_and_set(expected_version=1, snapshot=current.snapshot, now=NOW)
    assert updated is not None and updated.version == 2
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="version 1"):
        _attest(root)

    root2 = _workspace(tmp_path / "second")
    actual = SQLitePortfolioStore(SQLiteRuntime(root2 / "core.sqlite3")).get()
    nonflat_snapshot = replace(
        actual.snapshot,
        gross_exposure=Decimal("2"),
        net_exposure=Decimal("2"),
        signed_position_notional_by_symbol={"BTC/USD": Decimal("2")},
        strategy_gross_exposure={"test": Decimal("2")},
        strategy_signed_position_notional_by_symbol={"test": {"BTC/USD": Decimal("2")}},
    )

    class _FakePortfolioStore:
        def __init__(self, _runtime): pass
        def get(self): return SimpleNamespace(version=1, snapshot=nonflat_snapshot)

    import scripts.mac_crypto_cold_start_qualification_attestation as module
    monkeypatch.setattr(module, "SQLitePortfolioStore", _FakePortfolioStore)
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="not exactly flat"):
        _attest(root2)


def test_attestation_refuses_preview_scope_notional_or_authority_drift(tmp_path) -> None:
    root = _workspace(tmp_path)

    wrong_symbol = _preview_result()
    wrong_symbol["symbol"] = "ETH/USD"
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="exact BTC/USD"):
        _attest(root, preview_result=wrong_symbol)

    too_large = _preview_result(notional="5.01")
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="outside cold-start qualification bounds"):
        _attest(root, preview_result=too_large)

    write_enabled = _preview_result()
    assert isinstance(write_enabled["entry"], dict)
    write_enabled["entry"]["network_write_authorized"] = True
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="network write"):
        _attest(root, preview_result=write_enabled)

    approved = _preview_result()
    assert isinstance(approved["operator"], dict)
    approved["operator"]["approval_recorded"] = True
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="approval authority"):
        _attest(root, preview_result=approved)

    retry = _preview_result()
    assert isinstance(retry["ambiguity_policy"], dict)
    retry["ambiguity_policy"]["blind_retry"] = True
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="ambiguity policy"):
        _attest(root, preview_result=retry)


def test_attestation_refuses_writer_enabled_and_does_not_create_file(monkeypatch, tmp_path) -> None:
    root = _workspace(tmp_path)
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "ENABLED")
    with pytest.raises(CryptoColdStartQualificationAttestationError, match="refuses"):
        _attest(root)
    directory = root / ATTESTATION_DIR
    assert not directory.exists()
