from __future__ import annotations

from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mac_first_canary_unified_dashboard.py"
ATTEMPT_ID = "first-canary-0123456789abcdef0123456789abcdef"


def _module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return runpy.run_path(str(SCRIPT))


def _recovery_session(namespace, tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    execution_root = workspace / namespace["safe"].EXECUTION_DIR
    (execution_root / ATTEMPT_ID).mkdir(parents=True)
    session = namespace["UnifiedCanarySession"]()
    session.workspace = workspace
    session.credentials = ("paper-key", "paper-secret")
    monkeypatch.setattr(namespace["safe"], "_attempt_id", lambda raw: raw if raw == ATTEMPT_ID else (_ for _ in ()).throw(ValueError("invalid")))
    return session, workspace


def test_connect_recovery_discovery_resumes_exact_unresolved_attempt_before_any_new_post_authority(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _recovery_session(namespace, tmp_path, monkeypatch)
    monkeypatch.setattr(
        namespace["safe"],
        "_attempt_status",
        lambda *, workspace, attempt_id: {"phase": "RECOVERY_ONLY", "resolved": False, "preparation": None},
    )
    monkeypatch.setattr(namespace["real"], "_discover_ready_attempt", lambda *, workspace: pytest.fail("ready POST discovery must not run while recovery exists"))
    assert session._resume_exact_recovery_attempt() is True
    assert session.active_attempt_id == ATTEMPT_ID
    assert session.review_token is None
    assert session.execute_token is None


def test_prepare_is_blocked_while_prior_attempt_needs_reconciliation(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _recovery_session(namespace, tmp_path, monkeypatch)
    monkeypatch.setattr(
        namespace["safe"],
        "_attempt_status",
        lambda *, workspace, attempt_id: {"phase": "RECOVERY_ONLY", "resolved": False},
    )
    monkeypatch.setattr(namespace["safe"], "_run_child", lambda *args, **kwargs: pytest.fail("prepare child must not run"))
    with pytest.raises(namespace["UnifiedCanaryError"], match="earlier PAPER attempt is unresolved"):
        session.prepare()
    assert session.active_attempt_id == ATTEMPT_ID
    assert session.execute_token is None


def test_reset_cannot_hide_unresolved_attempt(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _recovery_session(namespace, tmp_path, monkeypatch)
    session.active_attempt_id = ATTEMPT_ID
    monkeypatch.setattr(
        namespace["safe"],
        "_attempt_status",
        lambda *, workspace, attempt_id: {"phase": "RECOVERY_ONLY", "resolved": False},
    )
    with pytest.raises(namespace["UnifiedCanaryError"], match="still unresolved"):
        session.reset()
    assert session.active_attempt_id == ATTEMPT_ID


def test_recover_calls_get_only_child_and_exposes_resolution_without_execute_token(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _recovery_session(namespace, tmp_path, monkeypatch)
    session.active_attempt_id = ATTEMPT_ID
    statuses = iter(
        [
            {"phase": "RECOVERY_ONLY", "resolved": False},
            {"phase": "RESOLVED", "resolved": True},
        ]
    )
    monkeypatch.setattr(namespace["safe"], "_attempt_status", lambda *, workspace, attempt_id: next(statuses))
    captured = {}

    def recover(payload):
        captured.update(payload)
        return {
            "ok": True,
            "returncode": 0,
            "error": "",
            "json": {
                "status": "CRYPTO_PAPER_FIRST_CANARY_RECOVERED_FLAT_NO_RETRY",
                "retry_post": False,
                "recovery_get_only": True,
                "live_trading": "BLOCKED",
            },
            "broker_write_performed": False,
        }

    monkeypatch.setattr(namespace["safe"], "_recover", recover)
    result = session.recover()
    assert result["phase"] == "RECOVERED_GET_ONLY"
    assert result["retry_post"] is False
    assert result["recovery"]["broker_write_performed"] is False
    assert captured["attempt_id"] == ATTEMPT_ID
    assert captured["paper_key"] == "paper-key"
    assert captured["paper_secret"] == "paper-secret"
    assert session.execute_token is None


def test_unified_ui_exposes_recovery_not_internal_attempt_plumbing() -> None:
    html = (ROOT / "web/mac_first_canary_unified.html").read_text(encoding="utf-8")
    assert "Reconciliar este intento" in html
    assert "api('/api/recover',{})" in html
    assert "Ver detalles técnicos" in html
    assert 'id="attempt' not in html
    assert "Attempt ID" not in html
