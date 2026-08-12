from __future__ import annotations

import io
from pathlib import Path
import runpy
import sys

import pytest

from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_submission import PaperSubmissionStatus
from test_r6_operational_prepare import build, run_prepare


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/r6_execute_paper_canary.py"


class TTYStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class NonTTYStringIO(io.StringIO):
    def isatty(self) -> bool:
        return False


def namespace() -> dict[str, object]:
    return runpy.run_path(str(SCRIPT))


def prepared_workspace(tmp_path):
    preparer, workspace, broker, submission, permit = build(tmp_path)
    prepared = run_prepare(preparer, submission, permit)
    assert broker.calls == 0
    return prepared, workspace


def test_launcher_requires_explicit_cli_flag_before_credentials(tmp_path, monkeypatch) -> None:
    _, workspace = prepared_workspace(tmp_path)
    ns = namespace()
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.delenv("R6_EXTERNAL_PAPER_WRITE", raising=False)

    with pytest.raises(SystemExit, match="--execute-paper-canary"):
        ns["main"](["--workspace", str(workspace.root)])


def test_launcher_requires_dedicated_enable_environment(tmp_path, monkeypatch) -> None:
    _, workspace = prepared_workspace(tmp_path)
    ns = namespace()
    monkeypatch.delenv("R6_EXTERNAL_PAPER_WRITE", raising=False)

    with pytest.raises(SystemExit, match="R6_EXTERNAL_PAPER_WRITE=ENABLED"):
        ns["main"](
            ["--workspace", str(workspace.root), "--execute-paper-canary"]
        )


def test_launcher_rejects_noninteractive_execution_before_credentials(tmp_path, monkeypatch) -> None:
    _, workspace = prepared_workspace(tmp_path)
    ns = namespace()
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "ENABLED")
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.setattr(sys, "stdin", NonTTYStringIO())
    monkeypatch.setattr(sys, "stdout", TTYStringIO())

    with pytest.raises(SystemExit, match="interactive TTY"):
        ns["main"](
            ["--workspace", str(workspace.root), "--execute-paper-canary"]
        )


def test_launcher_wrong_challenge_never_materializes_writer_or_runtime(tmp_path, monkeypatch) -> None:
    _, workspace = prepared_workspace(tmp_path)
    ns = namespace()
    main = ns["main"]
    called = {"credentials": 0, "writer": 0, "runtime": 0}

    def forbidden_credentials():
        called["credentials"] += 1
        raise AssertionError("credentials must not be read before exact challenge")

    class ForbiddenWriter:
        def __init__(self, **_kwargs):
            called["writer"] += 1
            raise AssertionError("writer must not be enabled before exact challenge")

    class ForbiddenRuntime:
        def __init__(self, **_kwargs):
            called["runtime"] += 1
            raise AssertionError("runtime must not exist before exact challenge")

    main.__globals__["_credentials_from_environment"] = forbidden_credentials
    main.__globals__["AlpacaPaperSingleShotWriter"] = ForbiddenWriter
    main.__globals__["PaperOperationalExecutionRuntime"] = ForbiddenRuntime
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "ENABLED")
    monkeypatch.setattr(sys, "stdin", TTYStringIO())
    monkeypatch.setattr(sys, "stdout", TTYStringIO())
    monkeypatch.setattr("builtins.input", lambda _prompt="": "NO")

    with pytest.raises(SystemExit, match="challenge did not match"):
        main(["--workspace", str(workspace.root), "--execute-paper-canary"])
    assert called == {"credentials": 0, "writer": 0, "runtime": 0}


def test_launcher_credentials_are_environment_only(monkeypatch) -> None:
    ns = namespace()
    monkeypatch.setenv("APCA_API_KEY_ID", "launcher-paper-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "launcher-paper-secret")
    credentials = ns["_credentials_from_environment"]()
    assert isinstance(credentials, AlpacaPaperCredentials)
    assert credentials.key_id == "launcher-paper-key"
    assert "launcher-paper-secret" not in repr(credentials)

    monkeypatch.delenv("APCA_API_SECRET_KEY")
    with pytest.raises(SystemExit, match="provided only through"):
        ns["_credentials_from_environment"]()


def test_launcher_workspace_rejects_missing_and_symlink_root(tmp_path) -> None:
    ns = namespace()
    with pytest.raises(SystemExit, match="does not exist"):
        ns["_open_workspace"](tmp_path / "missing")

    _, workspace = prepared_workspace(tmp_path / "real")
    link = tmp_path / "workspace-link"
    try:
        link.symlink_to(workspace.root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(SystemExit, match="cannot be a symlink"):
        ns["_open_workspace"](link)


def test_launcher_exact_challenge_can_only_delegate_to_injected_runtime_no_network(
    tmp_path,
    monkeypatch,
) -> None:
    prepared, workspace = prepared_workspace(tmp_path)
    ns = namespace()
    main = ns["main"]
    captured: dict[str, object] = {}

    class FakeWriter:
        def __init__(self, *, config):
            captured["writer_enabled"] = config.enabled

    class FakeSubmit:
        def __init__(self) -> None:
            self.order_id = prepared.result.package.order_id
            self.client_order_id = prepared.result.package.client_order_id
            self.attempt_id = prepared.result.package.attempt_id
            self.http_status = 200
            self.request_id = "fake-request"
            self.broker_order_id = "fake-paper-order"
            self.provisionally_accepted = True
            self.durable_status = PaperSubmissionStatus.UNKNOWN
            self.reconciliation_required = True

    class FakeResult:
        def __init__(self) -> None:
            self.submit = FakeSubmit()

    class FakeRuntime:
        def __init__(self, *, workspace, writer):
            captured["workspace"] = workspace.root
            captured["writer"] = writer

        def execute_once(self, *, credentials, now):
            captured["credential_reference"] = credentials.credential_reference
            captured["now"] = now
            return FakeResult()

    main.__globals__["AlpacaPaperSingleShotWriter"] = FakeWriter
    main.__globals__["PaperOperationalExecutionRuntime"] = FakeRuntime
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "ENABLED")
    monkeypatch.setenv("APCA_API_KEY_ID", "launcher-paper-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "launcher-paper-secret")
    monkeypatch.setattr(sys, "stdin", TTYStringIO())
    output = TTYStringIO()
    monkeypatch.setattr(sys, "stdout", output)
    challenge = ns["_execution_challenge"](
        attempt_id=prepared.result.package.attempt_id,
        package_hash=prepared.result.package.package_hash,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": challenge)

    assert main(["--workspace", str(workspace.root), "--execute-paper-canary"]) == 0
    assert captured["writer_enabled"] is True
    assert captured["workspace"] == workspace.root
    assert '"durable_submission_status": "UNKNOWN"' in output.getvalue()
    assert '"live_trading": "BLOCKED"' in output.getvalue()
