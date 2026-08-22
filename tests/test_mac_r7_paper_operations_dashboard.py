from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import mac_r7_paper_operations_dashboard as mac  # noqa: E402


class FakeOperationsSnapshot:
    def __init__(self, *, positions=(), open_orders=(), ready_for_close=False) -> None:
        self.portfolio = SimpleNamespace(positions=tuple(positions), open_orders=tuple(open_orders))
        self.ready_for_close_preparation = ready_for_close

    def to_dict(self) -> dict[str, object]:
        return {
            "environment": "PAPER",
            "position_count": len(self.portfolio.positions),
            "open_order_count": len(self.portfolio.open_orders),
            "blockers": [],
            "ready_for_close_preparation": self.ready_for_close_preparation,
            "broker_write_authorized": False,
            "retry_post": False,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
        }


class FakeCloseOperator:
    def __init__(self, *, pending: str | None = None, prepared=None, execute_result=None) -> None:
        self.pending = pending
        self.prepared = prepared or SimpleNamespace(summary=lambda: {"attempt_id_internal": "hidden", "symbol": "BTC/USD"})
        self.execute_result = execute_result or {
            "ok": True,
            "phase": "CLOSE_RECONCILED",
            "attempt_id_internal": "hidden",
            "broker_write_performed": True,
            "broker_post_attempt_burned": True,
            "broker_post_status": "accepted",
            "settlement": {"terminal": True, "flat": True, "next_action": "DONE_FLAT"},
            "retry_post": False,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
        }
        self.prepare_calls = 0
        self.approve_calls = 0
        self.execute_calls = 0
        self.recover_calls = 0

    def pending_recovery_attempt(self):
        return self.pending

    def prepare_full_close(self, **_kwargs):
        self.prepare_calls += 1
        return self.prepared

    def approve(self, **_kwargs):
        self.approve_calls += 1
        return object()

    def execute_once(self, **_kwargs):
        self.execute_calls += 1
        return self.execute_result

    def recover(self, **_kwargs):
        self.recover_calls += 1
        return self.execute_result


def _bind_close_operator(monkeypatch: pytest.MonkeyPatch, session: mac.PaperOperationsSession, fake: FakeCloseOperator) -> None:
    monkeypatch.setattr(session, "_close_operator", lambda: fake)


def test_policy_meta_uses_canonical_entry_limits_and_narrow_close_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(mac.CLOSE_WRITE_ENV, raising=False)
    meta = mac._policy_meta()
    assert meta == {
        "environment": "PAPER",
        "symbol": "BTC/USD",
        "side": "BUY",
        "order_type": "LIMIT",
        "time_in_force": "IOC",
        "min_notional_usd": "10",
        "target_notional_usd": "10.50",
        "hard_max_notional_usd": "12",
        "operations_get_only": True,
        "close_mode": "FULL_ONLY_FIRST_OPERATION",
        "close_side": "SELL",
        "close_order_type": "LIMIT",
        "close_time_in_force": "IOC",
        "close_max_slippage_bps": "25",
        "close_write_gate_enabled": False,
        "retry_post": False,
        "credentials_persisted": False,
        "live_trading": "BLOCKED",
    }


def test_operations_surface_allows_entry_only_when_flat_and_no_close_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    session = mac.PaperOperationsSession()
    monkeypatch.setattr(session, "_operations_snapshot", lambda: FakeOperationsSnapshot())
    _bind_close_operator(monkeypatch, session, FakeCloseOperator())

    result = session.operations()

    assert result["ok"] is True
    assert result["surface"] == "R7_PAPER_OPERATIONS"
    assert result["entry_preparation_allowed"] is True
    assert result["close_preparation_allowed"] is False
    assert result["close_recovery_pending"] is False
    assert result["close_execution_authorized"] is False
    assert result["broker_write_authorized"] is False
    assert result["retry_post"] is False
    assert result["credentials_persisted"] is False
    assert result["live_trading"] == "BLOCKED"


def test_operations_surface_exposes_close_readiness_but_not_write_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    session = mac.PaperOperationsSession()
    monkeypatch.setattr(
        session,
        "_operations_snapshot",
        lambda: FakeOperationsSnapshot(positions=(object(),), ready_for_close=True),
    )
    _bind_close_operator(monkeypatch, session, FakeCloseOperator())
    result = session.operations()
    assert result["entry_preparation_allowed"] is False
    assert result["close_preparation_allowed"] is True
    assert result["close_execution_authorized"] is False
    assert result["broker_write_authorized"] is False


def test_burned_close_recovery_blocks_new_entry_and_new_close(monkeypatch: pytest.MonkeyPatch) -> None:
    session = mac.PaperOperationsSession()
    fake = FakeCloseOperator(pending="internal-close-attempt")
    _bind_close_operator(monkeypatch, session, fake)
    monkeypatch.setattr(
        session,
        "_operations_snapshot",
        lambda: FakeOperationsSnapshot(positions=(object(),), ready_for_close=True),
    )
    result = session.operations()
    assert result["entry_preparation_allowed"] is False
    assert result["close_preparation_allowed"] is False
    assert result["close_recovery_pending"] is True
    with pytest.raises(mac.r6.base.UnifiedCanaryError, match="burned close attempt"):
        session._assert_no_existing_broker_exposure()


def test_operations_surface_blocks_entry_when_position_or_order_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    session = mac.PaperOperationsSession()
    _bind_close_operator(monkeypatch, session, FakeCloseOperator())

    monkeypatch.setattr(session, "_operations_snapshot", lambda: FakeOperationsSnapshot(positions=(object(),)))
    with pytest.raises(mac.r6.base.UnifiedCanaryError, match="existing position or open order"):
        session._assert_no_existing_broker_exposure()

    monkeypatch.setattr(session, "_operations_snapshot", lambda: FakeOperationsSnapshot(open_orders=(object(),)))
    with pytest.raises(mac.r6.base.UnifiedCanaryError, match="existing position or open order"):
        session._assert_no_existing_broker_exposure()


