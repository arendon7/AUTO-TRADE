from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/r6_connectivity_review_receipt.py"
SPEC = importlib.util.spec_from_file_location("r6_connectivity_review_receipt_cli", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def test_review_receipt_cli_refuses_write_enabled(monkeypatch) -> None:
    monkeypatch.setenv(cli._WRITE_ENV, "ENABLED")
    monkeypatch.delenv(cli._KEY_ENV, raising=False)
    monkeypatch.delenv(cli._SECRET_ENV, raising=False)
    with pytest.raises(SystemExit, match="WRITE=ENABLED"):
        cli._validate_offline_environment()


def test_review_receipt_cli_refuses_any_alpaca_credentials(monkeypatch) -> None:
    monkeypatch.setenv(cli._WRITE_ENV, "DISABLED")
    monkeypatch.setenv(cli._KEY_ENV, "paper-key")
    monkeypatch.delenv(cli._SECRET_ENV, raising=False)
    with pytest.raises(SystemExit, match="refuses Alpaca credentials"):
        cli._validate_offline_environment()


def test_review_receipt_cli_requires_existing_nonsymlink_workspace(tmp_path) -> None:
    with pytest.raises(SystemExit, match="existing non-symlink"):
        cli._workspace(tmp_path / "missing")
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SystemExit, match="existing non-symlink"):
        cli._workspace(link)


def test_review_receipt_cli_success_is_offline_and_non_authorizing(monkeypatch, tmp_path, capsys) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    body = {
        "symbol": "FIVE",
        "side": "buy",
        "quantity": "1",
        "order_type": "limit",
        "limit_price": "5.01",
        "take_profit_price": "5.12",
        "stop_loss_price": "4.95",
        "notional": "5.01",
        "effective_notional_cap": "10.00",
        "market_bid": "5.00",
        "market_ask": "5.01",
        "market_last": "5.00",
        "flat_position_count": 0,
        "flat_open_order_count": 0,
    }
    receipt = SimpleNamespace(
        order_id="order-1",
        client_order_id="client-1",
        attempt_id="attempt-1",
        receipt_hash="a" * 64,
        body=body,
    )

    class FakeBuilder:
        def __init__(self, workspace):
            self.workspace = workspace
        def build(self, *, now):
            return receipt

    monkeypatch.setenv(cli._WRITE_ENV, "DISABLED")
    monkeypatch.delenv(cli._KEY_ENV, raising=False)
    monkeypatch.delenv(cli._SECRET_ENV, raising=False)
    monkeypatch.setattr(cli, "ConnectivityOperatorReviewReceiptBuilder", FakeBuilder)
    assert cli.main(["--workspace", str(root)]) == 0
    out = capsys.readouterr().out
    assert '"status": "REVIEW_RECEIPT_FROZEN"' in out
    assert '"network_used": false' in out
    assert '"credentials_used": false' in out
    assert '"oms_staging_authorized": false' in out
    assert '"external_post_authorized": false' in out
    assert '"external_order_submitted": false' in out
    assert '"capital_authority": "NONE"' in out
    assert '"live_trading": "BLOCKED"' in out
    assert '"next_action": "SECOND_HUMAN_EXECUTION_INTENT_REQUIRED"' in out
