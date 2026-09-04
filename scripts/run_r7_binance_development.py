from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from math import isfinite
from pathlib import Path

from autotrade.research.autopilot import (
    DevelopmentResearchAutopilot,
    DevelopmentSelectionPolicy,
)
from autotrade.research.backtest import BacktestConfig
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.external_data import (
    BINANCE_KLINES_PATH,
    BINANCE_PUBLIC_DATA_HOST,
    FIXED_INTERVAL_MS,
    BinanceKlineRange,
    BinanceSpotHistoricalProvider,
    PublicDataPolicy,
    UrllibReadOnlyTransport,
)
from autotrade.research.market import InstrumentMetadata
from autotrade.research.splits import create_temporal_split
from autotrade.research.strategy_space import StrategyProgram, StrategySearchSpace
from autotrade.research.trials import SQLiteTrialLedger


MILLISECONDS_PER_YEAR = Decimal("31557600000")  # 365.25 days


def _decimal(value: str) -> Decimal:
    result = Decimal(value)
    if not result.is_finite():
        raise argparse.ArgumentTypeError("value must be finite")
    return result


def _aware_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _json_number(value: float) -> float | str:
    if isfinite(value):
        return value
    return "Infinity" if value > 0 else "-Infinity"


def _build_program(*, quantity: Decimal, target_bar_volatility: Decimal) -> StrategyProgram:
    quantity_text = str(quantity)
    target_vol_text = str(target_bar_volatility)
    spaces = (
        StrategySearchSpace(
            family_id="tsmom",
            strategy_version="1.0.0",
            kind="time_series_momentum",
            dimensions={
                "lookback_bars": (24, 72, 168, 336),
                "order_quantity": (quantity_text,),
                "entry_threshold": ("0", "0.01"),
                "position_mode": ("long_flat",),
            },
            max_candidates=16,
        ),
        StrategySearchSpace(
            family_id="donchian",
            strategy_version="1.0.0",
            kind="donchian_breakout",
            dimensions={
                "lookback_bars": (24, 72, 168, 336),
                "order_quantity": (quantity_text,),
                "position_mode": ("long_flat",),
            },
            max_candidates=8,
        ),
        StrategySearchSpace(
            family_id="meanrev",
            strategy_version="1.0.0",
            kind="mean_reversion_zscore",
            dimensions={
                "lookback_bars": (24, 72, 168),
                "entry_z": ("1.5", "2"),
                "exit_z": ("0.25", "0.5"),
                "order_quantity": (quantity_text,),
                "position_mode": ("long_flat",),
            },
            max_candidates=16,
        ),
        StrategySearchSpace(
            family_id="volmom",
            strategy_version="1.0.0",
            kind="volatility_managed_momentum",
            dimensions={
                "momentum_lookback_bars": (24, 72, 168),
                "volatility_window_bars": (24, 72),
                "base_quantity": (quantity_text,),
                "target_bar_volatility": (target_vol_text,),
                "min_scale": ("0.25",),
                "max_scale": ("1.5",),
                "entry_threshold": ("0", "0.01"),
                "position_mode": ("long_flat",),
            },
            max_candidates=16,
        ),
    )
    return StrategyProgram(
        program_id="r7-binance-core",
        spaces=spaces,
        max_total_candidates=64,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a bounded R7 DEVELOPMENT-only research campaign against immutable "
            "Binance Spot public klines. Protected HOLDOUT is created but never checked out."
        )
    )
    parser.add_argument("--symbol", required=True, help="Binance spot symbol, e.g. BTCUSDT")
    parser.add_argument("--interval", required=True, choices=sorted(FIXED_INTERVAL_MS))
    parser.add_argument("--start", required=True, type=_aware_iso)
    parser.add_argument("--end", required=True, type=_aware_iso)
    parser.add_argument("--train-bars", required=True, type=int)
    parser.add_argument("--development-bars", required=True, type=int)
    parser.add_argument("--price-tick", required=True, type=_decimal)
    parser.add_argument("--quantity-step", required=True, type=_decimal)
    parser.add_argument("--research-quantity", required=True, type=_decimal)
    parser.add_argument("--target-bar-volatility", type=_decimal, default=Decimal("0.01"))
    parser.add_argument("--initial-cash", type=_decimal, default=Decimal("100000"))
    parser.add_argument("--fee-bps", type=_decimal, default=Decimal("10"))
    parser.add_argument("--half-spread-bps", type=_decimal, default=Decimal("2"))
    parser.add_argument("--slippage-bps", type=_decimal, default=Decimal("5"))
    parser.add_argument("--max-leverage", type=_decimal, default=Decimal("1"))
    parser.add_argument(
        "--max-volume-participation", type=_decimal, default=Decimal("0.01")
    )
    parser.add_argument("--min-net-return", type=float, default=0.0)
    parser.add_argument("--min-sharpe", type=float, default=0.5)
    parser.add_argument("--max-drawdown", type=float, default=0.25)
    parser.add_argument("--min-profit-factor", type=float, default=1.0)
    parser.add_argument("--min-fills", type=int, default=10)
    parser.add_argument("--pbo-partitions", type=int, default=8)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument(
        "--code-version",
        required=True,
        help="Immutable AUTO-TRADE commit/tag used for this campaign.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/r7"))
    parser.add_argument(
        "--enable-public-data",
        action="store_true",
        help="Required explicit opt-in for the bounded read-only Binance GET request.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not args.enable_public_data:
        raise SystemExit("refusing network access without --enable-public-data")
    if args.train_bars <= 0 or args.development_bars <= 0:
        raise SystemExit("train/development bars must be > 0")
    if args.research_quantity <= 0:
        raise SystemExit("research quantity must be > 0")

    instrument = InstrumentMetadata(
        symbol=args.symbol,
        venue="BINANCE_SPOT",
        quote_currency="USDT",
        price_tick=args.price_tick,
        quantity_step=args.quantity_step,
    )
    request = BinanceKlineRange(
        instrument=instrument,
        interval=args.interval,
        start=args.start,
        end=args.end,
    )
    if request.expected_bars <= args.train_bars + args.development_bars:
        raise SystemExit("requested range must leave at least one protected HOLDOUT bar")

    policy = PublicDataPolicy(
        allowed_host=BINANCE_PUBLIC_DATA_HOST,
        allowed_paths=frozenset({BINANCE_KLINES_PATH}),
    )
    provider = BinanceSpotHistoricalProvider(
        transport=UrllibReadOnlyTransport(policy=policy),
        enabled=True,
        max_total_bars=request.expected_bars,
    )
    artifact = provider.fetch(request)

    split = create_temporal_split(
        artifact.dataset,
        train_bars=args.train_bars,
        development_bars=args.development_bars,
    )
    # Intentionally do not call split.protected_holdout.checkout().

    interval_ms = Decimal(FIXED_INTERVAL_MS[args.interval])
    backtest_config = BacktestConfig(
        initial_cash=args.initial_cash,
        cost_model=ExecutionCostModel(
            fee_bps=args.fee_bps,
            half_spread_bps=args.half_spread_bps,
            slippage_bps=args.slippage_bps,
        ),
        execution_delay_bars=1,
        annualization_factor=MILLISECONDS_PER_YEAR / interval_ms,
        max_leverage=args.max_leverage,
        max_volume_participation=args.max_volume_participation,
        allow_short=False,
    )
    selection_policy = DevelopmentSelectionPolicy(
        min_net_return=args.min_net_return,
        min_sharpe=args.min_sharpe,
        max_drawdown=args.max_drawdown,
        min_profit_factor=args.min_profit_factor,
        min_fills=args.min_fills,
    )
    program = _build_program(
        quantity=args.research_quantity,
        target_bar_volatility=args.target_bar_volatility,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = args.output_dir / f"{args.campaign_id}-dataset.json"
    artifact.write(dataset_path)
    ledger = SQLiteTrialLedger(args.output_dir / f"{args.campaign_id}-trials.db")
    result = DevelopmentResearchAutopilot(ledger=ledger).run(
        campaign_id=args.campaign_id,
        program=program,
        development_dataset=split.development,
        backtest_config=backtest_config,
        selection_policy=selection_policy,
        code_version=args.code_version,
        started_at=datetime.now(timezone.utc),
        pbo_partitions=args.pbo_partitions,
    )

    report = {
        "campaign_id": result.campaign_id,
        "code_version": args.code_version,
        "program_hash": result.program_hash,
        "source_dataset_hash": artifact.dataset.dataset_hash,
        "development_dataset_hash": result.dataset_hash,
        "train_bars": len(split.train.bars),
        "development_bars": len(split.development.bars),
        "protected_holdout": {
            "dataset_hash": split.protected_holdout.dataset_hash,
            "bar_count": split.protected_holdout.bar_count,
            "checked_out": False,
        },
        "modeled_costs": backtest_config.cost_model.fingerprint_payload(),
        "backtest_config_hash": backtest_config.config_hash,
        "candidate_count": program.candidate_count,
        "policy_eligible_trial_ids": list(result.policy_eligible_trial_ids),
        "selected_trial_id": result.selected_trial_id,
        "tournament": result.tournament.to_payload(),
        "pbo": (
            {
                "pbo": result.pbo_evidence.pbo,
                "partitions": result.pbo_evidence.partitions,
                "combinations_evaluated": result.pbo_evidence.combinations_evaluated,
            }
            if result.pbo_evidence is not None
            else {"unavailable_reason": result.pbo_unavailable_reason}
        ),
        "deflated_sharpe": (
            {
                "selected_trial_id": result.deflated_sharpe_evidence.selected_trial_id,
                "selected_sharpe": result.deflated_sharpe_evidence.selected_sharpe,
                "expected_max_sharpe": result.deflated_sharpe_evidence.expected_max_sharpe,
                "probability": result.deflated_sharpe_evidence.deflated_sharpe_probability,
                "family_size": result.deflated_sharpe_evidence.family_size,
            }
            if result.deflated_sharpe_evidence is not None
            else {"unavailable_reason": result.deflated_sharpe_unavailable_reason}
        ),
        "candidates": [
            {
                "trial_id": item.trial_id,
                "strategy_id": item.strategy_id,
                "strategy_version": item.strategy_version,
                "eligible": item.eligible,
                "result_hash": item.backtest_result.result_hash,
                "metrics": {
                    "net_return": _json_number(item.backtest_result.metrics.net_return),
                    "sharpe": _json_number(item.backtest_result.metrics.sharpe),
                    "sortino": _json_number(item.backtest_result.metrics.sortino),
                    "max_drawdown": _json_number(item.backtest_result.metrics.max_drawdown),
                    "profit_factor": _json_number(item.backtest_result.metrics.profit_factor),
                    "turnover": _json_number(item.backtest_result.metrics.turnover),
                    "fills": item.backtest_result.metrics.fills,
                    "total_fees": _json_number(item.backtest_result.metrics.total_fees),
                },
            }
            for item in result.candidates
        ],
    }
    report_path = args.output_dir / f"{args.campaign_id}-report.json"
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
