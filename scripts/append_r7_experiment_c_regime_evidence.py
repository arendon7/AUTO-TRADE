from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from autotrade.research.experiment_c import ExperimentCStrategySpec
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
)


def _decimal(value: str) -> Decimal:
    result = Decimal(value)
    if not result.is_finite():
        raise argparse.ArgumentTypeError("value must be finite")
    return result


def _load_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegimeAppendError(f"cannot read Experiment C report: {exc}") from exc
    if not isinstance(report, dict):
        raise RegimeAppendError("Experiment C report root must be object")
    required = {
        "experiment",
        "program_id",
        "interval",
        "train_bars",
        "development_bars",
        "source_dataset_hash",
        "development_dataset_hash",
        "protected_holdout",
        "modeled_costs",
        "backtest_config_hash",
        "robustness_eligible_trial_ids",
        "selected_trial_id",
        "statistical_promotion_trial_id",
        "candidates",
    }
    missing = required - set(report)
    if missing:
        raise RegimeAppendError(f"Experiment C report missing {sorted(missing)}")
    if report["experiment"] != "C" or report["program_id"] != "r7-experiment-c":
        raise RegimeAppendError("report is not frozen Experiment C")
    holdout = report["protected_holdout"]
    if not isinstance(holdout, dict) or holdout.get("checked_out") is not False:
        raise RegimeAppendError("report does not prove untouched HOLDOUT")
    return report


def _candidate_specs(report: dict[str, Any]) -> dict[str, ExperimentCStrategySpec]:
    requested = set(report["robustness_eligible_trial_ids"])
    specs: dict[str, ExperimentCStrategySpec] = {}
    for candidate in report["candidates"]:
        if not isinstance(candidate, dict):
            raise RegimeAppendError("candidate entry must be object")
        trial_id = candidate.get("trial_id")
        if trial_id not in requested:
            continue
        strategy_id = candidate.get("strategy_id")
        version = candidate.get("strategy_version")
        kind = candidate.get("kind")
        parameters = candidate.get("parameters")
        if not all(isinstance(value, str) for value in (trial_id, strategy_id, version, kind)):
            raise RegimeAppendError("Experiment C candidate identity is invalid")
        if not isinstance(parameters, dict):
            raise RegimeAppendError("Experiment C candidate parameters must be object")
        specs[trial_id] = ExperimentCStrategySpec(
            strategy_id=strategy_id,
            strategy_version=version,
            kind=kind,
            parameters=parameters,
        )
    missing = requested - set(specs)
    if missing:
        raise RegimeAppendError(
            f"robust Experiment C candidates missing from report: {sorted(missing)}"
        )
    return specs


def append_experiment_c_regime_evidence(
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
    statistical = str(report.get("statistical_promotion_trial_id") or "")
    selected_evidence = evidence.get(selected)
    selected_regime_passed = bool(selected_evidence and selected_evidence.passed)
    per_campaign_ready = (
        selected
        if selected
        and selected == statistical
        and selected_regime_passed
        and report["statistical_gate"]["passed"] is True
        else ""
    )

    reasons: list[str] = []
    if not selected:
        reasons.append("NO_ROBUST_SELECTED_STRATEGY")
    if selected and not selected_regime_passed:
        reasons.append("SELECTED_STRATEGY_REGIME_GATE_FAILED")
    if not statistical:
        reasons.append("STATISTICAL_PROMOTION_NOT_READY")
    if statistical and statistical != selected:
        reasons.append("STATISTICAL_SELECTION_IDENTITY_MISMATCH")
    reasons.append("CROSS_MARKET_BREADTH_PENDING")

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
        "selected_passed": selected_regime_passed,
        "candidates": [
            _evidence_payload(trial_id, evidence[trial_id])
            for trial_id in sorted(evidence)
        ],
    }
    report["per_campaign_development_ready_trial_id"] = per_campaign_ready
    report["cross_market_breadth_gate"] = {
        "status": "PENDING_AGGREGATE",
        "passed": False,
        "promotion_authority": False,
    }
    # Explicitly impossible at per-campaign stage. Aggregate breadth is still pending.
    report["holdout_ready_trial_id"] = ""
    report["holdout_gate"] = {
        "passed": False,
        "reasons": reasons,
        "requires": [
            "ROBUST_SELECTION",
            "STATISTICAL_GATE",
            "TRAIN_CALIBRATED_REGIME_STABILITY",
            "PREREGISTERED_CROSS_MARKET_BREADTH",
            "SEPARATE_HOLDOUT_PERMIT_GOVERNANCE",
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
        description=(
            "Append TRAIN-calibrated regime evidence to Experiment C while keeping "
            "cross-market breadth pending and HOLDOUT inaccessible."
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

    report = append_experiment_c_regime_evidence(
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
        f"{args.report} experiment=C "
        f"regime_candidates={len(report['regime_stability_gate']['candidates'])} "
        f"per_campaign_ready={bool(report['per_campaign_development_ready_trial_id'])} "
        "holdout_ready=False"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
