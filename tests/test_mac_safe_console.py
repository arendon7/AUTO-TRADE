from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/mac_safe_console.py"
SPEC = importlib.util.spec_from_file_location("mac_safe_console_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
console = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(console)


def _capture_run(monkeypatch):
    captured = []
    monkeypatch.setattr(
        console,
        "_run",
        lambda argv, **kwargs: captured.append((argv, kwargs)) or 0,
    )
    return captured


def test_safe_console_has_no_execution_subcommand() -> None:
    help_text = console._parser().format_help()
    assert "execute" not in help_text.lower()
    for command in (
        "init-workspace", "safety-rehearsal", "account-preflight", "asset-preflight",
        "flat-account-preflight", "market-preflight", "build-connectivity-candidate",
    ):
        assert command in help_text


def test_safe_console_refuses_enabled_write_gate(monkeypatch) -> None:
    monkeypatch.setenv(console.WRITE_ENV, console.WRITE_ENABLED)
    with pytest.raises(console.SafeConsoleError, match="R6_EXTERNAL_PAPER_WRITE=ENABLED"):
        console._require_safe_shell()


def test_safe_console_requires_bootstrap(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(console.WRITE_ENV, raising=False)
    monkeypatch.setattr(console, "PYTHON", tmp_path / "missing-python")
    with pytest.raises(console.SafeConsoleError, match="Missing .venv"):
        console._require_safe_shell()


def test_child_env_forces_write_disabled_and_can_strip_credentials(monkeypatch) -> None:
    monkeypatch.setenv(console.WRITE_ENV, "SOMETHING_ELSE")
    monkeypatch.setenv(console.KEY_ENV, "paper-key")
    monkeypatch.setenv(console.SECRET_ENV, "paper-secret")
    ordinary = console._child_env()
    assert ordinary[console.WRITE_ENV] == "DISABLED"
    assert ordinary[console.KEY_ENV] == "paper-key"
    credential_free = console._child_env(credential_free=True)
    assert credential_free[console.WRITE_ENV] == "DISABLED"
    assert console.KEY_ENV not in credential_free
    assert console.SECRET_ENV not in credential_free


def test_safe_console_forces_disabled_write_gate_for_children(monkeypatch) -> None:
    captured = {}
    class Result:
        returncode = 0
    def fake_run(argv, *, cwd, env, check):
        captured.update(argv=argv, cwd=cwd, env=env, check=check)
        return Result()
    monkeypatch.setattr(console.subprocess, "run", fake_run)
    monkeypatch.setenv(console.WRITE_ENV, "OTHER")
    assert console._run(["python", "noop.py"]) == 0
    assert captured["env"][console.WRITE_ENV] == "DISABLED"
    assert captured["check"] is False


def test_safe_console_local_actions_are_credential_free(monkeypatch) -> None:
    monkeypatch.setattr(console, "_require_safe_shell", lambda: None)
    for argv, expected in (
        (["init-workspace", "--workspace", "/tmp/w"], "mac_create_workspace.py"),
        (["doctor"], "mac_doctor.py"),
        (["rehearsal"], "mac_rehearsal.sh"),
        (["safety-rehearsal"], "mac_safety_rehearsal.py"),
        (["readiness", "--workspace", "/tmp/w"], "r6_inspect_paper_readiness.py"),
    ):
        captured = _capture_run(monkeypatch)
        assert console.main(argv) == 0
        command, kwargs = captured[-1]
        assert expected in " ".join(command)
        assert kwargs.get("credential_free") is True


def test_get_preflights_require_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(console, "_require_safe_shell", lambda: None)
    cases = (
        ["account-preflight", "--workspace", "/tmp/w", "--expected-account-id", "paper"],
        ["asset-preflight", "--workspace", "/tmp/w", "--symbol", "AAPL"],
        ["flat-account-preflight", "--workspace", "/tmp/w"],
        ["market-preflight", "--workspace", "/tmp/w", "--symbol", "AAPL"],
    )
    for argv in cases:
        captured = _capture_run(monkeypatch)
        assert console.main(argv) == 2
        assert captured == []


def test_get_preflights_route_only_to_read_scripts(monkeypatch) -> None:
    monkeypatch.setattr(console, "_require_safe_shell", lambda: None)
    cases = (
        (["account-preflight", "--workspace", "/tmp/w", "--expected-account-id", "paper", "--allow-paper-account-read"], "r6_external_paper_preflight.py"),
        (["asset-preflight", "--workspace", "/tmp/w", "--symbol", "AAPL", "--allow-paper-asset-read"], "r6_external_paper_asset_preflight.py"),
        (["flat-account-preflight", "--workspace", "/tmp/w", "--allow-paper-flat-account-read"], "r6_external_paper_flat_account_preflight.py"),
        (["market-preflight", "--workspace", "/tmp/w", "--symbol", "AAPL", "--allow-paper-market-read"], "r6_external_paper_market_preflight.py"),
    )
    for argv, expected in cases:
        captured = _capture_run(monkeypatch)
        assert console.main(argv) == 0
        command, kwargs = captured[-1]
        joined = " ".join(command)
        assert expected in joined
        assert "r6_execute_paper_canary.py" not in joined
        assert kwargs.get("credential_free", False) is False


def test_connectivity_candidate_routes_only_to_local_credential_free_builder(monkeypatch) -> None:
    monkeypatch.setattr(console, "_require_safe_shell", lambda: None)
    captured = _capture_run(monkeypatch)
    assert console.main(["build-connectivity-candidate", "--workspace", "/tmp/w"]) == 0
    command, kwargs = captured[-1]
    joined = " ".join(command)
    assert "r6_build_connectivity_candidate.py" in joined
    assert "r6_execute_paper_canary.py" not in joined
    assert "r6_external_paper_preflight.py" not in joined
    assert kwargs.get("credential_free") is True
