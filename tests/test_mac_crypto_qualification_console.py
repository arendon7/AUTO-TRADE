from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "scripts/mac_crypto_dashboard.py"
HTML_PATH = ROOT / "web/mac_crypto_dashboard.html"
SPEC = importlib.util.spec_from_file_location("mac_crypto_dashboard_qualification_under_test", SERVER_PATH)
assert SPEC is not None and SPEC.loader is not None
dashboard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dashboard
SPEC.loader.exec_module(dashboard)


def test_crypto_dashboard_meta_exposes_preview_but_zero_write_authority() -> None:
    meta = dashboard._meta()
    assert meta["qualification_preview_available"] is True
    assert meta["qualification_preview_symbol"] == "BTC/USD"
    assert meta["qualification_preview_max_notional_usd"] == "5"
    assert meta["qualification_preview_target_notional_usd"] == "2"
    assert meta["qualification_preview_write_authority"] is False
    assert meta["paper_write"] == "DISABLED"
    assert meta["capital_authority"] == "NONE"
    assert meta["live_trading"] == "BLOCKED"
    assert meta["broker_order_surface"] is False


def test_crypto_dashboard_preview_routes_only_to_read_only_preview_child(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        payload = {
            "status": "CRYPTO_PAPER_QUALIFICATION_PREVIEW_PASS",
            "broker_write_performed": False,
            "external_post_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(dashboard.subprocess, "run", fake_run)
    result = dashboard._run_canary_preview(
        {
            "workspace": str(workspace),
            "symbol": "BTC/USD",
            "paper_key": "paper-key",
            "paper_secret": "paper-secret",
        }
    )
    assert result["ok"] is True
    command, kwargs = calls[0]
    joined = " ".join(command)
    assert "scripts/mac_crypto_canary_preview.py" in joined
    assert "--allow-paper-crypto-read" in command
    assert "BTC/USD" in command
    assert kwargs["env"][dashboard.WRITE_ENV] == "DISABLED"
    assert kwargs["env"][dashboard.KEY_ENV] == "paper-key"
    assert kwargs["env"][dashboard.SECRET_ENV] == "paper-secret"
    assert result["broker_write_performed"] is False
    assert result["external_post_authorized"] is False
    assert result["operator_approval_authority"] == "NONE"
    assert result["capital_authority"] == "NONE"
    assert result["live_trading"] == "BLOCKED"


def test_crypto_dashboard_promotes_structured_preview_block_reason(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def fake_run(command, **kwargs):
        payload = {
            "status": "CRYPTO_PAPER_QUALIFICATION_PREVIEW_BLOCKED",
            "reason": "coordinator preparation blocked: example",
            "broker_write_performed": False,
            "external_post_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        return SimpleNamespace(returncode=2, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(dashboard.subprocess, "run", fake_run)
    result = dashboard._run_canary_preview(
        {
            "workspace": str(workspace),
            "symbol": "BTC/USD",
            "paper_key": "paper-key",
            "paper_secret": "paper-secret",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "coordinator preparation blocked: example"
    assert result["json"]["status"] == "CRYPTO_PAPER_QUALIFICATION_PREVIEW_BLOCKED"
    assert result["broker_write_performed"] is False
    assert result["external_post_authorized"] is False


def test_crypto_dashboard_preview_rejects_eth_before_child_process(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        dashboard.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("ETH preview must be rejected before child process"),
    )
    with pytest.raises(dashboard.CryptoDashboardError, match="fixed to BTC/USD"):
        dashboard._run_canary_preview(
            {
                "workspace": str(workspace),
                "symbol": "ETH/USD",
                "paper_key": "paper-key",
                "paper_secret": "paper-secret",
            }
        )


def test_crypto_qualification_ui_requires_btc_rehearsal_and_has_no_send_control() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    for anchor in (
        "TD-R6-017 · preparación segura",
        "APPROVAL AUTHORITY · NONE",
        "Qualification Preview · BTC/USD",
        "BLOQUEADO HASTA PASS BTC",
        "Preparar canary · NO POST",
        "hard cap Safety USD 5",
        "function resetPreview(",
        "function previewCanary(",
        'fetch("/api/canary-preview"',
        'lastPass!=="BTC/USD"',
        "no se puede reutilizar en la ejecución real",
        "todavía no existe en esta interfaz ningún botón que pueda enviar una orden",
        "blind_retry = false",
    ):
        assert anchor in html
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "alpaca_paper_writer",
        "stage_external_submission",
        "FinalGuardedCryptoEntryTransport",
        "record_operator_approval",
    ):
        assert forbidden not in html
