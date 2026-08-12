from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/r6_connectivity_bound_final_freshness.py"
SPEC = importlib.util.spec_from_file_location("r6_bound_final_freshness_cli", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def test_cli_requires_explicit_get_opt_in(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(cli._WRITE_ENV, "DISABLED")
    with pytest.raises(SystemExit, match="disabled unless"):
        cli.main(["--workspace", str(tmp_path)])


def test_cli_refuses_write_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(cli._WRITE_ENV, "ENABLED")
    with pytest.raises(SystemExit, match="GET-only"):
        cli.main([
            "--workspace", str(tmp_path), "--allow-paper-final-freshness-read"
        ])


def test_cli_credentials_are_environment_only(monkeypatch) -> None:
    monkeypatch.delenv(cli._KEY_ENV, raising=False)
    monkeypatch.delenv(cli._SECRET_ENV, raising=False)
    with pytest.raises(SystemExit, match="APCA_API_KEY_ID/APCA_API_SECRET_KEY"):
        cli._credentials()


def test_cli_success_still_exposes_no_staging_or_post(monkeypatch, tmp_path, capsys) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    now = datetime(2026, 8, 12, 7, 30, tzinfo=timezone.utc)
    binding = SimpleNamespace(
        order_id="order-1",
        execution_intent_decision_hash="a" * 64,
        final_freshness_permit_hash="b" * 64,
        binding_hash="c" * 64,
        issued_at=now,
        expires_at=now + timedelta(seconds=5),
    )
    result = SimpleNamespace(binding=binding)

    class FakeGuard:
        def __init__(self, workspace):
            self.workspace = workspace
        def acquire(self, *, credentials):
            return result

    monkeypatch.setenv(cli._WRITE_ENV, "DISABLED")
    monkeypatch.setenv(cli._KEY_ENV, "paper-key")
    monkeypatch.setenv(cli._SECRET_ENV, "paper-secret")
    monkeypatch.setattr(cli, "ConnectivityBoundFinalFreshnessGuard", FakeGuard)
    assert cli.main([
        "--workspace", str(root), "--allow-paper-final-freshness-read"
    ]) == 0
    out = capsys.readouterr().out
    assert '"network_read_count": 5' in out
    assert '"max_external_post_attempts": 1' in out
    assert '"oms_staging_authorized": false' in out
    assert '"external_post_authorized": false' in out
    assert '"external_order_submitted": false' in out
    assert '"capital_authority": "NONE"' in out
    assert '"live_trading": "BLOCKED"' in out
    assert '"next_action": "CONNECTIVITY_STAGING_BRIDGE_REQUIRED"' in out
