from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/mac_dashboard.py"
HTML_PATH = ROOT / "web/mac_dashboard.html"
HUB_PATH = ROOT / "web/mac_multi_asset.html"
SPEC = importlib.util.spec_from_file_location("mac_dashboard_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
dashboard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dashboard
SPEC.loader.exec_module(dashboard)


def test_dashboard_allowlist_has_no_execution_or_final_freshness_surface() -> None:
    names = set(dashboard.SAFE_ACTIONS)
    assert names == {
        "init_workspace", "doctor", "rehearsal", "safety_rehearsal", "readiness", "status",
        "account_discovery", "account_preflight", "asset_preflight", "flat_account_preflight", "market_preflight",
        "build_candidate", "prepare_candidate", "review_receipt", "crypto_rehearsal", "crypto_preview",
    }
    joined = " ".join(sorted(names)).lower()
    for forbidden in ("post", "submit", "stage", "execute", "final_freshness", "live"):
        assert forbidden not in joined


def test_safe_env_always_disables_writer_and_strips_inherited_credentials(monkeypatch) -> None:
    monkeypatch.setenv(dashboard.WRITE_ENV, "ENABLED")
    monkeypatch.setenv(dashboard.KEY_ENV, "old-key")
    monkeypatch.setenv(dashboard.SECRET_ENV, "old-secret")
    env = dashboard._safe_env()
    assert env[dashboard.WRITE_ENV] == "DISABLED"
    assert dashboard.KEY_ENV not in env
    assert dashboard.SECRET_ENV not in env


def test_safe_env_injects_paper_credentials_only_for_requested_child(monkeypatch) -> None:
    monkeypatch.setenv(dashboard.KEY_ENV, "old-key")
    monkeypatch.setenv(dashboard.SECRET_ENV, "old-secret")
    env = dashboard._safe_env(paper_credentials=("new-key", "new-secret"))
    assert env[dashboard.WRITE_ENV] == "DISABLED"
    assert env[dashboard.KEY_ENV] == "new-key"
    assert env[dashboard.SECRET_ENV] == "new-secret"


def test_doctor_works_before_workspace_exists(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard, "PYTHON", tmp_path / "python")
    command, credentials = dashboard._command("doctor", {"workspace": str(tmp_path / "not-created")})
    assert command[-1] == "doctor"
    assert "--workspace" not in command
    assert credentials is None


def test_local_action_commands_route_through_safe_console(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard, "PYTHON", tmp_path / "python")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for action, expected in (
        ("doctor", "doctor"),
        ("readiness", "readiness"),
        ("status", "pre-canary-status"),
        ("build_candidate", "build-connectivity-candidate"),
        ("prepare_candidate", "prepare-connectivity-candidate"),
        ("review_receipt", "review-receipt"),
    ):
        command, credentials = dashboard._command(action, {"workspace": str(workspace)})
        joined = " ".join(command)
        assert "scripts/mac_safe_console.py" in joined
        assert expected in joined
        assert credentials is None
        for forbidden in (
            "r6_execute_paper_canary.py", "r6_connectivity_bound_final_freshness.py",
            "stage_external_submission", "alpaca_paper_writer",
        ):
            assert forbidden not in joined


def test_get_actions_require_ephemeral_paper_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard, "PYTHON", tmp_path / "python")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(dashboard.DashboardError, match="PAPER key and secret"):
        dashboard._command("asset_preflight", {"workspace": str(workspace), "symbol": "AAPL"})
    command, credentials = dashboard._command(
        "asset_preflight",
        {"workspace": str(workspace), "symbol": "AAPL", "paper_key": "k", "paper_secret": "s"},
    )
    assert credentials == ("k", "s")
    assert "--allow-paper-asset-read" in command


def test_crypto_rehearsal_is_native_safe_action_without_writer_surface(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard, "PYTHON", tmp_path / "python")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command, credentials = dashboard._command(
        "crypto_rehearsal",
        {
            "workspace": str(workspace),
            "paper_key": "paper-key",
            "paper_secret": "paper-secret",
        },
    )
    joined = " ".join(command)
    assert credentials == ("paper-key", "paper-secret")
    assert "scripts/mac_crypto_paper_rehearsal.py" in joined
    assert "--allow-paper-crypto-read" in joined
    for forbidden in (
        "alpaca_paper_writer", "r6_execute_paper_canary.py", "stage_external_submission",
        "r6_connectivity_bound_final_freshness.py",
    ):
        assert forbidden not in joined


def test_crypto_preview_is_primary_control_center_safe_action_without_writer_surface(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard, "PYTHON", tmp_path / "python")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command, credentials = dashboard._command(
        "crypto_preview",
        {
            "workspace": str(workspace),
            "symbol": "BTC/USD",
            "paper_key": "paper-key",
            "paper_secret": "paper-secret",
        },
    )
    joined = " ".join(command)
    assert credentials == ("paper-key", "paper-secret")
    assert "scripts/mac_crypto_canary_preview.py" in joined
    assert "--allow-paper-crypto-read" in joined
    for forbidden in (
        "alpaca_paper_writer", "r6_execute_paper_canary.py", "stage_external_submission",
        "r6_connectivity_bound_final_freshness.py",
    ):
        assert forbidden not in joined

    with pytest.raises(dashboard.DashboardError, match="fixed to BTC/USD"):
        dashboard._command(
            "crypto_preview",
            {
                "workspace": str(workspace),
                "symbol": "ETH/USD",
                "paper_key": "paper-key",
                "paper_secret": "paper-secret",
            },
        )


