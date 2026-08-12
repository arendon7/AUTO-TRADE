from __future__ import annotations

import os
from pathlib import Path

import pytest

import scripts.mac_safe_console as console


def test_safe_console_has_no_execution_subcommand() -> None:
    parser = console._parser()
    help_text = parser.format_help()
    assert "execute" not in help_text.lower()
    assert "account-preflight" in help_text
    assert "market-preflight" in help_text


def test_safe_console_refuses_enabled_write_gate(monkeypatch) -> None:
    monkeypatch.setenv(console.WRITE_ENV, console.WRITE_ENABLED)
    monkeypatch.setattr(console.PYTHON, "is_file", lambda: True)
    with pytest.raises(console.SafeConsoleError, match="R6_EXTERNAL_PAPER_WRITE=ENABLED"):
        console._require_safe_shell()


def test_safe_console_requires_bootstrap(monkeypatch) -> None:
    monkeypatch.delenv(console.WRITE_ENV, raising=False)
    monkeypatch.setattr(console.PYTHON, "is_file", lambda: False)
    with pytest.raises(console.SafeConsoleError, match="Missing .venv"):
        console._require_safe_shell()


def test_safe_console_forces_disabled_write_gate_for_children(monkeypatch, tmp_path) -> None:
    captured = {}

    class Result:
        returncode = 0

    def fake_run(argv, *, cwd, env, check):
        captured["argv"] = argv
        captured["cwd"] = cwd
        captured["env"] = env
        captured["check"] = check
        return Result()

    monkeypatch.setattr(console.subprocess, "run", fake_run)
    monkeypatch.setenv(console.WRITE_ENV, "SOMETHING_ELSE")
    assert console._run(["python", "noop.py"]) == 0
    assert captured["env"][console.WRITE_ENV] == "DISABLED"
    assert captured["check"] is False


def test_safe_console_account_preflight_requires_explicit_read_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(console, "_require_safe_shell", lambda: None)
    called = []
    monkeypatch.setattr(console, "_run", lambda argv: called.append(argv) or 0)

    rc = console.main(
        [
            "account-preflight",
            "--workspace",
            "/tmp/workspace",
            "--expected-account-id",
            "paper-account",
        ]
    )
    assert rc == 2
    assert called == []


def test_safe_console_market_preflight_requires_explicit_read_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(console, "_require_safe_shell", lambda: None)
    called = []
    monkeypatch.setattr(console, "_run", lambda argv: called.append(argv) or 0)

    rc = console.main(
        ["market-preflight", "--workspace", "/tmp/workspace", "--symbol", "AAPL"]
    )
    assert rc == 2
    assert called == []


def test_safe_console_readiness_routes_only_to_readonly_inspector(monkeypatch) -> None:
    monkeypatch.setattr(console, "_require_safe_shell", lambda: None)
    captured = []
    monkeypatch.setattr(console, "_run", lambda argv: captured.append(argv) or 0)

    rc = console.main(["readiness", "--workspace", "/tmp/workspace"])
    assert rc == 0
    joined = " ".join(captured[0])
    assert "r6_inspect_paper_readiness.py" in joined
    assert "r6_execute_paper_canary.py" not in joined
