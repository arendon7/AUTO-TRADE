from pathlib import Path
import runpy
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_first_canary_mac_dashboard.py"
DASHBOARD = ROOT / "scripts/mac_first_canary_dashboard.py"


def test_first_canary_mac_dashboard_boundary_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "first-canary Mac dashboard boundary: PASS" in result.stdout
    assert "execute endpoint absent" in result.stdout
    assert "credentials never persisted" in result.stdout
    assert "real POST OFF" in result.stdout
    assert "LIVE BLOCKED" in result.stdout


def test_first_canary_dashboard_binds_loopback_only() -> None:
    namespace = runpy.run_path(str(DASHBOARD))
    error = namespace["FirstCanaryDashboardError"]
    with pytest.raises(error, match="127.0.0.1"):
        namespace["_start_server"]("0.0.0.0", 0)


def test_first_canary_dashboard_child_env_forces_write_disabled(monkeypatch) -> None:
    namespace = runpy.run_path(str(DASHBOARD))
    monkeypatch.setenv("R6_EXTERNAL_PAPER_WRITE", "ENABLED")
    monkeypatch.setenv("APCA_API_KEY_ID", "parent-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "parent-secret")
    env = namespace["_safe_env"](("ephemeral-key", "ephemeral-secret"))
    assert env["R6_EXTERNAL_PAPER_WRITE"] == "DISABLED"
    assert env["APCA_API_KEY_ID"] == "ephemeral-key"
    assert env["APCA_API_SECRET_KEY"] == "ephemeral-secret"


def test_first_canary_dashboard_meta_keeps_real_execution_disabled() -> None:
    namespace = runpy.run_path(str(DASHBOARD))
    meta = namespace["_meta"]()
    assert meta["environment"] == "PAPER"
    assert meta["symbol"] == "BTC/USD"
    assert meta["hard_max_notional_usd"] == "5"
    assert meta["real_execution_enabled"] is False
    assert meta["generic_control_center_write_enabled"] is False
    assert meta["credentials_persisted"] is False
    assert meta["retry_post"] is False
    assert meta["live_trading"] == "BLOCKED"
