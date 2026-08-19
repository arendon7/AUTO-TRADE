from __future__ import annotations

from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mac_first_canary_unified_auto_settle.py"
ATTEMPT_ID = "first-canary-0123456789abcdef0123456789abcdef"


def _module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return runpy.run_path(str(SCRIPT))


def _session(namespace, tmp_path):
    session = namespace["AutoSettlementSession"]()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session.workspace = workspace
    session.credentials = ("paper-key", "paper-secret")
    session.active_attempt_id = ATTEMPT_ID
    return session, workspace


def test_auto_settlement_resolves_on_second_get_without_post_retry(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _session(namespace, tmp_path)
    calls: list[int] = []
    sleeps: list[float] = []

    def recover(payload):
        calls.append(len(calls) + 1)
        return {
            "ok": True,
            "returncode": 0,
            "error": "",
            "broker_write_performed": False,
            "json": {
                "status": (
                    "CRYPTO_PAPER_FIRST_CANARY_RECOVERY_PENDING"
                    if len(calls) == 1
                    else "CRYPTO_PAPER_FIRST_CANARY_RECOVERED_FLAT_NO_RETRY"
                ),
                "resulting_lifecycle_status": "FLAT_RECONCILED" if len(calls) == 2 else "UNKNOWN",
                "retry_post": False,
            },
        }

    monkeypatch.setattr(namespace["base"].safe, "_recover", recover)
    monkeypatch.setattr(
        namespace["base"].safe,
        "_attempt_status",
        lambda *, workspace, attempt_id: {
            "phase": "RESOLVED" if len(calls) >= 2 else "RECOVERY_ONLY",
            "resolved": len(calls) >= 2,
        },
    )
    monkeypatch.setattr(namespace["time"], "sleep", lambda delay: sleeps.append(delay))

    result = session._auto_recover_if_needed({"recovery_get_only": True})
    assert result is not None
    assert result["auto_settlement_attempts"] == 2
    assert result["auto_settlement_resolved"] is True
    assert result["auto_settlement_exhausted"] is False
    assert result["broker_write_performed"] is False
    assert result["retry_post"] is False
    assert calls == [1, 2]
    assert sleeps == [1.0]


def test_auto_settlement_stops_after_bounded_get_budget(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _session(namespace, tmp_path)
    calls: list[int] = []
    sleeps: list[float] = []

    def recover(payload):
        calls.append(len(calls) + 1)
        return {
            "ok": True,
            "returncode": 0,
            "error": "",
            "broker_write_performed": False,
            "json": {"status": "CRYPTO_PAPER_FIRST_CANARY_RECOVERY_PENDING", "retry_post": False},
        }

    monkeypatch.setattr(namespace["base"].safe, "_recover", recover)
    monkeypatch.setattr(
        namespace["base"].safe,
        "_attempt_status",
        lambda *, workspace, attempt_id: {"phase": "RECOVERY_ONLY", "resolved": False},
    )
    monkeypatch.setattr(namespace["time"], "sleep", lambda delay: sleeps.append(delay))

    result = session._auto_recover_if_needed({"recovery_get_only": True})
    assert result is not None
    assert result["ok"] is False
    assert result["auto_settlement_attempts"] == 4
    assert result["auto_settlement_resolved"] is False
    assert result["auto_settlement_exhausted"] is True
    assert result["retry_post"] is False
    assert result["broker_write_performed"] is False
    assert calls == [1, 2, 3, 4]
    assert sleeps == [1.0, 2.0, 4.0]


def test_auto_settlement_stops_immediately_on_manual_review(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _session(namespace, tmp_path)
    calls = []

    def recover(payload):
        calls.append(1)
        return {
            "ok": True,
            "returncode": 0,
            "error": "",
            "broker_write_performed": False,
            "json": {"status": "CRYPTO_PAPER_FIRST_CANARY_HALTED_MANUAL_REVIEW", "retry_post": False},
        }

    monkeypatch.setattr(namespace["base"].safe, "_recover", recover)
    monkeypatch.setattr(
        namespace["base"].safe,
        "_attempt_status",
        lambda *, workspace, attempt_id: {"phase": "RESOLVED", "resolved": True},
    )
    monkeypatch.setattr(namespace["time"], "sleep", lambda delay: None)

    result = session._auto_recover_if_needed({"recovery_get_only": True})
    assert result is not None
    assert result["manual_review_required"] is True
    assert result["auto_settlement_attempts"] == 1
    assert result["retry_post"] is False
    assert calls == [1]


def test_execute_maps_terminal_flat_recovery_to_simple_operator_result(tmp_path, monkeypatch) -> None:
    namespace = _module(monkeypatch)
    session, workspace = _session(namespace, tmp_path)
    monkeypatch.setattr(
        namespace["queue"].QueuedRecoverySession,
        "execute",
        lambda self, payload: {
            "ok": True,
            "phase": "RECOVERED_GET_ONLY",
            "recovery": {
                "ok": True,
                "auto_settlement_attempts": 2,
                "auto_settlement_resolved": True,
                "auto_settlement_exhausted": False,
                "manual_review_required": False,
                "retry_post": False,
                "broker_write_performed": False,
                "json": {
                    "status": "CRYPTO_PAPER_FIRST_CANARY_RECOVERED_FLAT_NO_RETRY",
                    "resulting_lifecycle_status": "FLAT_RECONCILED",
                },
            },
            "retry_post": False,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
        },
    )
    result = session.execute({"execute_confirmed": True})
    assert result["phase"] == "SETTLED_FLAT"
    assert result["headline"] == "Canary PAPER cerrado · sin exposición"
    assert result["auto_settlement"] is True
    assert result["retry_post"] is False


def test_terminal_classifier_distinguishes_filled_and_canceled(monkeypatch) -> None:
    namespace = _module(monkeypatch)
    classify = namespace["_terminal_operator_result"]

    filled = classify({"json": {"status": "FILLED", "lifecycle_status": "FILLED"}})
    canceled = classify({"json": {"status": "CANCELED", "lifecycle_status": "CANCELED"}})

    assert filled[0] == "SETTLED_FILLED"
    assert canceled[0] == "SETTLED_CANCELED"


def test_auto_settlement_surface_has_no_writer_or_direct_network_authority() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in (
        "real._run_execute(",
        "HttpsAlpacaPaperCryptoWriteTransport",
        "AlpacaPaperCryptoWriter",
        "urllib",
        "requests",
        "httpx",
        "socket",
        "APCA_API_SECRET_KEY",
        "R6_EXTERNAL_PAPER_WRITE=ENABLED",
    ):
        assert forbidden not in source
    assert "base.safe._recover(" in source
    assert "AUTO_SETTLE_DELAYS_SECONDS = (0.0, 1.0, 2.0, 4.0)" in source
    assert '"retry_post": False' in source
