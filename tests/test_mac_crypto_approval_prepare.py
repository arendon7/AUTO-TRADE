from __future__ import annotations

from decimal import Decimal
import importlib.util
from pathlib import Path
import sys

import pytest

from autotrade.domain import OrderType
from test_r6_paper_crypto_canary_coordinator import NOW, _prepare


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "mac_crypto_approval_prepare.py"
SPEC = importlib.util.spec_from_file_location("mac_crypto_approval_prepare_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
approval = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = approval
SPEC.loader.exec_module(approval)


def test_prepare_restores_preview_module_after_success(monkeypatch, tmp_path) -> None:
    prepared, _lifecycle = _prepare(tmp_path / "package")
    original_context = approval.preview.CryptoOperatorDecisionContext
    original_limits = approval.preview.SafetyLimits
    original_strategy = approval.preview.STRATEGY_ID
    seen: dict[str, object] = {}

    def fake_preview_run(*, workspace_path, credentials, now, symbol):
        del workspace_path, credentials, now
        assert symbol == "BTC/USD"
        assert approval.preview.CryptoOperatorDecisionContext is not original_context
        assert approval.preview.SafetyLimits is not original_limits
        assert approval.preview.STRATEGY_ID == approval.APPROVAL_STRATEGY_ID
        context = approval.preview.CryptoOperatorDecisionContext.from_prepared_package(
            prepared.package,
            attempt_id="ignored-by-approval-wrapper",
        )
        seen["context"] = context
        return {
            "status": "CRYPTO_PAPER_QUALIFICATION_PREVIEW_PASS",
            "mode": "DRY_RUN_NO_POST",
            "operator": {
                "approval_recorded": False,
                "decision_consumed": False,
                "dry_run_attempt_id": "old-preview-attempt",
                "dry_run_challenge": "old-preview-challenge",
                "execution_deadline": prepared.package.execution_deadline.isoformat(),
                "reusable_for_real_execution": False,
            },
            "entry": {"payload": prepared.broker_order.to_payload()},
            "broker_write_performed": False,
            "external_post_authorized": False,
            "operator_approval_authority": "NONE",
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }

    monkeypatch.setattr(approval.preview, "run", fake_preview_run)
    result = approval.run(
        workspace_path=tmp_path,
        credentials=object(),
        now=NOW,
        symbol="BTC/USD",
    )

    context = seen["context"]
    assert context.attempt_id.startswith("approval-uat-")
    assert result["status"] == "CRYPTO_PAPER_ONE_SHOT_APPROVAL_PREPARED"
    assert result["mode"] == "ONE_SHOT_APPROVAL_REHEARSAL_NO_POST"
    assert result["operator"]["approval_context"] == context.to_dict()
    assert result["operator"]["approval_challenge"] == approval.crypto_operator_confirmation_challenge(context)
    assert result["operator"]["approval_attempt_id"] == context.attempt_id
    assert result["operator"]["approval_recorded"] is False
    assert result["operator"]["decision_consumed"] is False
    assert result["operator"]["uat_only"] is True
    assert result["operator"]["reusable_for_real_execution"] is False
    assert result["operator_approval_authority"] == "PREPARED_NOT_RECORDED"
    assert result["broker_write_performed"] is False
    assert result["external_post_authorized"] is False
    assert result["capital_authority"] == "NONE"
    assert result["live_trading"] == "BLOCKED"

    assert approval.preview.CryptoOperatorDecisionContext is original_context
    assert approval.preview.SafetyLimits is original_limits
    assert approval.preview.STRATEGY_ID == original_strategy


def test_prepare_restores_preview_module_after_failure(monkeypatch, tmp_path) -> None:
    original_context = approval.preview.CryptoOperatorDecisionContext
    original_limits = approval.preview.SafetyLimits
    original_strategy = approval.preview.STRATEGY_ID

    def failing_preview_run(**_kwargs):
        assert approval.preview.CryptoOperatorDecisionContext is not original_context
        assert approval.preview.SafetyLimits is not original_limits
        assert approval.preview.STRATEGY_ID == approval.APPROVAL_STRATEGY_ID
        raise RuntimeError("synthetic preparation failure")

    monkeypatch.setattr(approval.preview, "run", failing_preview_run)
    with pytest.raises(RuntimeError, match="synthetic preparation failure"):
        approval.run(
            workspace_path=tmp_path,
            credentials=object(),
            now=NOW,
            symbol="BTC/USD",
        )

    assert approval.preview.CryptoOperatorDecisionContext is original_context
    assert approval.preview.SafetyLimits is original_limits
    assert approval.preview.STRATEGY_ID == original_strategy


def test_approval_safety_limits_override_only_decision_ttl(monkeypatch, tmp_path) -> None:
    prepared, _lifecycle = _prepare(tmp_path / "package")
    original_limits = approval.preview.SafetyLimits
    captured: dict[str, object] = {}

    def fake_preview_run(*, workspace_path, credentials, now, symbol):
        del workspace_path, credentials, now, symbol
        proxy = approval.preview.SafetyLimits
        limits = proxy(
            limits_version="approval-test",
            allowed_symbols=frozenset({"BTC/USD"}),
            allowed_order_types=frozenset({OrderType.LIMIT}),
            max_order_notional=Decimal("5"),
            max_position_notional=Decimal("5"),
            max_strategy_gross_exposure=Decimal("5"),
            max_portfolio_gross_exposure=Decimal("5"),
            max_net_exposure=Decimal("5"),
            max_leverage=Decimal("1"),
            max_daily_loss=Decimal("1"),
            max_drawdown=Decimal("0.01"),
            max_open_orders=1,
            stale_market_data_ms=60_000,
            price_deviation_bps=Decimal("100"),
            decision_ttl_ms=15_000,
        )
        captured["ttl"] = limits.decision_ttl_ms
        approval.preview.CryptoOperatorDecisionContext.from_prepared_package(
            prepared.package,
            attempt_id="ignored",
        )
        return {
            "operator": {"execution_deadline": prepared.package.execution_deadline.isoformat()},
            "entry": {},
            "broker_write_performed": False,
            "external_post_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }

    monkeypatch.setattr(approval.preview, "run", fake_preview_run)
    approval.run(workspace_path=tmp_path, credentials=object(), now=NOW, symbol="BTC/USD")
    assert captured["ttl"] == approval.APPROVAL_DECISION_TTL_MS
    assert approval.preview.SafetyLimits is original_limits
