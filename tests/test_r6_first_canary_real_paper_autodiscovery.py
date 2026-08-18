from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mac_first_canary_real_paper_dashboard.py"
ATTEMPT_A = "first-canary-0123456789abcdef0123456789abcdef"
ATTEMPT_B = "first-canary-fedcba9876543210fedcba9876543210"


def _module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return runpy.run_path(str(SCRIPT))


def _attempt_dir(workspace: Path, attempt_id: str) -> Path:
    path = workspace / "first_canary_execution" / attempt_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _approval(path: Path, *, expires_at: datetime) -> None:
    (path / "approval.json").write_text(
        json.dumps({"expires_at": expires_at.isoformat()}),
        encoding="utf-8",
    )


def _ready_status(*, deadline: datetime):
    def fake(*, workspace: Path, attempt_id: str):
        return {
            "attempt_id": attempt_id,
            "ready_for_real_post": True,
            "preparation": {"execution_deadline": deadline.isoformat()},
        }

    return fake


def test_discovery_auto_selects_exactly_one_fresh_ready_attempt(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    now = datetime.now(timezone.utc)
    attempt = _attempt_dir(workspace, ATTEMPT_A)
    _approval(attempt, expires_at=now + timedelta(minutes=2))
    namespace["_discover_ready_attempt"].__globals__["_status"] = _ready_status(
        deadline=now + timedelta(minutes=2)
    )

    result = namespace["_discover_ready_attempt"](workspace=workspace)

    assert result == {
        "selection_status": "EXACT_ONE_READY",
        "attempt_id": ATTEMPT_A,
        "ready_count": 1,
        "expired_count": 0,
        "invalid_count": 0,
        "auto_selected": True,
    }


def test_discovery_never_guesses_when_multiple_attempts_are_ready(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    now = datetime.now(timezone.utc)
    for attempt_id in (ATTEMPT_A, ATTEMPT_B):
        attempt = _attempt_dir(workspace, attempt_id)
        _approval(attempt, expires_at=now + timedelta(minutes=2))
    namespace["_discover_ready_attempt"].__globals__["_status"] = _ready_status(
        deadline=now + timedelta(minutes=2)
    )

    result = namespace["_discover_ready_attempt"](workspace=workspace)

    assert result["selection_status"] == "AMBIGUOUS_MULTIPLE_READY"
    assert result["attempt_id"] is None
    assert result["ready_count"] == 2
    assert result["auto_selected"] is False


def test_execute_boundary_rejects_manual_id_when_discovery_is_ambiguous(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    globals_ = namespace["_run_execute"].__globals__
    globals_["_workspace_value"] = lambda raw: tmp_path
    globals_["_attempt_id"] = lambda raw: ATTEMPT_A
    globals_["_discover_ready_attempt"] = lambda *, workspace: {
        "selection_status": "AMBIGUOUS_MULTIPLE_READY",
        "attempt_id": None,
        "ready_count": 2,
        "expired_count": 0,
        "invalid_count": 0,
        "auto_selected": False,
    }
    globals_["_credentials"] = lambda payload: pytest.fail(
        "credentials must not be read when attempt selection is ambiguous"
    )

    with pytest.raises(
        namespace["FirstCanaryRealPaperDashboardError"],
        match="requires exactly one fresh approved unstarted attempt",
    ):
        namespace["_run_execute"](
            {
                "workspace": str(tmp_path),
                "attempt_id": ATTEMPT_A,
                "confirmation": "synthetic-exact-challenge",
            }
        )


def test_discovery_excludes_expired_package_or_approval(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    now = datetime.now(timezone.utc)
    first = _attempt_dir(workspace, ATTEMPT_A)
    second = _attempt_dir(workspace, ATTEMPT_B)
    _approval(first, expires_at=now - timedelta(seconds=1))
    _approval(second, expires_at=now + timedelta(minutes=2))

    def status(*, workspace: Path, attempt_id: str):
        deadline = (
            now + timedelta(minutes=2)
            if attempt_id == ATTEMPT_A
            else now - timedelta(seconds=1)
        )
        return {
            "attempt_id": attempt_id,
            "ready_for_real_post": True,
            "preparation": {"execution_deadline": deadline.isoformat()},
        }

    namespace["_discover_ready_attempt"].__globals__["_status"] = status
    result = namespace["_discover_ready_attempt"](workspace=workspace)

    assert result["selection_status"] == "NO_READY_ATTEMPT"
    assert result["ready_count"] == 0
    assert result["expired_count"] == 2
    assert result["auto_selected"] is False


def test_discovery_ignores_non_attempt_names_and_counts_corrupt_candidate(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    execution = workspace / "first_canary_execution"
    execution.mkdir()
    (execution / "notes.txt").write_text("not an attempt", encoding="utf-8")
    attempt = _attempt_dir(workspace, ATTEMPT_A)
    (attempt / "approval.json").write_text("{bad-json", encoding="utf-8")
    namespace["_discover_ready_attempt"].__globals__["_status"] = _ready_status(
        deadline=datetime.now(timezone.utc) + timedelta(minutes=2)
    )

    result = namespace["_discover_ready_attempt"](workspace=workspace)

    assert result["selection_status"] == "NO_READY_ATTEMPT"
    assert result["ready_count"] == 0
    assert result["invalid_count"] == 1


def test_discovery_with_no_execution_directory_is_non_authorizing(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = namespace["_discover_ready_attempt"](workspace=workspace)

    assert result["selection_status"] == "NO_READY_ATTEMPT"
    assert result["attempt_id"] is None
    assert result["auto_selected"] is False