def test_account_discovery_is_get_only_and_needs_no_expected_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard, "PYTHON", tmp_path / "python")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command, credentials = dashboard._command(
        "account_discovery",
        {"workspace": str(workspace), "paper_key": "paper-key", "paper_secret": "paper-secret"},
    )
    joined = " ".join(command)
    assert credentials == ("paper-key", "paper-secret")
    assert "account-discovery" in joined
    assert "--allow-paper-account-discovery-read" in joined
    assert "--expected-account-id" not in joined
    assert "post" not in joined.lower()


def test_account_attestation_rejects_email_before_child_process(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard, "PYTHON", tmp_path / "python")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(dashboard.DashboardError, match="not an email"):
        dashboard._command(
            "account_preflight",
            {
                "workspace": str(workspace),
                "paper_key": "paper-key",
                "paper_secret": "paper-secret",
                "account_id": "person@example.com",
            },
        )


def test_redaction_removes_credentials_from_captured_output() -> None:
    redacted = dashboard._redact("key=abc secret=xyz", ("abc", "xyz"))
    assert "abc" not in redacted
    assert "xyz" not in redacted
    assert redacted.count("[REDACTED]") == 2


def test_dashboard_refuses_non_localhost_bind() -> None:
    with pytest.raises(dashboard.DashboardError, match="127.0.0.1"):
        dashboard._start_server("0.0.0.0", 0)


def test_native_multi_asset_meta_keeps_all_execution_disabled() -> None:
    meta = dashboard._build_meta()
    assert meta["native_multi_asset_control_center"] is True
    assert meta["asset_classes"] == ["US_EQUITY", "CRYPTO"]
    assert meta["equity_route"] == "/equities"
    assert meta["crypto_route"] == "/crypto"
    assert meta["crypto_rehearsal_available"] is True
    assert meta["qualification_preview_available"] is True
    assert meta["qualification_preview_symbol"] == "BTC/USD"
    assert meta["qualification_preview_max_notional_usd"] == "5"
    assert meta["qualification_preview_target_notional_usd"] == "2"
    assert meta["qualification_preview_write_authority"] is False
    assert meta["qualification_preview_response_recovery"] == "PRIMARY_CONTROL_CENTER_SAME_ATTEMPT_GET"
    assert meta["equity_execution_from_dashboard"] is False
    assert meta["crypto_execution_from_dashboard"] is False
    assert meta["order_execution_from_dashboard"] is False
    assert meta["external_paper_write"] == "DISABLED"
    assert meta["live_trading"] == "BLOCKED"


def test_native_multi_asset_hub_exposes_both_products_without_post_controls() -> None:
    html = HUB_PATH.read_text(encoding="utf-8")
    for anchor in (
        "AUTO-TRADE R6 · Multi-Asset",
        "US Equities",
        "Crypto 24/7",
        'href="/equities"',
        'href="/crypto"',
        "PAPER WRITE · DISABLED",
        "LIVE · BLOCKED",
        "Broker POST desde Hub: NO",
        "nunca reutiliza bracket equity",
    ):
        assert anchor in html
    for forbidden in (
        "localStorage", "sessionStorage", "r6_execute_paper_canary", "alpaca_paper_writer",
        "stage_external_submission",
    ):
        assert forbidden not in html


def test_macos_browser_open_prefers_usr_bin_open_without_shell(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    monkeypatch.setattr(dashboard.subprocess, "run", fake_run)
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda *_args, **_kwargs: pytest.fail("fallback must not run"))
    assert dashboard._open_browser("http://127.0.0.1:8765/") is True
    assert calls[0][0] == ["/usr/bin/open", "http://127.0.0.1:8765/"]
    assert calls[0][1].get("shell") is None
    assert calls[0][1]["check"] is False


def test_macos_browser_open_falls_back_without_killing_server(monkeypatch) -> None:
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    monkeypatch.setattr(dashboard.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no open")))
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda *_args, **_kwargs: True)
    assert dashboard._open_browser("http://127.0.0.1:8765/") is True


def test_dashboard_guides_first_use_and_prevents_out_of_order_clicks() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    for anchor in (
        "Empieza aquí",
        "Probar la app sin broker",
        "Alpaca PAPER, sólo lectura",
        "Strategy Lab",
        "TradingView no es necesario para R6",
        "friendlyError",
        "account_attestation.json",
        "connectivity_candidate.json",
        "applyGuards",
        "Completa primero el paso indicado como SIGUIENTE",
        "PRUEBA LOCAL TERMINADA",
    ):
        assert anchor in html
    assert 'const steps=["init_workspace","doctor","safety_rehearsal"]' in html
    assert 'currentGate()' in html
    assert 'c===gate' in html


def test_dashboard_discovers_account_before_explicit_attestation() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    for anchor in (
        "Identificar mi cuenta PAPER",
        "ID interno de cuenta PAPER detectado",
        "No pongas tu correo aquí",
        "Confirmar y verificar esta cuenta",
        'request("account_discovery"',
        "state.accountDiscovered=true",
        "Aún no se creó attestation",
        'value="AAPL"',
    ):
        assert anchor in html
    assert 'id="accountId" class="input" readonly' in html


def test_dashboard_keeps_technical_diagnostics_secondary_and_safe() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    assert "Actividad entendible" in html
    assert "Diagnóstico técnico" in html
    assert "fuera del dashboard" in html
    assert "NO DISPONIBLE" in html
    assert "Las credenciales no se guardan" in html
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "r6_execute_paper_canary",
        "r6_connectivity_bound_final_freshness",
    ):
        assert forbidden not in html
