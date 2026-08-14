from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from test_r6_paper_crypto_canary_coordinator import NOW, _account, _asset, _market


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/mac_crypto_canary_preview.py"
SPEC = importlib.util.spec_from_file_location("mac_crypto_canary_preview_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
preview = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preview
SPEC.loader.exec_module(preview)


def test_preview_has_stricter_five_dollar_cap_and_deterministic_reference_protection() -> None:
    assert preview.PREVIEW_MAX_NOTIONAL == Decimal("5")
    assert preview.QUALIFICATION_STOP_BPS == Decimal("100")
    assert preview.QUALIFICATION_LIMIT_BPS == Decimal("150")
    stop, limit = preview._qualification_protection_reference(Decimal("100000"), Decimal("1"))
    assert stop == Decimal("99000")
    assert limit == Decimal("98500")
    assert limit <= stop


def test_preview_source_has_no_operator_issuance_or_writer_path() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "alpaca_paper_crypto_writer",
        "alpaca_paper_crypto_pre_io",
        "FinalGuardedCryptoEntryTransport",
        "record_operator_approval(",
        ".consume(",
        "stage_external_submission",
        'os.environ[WRITE_ENV] = "ENABLED"',
        'env[WRITE_ENV] = "ENABLED"',
    ):
        assert forbidden not in source
    for required in (
        'PREVIEW_MAX_NOTIONAL = Decimal("5")',
        'os.environ.get(WRITE_ENV) == "ENABLED"',
        'raise CryptoPaperCanaryPreviewError("canary preview refuses R6_EXTERNAL_PAPER_WRITE=ENABLED")',
        '"mode": "DRY_RUN_NO_POST"',
        '"broker_write_performed": False',
        '"external_post_authorized": False',
        '"operator_approval_authority": "NONE"',
        '"reusable_for_real_execution": False',
        '"blind_retry": False',
        '"STOP_LIMIT_IS_NOT_A_GUARANTEED_EXIT_OR_MAX_LOSS"',
    ):
        assert required in source


def test_preview_runs_certified_coordinator_in_temporary_runtime_without_post(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    account = _account(observed=NOW)
    (workspace / "account_attestation.json").write_text(
        json.dumps(
            {
                "environment": "PAPER",
                "credentials_persisted": False,
                "account_id": account.account_id,
            }
        ),
        encoding="utf-8",
    )
    asset = replace(
        _asset(account, observed=NOW, price_increment=Decimal("1")),
        min_order_size=Decimal("0.00001"),
        min_trade_increment=Decimal("0.00001"),
    )
    market = _market(observed=NOW)
    flat = SimpleNamespace(clean_for_first_canary=True, position_count=0, open_order_count=0)

    class AccountGateway:
        def __init__(self, *args, **kwargs): pass
        def attest_account(self, **kwargs): return account

    class AssetGateway:
        def __init__(self, *args, **kwargs): pass
        def attest_asset(self, **kwargs): return asset

    class FlatGateway:
        def __init__(self, *args, **kwargs): pass
        def attest_flatness(self, **kwargs): return flat

    class MarketGateway:
        def __init__(self, *args, **kwargs): pass
        def attest_snapshot(self, **kwargs): return market

    monkeypatch.setattr(preview, "AlpacaPaperAccountGateway", AccountGateway)
    monkeypatch.setattr(preview, "AlpacaPaperCryptoAssetGateway", AssetGateway)
    monkeypatch.setattr(preview, "AlpacaPaperFlatAccountGateway", FlatGateway)
    monkeypatch.setattr(preview, "AlpacaPaperCryptoMarketDataGateway", MarketGateway)
    monkeypatch.delenv(preview.WRITE_ENV, raising=False)

    result = preview.run(
        workspace_path=workspace,
        credentials=AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret"),
        now=NOW,
        symbol="BTC/USD",
    )
    assert result["status"] == "CRYPTO_PAPER_QUALIFICATION_PREVIEW_PASS"
    assert result["mode"] == "DRY_RUN_NO_POST"
    assert result["entry"]["payload"]["side"] == "buy"
    assert result["entry"]["payload"]["type"] == "limit"
    assert result["entry"]["payload"]["time_in_force"] == "ioc"
    assert Decimal(result["entry"]["notional"]) <= Decimal("5")
    assert result["entry"]["network_write_authorized"] is False
    assert result["operator"]["approval_recorded"] is False
    assert result["operator"]["decision_consumed"] is False
    assert result["operator"]["reusable_for_real_execution"] is False
    assert result["operator"]["dry_run_challenge"].startswith("APPROVE CRYPTO PAPER BTC/USD ")
    assert result["protection"]["quantity_rule"] == "EXACT_CONFIRMED_NET_LONG_AFTER_RECONCILIATION"
    assert result["ambiguity_policy"]["blind_retry"] is False
    assert result["broker_write_performed"] is False
    assert result["external_post_authorized"] is False
    assert result["capital_authority"] == "NONE"
    assert result["live_trading"] == "BLOCKED"


def test_preview_refuses_second_pair_and_enabled_writer(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    credentials = AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret")
    with pytest.raises(preview.CryptoPaperCanaryPreviewError, match="fixed to BTC/USD"):
        preview.run(workspace_path=workspace, credentials=credentials, now=NOW, symbol="ETH/USD")

    monkeypatch.setenv(preview.WRITE_ENV, "ENABLED")
    with pytest.raises(preview.CryptoPaperCanaryPreviewError, match="refuses"):
        preview.run(workspace_path=workspace, credentials=credentials, now=NOW, symbol="BTC/USD")
