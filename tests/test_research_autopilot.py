from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.research.autopilot import (
    DevelopmentResearchAutopilot,
    DevelopmentSelectionPolicy,
)
from autotrade.research.backtest import BacktestConfig
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.market import Bar, InstrumentMetadata, MarketDataset
from autotrade.research.strategy_space import StrategyProgram, StrategySearchSpace
from autotrade.research.trials import SQLiteTrialLedger, TrialStatus


def _dataset(now, *, count: int = 33) -> MarketDataset:
    instrument = InstrumentMetadata(
        symbol="BTCUSDT",
        venue="BINANCE_SPOT",
        quote_currency="USDT",
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.00001"),
    )
    bars: list[Bar] = []
    price = Decimal("100")
    for index in range(count):
        # Persistent drift plus deterministic oscillation gives trend and
        # contrarian candidates non-degenerate closed-bar observations.
        price += Decimal("1.1") if index % 5 else Decimal("-1.4")
        bars.append(
            Bar(
                symbol=instrument.symbol,
                started_at=now + timedelta(hours=index),
                timeframe_seconds=3600,
                open=price - Decimal("0.2"),
                high=price + Decimal("0.8"),
                low=price - Decimal("0.8"),
                close=price,
                volume=Decimal("100000"),
            )
        )
    return MarketDataset(
        instrument=instrument,
        bars=tuple(bars),
        source="r7-autopilot-test-fixture",
    )


def _config() -> BacktestConfig:
    return BacktestConfig(
        initial_cash=Decimal("100000"),
        cost_model=ExecutionCostModel(
            fee_bps=Decimal("5"),
            half_spread_bps=Decimal("2"),
            slippage_bps=Decimal("2"),
        ),
        execution_delay_bars=1,
        annualization_factor=Decimal("8760"),
        max_leverage=Decimal("1"),
        max_volume_participation=Decimal("0.01"),
        allow_short=True,
    )


def _program() -> StrategyProgram:
    momentum = StrategySearchSpace(
        family_id="tsmom",
        strategy_version="1.0.0",
        kind="time_series_momentum",
        dimensions={
            "lookback_bars": (2, 4),
            "order_quantity": ("1",),
            "entry_threshold": ("0",),
            "position_mode": ("long_short",),
        },
        max_candidates=4,
    )
    mean_reversion = StrategySearchSpace(
        family_id="meanrev",
        strategy_version="1.0.0",
        kind="mean_reversion_zscore",
        dimensions={
            "lookback_bars": (5,),
            "entry_z": ("1", "1.5"),
            "exit_z": ("0.25",),
            "order_quantity": ("1",),
            "position_mode": ("long_short",),
        },
        max_candidates=4,
    )
    return StrategyProgram(
        program_id="r7-test-program",
        spaces=(momentum, mean_reversion),
        max_total_candidates=8,
    )


def test_autopilot_preregisters_complete_program_and_ranks_development(tmp_path, now) -> None:
    ledger = SQLiteTrialLedger(tmp_path / "autopilot.db")
    program = _program()

    result = DevelopmentResearchAutopilot(ledger=ledger).run(
        campaign_id="campaign-r7",
        program=program,
        development_dataset=_dataset(now),
        backtest_config=_config(),
        selection_policy=DevelopmentSelectionPolicy(
            min_net_return=-1.0,
            min_sharpe=-1000.0,
            max_drawdown=1.0,
            min_profit_factor=0.0,
            min_fills=0,
        ),
        code_version="test-code-version",
        started_at=now,
        pbo_partitions=4,
    )

    accounting = ledger.require_complete_campaign("campaign-r7")
    assert accounting.expected_trial_ids == program.expected_trial_ids
    assert accounting.failed_trial_ids == ()
    assert len(accounting.completed_trial_ids) == program.candidate_count
    assert len(result.candidates) == program.candidate_count
    assert result.dataset_hash == _dataset(now).dataset_hash
    assert result.program_hash == program.canonical_hash
    assert result.tournament.campaign_id == "campaign-r7"
    assert result.tournament.winner_trial_id
    assert result.selected_trial_id == result.tournament.winner_trial_id
    assert set(result.policy_eligible_trial_ids) == set(program.expected_trial_ids)
    assert all(
        ledger.get_trial(trial_id).status is TrialStatus.COMPLETED  # type: ignore[union-attr]
        for trial_id in program.expected_trial_ids
    )


