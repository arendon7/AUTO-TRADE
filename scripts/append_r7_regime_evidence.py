from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from autotrade.research.backtest import BacktestConfig
from autotrade.research.costs import ExecutionCostModel
from autotrade.research.external_data import ExternalDatasetArtifact, FIXED_INTERVAL_MS
from autotrade.research.regime_stability import (
    RegimeStabilityConfig,
    RegimeStabilityEvaluator,
    RegimeStabilityPolicy,
)
from autotrade.research.splits import create_temporal_split
from autotrade.research.strategy_catalog import LibraryStrategySpec


MILLISECONDS_PER_YEAR = Decimal("31557600000")  # 365.25 days
_KIND_BY_PREFIX = {
    "tsmom": "time_series_momentum",
    "donchian": "donchian_breakout",
    "meanrev": "mean_reversion_zscore",
    "volmom": "volatility_managed_momentum",
}


class RegimeAppendError(RuntimeError):
    pass


def _decimal(value: str) -> Decimal:
    result = Decimal(value)
    if not result.is_finite():
        raise argparse.ArgumentTypeError("value must be finite")
    return result


def _load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegimeAppendError(f"cannot read report: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegimeAppendError("report root must be an object")
    required = {
        "interval",
        "train_bars",
        "development_bars",
        "source_dataset_hash",
        "development_dataset_hash",
        "protected_holdout",
        "modeled_costs",
        "backtest_config_hash",
        "robustness_eligible_trial_ids",
        "promotion_ready_trial_id",
        "selected_trial_id",
        "candidates",
    }
    missing = required - set(payload)
    if missing:
        raise RegimeAppendError(f"report missing required fields: {sorted(missing)}")
    holdout = payload["protected_holdout"]
    if not isinstance(holdout, dict) or holdout.get("checked_out") is not False:
        raise RegimeAppendError("report does not prove untouched HOLDOUT")
    return payload


def _strategy_kind(strategy_id: str) -> str:
    prefix = strategy_id.split("-", 1)[0]
    try:
        return _KIND_BY_PREFIX[prefix]
    except KeyError as exc:
        raise RegimeAppendError(f"unknown R7 strategy prefix: {prefix}") from exc


def _candidate_specs(report: dict[str, Any]) -> dict[str, LibraryStrategySpec]:
    requested = set(report["robustness_eligible_trial_ids"])
    specs: dict[str, LibraryStrategySpec] = {}
    for candidate in report["candidates"]:
        if not isinstance(candidate, dict):
            raise RegimeAppendError("candidate entry must be an object")
        trial_id = candidate.get("trial_id")
        if trial_id not in requested:
            continue
        strategy_id = candidate.get("strategy_id")
        strategy_version = candidate.get("strategy_version")
        parameters = candidate.get("parameters")
        if not isinstance(trial_id, str) or not isinstance(strategy_id, str):
            raise RegimeAppendError("candidate identity is invalid")
        if not isinstance(strategy_version, str) or not isinstance(parameters, dict):
            raise RegimeAppendError("candidate strategy specification is invalid")
        specs[trial_id] = LibraryStrategySpec(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            kind=_strategy_kind(strategy_id),
            parameters=parameters,
        )
    missing = requested - set(specs)
    if missing:
        raise RegimeAppendError(
            f"robust candidates missing from report candidate ledger: {sorted(missing)}"
        )
    return specs


def _backtest_config(
    *,
    report: dict[str, Any],
    initial_cash: Decimal,
    max_leverage: Decimal,
    max_volume_participation: Decimal,
) -> BacktestConfig:
    interval = report["interval"]
    if interval not in FIXED_INTERVAL_MS:
        raise RegimeAppendError(f"unsupported report interval: {interval}")
    costs = report["modeled_costs"]
    if not isinstance(costs, dict):
        raise RegimeAppendError("modeled_costs must be an object")
    model = ExecutionCostModel(
        fee_bps=Decimal(str(costs["fee_bps"])),
        half_spread_bps=Decimal(str(costs["half_spread_bps"])),
        slippage_bps=Decimal(str(costs["slippage_bps"])),
        allow_zero_total_costs=bool(costs.get("allow_zero_total_costs", False)),
    )
    config = BacktestConfig(
        initial_cash=initial_cash,
        cost_model=model,
        execution_delay_bars=1,
        annualization_factor=(
            MILLISECONDS_PER_YEAR / Decimal(FIXED_INTERVAL_MS[interval])
        ),
        max_leverage=max_leverage,
        max_volume_participation=max_volume_participation,
        allow_short=False,
    )
    if config.config_hash != report["backtest_config_hash"]:
        raise RegimeAppendError(
            "reconstructed BacktestConfig does not match campaign config hash"
        )
    return config


def _evidence_payload(trial_id: str, evidence) -> dict[str, Any]:
    return {
        "trial_id": trial_id,
        "strategy_id": evidence.strategy_id,
        "strategy_version": evidence.strategy_version,
        "model_fingerprint": evidence.model_fingerprint,
        "evaluation_fingerprint": evidence.evaluation_fingerprint,
        "volatility_window_bars": evidence.volatility_window_bars,
        "low_threshold": str(evidence.low_threshold),
        "high_threshold": str(evidence.high_threshold),
        "observed_states": [state.value for state in evidence.observed_states],
        "worst_state_compounded_return": evidence.worst_state_compounded_return,
        "worst_state_sharpe": evidence.worst_state_sharpe,
        "passed": evidence.passed,
        "reasons": list(evidence.reasons),
        "buckets": [
            {
                "state": bucket.state.value,
                "observations": bucket.observations,
                "mean_period_return": bucket.mean_period_return,
                "compounded_return": bucket.compounded_return,
                "annualized_sharpe": bucket.annualized_sharpe,
                "positive_return_ratio": bucket.positive_return_ratio,
            }
            for bucket in evidence.buckets
        ],
    }


def append_regime_evidence(
    *,
    report_path: Path,
    dataset_path: Path,
    initial_cash: Decimal,
    max_leverage: Decimal,
    max_volume_participation: Decimal,
    config: RegimeStabilityConfig,
    policy: RegimeStabilityPolicy,
) -> dict[str, Any]:
    report = _load_report(report_path)
    artifact = ExternalDatasetArtifact.read(dataset_path)
    if artifact.dataset.dataset_hash != report["source_dataset_hash"]:
        raise RegimeAppendError("source dataset hash does not match campaign report")

    split = create_temporal_split(
        artifact.dataset,
        train_bars=int(report["train_bars"]),
        development_bars=int(report["development_bars"]),
    )
    if split.development.dataset_hash != report["development_dataset_hash"]:
        raise RegimeAppendError("DEVELOPMENT dataset hash does not match campaign report")
    if split.protected_holdout.dataset_hash != report["protected_holdout"]["dataset_hash"]:
        raise RegimeAppendError("protected HOLDOUT hash changed during reconstruction")

    backtest_config = _backtest_config(
        report=report,
        initial_cash=initial_cash,
        max_leverage=max_leverage,
        max_volume_participation=max_volume_participation,
    )
    specs = _candidate_specs(report)
    evaluator = RegimeStabilityEvaluator()
    evidence_by_trial = {
        trial_id: evaluator.evaluate(
            candidate=spec,
            train_dataset=split.train,
            development_dataset=split.development,
            backtest_config=backtest_config,
            config=config,
            policy=policy,
        )
        for trial_id, spec in sorted(specs.items())
    }

    selected_trial_id = str(report.get("selected_trial_id") or "")
    selected_evidence = evidence_by_trial.get(selected_trial_id)
    selected_regime_passed = bool(selected_evidence and selected_evidence.passed)
    statistical_promotion = str(report.get("promotion_ready_trial_id") or "")
    holdout_ready = (
        statistical_promotion
        if statistical_promotion
        and statistical_promotion == selected_trial_id
        and selected_regime_passed
        else ""
    )

    holdout_reasons: list[str] = []
    if not statistical_promotion:
        holdout_reasons.append("STATISTICAL_PROMOTION_NOT_READY")
    if selected_trial_id and not selected_regime_passed:
        holdout_reasons.append("SELECTED_STRATEGY_REGIME_GATE_FAILED")
    if statistical_promotion and statistical_promotion != selected_trial_id:
        holdout_reasons.append("STATISTICAL_SELECTION_IDENTITY_MISMATCH")
    if not selected_trial_id:
        holdout_reasons.append("NO_ROBUST_SELECTED_STRATEGY")

    report["regime_stability_gate"] = {
        "calibration_phase": "TRAIN",
        "evaluation_phase": "DEVELOPMENT",
        "config": {
            "volatility_window_bars": config.volatility_window_bars,
            "low_quantile": str(config.low_quantile),
            "high_quantile": str(config.high_quantile),
            "min_calibration_observations": config.min_calibration_observations,
        },
        "policy": {
            "min_observed_states": policy.min_observed_states,
            "min_observations_per_state": policy.min_observations_per_state,
            "min_worst_state_compounded_return": (
                policy.min_worst_state_compounded_return
            ),
            "min_worst_state_sharpe": policy.min_worst_state_sharpe,
        },
        "evaluated_trial_ids": sorted(evidence_by_trial),
        "selected_trial_id": selected_trial_id,
        "selected_passed": selected_regime_passed,
        "candidates": [
            _evidence_payload(trial_id, evidence_by_trial[trial_id])
            for trial_id in sorted(evidence_by_trial)
        ],
    }
    report["holdout_ready_trial_id"] = holdout_ready
    report["holdout_gate"] = {
        "passed": bool(holdout_ready),
        "reasons": holdout_reasons,
        "requires": [
            "ROBUST_SELECTION",
            "STATISTICAL_GATE",
            "TRAIN_CALIBRATED_REGIME_STABILITY",
        ],
    }

    # This postprocessor never checks out HOLDOUT; preserve and re-assert the proof.
    report["protected_holdout"]["checked_out"] = False
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Append TRAIN-calibrated volatility-regime stability evidence to an "
            "existing R7 DEVELOPMENT report without checking out HOLDOUT."
        )
    )
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--initial-cash", type=_decimal, default=Decimal("100000"))
    parser.add_argument("--max-leverage", type=_decimal, default=Decimal("1"))
    parser.add_argument(
        "--max-volume-participation", type=_decimal, default=Decimal("0.01")
    )
    parser.add_argument("--volatility-window-bars", type=int, default=24)
    parser.add_argument("--low-quantile", type=_decimal, default=Decimal("0.33"))
    parser.add_argument("--high-quantile", type=_decimal, default=Decimal("0.67"))
    parser.add_argument("--min-calibration-observations", type=int, default=30)
    parser.add_argument("--min-observed-states", type=int, default=2)
    parser.add_argument("--min-observations-per-state", type=int, default=10)
    parser.add_argument("--min-worst-state-return", type=float, default=-0.10)
    parser.add_argument("--min-worst-state-sharpe", type=float, default=-1.0)
    args = parser.parse_args()

    report = append_regime_evidence(
        report_path=args.report,
        dataset_path=args.dataset,
        initial_cash=args.initial_cash,
        max_leverage=args.max_leverage,
        max_volume_participation=args.max_volume_participation,
        config=RegimeStabilityConfig(
            volatility_window_bars=args.volatility_window_bars,
            low_quantile=args.low_quantile,
            high_quantile=args.high_quantile,
            min_calibration_observations=args.min_calibration_observations,
        ),
        policy=RegimeStabilityPolicy(
            min_observed_states=args.min_observed_states,
            min_observations_per_state=args.min_observations_per_state,
            min_worst_state_compounded_return=args.min_worst_state_return,
            min_worst_state_sharpe=args.min_worst_state_sharpe,
        ),
    )
    print(
        f"{args.report} regime_candidates="
        f"{len(report['regime_stability_gate']['candidates'])} "
        f"holdout_ready={bool(report['holdout_ready_trial_id'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
