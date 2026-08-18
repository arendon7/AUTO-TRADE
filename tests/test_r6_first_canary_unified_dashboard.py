from __future__ import annotations

from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mac_first_canary_unified_dashboard.py"
CHECKER = ROOT / "scripts/check_r6_first_canary_unified_dashboard.py"
ATTEMPT_ID = "first-canary-0123456789abcdef0123456789abcdef"


def _module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return runpy.run_path(str(SCRIPT))


def _session(namespace, tmp_path, monkeypatch):
    session = namespace["UnifiedCanarySession"]()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(namespace["safe"], "_workspace_value", lambda raw: workspace)
    monkeypatch.setattr(namespace["safe"], "_credentials", lambda payload: ("paper-key", "paper-secret"))
    monkeypatch.setattr(session, "_resume_exact_ready_attempt", lambda: False)
    session.connect({"workspace": str(workspace), "paper_key": "paper-key", "paper_secret": "paper-secret"})
    return session, workspace


def test_connect_keeps_credentials_only_in_ephemeral_session(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _session(namespace, tmp_path, monkeypatch)
    assert session.workspace == workspace
    assert session.credentials == ("paper-key", "paper-secret")
    assert session.active_attempt_id is None


def test_prepare_generates_internal_attempt_and_reuses_restart_safe_child(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _session(namespace, tmp_path, monkeypatch)
    monkeypatch.setattr(namespace["secrets"], "token_hex", lambda size: "0" * 32)
    calls = []

    def child(command, *, credentials, timeout, **kwargs):
        calls.append((command, credentials, timeout))
        return {"ok": True, "error": ""}

    monkeypatch.setattr(namespace["safe"], "_run_child", child)
    monkeypatch.setattr(
        namespace["safe"],
        "_attempt_status",
        lambda *, workspace, attempt_id: {
            "phase": "APPROVAL_REQUIRED",
            "preparation": {
                "symbol": "BTC/USD",
                "prepared_notional": "2.01",
                "prepared_quantity": "0.00003",
                "prepared_limit_price": "67000",
                "execution_deadline": "2026-08-18T23:00:00+00:00",
            },
        },
    )
    result = session.prepare()
    assert result["phase"] == "REVIEW_READY"
    assert result["summary"]["notional_usd"] == "2.01"
    assert session.active_attempt_id == "first-canary-" + "0" * 32
    assert calls[0][0][0] == "scripts/mac_crypto_first_canary_prepare_restart_safe.py"
    assert calls[0][1] == ("paper-key", "paper-secret")
    assert calls[0][2] == 75


def test_approval_requires_explicit_review_click_but_hides_hash_copying(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _session(namespace, tmp_path, monkeypatch)
    session.active_attempt_id = ATTEMPT_ID
    with pytest.raises(namespace["UnifiedCanaryError"], match="review confirmation"):
        session.approve({"review_confirmed": False})

    captured = {}
    monkeypatch.setattr(
        namespace["safe"],
        "_attempt_status",
        lambda *, workspace, attempt_id: {
            "phase": "APPROVAL_REQUIRED",
            "operator_context": {"attempt_id": attempt_id},
            "operator_challenge": "INTERNAL-EXACT-APPROVAL-CHALLENGE",
        },
    )

    def child(command, *, credentials, stdin_payload, timeout, **kwargs):
        captured.update(stdin_payload)
        return {"ok": True, "error": ""}

    monkeypatch.setattr(namespace["safe"], "_run_child", child)
    monkeypatch.setattr(
        namespace["real"],
        "_status",
        lambda *, workspace, attempt_id: {
            "ready_for_real_post": True,
            "preparation": {"symbol": "BTC/USD", "notional": "2.01", "quantity": "0.00003", "limit_price": "67000"},
        },
    )
    result = session.approve({"review_confirmed": True})
    assert result["phase"] == "FINAL_CONFIRMATION_READY"
    assert captured["confirmation"] == "INTERNAL-EXACT-APPROVAL-CHALLENGE"
    assert captured["operator_id"] == "operator-001"


def test_execute_requires_explicit_final_click_and_exact_unique_attempt(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _session(namespace, tmp_path, monkeypatch)
    session.active_attempt_id = ATTEMPT_ID
    with pytest.raises(namespace["UnifiedCanaryError"], match="final PAPER execution confirmation"):
        session.execute({"execute_confirmed": False})

    monkeypatch.setattr(
        namespace["real"],
        "_discover_ready_attempt",
        lambda *, workspace: {
            "selection_status": "EXACT_ONE_READY",
            "attempt_id": ATTEMPT_ID,
        },
    )
    monkeypatch.setattr(
        namespace["real"],
        "_status",
        lambda *, workspace, attempt_id: {
            "ready_for_real_post": True,
            "external_post_challenge": "INTERNAL-EXACT-POST-CHALLENGE",
            "recovery_get_only": False,
        },
    )
    captured = {}

    def run_execute(payload):
        captured.update(payload)
        return {"ok": True, "returncode": 0, "error": "", "broker_write_performed": True, "json": {"status": "SIMULATED"}}

    monkeypatch.setattr(namespace["real"], "_run_execute", run_execute)
    monkeypatch.setattr(session, "_auto_recover_if_needed", lambda status: None)
    result = session.execute({"execute_confirmed": True})
    assert captured["attempt_id"] == ATTEMPT_ID
    assert captured["confirmation"] == "INTERNAL-EXACT-POST-CHALLENGE"
    assert captured["paper_key"] == "paper-key"
    assert captured["paper_secret"] == "paper-secret"
    assert result["retry_post"] is False
    assert result["live_trading"] == "BLOCKED"


def test_execute_never_uses_manual_attempt_when_unique_discovery_disagrees(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _session(namespace, tmp_path, monkeypatch)
    session.active_attempt_id = ATTEMPT_ID
    monkeypatch.setattr(
        namespace["real"],
        "_discover_ready_attempt",
        lambda *, workspace: {
            "selection_status": "AMBIGUOUS_MULTIPLE_READY",
            "attempt_id": None,
        },
    )
    monkeypatch.setattr(namespace["real"], "_run_execute", lambda payload: pytest.fail("writer path must not be reached"))
    with pytest.raises(namespace["UnifiedCanaryError"], match="unique fresh executable"):
        session.execute({"execute_confirmed": True})


def test_action_lock_blocks_duplicate_click_before_any_second_authority(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _session(namespace, tmp_path, monkeypatch)
    session.active_attempt_id = ATTEMPT_ID
    assert session._action_lock.acquire(blocking=False)
    try:
        with pytest.raises(namespace["UnifiedCanaryError"], match="already running"):
            session.execute({"execute_confirmed": True})
    finally:
        session._action_lock.release()


def test_ambiguous_execution_exception_transitions_to_get_only_recovery(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _session(namespace, tmp_path, monkeypatch)
    session.active_attempt_id = ATTEMPT_ID
    monkeypatch.setattr(
        namespace["real"],
        "_discover_ready_attempt",
        lambda *, workspace: {"selection_status": "EXACT_ONE_READY", "attempt_id": ATTEMPT_ID},
    )
    monkeypatch.setattr(
        namespace["real"],
        "_status",
        lambda *, workspace, attempt_id: {
            "ready_for_real_post": True,
            "external_post_challenge": "INTERNAL",
            "recovery_get_only": True,
        },
    )
    monkeypatch.setattr(namespace["real"], "_run_execute", lambda payload: (_ for _ in ()).throw(TimeoutError("synthetic timeout")))
    monkeypatch.setattr(session, "_auto_recover_if_needed", lambda status: {"ok": True, "broker_write_performed": False, "retry_post": False})
    result = session.execute({"execute_confirmed": True})
    assert result["ok"] is False
    assert result["phase"] == "RECOVERY_ONLY"
    assert result["retry_post"] is False
    assert result["recovery"]["broker_write_performed"] is False


def test_unified_ui_contains_no_attempt_or_challenge_input_plumbing() -> None:
    html = (ROOT / "web/mac_first_canary_unified.html").read_text(encoding="utf-8")
    assert 'id="attempt' not in html
    assert 'id="challenge' not in html
    assert "Copiar challenge" not in html
    assert "Attempt ID" not in html
    assert "Aprobar preparación" in html
    assert "EJECUTAR UNA VEZ EN PAPER" in html


def test_unified_static_boundary_checker_passes_repository() -> None:
    namespace = runpy.run_path(str(CHECKER))
    assert namespace["main"]() == 0
