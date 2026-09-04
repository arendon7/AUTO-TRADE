from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from autotrade.research.experiment_b import ExperimentBStrategySpec
from autotrade.research.external_data import ExternalDatasetArtifact
from autotrade.research.regime_stability import (
    RegimeStabilityConfig,
    RegimeStabilityEvaluator,
    RegimeStabilityPolicy,
)
from autotrade.research.splits import create_temporal_split

from append_r7_regime_evidence import (
    RegimeAppendError,
    _backtest_config,
    _evidence_payload,
    _load_report,
)


_KIND_BY_PREFIX = {
    "b-dma": "dual_moving_average_trend",
    "b-atr": "atr_impulse_breakout",
    "b-pullback": "trend_pullback",
}


def _decimal(value: str) -> Decimal:
    result = Decimal(value)
    if not result.is_finite():
        raise argparse.ArgumentTypeError("value must be finite")
    return result


def _kind(strategy_id: str) -> str:
    for prefix, kind in _KIND_BY_PREFIX.items():
        if strategy_id.startswith(prefix + "-"):
            return kind
    raise RegimeAppendError(f"unknown Experiment B strategy identity: {strategy_id}")


def _candidate_specs(report: dict[str, Any]) -> dict[str, ExperimentBStrategySpec]:
    requested = set(report["robustness_eligible_trial_ids"])
    specs: dict[str, ExperimentBStrategySpec] = {}
    for candidate in report["candidates"]:
        if not isinstance(candidate, dict):
            raise RegimeAppendError("candidate entry must be an object")
        trial_id = candidate.get("trial_id")
        if trial_id not in requested:
            continue
        strategy_id = candidate.get("strategy_id")
        version = candidate.get("strategy_version")
        parameters = candidate.get("parameters")
        if not isinstance(trial_id, str) or not isinstance(strategy_id, str):
            raise RegimeAppendError("candidate identity is invalid")
        if not isinstance(version, str) or not isinstance(parameters, dict):
            raise RegimeAppendError("candidate specification is invalid")
        specs[trial_id] = ExperimentBStrategySpec(
            strategy_id=strategy_id,
            strategy_version=version,
            kind=_kind(strategy_id),
            parameters=parameters,
        )
    missing = requested - set(specs)
    if missing:
        raise RegimeAppendError(
            f"robust Experiment B candidates missing from ledger: {sorted(missing)}"
        )
    return specs


def append_experiment_b_regime_evidence(
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
    if report.get("experiment") != "B" or report.get("program_id") != "r7-experiment-b":
        raise RegimeAppendError("report is not a preregistered Experiment B report")

    artifact = ExternalDatasetArtifact.read(dataset_path)
    if artifact.dataset.dataset_hash != report["source_dataset_hash"]:
        raise RegimeAppendError("source dataset hash mismatch")
    split = create_temporal_split(
        artifact.dataset,
        train_bars=int(report["train_bars"]),
        development_bars=int(report["development_bars"]),
    )
    if split.development.dataset_hash != report["development_dataset_hash"]:
        raise RegimeAppendError("DEVELOPMENT dataset hash mismatch")
    if split.protected_holdout.dataset_hash != report["protected_holdout"]["dataset_hash"]:
        raise RegimeAppendError("protected HOLDOUT hash changed")

    backtest_config = _backtest_config(
        report=report,
        initial_cash=initial_cash,
        max_leverage=max_leverage,
        max_volume_participation=max_volume_participation,
    )
    specs = _candidate_specs(report)
    evaluator = RegimeStabilityEvaluator()
    evidence = {
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

    selected = str(report.get("selected_trial_id") or "")
    selected_evidence = evidence.get(selected)
    selected_passed = bool(selected_evidence and selected_evidence.passed)
    statistical = str(report.get("promotion_ready_trial_id") or "")
    holdout_ready = (
        statistical
        if statistical and statistical == selected and selected_passed
        else ""
    )

    reasons: list[str] = []
    if not selected:
        reasons.append("NO_ROBUST_SELECTED_STRATEGY")
    if selected and not selected_passed:
        reasons.append("SELECTED_STRATEGY_REGIME_GATE_FAILED")
    if not statistical:
        reasons.append("STATISTICAL_PROMOTION_NOT_READY")
    if statistical and statistical != selected:
        reasons.append("STATISTICAL_SELECTION_IDENTITY_MISMATCH")

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
        "evaluated_trial_ids": sorted(evidence),
        "selected_trial_id": selected,
        "selected_passed": selected_passed,
        "candidates": [
            _evidence_payload(trial_id, evidence[trial_id])
            for trial_id in sorted(evidence)
        ],
    }
    report["holdout_ready_trial_id"] = holdout_ready
    report["holdout_gate"] = {
        "passed": bool(holdout_ready),
        "reasons": reasons,
        "requires": [
            "ROBUST_SELECTION",
            "STATISTICAL_GATE",
            "TRAIN_CALIBRATED_REGIME_STABILITY",
        ],
    }
    report["protected_holdout"]["checked_out"] = False
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Append TRAIN-calibrated regime evidence to Experiment B."
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

    report = append_experiment_b_regime_evidence(
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
        f"{args.report} experiment=B "
        f"regime_candidates={len(report['regime_stability_gate']['candidates'])} "
        f"holdout_ready={bool(report['holdout_ready_trial_id'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
