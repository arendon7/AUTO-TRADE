from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/r6_issue_connectivity_operator_decision.py"
SPEC = importlib.util.spec_from_file_location("r6_connectivity_operator_cli", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def test_cli_refuses_write_enabled(monkeypatch) -> None:
    monkeypatch.setenv(cli._WRITE_ENV, "ENABLED")
    monkeypatch.delenv(cli._KEY_ENV, raising=False)
    monkeypatch.delenv(cli._SECRET_ENV, raising=False)
    with pytest.raises(SystemExit, match="WRITE=ENABLED"):
        cli._validate_local_only_environment()


def test_cli_refuses_inherited_alpaca_credentials(monkeypatch) -> None:
    monkeypatch.setenv(cli._WRITE_ENV, "DISABLED")
    monkeypatch.setenv(cli._KEY_ENV, "paper-key")
    monkeypatch.delenv(cli._SECRET_ENV, raising=False)
    with pytest.raises(SystemExit, match="refuses Alpaca credentials"):
        cli._validate_local_only_environment()


def test_cli_ttl_is_bounded() -> None:
    assert cli._ttl(60) == 60
    for value in (0, -1, 121, True):
        with pytest.raises(SystemExit, match="between 1 and 120"):
            cli._ttl(value)


def test_cli_requires_interactive_tty(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "_validate_local_only_environment", lambda: None)
    monkeypatch.setattr(cli, "_workspace", lambda path: SimpleNamespace(root=path))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    with pytest.raises(SystemExit, match="interactive TTY"):
        cli.main(["--workspace", str(tmp_path), "--operator-id", "operator:test"])


def test_cli_challenge_mismatch_records_no_authority(monkeypatch, tmp_path) -> None:
    context = SimpleNamespace(
        order_id="order-1",
        client_order_id="client-1",
        notional="5.00",
        connectivity_preparation_hash="a" * 64,
        connectivity_binding_hash="b" * 64,
        core_db_sha256_after_preparation="c" * 64,
        context_hash="d" * 64,
    )
    calls = {"issue": 0}

    class FakeBridge:
        def __init__(self, workspace):
            self.workspace = workspace
        def prepare_context(self, *, now):
            return context
        def issue(self, **kwargs):
            calls["issue"] += 1
            raise AssertionError("must not issue")

    monkeypatch.setattr(cli, "_validate_local_only_environment", lambda: None)
    monkeypatch.setattr(cli, "_workspace", lambda path: SimpleNamespace(root=path))
    monkeypatch.setattr(cli, "ConnectivityOperatorBridge", FakeBridge)
    monkeypatch.setattr(cli, "connectivity_operator_confirmation_challenge", lambda ctx: "APPROVE CONNECTIVITY deadbeef0000")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "NO")
    with pytest.raises(SystemExit, match="challenge did not match"):
        cli.main(["--workspace", str(tmp_path), "--operator-id", "operator:test"])
    assert calls["issue"] == 0


def test_cli_success_issues_only_connectivity_authority(monkeypatch, tmp_path, capsys) -> None:
    context = SimpleNamespace(
        order_id="order-1",
        client_order_id="client-1",
        notional="5.00",
        connectivity_preparation_hash="a" * 64,
        connectivity_binding_hash="b" * 64,
        core_db_sha256_after_preparation="c" * 64,
        context_hash="d" * 64,
    )
    now = datetime(2026, 8, 12, 5, 0, tzinfo=timezone.utc)
    decision = SimpleNamespace(
        decision_hash="e" * 64,
        operator_id="operator:test",
        issued_at=now,
        expires_at=now.replace(minute=1),
    )
    state = SimpleNamespace(status=SimpleNamespace(value="ISSUED"), decision=decision)
    captured = {}

    class FakeBridge:
        def __init__(self, workspace):
            self.workspace = workspace
        def prepare_context(self, *, now):
            return context
        def issue(self, **kwargs):
            captured.update(kwargs)
            return state

    monkeypatch.setattr(cli, "_validate_local_only_environment", lambda: None)
    monkeypatch.setattr(cli, "_workspace", lambda path: SimpleNamespace(root=path))
    monkeypatch.setattr(cli, "ConnectivityOperatorBridge", FakeBridge)
    monkeypatch.setattr(cli, "connectivity_operator_confirmation_challenge", lambda ctx: "APPROVE CONNECTIVITY deadbeef0000")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "APPROVE CONNECTIVITY deadbeef0000")
    assert cli.main(["--workspace", str(tmp_path), "--operator-id", "operator:test"]) == 0
    assert captured["context"] is context
    assert captured["operator_id"] == "operator:test"
    out = capsys.readouterr().out
    assert '"oms_staging_authorized": false' in out
    assert '"external_post_authorized": false' in out
    assert '"next_action": "CONNECTIVITY_FINAL_FRESHNESS_REQUIRED"' in out
    assert '"live_trading": "BLOCKED"' in out
