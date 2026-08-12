from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mac_create_workspace.py"


def _env(**updates: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("APCA_API_KEY_ID", None)
    env.pop("APCA_API_SECRET_KEY", None)
    env.pop("R6_EXTERNAL_PAPER_WRITE", None)
    env.update(updates)
    return env


def _run(workspace: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--workspace", str(workspace)],
        cwd=ROOT,
        env=env or _env(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_mac_workspace_initializer_creates_private_broker_inert_workspace(tmp_path) -> None:
    workspace = tmp_path / "paper-workspace"
    result = _run(workspace)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["workspace"] == str(workspace.resolve())
    assert payload["workspace_mode"] == "0700"
    assert payload["phase"] == "ACCOUNT_PREFLIGHT_REQUIRED"
    assert payload["broker_network_used"] is False
    assert payload["broker_write_performed"] is False
    assert payload["execution_authorized"] is False
    assert payload["credentials_used"] is False
    assert payload["capital_authority"] == "NONE"
    assert payload["live_trading_status"] == "BLOCKED"
    assert stat.S_IMODE(workspace.stat().st_mode) & 0o077 == 0
    assert list(workspace.iterdir()) == []


def test_mac_workspace_initializer_rejects_external_write_gate(tmp_path) -> None:
    result = _run(
        tmp_path / "blocked",
        env=_env(R6_EXTERNAL_PAPER_WRITE="ENABLED"),
    )
    assert result.returncode != 0
    assert "R6_EXTERNAL_PAPER_WRITE=ENABLED" in result.stderr
    assert not (tmp_path / "blocked").exists()


def test_mac_workspace_initializer_rejects_loaded_credentials(tmp_path) -> None:
    result = _run(
        tmp_path / "blocked",
        env=_env(APCA_API_KEY_ID="paper-key"),
    )
    assert result.returncode != 0
    assert "credential-free" in result.stderr
    assert not (tmp_path / "blocked").exists()


def test_mac_workspace_initializer_rejects_nonempty_directory(tmp_path) -> None:
    workspace = tmp_path / "existing"
    workspace.mkdir()
    (workspace / "sentinel.txt").write_text("do-not-touch", encoding="utf-8")

    result = _run(workspace)
    assert result.returncode != 0
    assert "non-empty directory" in result.stderr
    assert (workspace / "sentinel.txt").read_text(encoding="utf-8") == "do-not-touch"


def test_mac_workspace_initializer_rejects_repo_local_workspace_without_creating_it() -> None:
    workspace = ROOT / "_AUTO_TRADE_R6_FORBIDDEN_TEST_WORKSPACE"
    assert not workspace.exists()
    result = _run(workspace)
    assert result.returncode != 0
    assert "outside the git repository" in result.stderr
    assert not workspace.exists()
