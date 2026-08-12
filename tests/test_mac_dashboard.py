from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/mac_dashboard.py"
SPEC = importlib.util.spec_from_file_location("mac_dashboard_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
dashboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dashboard)


def test_dashboard_allowlist_has_no_execution_or_final_freshness_surface() -> None:
    names = set(dashboard.SAFE_ACTIONS)
    assert names == {
        "init_workspace", "doctor", "rehearsal", "safety_rehearsal", "readiness", "status",
        "account_preflight", "asset_preflight", "flat_account_preflight", "market_preflight",
        "build_candidate", "prepare_candidate", "review_receipt",
    }
    joined = " ".join(sorted(names)).lower()
    for forbidden in ("post", "submit", "stage", "execute", "final_freshness", "live"):
        assert forbidden not in joined


def test_safe_env_always_disables_writer_and_strips_inherited_credentials(monkeypatch) -> None:
    monkeypatch.setenv(dashboard.WRITE_ENV, "ENABLED")
    monkeypatch.setenv(dashboard.KEY_ENV, "old-key")
    monkeypatch.setenv(dashboard.SECRET_ENV, "old-secret")
    env = dashboard._safe_env()
    assert env[dashboard.WRITE_ENV] == "DISABLED"
    assert dashboard.KEY_ENV not in env
    assert dashboard.SECRET_ENV not in env


def test_safe_env_injects_paper_credentials_only_for_requested_child(monkeypatch) -> None:
    monkeypatch.setenv(dashboard.KEY_ENV, "old-key")
    monkeypatch.setenv(dashboard.SECRET_ENV, "old-secret")
    env = dashboard._safe_env(paper_credentials=("new-key", "new-secret"))
    assert env[dashboard.WRITE_ENV] == "DISABLED"
    assert env[dashboard.KEY_ENV] == "new-key"
    assert env[dashboard.SECRET_ENV] == "new-secret"


def test_local_action_commands_route_through_safe_console(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard, "PYTHON", tmp_path / "python")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for action, expected in (
        ("doctor", "doctor"),
        ("readiness", "readiness"),
        ("status", "pre-canary-status"),
        ("build_candidate", "build-connectivity-candidate"),
        ("prepare_candidate", "prepare-connectivity-candidate"),
        ("review_receipt", "review-receipt"),
    ):
        command, credentials = dashboard._command(action, {"workspace": str(workspace)})
        joined = " ".join(command)
        assert "scripts/mac_safe_console.py" in joined
        assert expected in joined
        assert credentials is None
        for forbidden in (
            "r6_execute_paper_canary.py", "r6_connectivity_bound_final_freshness.py",
            "stage_external_submission", "alpaca_paper_writer",
        ):
            assert forbidden not in joined


def test_get_actions_require_ephemeral_paper_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard, "PYTHON", tmp_path / "python")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(dashboard.DashboardError, match="PAPER key and secret"):
        dashboard._command("asset_preflight", {"workspace": str(workspace), "symbol": "AAPL"})
    command, credentials = dashboard._command(
        "asset_preflight",
        {"workspace": str(workspace), "symbol": "AAPL", "paper_key": "k", "paper_secret": "s"},
    )
    assert credentials == ("k", "s")
    assert "--allow-paper-asset-read" in command


def test_redaction_removes_credentials_from_captured_output() -> None:
    redacted = dashboard._redact("key=abc secret=xyz", ("abc", "xyz"))
    assert "abc" not in redacted
    assert "xyz" not in redacted
    assert redacted.count("[REDACTED]") == 2


def test_dashboard_refuses_non_localhost_bind() -> None:
    with pytest.raises(dashboard.DashboardError, match="127.0.0.1"):
        dashboard._start_server("0.0.0.0", 0)
