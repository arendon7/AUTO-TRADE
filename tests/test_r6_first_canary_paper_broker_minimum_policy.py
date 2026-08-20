from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import runpy

import pytest

import autotrade.first_canary_execution_gate as execution_gate
from autotrade.first_canary_paper_policy import (
    FIRST_CANARY_PAPER_MAX_ACCOUNT_FRACTION,
    FIRST_CANARY_PAPER_MAX_NOTIONAL,
    FIRST_CANARY_PAPER_MIN_NOTIONAL,
    FIRST_CANARY_PAPER_TARGET_NOTIONAL,
    validate_first_canary_notional,
)


ROOT = Path(__file__).resolve().parents[1]


def test_isolated_policy_matches_alpaca_crypto_usd_cost_basis_floor() -> None:
    assert FIRST_CANARY_PAPER_MIN_NOTIONAL == Decimal("10")
    assert FIRST_CANARY_PAPER_TARGET_NOTIONAL == Decimal("10.50")
    assert FIRST_CANARY_PAPER_MAX_NOTIONAL == Decimal("12")
    assert FIRST_CANARY_PAPER_MAX_ACCOUNT_FRACTION == Decimal("0.001")
    assert FIRST_CANARY_PAPER_MIN_NOTIONAL < FIRST_CANARY_PAPER_TARGET_NOTIONAL < FIRST_CANARY_PAPER_MAX_NOTIONAL


def test_policy_rejects_previous_two_dollar_canary() -> None:
    with pytest.raises(ValueError, match="USD 10-12"):
        validate_first_canary_notional(Decimal("2.000045696924"))
    validate_first_canary_notional(Decimal("10"))
    validate_first_canary_notional(Decimal("10.50"))
    validate_first_canary_notional(Decimal("12"))
    with pytest.raises(ValueError, match="USD 10-12"):
        validate_first_canary_notional(Decimal("12.000000001"))


def test_restart_safe_policy_lifts_qty_above_broker_cost_basis_even_if_asset_min_is_smaller() -> None:
    restart = runpy.run_path(str(ROOT / "scripts/mac_crypto_first_canary_prepare_restart_safe.py"))
    base = runpy.run_path(str(ROOT / "scripts/mac_crypto_first_canary_prepare.py"))
    prepare = base["prepare_first_canary"]
    restart["_bind_isolated_paper_policy"](prepare)

    quantity = prepare.__globals__["_quantity"](
        min_order_size=Decimal("0.000001"),
        min_trade_increment=Decimal("0.000000001"),
        limit_price=Decimal("68711.203"),
    )
    notional = quantity * Decimal("68711.203")

    assert notional >= FIRST_CANARY_PAPER_TARGET_NOTIONAL
    assert notional <= FIRST_CANARY_PAPER_MAX_NOTIONAL
    assert quantity > Decimal("0.000029108")


def test_execution_process_binds_same_isolated_notional_window() -> None:
    namespace = runpy.run_path(str(ROOT / "scripts/mac_crypto_first_canary_execute_real_paper.py"))
    old_min = execution_gate.MIN_NOTIONAL
    old_max = execution_gate.MAX_NOTIONAL
    try:
        namespace["_bind_isolated_execution_policy"]()
        assert execution_gate.MIN_NOTIONAL == FIRST_CANARY_PAPER_MIN_NOTIONAL
        assert execution_gate.MAX_NOTIONAL == FIRST_CANARY_PAPER_MAX_NOTIONAL
    finally:
        execution_gate.MIN_NOTIONAL = old_min
        execution_gate.MAX_NOTIONAL = old_max


def test_one_app_operator_copy_exposes_new_cap_and_never_old_five_dollar_cap() -> None:
    html = (ROOT / "web/mac_first_canary_unified.html").read_text(encoding="utf-8")
    assert "USD 10–12" in html
    assert "alrededor de USD 10.50" in html
    assert "máximo USD 12" in html
    assert "máx. USD 5" not in html
    assert "máximo USD 5" not in html
    assert "no supera USD 5" not in html
