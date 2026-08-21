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
    def __init__(self, *, positions=(), open_orders=()) -> None:
        self.portfolio = SimpleNamespace(positions=tuple(positions), open_orders=tuple(open_orders))

    def to_dict(self) -> dict[str, object]:
        return {
            "environment": "PAPER",
            "position_count": len(self.portfolio.positions),
            "open_order_count": len(self.portfolio.open_orders),
            "blockers": [],
            "ready_for_close_preparation": bool(self.portfolio.positions) and not self.portfolio.open_orders,
            "broker_write_authorized": False,
            "retry_post": False,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
        }


def test_policy_meta_uses_canonical_first_canary_limits() -> None:
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
        "close_execution_authorized": False,
        "retry_post": False,
        "credentials_persisted": False,
        "live_trading": "BLOCKED",
    }


def test_operations_surface_is_read_only_and_allows_entry_only_when_flat(monkeypatch: pytest.MonkeyPatch) -> None:
    session = mac.PaperOperationsSession()
    monkeypatch.setattr(session, "_operations_snapshot", lambda: FakeOperationsSnapshot())

    result = session.operations()

    assert result["ok"] is True
    assert result["surface"] == "R7_PAPER_OPERATIONS_READ_ONLY"
    assert result["entry_preparation_allowed"] is True
    assert result["close_execution_authorized"] is False
    assert result["broker_write_authorized"] is False
    assert result["retry_post"] is False
    assert result["credentials_persisted"] is False
    assert result["live_trading"] == "BLOCKED"


def test_operations_surface_blocks_new_entry_when_position_or_order_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    session = mac.PaperOperationsSession()

    monkeypatch.setattr(
        session,
        "_operations_snapshot",
        lambda: FakeOperationsSnapshot(positions=(object(),)),
    )
    positioned = session.operations()
    assert positioned["entry_preparation_allowed"] is False
    with pytest.raises(mac.r6.base.UnifiedCanaryError, match="existing position or open order"):
        session._assert_no_existing_broker_exposure()

    monkeypatch.setattr(
        session,
        "_operations_snapshot",
        lambda: FakeOperationsSnapshot(open_orders=(object(),)),
    )
    ordered = session.operations()
    assert ordered["entry_preparation_allowed"] is False
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


def test_r7_session_does_not_override_certified_r6_authority_methods() -> None:
    own = mac.PaperOperationsSession.__dict__
    for method in ("connect", "prepare", "approve", "execute", "recover", "reset"):
        assert method not in own
    assert "operations" in own
    assert "_assert_no_existing_broker_exposure" in own


def test_r7_handler_adds_get_only_surface_and_no_close_route() -> None:
    own = mac.PaperOperationsHandler.__dict__
    assert "do_GET" in own
    assert "do_POST" not in own

    overlay = (SCRIPTS / "mac_r7_paper_operations_dashboard.py").read_text(encoding="utf-8")
    html = (ROOT / "web/mac_r7_paper_operations.html").read_text(encoding="utf-8")
    assert '"/api/operations"' in overlay
    assert "/api/close" not in overlay
    assert "/api/close" not in html
    assert "Aún no está habilitado." in html
    assert "ENTRY USD 10–12" in html
    assert "USD 1-5" not in html
    assert "USD 1–5" not in html
