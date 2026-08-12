from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/r6_connectivity_final_freshness.py"
SPEC = importlib.util.spec_from_file_location("r6_connectivity_final_freshness_cli", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def test_cli_requires_explicit_get_only_opt_in(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(cli._WRITE_ENV, "DISABLED")
    with pytest.raises(SystemExit, match="disabled unless"):
        cli.main(["--workspace", str(tmp_path)])


def test_cli_refuses_write_enabled_before_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(cli._WRITE_ENV, "ENABLED")
    monkeypatch.setenv(cli._KEY_ENV, "paper-key")
    monkeypatch.setenv(cli._SECRET_ENV, "paper-secret")
    with pytest.raises(SystemExit, match="GET-only"):
        cli.main([
            "--workspace", str(tmp_path), "--allow-paper-final-freshness-read"
        ])


def test_cli_credentials_are_environment_only(monkeypatch) -> None:
    monkeypatch.delenv(cli._KEY_ENV, raising=False)
    monkeypatch.delenv(cli._SECRET_ENV, raising=False)
    with pytest.raises(SystemExit, match="APCA_API_KEY_ID/APCA_API_SECRET_KEY"):
        cli._credentials()
    monkeypatch.setenv(cli._KEY_ENV, "paper-key")
    monkeypatch.setenv(cli._SECRET_ENV, "paper-secret")
    creds = cli._credentials()
    assert isinstance(creds, AlpacaPaperCredentials)
    assert "paper-secret" not in repr(creds)


def test_cli_success_exposes_only_get_eligibility(monkeypatch, tmp_path, capsys) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    now = datetime(2026, 8, 12, 6, 0, tzinfo=timezone.utc)
    permit = SimpleNamespace(
        order_id="order-1",
        permit_hash="a" * 64,
        fresh_risk_decision_id="risk-1",
        fresh_risk_decision_fingerprint="b" * 64,
        issued_at=now,
        expires_at=now + timedelta(seconds=5),
    )
    result = SimpleNamespace(
        state=SimpleNamespace(status=SimpleNamespace(value="ISSUED")),
        permit=permit,
    )
    captured = {}

    class FakeGuard:
        def __init__(self, workspace):
            captured["workspace"] = workspace
        def acquire(self, *, credentials):
            captured["credentials"] = credentials
            return result

    monkeypatch.setenv(cli._WRITE_ENV, "DISABLED")
    monkeypatch.setenv(cli._KEY_ENV, "paper-key")
    monkeypatch.setenv(cli._SECRET_ENV, "paper-secret")
    monkeypatch.setattr(cli, "ConnectivityFinalFreshnessGuard", FakeGuard)
    assert cli.main([
        "--workspace", str(root), "--allow-paper-final-freshness-read"
    ]) == 0
    out = capsys.readouterr().out
    assert '"network_read_count": 5' in out
    assert '"network_methods": ["GET", "GET", "GET", "GET", "GET"]' in out
    assert '"oms_staging_authorized": false' in out
    assert '"external_post_authorized": false' in out
    assert '"external_order_submitted": false' in out
    assert '"capital_authority": "NONE"' in out
    assert '"live_trading": "BLOCKED"' in out
    assert '"next_action": "EXPLICIT_CONNECTIVITY_EXECUTION_DECISION_REQUIRED"' in out
    assert captured["credentials"].credential_reference
