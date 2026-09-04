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
    StatisticalSelectionPolicy,
)
from autotrade.research.backtest import BacktestConfig
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.experiment_c import build_experiment_c_program
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
from autotrade.research.robustness import (
    RobustnessPolicy,
    WalkForwardConfig,
    default_stress_scenarios,
)
from autotrade.research.splits import create_temporal_split
from autotrade.research.trials import SQLiteTrialLedger


MILLISECONDS_PER_YEAR = Decimal("31557600000")


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


def _optional_json_number(value: float | None) -> float | str | None:
    return None if value is None else _json_number(value)


def _pbo_payload(result) -> dict[str, object]:
    if result.pbo_evidence is not None:
        payload: dict[str, object] = {
            "pbo": result.pbo_evidence.pbo,
            "partitions": result.pbo_evidence.partitions,
            "combinations_evaluated": result.pbo_evidence.combinations_evaluated,
        }
    else:
        payload = {"unavailable_reason": result.pbo_unavailable_reason}
    diagnostics = result.pbo_diagnostics
    payload["diagnostics"] = (
        {
            "ready": diagnostics.ready,
            "partitions": diagnostics.partitions,
            "observations": diagnostics.observations,
            "combinations_evaluated": diagnostics.combinations_evaluated,
            "trial_count": diagnostics.trial_count,
            "blocking_trial_ids": list(diagnostics.blocking_trial_ids),
            "no_activity_trial_ids": list(diagnostics.no_activity_trial_ids),
            "trials": [
                {
                    "trial_id": item.trial_id,
                    "observations": item.observations,
                    "nonzero_observations": item.nonzero_observations,
                    "zero_variance_full_series": item.zero_variance_full_series,
                    "zero_variance_train_segments": item.zero_variance_train_segments,
                    "zero_variance_test_segments": item.zero_variance_test_segments,
                    "blocks_pbo": item.blocks_pbo,
                }
                for item in diagnostics.trials
            ],
        }
        if diagnostics is not None
        else None
    )
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run frozen R7 Experiment C against bounded Binance Spot public klines. "
            "DEVELOPMENT only; protected HOLDOUT is never checked out."
        )
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", required=True, choices=sorted(FIXED_INTERVAL_MS))
    parser.add_argument("--start", required=True, type=_aware_iso)
    parser.add_argument("--end", required=True, type=_aware_iso)
    parser.add_argument("--train-bars", required=True, type=int)
    parser.add_argument("--development-bars", required=True, type=int)
    parser.add_argument("--price-tick", required=True, type=_decimal)
    parser.add_argument("--quantity-step", required=True, type=_decimal)
    parser.add_argument("--research-quantity", required=True, type=_decimal)
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
    parser.add_argument("--walk-forward-train-bars", type=int, default=600)
    parser.add_argument("--walk-forward-evaluation-bars", type=int, default=300)
    parser.add_argument("--walk-forward-step-bars", type=int, default=300)
    parser.add_argument("--walk-forward-min-folds", type=int, default=3)
    parser.add_argument("--min-positive-fold-ratio", type=float, default=0.50)
    parser.add_argument("--min-median-fold-sharpe", type=float, default=0.0)
    parser.add_argument("--min-worst-fold-net-return", type=float, default=-0.10)
    parser.add_argument("--max-worst-fold-drawdown", type=float, default=0.30)
    parser.add_argument("--min-stress-pass-ratio", type=float, default=2 / 3)
    parser.add_argument("--min-worst-stress-net-return", type=float, default=-0.10)
    parser.add_argument("--max-worst-stress-drawdown", type=float, default=0.35)
    parser.add_argument(
        "--min-deflated-sharpe-probability", type=float, default=0.95
    )
    parser.add_argument("--max-pbo", type=float, default=0.50)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--code-version", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/r7-c"))
    parser.add_argument("--enable-public-data", action="store_true")
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
        raise SystemExit("requested range must leave protected HOLDOUT bars")

    provider = BinanceSpotHistoricalProvider(
        transport=UrllibReadOnlyTransport(
            policy=PublicDataPolicy(
                allowed_host=BINANCE_PUBLIC_DATA_HOST,
                allowed_paths=frozenset({BINANCE_KLINES_PATH}),
            )
        ),
        enabled=True,
        max_total_bars=request.expected_bars,
    )
    artifact = provider.fetch(request)
    split = create_temporal_split(
        artifact.dataset,
        train_bars=args.train_bars,
        development_bars=args.development_bars,
    )
    # Experiment C deliberately does not call split.protected_holdout.checkout().

    backtest_config = BacktestConfig(
        initial_cash=args.initial_cash,
        cost_model=ExecutionCostModel(
            fee_bps=args.fee_bps,
            half_spread_bps=args.half_spread_bps,
            slippage_bps=args.slippage_bps,
        ),
        execution_delay_bars=1,
        annualization_factor=(
            MILLISECONDS_PER_YEAR / Decimal(FIXED_INTERVAL_MS[args.interval])
        ),
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
    statistical_policy = StatisticalSelectionPolicy(
        min_deflated_sharpe_probability=args.min_deflated_sharpe_probability,
        max_pbo=args.max_pbo,
        require_pbo=True,
        require_deflated_sharpe=True,
    )
    robustness_policy = RobustnessPolicy(
        min_positive_fold_ratio=args.min_positive_fold_ratio,
        min_median_fold_sharpe=args.min_median_fold_sharpe,
        min_worst_fold_net_return=args.min_worst_fold_net_return,
        max_worst_fold_drawdown=args.max_worst_fold_drawdown,
        min_stress_pass_ratio=args.min_stress_pass_ratio,
        min_worst_stress_net_return=args.min_worst_stress_net_return,
        max_worst_stress_drawdown=args.max_worst_stress_drawdown,
    )
    walk_forward = WalkForwardConfig(
        train_bars=args.walk_forward_train_bars,
        evaluation_bars=args.walk_forward_evaluation_bars,
        step_bars=args.walk_forward_step_bars,
        min_folds=args.walk_forward_min_folds,
    )
    program = build_experiment_c_program(quantity=args.research_quantity)

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
        robustness_policy=robustness_policy,
        walk_forward_config=walk_forward,
        stress_scenarios=default_stress_scenarios(),
        statistical_policy=statistical_policy,
    )

    specs_by_trial = {program.trial_id_for(spec): spec for spec in program.candidates()}
    report = {
        "experiment": "C",
        "campaign_id": result.campaign_id,
        "code_version": args.code_version,
        "symbol": args.symbol,
        "interval": args.interval,
        "requested_start": args.start.isoformat(),
        "requested_end": args.end.isoformat(),
        "program_id": program.program_id,
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
        "tournament_selected_trial_id": result.tournament_selected_trial_id,
        "robustness_eligible_trial_ids": list(result.robustness_eligible_trial_ids),
        "selected_trial_id": result.selected_trial_id,
        "statistical_promotion_trial_id": result.promotion_ready_trial_id,
        "statistical_gate": {
            "passed": result.statistical_gate_passed,
            "reasons": list(result.statistical_gate_reasons),
            "policy": {
                "min_deflated_sharpe_probability": (
                    statistical_policy.min_deflated_sharpe_probability
                ),
                "max_pbo": statistical_policy.max_pbo,
                "require_pbo": True,
                "require_deflated_sharpe": True,
            },
        },
        "cross_market_breadth_gate": {
            "status": "PENDING_AGGREGATE",
            "passed": False,
            "promotion_authority": False,
        },
        "tournament": result.tournament.to_payload(),
        "robustness": [
            {
                "strategy_id": item.strategy_id,
                "strategy_version": item.strategy_version,
                "fingerprint": item.fingerprint,
                "passed": item.passed,
                "positive_fold_ratio": item.positive_fold_ratio,
                "median_fold_sharpe": _optional_json_number(item.median_fold_sharpe),
                "worst_fold_net_return": _optional_json_number(item.worst_fold_net_return),
                "worst_fold_drawdown": _optional_json_number(item.worst_fold_drawdown),
                "stress_pass_ratio": item.stress_pass_ratio,
                "worst_stress_net_return": _optional_json_number(
                    item.worst_stress_net_return
                ),
                "worst_stress_drawdown": _optional_json_number(
                    item.worst_stress_drawdown
                ),
                "walk_forward": [
                    {
                        "fold_index": fold.fold_index,
                        "dataset_hash": fold.dataset_hash,
                        "net_return": _optional_json_number(fold.net_return),
                        "sharpe": _optional_json_number(fold.sharpe),
                        "max_drawdown": _optional_json_number(fold.max_drawdown),
                        "fills": fold.fills,
                        "result_hash": fold.result_hash,
                        "failure_code": fold.failure_code,
                    }
                    for fold in item.walk_forward
                ],
                "stress": [
                    {
                        "scenario_id": stress.scenario_id,
                        "scenario_fingerprint": stress.scenario_fingerprint,
                        "config_hash": stress.config_hash,
                        "net_return": _optional_json_number(stress.net_return),
                        "sharpe": _optional_json_number(stress.sharpe),
                        "max_drawdown": _optional_json_number(stress.max_drawdown),
                        "fills": stress.fills,
                        "result_hash": stress.result_hash,
                        "failure_code": stress.failure_code,
                        "passed": stress.passed,
                    }
                    for stress in item.stress
                ],
            }
            for item in result.robustness_evidence
        ],
        "pbo": _pbo_payload(result),
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
                "kind": specs_by_trial[item.trial_id].kind,
                "parameters": item.backtest_result.strategy_parameters,
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
