from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mac_safety_rehearsal.py"


def run(*args: str) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(result.stdout) if result.returncode == 0 else {}
    return result, payload


def test_default_safety_rehearsal_is_kernel_approved_but_never_external_authority() -> None:
    result, payload = run()
    assert result.returncode == 0, result.stderr
    assert payload["mode"] == "LOCAL_SAFETY_REHEARSAL_ONLY"
    assert payload["risk_decision"]["status"] == "APPROVED"
    assert payload["risk_decision"]["reason_code"] == "APPROVED"
    assert payload["risk_decision"]["approved_notional"] == "25.00"
    assert payload["risk_decision_created_by"] == "CapitalSafetyKernel.evaluate"
    assert payload["ledger_event_count"] == 1
    assert payload["broker_network_used"] is False
    assert payload["broker_write_performed"] is False
    assert payload["oms_staging_performed"] is False
    assert payload["operator_authority_created"] is False
    assert payload["external_execution_authorized"] is False
    assert payload["capital_authority"] == "NONE"
    assert payload["profitability_claim"] is False
    assert payload["strategy_promotion_claim"] is False
    assert payload["live_trading_status"] == "BLOCKED"


def test_safety_rehearsal_rejects_stale_market() -> None:
    result, payload = run("--market-age-ms", "3001")
    assert result.returncode == 0
    assert payload["risk_decision"]["status"] == "REJECTED"
    assert payload["risk_decision"]["reason_code"] == "STALE_MARKET_DATA"


def test_safety_rehearsal_rejects_reconciliation_mismatch() -> None:
    result, payload = run("--reconciliation-failed")
    assert result.returncode == 0
    assert payload["risk_decision"]["reason_code"] == "RECONCILIATION_MISMATCH"


def test_safety_rehearsal_rejects_unknown_broker_state() -> None:
    result, payload = run("--broker-state-unknown")
    assert result.returncode == 0
    assert payload["risk_decision"]["reason_code"] == "BROKER_STATE_UNKNOWN"


def test_safety_rehearsal_rejects_kill_switch_for_new_risk() -> None:
    result, payload = run("--kill-switch")
    assert result.returncode == 0
    assert payload["risk_decision"]["reason_code"] == "KILL_SWITCH_ACTIVE"
    assert payload["ledger_event_count"] == 2


def test_safety_rehearsal_rejects_symbol_not_in_fixed_rehearsal_allowlist() -> None:
    result, payload = run("--symbol", "TSLA")
    assert result.returncode == 0
    assert payload["risk_decision"]["reason_code"] == "SYMBOL_NOT_ALLOWED"


def test_safety_rehearsal_rejects_market_order_under_fixed_limit_only_policy() -> None:
    result, payload = run("--order-type", "MARKET")
    assert result.returncode == 0
    assert payload["risk_decision"]["reason_code"] == "ORDER_TYPE_NOT_ALLOWED"


def test_safety_rehearsal_rejects_order_above_fixed_notional_cap() -> None:
    result, payload = run("--quantity", "2")
    assert result.returncode == 0
    assert payload["risk_decision"]["reason_code"] == "MAX_ORDER_NOTIONAL"


def test_safety_rehearsal_rejects_excessive_open_orders() -> None:
    result, payload = run("--open-orders", "5")
    assert result.returncode == 0
    assert payload["risk_decision"]["reason_code"] == "MAX_OPEN_ORDERS"


def test_safety_rehearsal_fixed_limits_cannot_be_inflated_from_cli() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--max-order-notional",
            "1000000",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr
