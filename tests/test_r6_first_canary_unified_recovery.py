from __future__ import annotations

from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mac_first_canary_unified_dashboard.py"
QUEUE_SCRIPT = ROOT / "scripts/mac_first_canary_unified_queue.py"
ATTEMPT_ID = "first-canary-0123456789abcdef0123456789abcdef"
ATTEMPT_ID_2 = "first-canary-1123456789abcdef0123456789abcdef"


def _module(monkeypatch, script=SCRIPT):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return runpy.run_path(str(script))


def _recovery_session(namespace, tmp_path, monkeypatch, attempt_ids=(ATTEMPT_ID,)):
    workspace = tmp_path / "workspace"
    execution_root = workspace / namespace["safe"].EXECUTION_DIR
    for attempt_id in attempt_ids:
        (execution_root / attempt_id).mkdir(parents=True, exist_ok=True)
    session = namespace["UnifiedCanarySession"]()
    session.workspace = workspace
    session.credentials = ("paper-key", "paper-secret")
    valid = set(attempt_ids)
    monkeypatch.setattr(
        namespace["safe"],
        "_attempt_id",
        lambda raw: raw if raw in valid else (_ for _ in ()).throw(ValueError("invalid")),
    )
    return session, workspace


def _queue_session(namespace, tmp_path, monkeypatch, attempt_ids=(ATTEMPT_ID, ATTEMPT_ID_2)):
    workspace = tmp_path / "workspace"
    execution_root = workspace / namespace["base"].safe.EXECUTION_DIR
    for attempt_id in attempt_ids:
        (execution_root / attempt_id).mkdir(parents=True, exist_ok=True)
    session = namespace["QueuedRecoverySession"]()
    session.workspace = workspace
    session.credentials = ("paper-key", "paper-secret")
    valid = set(attempt_ids)
    monkeypatch.setattr(
        namespace["base"].safe,
        "_attempt_id",
        lambda raw: raw if raw in valid else (_ for _ in ()).throw(ValueError("invalid")),
    )
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


def test_queue_connect_accepts_multiple_unresolved_without_creating_post_authority(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch, QUEUE_SCRIPT)
    session, workspace = _queue_session(namespace, tmp_path, monkeypatch)
    monkeypatch.setattr(
        namespace["base"].safe,
        "_attempt_status",
        lambda *, workspace, attempt_id: {
            "phase": "RECOVERY_ONLY",
            "resolved": False,
            "execution_status": None,
            "reconciliation_failure_status": None,
            "reconciliation_pending_status": None,
        },
    )
    monkeypatch.setattr(
        namespace["base"].real,
        "_discover_ready_attempt",
        lambda *, workspace: pytest.fail("POST-ready discovery must not run with unresolved queue"),
    )
    result = session.connect(
        {
            "workspace": str(workspace),
            "paper_key": "paper-key",
            "paper_secret": "paper-secret",
        }
    )
    assert result["phase"] == "RECOVERY_ONLY"
    assert result["active_recovery_resumed"] is True
    assert result["pending_recovery_count"] == 2
    assert result["retry_post"] is False
    assert session.active_attempt_id in {ATTEMPT_ID, ATTEMPT_ID_2}
    assert session.execute_token is None
    assert session.review_token is None


def test_queue_recover_drains_two_resolvable_attempts_by_get_only(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch, QUEUE_SCRIPT)
    session, workspace = _queue_session(namespace, tmp_path, monkeypatch)
    resolved: set[str] = set()
    calls: list[str] = []

    def status(*, workspace, attempt_id):
        if attempt_id in resolved:
            return {"phase": "RESOLVED", "resolved": True}
        return {
            "phase": "RECOVERY_ONLY",
            "resolved": False,
            "execution_status": "UNKNOWN",
            "reconciliation_failure_status": "HTTP_404",
            "reconciliation_pending_status": None,
        }

    def recover(payload):
        attempt_id = payload["attempt_id"]
        calls.append(attempt_id)
        resolved.add(attempt_id)
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

    monkeypatch.setattr(namespace["base"].safe, "_attempt_status", status)
    monkeypatch.setattr(namespace["base"].safe, "_recover", recover)
    result = session.recover()
    assert result["phase"] == "RECOVERED_GET_ONLY"
    assert result["reconciled_count"] == 2
    assert result["pending_recovery_count"] == 0
    assert set(calls) == {ATTEMPT_ID, ATTEMPT_ID_2}
    assert result["retry_post"] is False
    assert session.active_attempt_id is None
    assert session.execute_token is None


def test_queue_recovery_stops_at_first_still_pending_attempt(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch, QUEUE_SCRIPT)
    session, workspace = _queue_session(namespace, tmp_path, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        namespace["base"].safe,
        "_attempt_status",
        lambda *, workspace, attempt_id: {
            "phase": "RECOVERY_ONLY",
            "resolved": False,
            "execution_status": "UNKNOWN",
            "reconciliation_failure_status": "HTTP_404",
            "reconciliation_pending_status": None,
        },
    )

    def recover(payload):
        calls.append(payload["attempt_id"])
        return {
            "ok": True,
            "returncode": 0,
            "error": "",
            "json": {
                "status": "CRYPTO_PAPER_FIRST_CANARY_GET_ONLY_RECONCILIATION_PENDING_NO_RETRY",
                "retry_post": False,
                "recovery_get_only": True,
                "live_trading": "BLOCKED",
            },
            "broker_write_performed": False,
        }

    monkeypatch.setattr(namespace["base"].safe, "_recover", recover)
    result = session.recover()
    assert result["phase"] == "RECOVERY_ONLY"
    assert result["reconciled_count"] == 0
    assert result["pending_recovery_count"] == 2
    assert len(calls) == 1
    assert result["retry_post"] is False
    assert session.execute_token is None


def test_queue_source_has_no_direct_execute_or_network_authority() -> None:
    source = QUEUE_SCRIPT.read_text(encoding="utf-8")
    assert "real._run_execute(" not in source
    assert "HttpsAlpacaPaperCryptoWriteTransport" not in source
    assert "AlpacaPaperCryptoWriter" not in source
    assert "APCA_API_SECRET_KEY" not in source
    assert "APCA_API_KEY_ID" not in source
    assert '"retry_post": False' in source


def test_unified_ui_exposes_recovery_not_internal_attempt_plumbing() -> None:
    html = (ROOT / "web/mac_first_canary_unified.html").read_text(encoding="utf-8")
    assert "Reconciliar este intento" in html
    assert "api('/api/recover',{})" in html
    assert "Ver detalles técnicos" in html
    assert 'id="attempt' not in html
    assert "Attempt ID" not in html
