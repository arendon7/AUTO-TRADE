from __future__ import annotations

from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "AUTO_TRADE_MAC.command"


def test_finder_launcher_exists_and_is_executable() -> None:
    assert LAUNCHER.is_file()
    assert LAUNCHER.stat().st_mode & stat.S_IXUSR


def test_finder_launcher_is_write_disabled_and_broker_inert() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert "export R6_EXTERNAL_PAPER_WRITE=DISABLED" in source
    assert '"${R6_EXTERNAL_PAPER_WRITE:-DISABLED}" == "ENABLED"' in source
    assert "unset APCA_API_KEY_ID" in source
    assert "unset APCA_API_SECRET_KEY" in source
    assert "scripts/mac_bootstrap.sh" in source
    assert "scripts/mac_start.sh" in source

    forbidden = (
        "r6_execute_paper_canary.py",
        "--execute-paper-canary",
        "alpaca_paper_writer",
        "alpaca_paper_execution_bridge",
        "stage_external_submission",
        "submit_once",
        "source .env",
        "curl ",
        "wget ",
    )
    for value in forbidden:
        assert value not in source


def test_finder_launcher_offers_only_safe_operator_actions() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert "run_safe init-workspace" in source
    assert "run_safe doctor" in source
    assert "run_safe rehearsal" in source
    assert "run_safe readiness" in source
    assert "account-preflight" in source
    assert "flat-account-preflight" in source
    assert "market-preflight" in source
    assert "account -> flat-account -> market" in source
    assert "Orden real desde este launcher: IMPOSIBLE" in source