def test_autopilot_records_no_synthetic_p_values(tmp_path, now) -> None:
    ledger = SQLiteTrialLedger(tmp_path / "no-pvalues.db")

    DevelopmentResearchAutopilot(ledger=ledger).run(
        campaign_id="campaign-no-p",
        program=_program(),
        development_dataset=_dataset(now),
        backtest_config=_config(),
        selection_policy=DevelopmentSelectionPolicy(min_fills=0),
        code_version="test-code-version",
        started_at=now,
        pbo_partitions=4,
    )

    assert all(
        record.p_value is None for record in ledger.list_trials("campaign-no-p")
    )


def test_autopilot_policy_can_decline_every_candidate_without_erasing_trials(tmp_path, now) -> None:
    ledger = SQLiteTrialLedger(tmp_path / "strict-policy.db")

    result = DevelopmentResearchAutopilot(ledger=ledger).run(
        campaign_id="campaign-strict",
        program=_program(),
        development_dataset=_dataset(now),
        backtest_config=_config(),
        selection_policy=DevelopmentSelectionPolicy(
            min_net_return=100.0,
            min_sharpe=1000.0,
            max_drawdown=0.01,
            min_profit_factor=1000.0,
            min_fills=10000,
        ),
        code_version="test-code-version",
        started_at=now,
        pbo_partitions=4,
    )

    assert result.policy_eligible_trial_ids == ()
    assert result.selected_trial_id == ""
    assert len(ledger.require_complete_campaign("campaign-strict").completed_trial_ids) == 4
    # Statistical evidence can still use the complete frozen family even if the
    # commercial/risk selection policy accepts none of it.
    assert result.tournament.winner_trial_id


def test_autopilot_rejects_invalid_governance_inputs(tmp_path, now) -> None:
    autopilot = DevelopmentResearchAutopilot(
        ledger=SQLiteTrialLedger(tmp_path / "invalid.db")
    )

    with pytest.raises(ValueError, match="campaign_id"):
        autopilot.run(
            campaign_id="",
            program=_program(),
            development_dataset=_dataset(now),
            backtest_config=_config(),
            selection_policy=DevelopmentSelectionPolicy(),
            code_version="code",
            started_at=now,
            pbo_partitions=4,
        )

    with pytest.raises(ValueError, match="even integer"):
        autopilot.run(
            campaign_id="bad-partitions",
            program=_program(),
            development_dataset=_dataset(now),
            backtest_config=_config(),
            selection_policy=DevelopmentSelectionPolicy(),
            code_version="code",
            started_at=now,
            pbo_partitions=3,
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        autopilot.run(
            campaign_id="bad-time",
            program=_program(),
            development_dataset=_dataset(now),
            backtest_config=_config(),
            selection_policy=DevelopmentSelectionPolicy(),
            code_version="code",
            started_at=now.replace(tzinfo=None),
            pbo_partitions=4,
        )


def test_selection_policy_validation_is_fail_closed() -> None:
    with pytest.raises(ValueError, match="max_drawdown"):
        DevelopmentSelectionPolicy(max_drawdown=1.1)
    with pytest.raises(ValueError, match="min_profit_factor"):
        DevelopmentSelectionPolicy(min_profit_factor=-1)
    with pytest.raises(ValueError, match="min_fills"):
        DevelopmentSelectionPolicy(min_fills=-1)
