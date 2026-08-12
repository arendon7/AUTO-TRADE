from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/r6_issue_connectivity_execution_intent.py"
SPEC = importlib.util.spec_from_file_location("r6_connectivity_execution_intent_cli", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def test_cli_refuses_write_enabled(monkeypatch) -> None:
    monkeypatch.setenv(cli._WRITE_ENV, "ENABLED")
    monkeypatch.delenv(cli._KEY_ENV, raising=False)
    monkeypatch.delenv(cli._SECRET_ENV, raising=False)
    with pytest.raises(SystemExit, match="WRITE=ENABLED"):
        cli._validate_local_only_environment()


def test_cli_refuses_alpaca_credentials(monkeypatch) -> None:
    monkeypatch.setenv(cli._WRITE_ENV, "DISABLED")
    monkeypatch.setenv(cli._KEY_ENV, "paper-key")
    with pytest.raises(SystemExit, match="refuses Alpaca credentials"):
        cli._validate_local_only_environment()


def test_cli_ttl_is_bounded() -> None:
    assert cli._ttl(45) == 45
    for value in (0, -1, 91, True):
        with pytest.raises(SystemExit, match="between 1 and 90"):
            cli._ttl(value)


def test_cli_requires_tty_before_bridge(monkeypatch, tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(cli, "_validate_local_only_environment", lambda: None)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    with pytest.raises(SystemExit, match="interactive TTY"):
        cli.main(["--workspace", str(root), "--operator-id", "operator:test"])


def _fixtures():
    context = SimpleNamespace(
        order_id="order-1",
        client_order_id="client-1",
        context_hash="a" * 64,
    )
    receipt = SimpleNamespace(
        receipt_hash="d" * 64,
        body={
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
        },
    )
    return context, receipt


def test_cli_challenge_mismatch_records_nothing(monkeypatch, tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    context, receipt = _fixtures()
    calls = {"issue": 0}

    class FakeBridge:
        def __init__(self, workspace):
            pass
        def prepare(self, *, now):
            return context, receipt
        def issue(self, **kwargs):
            calls["issue"] += 1
            raise AssertionError("must not issue")

    monkeypatch.setattr(cli, "_validate_local_only_environment", lambda: None)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli, "ConnectivityReviewedExecutionIntentBridge", FakeBridge)
    monkeypatch.setattr(cli, "reviewed_execution_intent_challenge", lambda ctx, rec: "CONFIRM PAPER EXECUTION abc REVIEW def")
    monkeypatch.setattr("builtins.input", lambda prompt="": "NO")
    with pytest.raises(SystemExit, match="challenge did not match"):
        cli.main(["--workspace", str(root), "--operator-id", "operator:test"])
    assert calls["issue"] == 0


def test_cli_success_still_has_no_staging_or_post(monkeypatch, tmp_path, capsys) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    context, receipt = _fixtures()
    now = datetime(2026, 8, 12, 7, 0, tzinfo=timezone.utc)
    decision = SimpleNamespace(
        decision_hash="b" * 64,
        operator_id="operator:test",
        issued_at=now,
        expires_at=now + timedelta(seconds=30),
    )
    state = SimpleNamespace(status=SimpleNamespace(value="ISSUED"), decision=decision)
    review_binding = SimpleNamespace(binding_hash="e" * 64)
    captured = {}

    class FakeBridge:
        def __init__(self, workspace):
            pass
        def prepare(self, *, now):
            return context, receipt
        def issue(self, **kwargs):
            captured.update(kwargs)
            return state, review_binding

    challenge = "CONFIRM PAPER EXECUTION abc REVIEW def"
    monkeypatch.setattr(cli, "_validate_local_only_environment", lambda: None)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli, "ConnectivityReviewedExecutionIntentBridge", FakeBridge)
    monkeypatch.setattr(cli, "reviewed_execution_intent_challenge", lambda ctx, rec: challenge)
    monkeypatch.setattr("builtins.input", lambda prompt="": challenge)
    assert cli.main(["--workspace", str(root), "--operator-id", "operator:test"]) == 0
    assert captured["operator_id"] == "operator:test"
    assert captured["receipt_hash"] == receipt.receipt_hash
    out = capsys.readouterr().out
    assert "Review receipt:" in out
    assert '"operator_review_receipt_hash":' in out
    assert '"execution_review_binding_hash":' in out
    assert '"max_external_post_attempts": 1' in out
    assert '"final_freshness_required": true' in out
    assert '"oms_staging_authorized": false' in out
    assert '"external_post_authorized": false' in out
    assert '"external_order_submitted": false' in out
    assert '"capital_authority": "NONE"' in out
    assert '"live_trading": "BLOCKED"' in out
    assert '"next_action": "REVIEWED_BOUND_FINAL_FRESHNESS_REQUIRED"' in out