def test_operations_snapshot_uses_ephemeral_credentials_and_read_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    expected = FakeOperationsSnapshot()

    class FakeReadModel:
        def __init__(self, *, workspace_path: Path) -> None:
            captured["workspace"] = workspace_path

        def snapshot(self, *, credentials, now):
            captured["credential_reference"] = credentials.credential_reference
            captured["key_id"] = credentials.key_id
            captured["secret_key"] = credentials.secret_key
            captured["now"] = now
            return expected

    session = mac.PaperOperationsSession()
    session.workspace = tmp_path
    session.credentials = ("paper-key", "paper-secret")
    monkeypatch.setattr(mac, "PaperOperationsReadModel", FakeReadModel)

    actual = session._operations_snapshot()

    assert actual is expected
    assert captured["workspace"] == tmp_path
    assert captured["key_id"] == "paper-key"
    assert captured["secret_key"] == "paper-secret"
    assert captured["credential_reference"]
    assert captured["now"].tzinfo is not None  # type: ignore[union-attr]


def test_r7_session_keeps_r6_entry_methods_inherited_and_adds_only_close_facade_methods() -> None:
    own = mac.PaperOperationsSession.__dict__
    for method in ("prepare", "approve", "execute", "recover", "reset"):
        assert method not in own
    assert "connect" in own
    assert "operations" in own
    for method in ("close_prepare", "close_approve", "close_execute", "close_recover"):
        assert method in own


def test_close_prepare_and_approve_use_one_time_review_token(monkeypatch: pytest.MonkeyPatch) -> None:
    session = mac.PaperOperationsSession()
    session.credentials = ("paper-key", "paper-secret")
    fake = FakeCloseOperator()
    _bind_close_operator(monkeypatch, session, fake)
    monkeypatch.setattr(session, "_paper_credentials", lambda: object())

    prepared = session.close_prepare()
    token = prepared["review_token"]
    assert fake.prepare_calls == 1
    assert prepared["summary"] == {"symbol": "BTC/USD"}
    assert prepared["broker_write_performed"] is False

    with pytest.raises(mac.r6.base.UnifiedCanaryError, match="stale"):
        session.close_approve({"close_review_confirmed": True, "close_review_token": "wrong"})

    monkeypatch.setattr(session, "_require_close_prepared", lambda: fake.prepared)
    approved = session.close_approve({"close_review_confirmed": True, "close_review_token": token})
    assert fake.approve_calls == 1
    assert approved["execute_token"]
    assert session.close_review_token is None
    with pytest.raises(mac.r6.base.UnifiedCanaryError, match="stale"):
        session.close_approve({"close_review_confirmed": True, "close_review_token": token})


def test_close_execute_consumes_token_before_facade_and_hides_internal_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    session = mac.PaperOperationsSession()
    session.credentials = ("paper-key", "paper-secret")
    fake = FakeCloseOperator()
    _bind_close_operator(monkeypatch, session, fake)
    monkeypatch.setattr(session, "_paper_credentials", lambda: object())
    monkeypatch.setattr(session, "_require_close_prepared", lambda: fake.prepared)
    session.close_execute_token = "one-shot-close-token"

    result = session.close_execute(
        {"close_execute_confirmed": True, "close_execute_token": "one-shot-close-token"}
    )
    assert fake.execute_calls == 1
    assert session.close_execute_token is None
    assert result["phase"] == "CLOSE_RECONCILED"
    assert "attempt_id_internal" not in result
    assert result["retry_post"] is False
    with pytest.raises(mac.r6.base.UnifiedCanaryError, match="stale or already consumed"):
        session.close_execute(
            {"close_execute_confirmed": True, "close_execute_token": "one-shot-close-token"}
        )
    assert fake.execute_calls == 1


def test_close_recover_calls_only_high_level_facade(monkeypatch: pytest.MonkeyPatch) -> None:
    session = mac.PaperOperationsSession()
    session.credentials = ("paper-key", "paper-secret")
    fake = FakeCloseOperator()
    _bind_close_operator(monkeypatch, session, fake)
    monkeypatch.setattr(session, "_paper_credentials", lambda: object())
    result = session.close_recover()
    assert fake.recover_calls == 1
    assert result["broker_write_performed"] is True  # sanitized fake result proves facade payload is preserved
    assert "attempt_id_internal" not in result


def test_r7_handler_exposes_exact_close_routes_but_no_low_level_close_authority() -> None:
    own = mac.PaperOperationsHandler.__dict__
    assert "do_GET" in own
    assert "do_POST" in own

    overlay = (SCRIPTS / "mac_r7_paper_operations_dashboard.py").read_text(encoding="utf-8")
    html = (ROOT / "web/mac_r7_paper_operations.html").read_text(encoding="utf-8")
    assert '"/api/operations"' in overlay
    for route in ("prepare", "approve", "execute", "recover"):
        assert overlay.count(f'"/api/close/{route}"') == 1
        assert f"post('/api/close/{route}'" in html
    for forbidden in (
        "paper_close_writer",
        "paper_close_execution_bridge",
        "paper_close_reconciliation",
        "PaperCloseWriter",
        "PaperCloseExecutionBridge",
        "submit_once(",
    ):
        assert forbidden not in overlay
    assert "CERRAR UNA VEZ EN PAPER" in html
    assert "Reconciliar cierre por GET" in html
    assert "NO vuelvas a pulsar cerrar" in html
    assert "ENTRY USD 10–12" in html
    assert "USD 1-5" not in html
    assert "USD 1–5" not in html
