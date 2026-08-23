from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import autotrade.strategy_lab_promotion as promotion


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/mac_dashboard.py"
HUB_PATH = ROOT / "web/mac_multi_asset.html"
LAB_PATH = ROOT / "web/mac_strategy_lab.html"
SPEC = importlib.util.spec_from_file_location("mac_dashboard_w79_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
dashboard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dashboard
SPEC.loader.exec_module(dashboard)
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _thresholds():
    return promotion.build_strategy_promotion_threshold_policy(
        threshold_policy_id="thresholds-ui-001",
        development_campaign_id="development-ui-001",
        holdout_campaign_id="holdout-ui-001",
        holdout_trial_id="holdout-trial-ui-001",
        max_holm_adjusted_p=Decimal("0.05"),
        min_holdout_net_return=Decimal("0.01"),
        max_holdout_drawdown=Decimal("0.10"),
        min_holdout_fills=5,
        min_execution_fill_ratio=Decimal("0.50"),
        max_execution_adverse_slippage_bps=Decimal("8"),
    )


def _core_with_threshold(workspace: Path) -> Path:
    workspace.mkdir()
    path = workspace / "core.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE strategy_promotion_threshold_policies (
            threshold_policy_id TEXT PRIMARY KEY,
            threshold_policy_hash TEXT NOT NULL UNIQUE,
            development_campaign_id TEXT NOT NULL UNIQUE,
            holdout_campaign_id TEXT NOT NULL UNIQUE,
            registered_at TEXT NOT NULL,
            policy_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE strategy_promotion_policies (
            policy_id TEXT PRIMARY KEY,
            policy_hash TEXT NOT NULL UNIQUE,
            threshold_policy_id TEXT NOT NULL UNIQUE,
            threshold_policy_hash TEXT NOT NULL,
            development_campaign_id TEXT NOT NULL,
            holdout_campaign_id TEXT NOT NULL UNIQUE,
            registered_at TEXT NOT NULL,
            policy_json TEXT NOT NULL
        )
        """
    )
    thresholds = _thresholds()
    conn.execute(
        "INSERT INTO strategy_promotion_threshold_policies VALUES (?, ?, ?, ?, ?, ?)",
        (
            thresholds.threshold_policy_id,
            thresholds.threshold_policy_hash,
            thresholds.development_campaign_id,
            thresholds.holdout_campaign_id,
            NOW.isoformat(),
            _canonical(thresholds.to_dict()),
        ),
    )
    conn.commit()
    conn.close()
    return path


def test_strategy_lab_is_not_a_safe_post_action() -> None:
    assert "strategy_lab" not in dashboard.SAFE_ACTIONS
    assert "strategy-lab" not in dashboard.SAFE_ACTIONS
    assert set(dashboard.SAFE_ACTIONS) == {
        "init_workspace", "doctor", "rehearsal", "safety_rehearsal", "readiness", "status",
        "account_discovery", "account_preflight", "asset_preflight", "flat_account_preflight", "market_preflight",
        "build_candidate", "prepare_candidate", "review_receipt", "crypto_rehearsal", "crypto_preview",
    }


def test_strategy_lab_meta_is_explicitly_read_only() -> None:
    meta = dashboard._build_meta()
    assert meta["strategy_lab_route"] == "/strategy-lab"
    assert meta["strategy_lab_read_only"] is True
    assert meta["strategy_lab_paper_candidate_authorized"] is False
    assert meta["strategy_lab_gate_evidence"] == "NOT_PERSISTED_BY_W79"
    assert meta["external_paper_write"] == "DISABLED"
    assert meta["capital_authority"] == "NONE"
    assert meta["live_trading"] == "BLOCKED"


def test_strategy_lab_server_helper_reads_existing_core_without_credentials(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    _core_with_threshold(workspace)
    monkeypatch.setenv(dashboard.KEY_ENV, "must-not-be-used")
    monkeypatch.setenv(dashboard.SECRET_ENV, "must-not-be-used")
    value = dashboard._strategy_lab_value(str(workspace))
    assert value["ok"] is True
    assert value["paper_candidate_authorized"] is False
    assert value["broker_network_used"] is False
    assert value["broker_write_performed"] is False
    assert value["credentials_used"] is False
    assert value["external_execution_authorized"] is False
    assert value["capital_authority"] == "NONE"
    assert value["live_trading"] == "BLOCKED"
    lab = value["strategy_lab"]
    assert lab["governance_state"] == "THRESHOLDS_PREREGISTERED"
    assert lab["threshold_count"] == 1
    assert lab["candidate_count"] == 0
    assert lab["gate_evidence_state"] == "NOT_PERSISTED_BY_W79"


def test_fail_closed_strategy_lab_response_never_grants_authority() -> None:
    value = dashboard._fail_closed_strategy_lab_value("broken evidence")
    assert value == {
        "ok": False,
        "error": "broken evidence",
        "paper_candidate_authorized": False,
        "broker_network_used": False,
        "broker_write_performed": False,
        "credentials_used": False,
        "external_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


def test_strategy_lab_html_has_get_only_operator_surface() -> None:
    html = LAB_PATH.read_text(encoding="utf-8")
    for anchor in (
        "AUTO-TRADE · Strategy Lab",
        "READ ONLY",
        "PAPER CANDIDATE · FALSE",
        "CAPITAL · NONE",
        "LIVE · BLOCKED",
        "Broker POST: NO",
        "NOT_PERSISTED_BY_W79",
        "Actualizar evidencia · GET",
        'fetch("/api/strategy-lab?workspace="',
        'method:"GET"',
        "los resultados de gates NO se sintetizan",
    ):
        assert anchor in html
    for forbidden in (
        'method:"POST"',
        "/api/action",
        "/api/rehearsal",
        "/api/canary-preview",
        "localStorage",
        "sessionStorage",
        'type="password"',
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
    ):
        assert forbidden not in html


def test_multi_asset_hub_links_strategy_lab_without_claiming_authority() -> None:
    html = HUB_PATH.read_text(encoding="utf-8")
    for anchor in (
        "Strategy Lab",
        'href="/strategy-lab"',
        "SQLite mode=ro + query_only",
        "PAPER candidate FALSE · CAPITAL NONE · LIVE BLOCKED",
        "Broker POST desde Hub: NO",
    ):
        assert anchor in html
    for forbidden in ("localStorage", "sessionStorage", 'href="/api/strategy-lab"'):
        assert forbidden not in html
